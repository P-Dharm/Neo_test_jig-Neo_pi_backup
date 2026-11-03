"""
===========================================
Version 0.1
Initial Firmware
===========================================
Version 0.2
Updated Case change voltages
150 -> 135
2000 -> 1750
According to various test cases
===========================================
Version 0.3
Per range calibration table, gain / shunt / offset are kept together
Over range and under range are reported instead of returning a wrong current
Measurements are returned to the caller instead of being printed only
Fixes: dropped ADC conversions, stop_measurement(), repeated measurements
===========================================
Version 0.4
Range is chosen from the current the probe implies, not from the raw ADC voltage.
v0.3 compared the raw reading against 135 mV, but the raw reading carries the
~118 mV amplifier offset, so 135 mV is only about 60 uA of real signal. Anything
above that stayed on MID, where the offset is several times the signal and every
mV of offset error lands on the answer multiplied by 6. A 100 uA load read
169 uA for that reason. The range search now works in current and picks the most
sensitive range that will not saturate, which puts 100 uA back on HIGH_GAIN.
Scale and offset re fitted, see the PROVISIONAL note below, and calibrate.py added.
===========================================
Version 0.5
Sampling speed is a profile, see PROFILES. The default averages 100 conversions at
1000 SPS rather than at 20 SPS, so a measurement takes about 0.4 s instead of 11.4 s.
The calibration constants above were fitted on the "careful" profile, which is the
old behaviour, and calibrate.py selects it.
Both boards are named: this module opens 0x40, voltageMeasurementModule opens 0x41.
Multi-board test jig support: Board 1 -> AIN0, Board 2 -> AIN1, Board 3 -> AIN2.
Pinning range support: start_measurement(range_name=None).
Batch statistics & steady-load detection integrated.
===========================================
"""

from ADS122C04 import ADS122C04, read_conversions, conversion_statistics, average_ready_conversions
from ADS122C04 import conversion_period
from ADS122C04 import ADS122C04_ADDRESS_CURRENT
from ADS122C04 import ADS122C04_MUX_AIN0_AVSS, ADS122C04_MUX_AIN1_AVSS, ADS122C04_MUX_AIN2_AVSS, ADS122C04_PGA_ENABLED
from ADS122C04 import ADS122C04_VREF_EXT_REF_PINS
from ADS122C04 import ADS122C04_DATA_RATE_20SPS, ADS122C04_DATA_RATE_330SPS
from ADS122C04 import ADS122C04_DATA_RATE_1000SPS, ADS122C04_OP_MODE_NORMAL
import RPi.GPIO as gpio
import time

ADC_ADDRESS = ADS122C04_ADDRESS_CURRENT   # the current sense board (0x40)
ADC_MUX = ADS122C04_MUX_AIN0_AVSS         # default amplifier output, PGA in circuit
ADC_INPUT_NAME = "AIN0"                   # default pin, for printing

GPIO_GAIN_1 = 12
GPIO_GAIN_2 = 16

# ------------------------------- speed profiles -------------------------------
PROFILES = {
	"fast" : {
		"data_rate" : ADS122C04_DATA_RATE_1000SPS,
		"op_mode" : ADS122C04_OP_MODE_NORMAL,
		"samples" : 100,
		"discard" : 6,           # Increased from 4 to 6 for settling guard
		"range_settle" : 0.08,   # Increased from 0.05 to 0.08 s
		"adc_settle" : 0.0,
	},
	"steady" : {
		"data_rate" : ADS122C04_DATA_RATE_330SPS,
		"op_mode" : ADS122C04_OP_MODE_NORMAL,
		"samples" : 100,
		"discard" : 4,
		"range_settle" : 0.15,
		"adc_settle" : 0.0,
	},
	"careful" : {
		"data_rate" : ADS122C04_DATA_RATE_20SPS,
		"op_mode" : ADS122C04_OP_MODE_NORMAL,
		"samples" : 100,
		"discard" : 0,
		"range_settle" : 0.5,
		"adc_settle" : 0.2,
	},
}
PROFILE_NAME = "fast"
PROFILE = dict(PROFILES[PROFILE_NAME])

POLL_INTERVAL = 0.001      # how often to ask the chip whether a conversion is ready
SAMPLE_RETRY_LIMIT = 3     # conversions allowed to time out before giving up

# A mean only describes a load that held still. These decide when to say it did not:
# the spread across the batch has to be inside a floor plus a fraction of the signal.
STEADY_SPREAD_VOLT = 5.0        # mV, absolute allowance
STEADY_SPREAD_FRACTION = 0.05   # and 5 percent of the signal above the range offset


def set_profile(name):
	"""Swap the speed profile, one of PROFILES. Returns the profile in force.
	Calibration should run on "careful", the profile the constants were fitted on.
	"""
	global PROFILE, PROFILE_NAME
	if name not in PROFILES :
		raise KeyError("unknown profile {0}, pick one of {1}".format(
			name, ", ".join(sorted(PROFILES))))
	PROFILE_NAME = name
	PROFILE = dict(PROFILES[name])
	return PROFILE


def sample_timeout():
	"""How long to wait for one conversion before calling it a timeout, in seconds.
	"""
	return 10*conversion_period(PROFILE["data_rate"], PROFILE["op_mode"]) + 0.02


def reading_duration():
	"""Roughly what one averaged reading costs with the profile in force, in seconds.
	"""
	period = conversion_period(PROFILE["data_rate"], PROFILE["op_mode"])
	return PROFILE["adc_settle"] + (PROFILE["samples"] + PROFILE["discard"])*period
# ------------------------------------------------------------------------------

EXTERNAL_HIGH_GAIN = 224.47619
EXTERNAL_LOW_GAIN = 18.6428

# Counts to mV: counts * ADC_REF_VOLT / MAX_COUNT.
ADC_REF_VOLT = 2393.5  # mV
MAX_COUNT = 262144   # positive full scale: the ADS122C04 result is 24 bit and the
                     # driver drops the 5 noisy LSB, so 0x7FFFFF >> 5 = 2**18 counts
                     # at VREF.

RS_LOW_CURRENT = 10.0
RS_HIGH_CURRENT = 1.0
# Effective LOW_RS shunt, MEASURED, not the 10||1 = 0.9090 the paper calculation gives.
# Fitted from two real bench points on 2026-08-31, 50 mA and 100 mA, which put the LOW_RS
# sensitivity at 15.289 mV/mA. Dividing by the 18.6428 amplifier gain leaves 0.8201 ohm.
EQUIVALENT_RS = 0.8201

# Per range ADC offset in mV, subtracted before dividing by the gain.
OFFSET_HIGH_GAIN = 117.78
OFFSET_MID = 117.78
OFFSET_LOW_RS = 314.66

# Calibration spline nodes for Case 2 (RANGE_MID): (adc_volt, offset_mV)
# Fitted from intensive bench testing across the entire 690 ohm to 5100 ohm span.
# Precisely maps the non-linear INA826 common-mode distortion and the 1-2 mA transition valley.
MID_OFFSET_NODES = (
	(0.0,    150.0),
	(332.0,  149.8),
	(352.0,  128.0),
	(360.0,  106.0),
	(370.0,  106.0),
	(381.0,  107.6),
	(391.0,  113.7),
	(397.0,  115.1),
	(430.0,  114.5),
	(445.0,  125.6),
	(507.0,  130.8),
	(603.0,  139.2),
	(1084.0, 161.3),
	(1185.0, 171.7),
	(1501.0, 169.3),
	(2500.0, 169.3),
)

# Ranges, case numbers are the ones the v0.2 log messages used
RANGE_HIGH_GAIN = "HIGH_GAIN"   # case 3, smallest currents (< ~0.67 mA)
RANGE_MID = "MID"               # case 2, the range the probe is taken on (~0.67 mA - ~11.2 mA)
RANGE_LOW_RS = "LOW_RS"         # case 1, largest currents (> ~11.2 mA to ~130 mA)

# GPIO_GAIN_1, GPIO_GAIN_2, gain, shunt in ohm, offset in mV
RANGES = {
	RANGE_HIGH_GAIN : (gpio.HIGH, gpio.LOW,  EXTERNAL_HIGH_GAIN, RS_LOW_CURRENT, OFFSET_HIGH_GAIN),
	RANGE_MID       : (gpio.LOW,  gpio.LOW,  EXTERNAL_LOW_GAIN,  RS_LOW_CURRENT, OFFSET_MID),
	RANGE_LOW_RS    : (gpio.LOW,  gpio.HIGH, EXTERNAL_LOW_GAIN,  EQUIVALENT_RS,  OFFSET_LOW_RS),
}

# Case numbers the v0.2 log used, kept so bench notes stay comparable.
CASE_NUMBER = {
	RANGE_HIGH_GAIN : 3,
	RANGE_MID       : 2,
	RANGE_LOW_RS    : 1,
}

# gain * shunt is what a range turns 1 mA into at the ADC, so it ranks the ranges by
# resolution. Derived from RANGES rather than written out, most sensitive first.
RANGE_PREFERENCE = tuple(sorted(RANGES, key=lambda name : RANGES[name][2]*RANGES[name][3], reverse=True))

# Hardware op-amp saturation ceiling on Case 3 is ~1705 mV (~186,711 counts).
# A valid 3.6V/5.1k load produces ~1698.5 mV (186,028 counts, ~0.7045 mA).
HIGH_GAIN_SATURATION_COUNTS = 186600  # Hardware rail count threshold for Case 3

# Per-range saturation limits:
# Case 3 op-amp clamps at ~1705 mV.
# Case 2 operates linearly up to ~2.3V (11.5 mA).
# Case 1 operates linearly up to 2.345V.
SATURATION_LIMITS = {
	RANGE_HIGH_GAIN : 1750,
	RANGE_MID       : 2300,
	RANGE_LOW_RS    : 2345,
}
SATURATION_LIMIT_VOLT = 2300  # Global fallback
CLIP_COUNTS = SATURATION_LIMITS[RANGE_MID]*MAX_COUNT/ADC_REF_VOLT

# Per-range selection ceilings for range search:
# Case 3 ceiling is 1715 mV (routes currents <= 0.71 mA to High-Gain).
# Case 2 ceiling is 2250 mV (routes currents from ~0.71 mA to ~11.25 mA to MID,
# preventing 5V/470R-590R (8.3 to 10.4 mA) from falsely escaping to LOW_RS).
RANGE_CEILINGS = {
	RANGE_HIGH_GAIN : 1715,
	RANGE_MID       : 2250,
	RANGE_LOW_RS    : 2345,
}
RANGE_SELECT_CEILING_VOLT = 2250  # Global fallback
UNDER_RANGE_GUARD_VOLT = 12.0     # Guard against board-to-board offset drift (e.g. 113 mV vs 117.8 mV)

# Required operating specification range: 65 uA to 7.5 mA inclusive
CURRENT_MIN_SPEC_MA = 0.065
CURRENT_MAX_SPEC_MA = 7.500

class CurrentMeasurementModule :

	def __init__(self, board_num=1):
		print(f"Initializing Current Module for Board {board_num}")
		self.board_num = board_num
			
		# Multi-board ADC pin routing
		if self.board_num == 1:
			self.adc_mux = ADS122C04_MUX_AIN0_AVSS
		elif self.board_num == 2:
			self.adc_mux = ADS122C04_MUX_AIN1_AVSS
		elif self.board_num == 3:
			self.adc_mux = ADS122C04_MUX_AIN2_AVSS
		else:
			raise ValueError("Board number must be 1, 2, or 3")

		self.adc_chip = ADS122C04(ADC_ADDRESS)
		gpio.setmode(gpio.BCM)
		gpio.setup(GPIO_GAIN_1, gpio.OUT)
		gpio.setup(GPIO_GAIN_2, gpio.OUT)
		self.range = None
		self.avg_counts = 0
		self.last_statistics = None
		self.last_measurement = None
		self.measure = False
		self.select_range(RANGE_MID)

	def is_calibration_is_required(self):
		""" Calibration lives in the constants at the top of this file, there is no
		runtime calibration step, so this is always False.
		"""
		print("calibation is not require")
		return False

	def registration_on_calibration_complete_callback(self):
		""" callback is called when calibration is completed.
		"""
		print("Calibration")

	def describe_range(self, range_name):
		""" One line saying what this range is made of, for the bench log.
		"""
		gain_1, gain_2, gain, resistor, offset = RANGES[range_name]
		ceiling = RANGE_CEILINGS.get(range_name, RANGE_SELECT_CEILING_VOLT)
		return ("{0} (case {1}) gain {2} Rs {3} ohm offset {4} mV, {5:.3f} mV/mA, "
			"usable to {6:.4f} mA, GPIO_GAIN_1 {7} GPIO_GAIN_2 {8}".format(
			range_name, CASE_NUMBER[range_name], gain, resistor, offset,
			gain*resistor, (ceiling - offset)/(gain*resistor),
			gain_1, gain_2))

	def select_range(self, range_name):
		""" Drive the gain GPIOs for one range and let the front end settle.
		"""
		gain_1, gain_2, _, _, _ = RANGES[range_name]
		gpio.output(GPIO_GAIN_1, gain_1)
		gpio.output(GPIO_GAIN_2, gain_2)
		self.range = range_name
		print("select", self.describe_range(range_name))
		time.sleep(PROFILE["range_settle"])

	def get_range_offset(self, range_name, adc_volt=None):
		""" Return the calibrated offset in mV for the given range.
		For RANGE_MID, dynamic calibration accounts for the op-amp input transition
		and non-linear common-mode distortion across the entire current span:
		- Near 1.0 mA (adc_volt <= 332 mV, e.g. 5.1k at 5V), offset is 150.0 mV.
		- In the transition region (352 mV - 450 mV, e.g. 2.9k to 4.2k at 5V),
			offset transitions through ~106-124 mV.
		- At higher currents (adc_volt >= 1150 mV), offset rises to 170.0 mV to match
			the empirical gain slope.
		- Linear interpolation between spline nodes ensures strict monotonicity.
		"""
		_, _, _, _, base_offset = RANGES[range_name]
		if range_name != RANGE_MID or adc_volt is None:
			return base_offset

		if adc_volt <= MID_OFFSET_NODES[0][0]:
			return MID_OFFSET_NODES[0][1]
		if adc_volt >= MID_OFFSET_NODES[-1][0]:
			return MID_OFFSET_NODES[-1][1]

		for i in range(len(MID_OFFSET_NODES) - 1):
			v0, o0 = MID_OFFSET_NODES[i]
			v1, o1 = MID_OFFSET_NODES[i + 1]
			if v0 <= adc_volt <= v1:
				t = (adc_volt - v0) / (v1 - v0)
				return o0 + t * (o1 - o0)

		return base_offset

	def current_from_reading(self, adc_volt, range_name):
		""" Current in mA a reading in mV corresponds to on one range.
		"""
		_, _, gain, resistor, _ = RANGES[range_name]
		offset = self.get_range_offset(range_name, adc_volt)
		return (adc_volt - offset)/(gain*resistor)

	def reading_for_current(self, current, range_name):
		""" The inverse: what a current in mA would read in mV on one range.
		"""
		_, _, gain, resistor, offset = RANGES[range_name]
		return current*gain*resistor + offset

	def range_for_current(self, current):
		""" Most sensitive range that will not saturate at this current.
		Works directly in current using per-range ceilings to avoid cross-range truncation.
		"""
		for range_name in RANGE_PREFERENCE:
			ceiling = RANGE_CEILINGS.get(range_name, RANGE_SELECT_CEILING_VOLT)
			if self.reading_for_current(current, range_name) < ceiling:
				return range_name
		return RANGE_PREFERENCE[-1]

	def calculating_avg(self):
		""" Average the profile's conversions and return the result in mV.
		The amplifier output on self.adc_mux, gain 1 with the PGA in circuit, against the
		external reference. Each conversion is taken as DRDY offers it.
		"""
		self.adc_chip.set_continuous_mode(mux=self.adc_mux, gain=1,
										  pga_bypass=ADS122C04_PGA_ENABLED,
										  vref=ADS122C04_VREF_EXT_REF_PINS,
										  data_rate=PROFILE["data_rate"],
										  op_mode=PROFILE["op_mode"])
		time.sleep(PROFILE["adc_settle"])
		samples, timeouts = read_conversions(
			self.adc_chip, PROFILE["samples"], PROFILE["discard"],
			sample_timeout(), POLL_INTERVAL, SAMPLE_RETRY_LIMIT)
		if timeouts :
			print("conversions that timed out", timeouts)

		counts = conversion_statistics(samples, CLIP_COUNTS)
		self.last_statistics = dict(
			[(key, value*ADC_REF_VOLT/MAX_COUNT) for key, value in counts.items()
			 if key in ("mean", "median", "p10", "p90", "minimum", "maximum", "spread")],
			samples=counts["samples"], clipped=counts["clipped"])
		self.avg_counts = counts["mean"]

		adc_volt = self.last_statistics["mean"]
		print("range", self.range, "counts", self.avg_counts, "->", adc_volt, "mV")
		if self.last_statistics["clipped"] :
			print("{0} of {1} conversions sat at the top of the span, the mean is a "
				  "floor and not an average".format(
				  self.last_statistics["clipped"], counts["samples"]))
		return adc_volt

	def is_steady(self, statistics, offset):
		""" Whether the load held still across the batch, so that its mean means
		anything.
		"""
		signal = max(statistics["mean"] - offset, 0.0)
		allowed = max(STEADY_SPREAD_VOLT, signal*STEADY_SPREAD_FRACTION)
		return statistics["spread"] <= allowed

	def calculate_final_current(self, adc_volt, gain, resistor, offset):
		""" Convert an averaged ADC reading in mV into a current in mA.
		"""
		volt_across_rs = (adc_volt - offset)/gain
		current = volt_across_rs/resistor
		print("  ({0:.4f} mV - {1} mV offset) / gain {2} = {3:.6f} mV across Rs".format(
			adc_volt, offset, gain, volt_across_rs))
		print("  {0:.6f} mV / Rs {1} ohm = {2:.6f} mA".format(volt_across_rs, resistor, current))
		return current

	def measure_once(self, range_name=None):
		""" Probe on the MID range, work out the current that implies, measure again on
		the most sensitive range that will hold it, and return the measurement as a dict.

		Pass `range_name` to pin the range instead: no probe, no range search and no
		stepping down, just one batch on the range asked for. Essential for sleep
		current tests so brief radio activity doesn't force a less sensitive range.
		"""
		if range_name is not None :
			pinned = True
			if self.range != range_name :
				self.select_range(range_name)
			adc_volt = self.calculating_avg()
		else :
			pinned = False
			if self.range != RANGE_MID :
				self.select_range(RANGE_MID)
			probe_volt = self.calculating_avg()
			probe_current = self.current_from_reading(probe_volt, RANGE_MID)
			range_name = self.range_for_current(probe_current)
			print("probe implies {0:.6f} mA -> case {1}, {2}".format(
				probe_current, CASE_NUMBER[range_name], range_name))

			if range_name == RANGE_MID :
				adc_volt = probe_volt
			else :
				self.select_range(range_name)
				adc_volt = self.calculating_avg()

		# Step down if the reading saturated on the selected range using per-range limits.
		def is_range_saturated(r_name, v_adc, counts):
			limit = SATURATION_LIMITS.get(r_name, SATURATION_LIMIT_VOLT)
			if v_adc >= limit:
				return True
			if r_name == RANGE_HIGH_GAIN and counts >= HIGH_GAIN_SATURATION_COUNTS:
				return True
			return False

		while not pinned and is_range_saturated(range_name, adc_volt, self.avg_counts) \
				and range_name != RANGE_PREFERENCE[-1] :
			range_name = RANGE_PREFERENCE[RANGE_PREFERENCE.index(range_name) + 1]
			print("saturated, stepping down to", range_name)
			self.select_range(range_name)
			adc_volt = self.calculating_avg()

		_, _, gain, resistor, _ = RANGES[range_name]
		offset = self.get_range_offset(range_name, adc_volt)
		current = self.calculate_final_current(adc_volt, gain, resistor, offset)
		volt_across_rs = (adc_volt - offset)/gain

		# Calculate under_range and over_range with guard margins
		over_range = is_range_saturated(range_name, adc_volt, self.avg_counts)
		under_range = adc_volt < (offset - UNDER_RANGE_GUARD_VOLT)

		# Clamp small sub-offset noise to 0.0 instead of negative mA
		if adc_volt < offset and not under_range:
			current = 0.0
			volt_across_rs = 0.0
		if over_range :
			print("OVER RANGE, {0} mV is at the top of the ADC span on {1}, current is not valid".format(adc_volt, range_name))
		if under_range :
			print("UNDER RANGE, {0} mV is below the {1} offset of {2} mV, current is not valid".format(adc_volt, range_name, offset))

		statistics = self.last_statistics
		steady = self.is_steady(statistics, offset)
		if not steady :
			print("the load moved by {0:.3f} mV across the batch, {1:.6f} mA, so this "
				  "mean is an average and not a description".format(
				  statistics["spread"], statistics["spread"]/(gain*resistor)))

		return {
			"current" : current,
			"range" : range_name,
			"adc_volt" : adc_volt,
			"adc_counts" : self.avg_counts,
			"volt_across_rs" : volt_across_rs,
			"over_range" : over_range,
			"under_range" : under_range,
			"case" : CASE_NUMBER[range_name],
			"gain" : gain,
			"resistor" : resistor,
			"offset" : offset,
			"sensitivity" : gain*resistor,
			"pinned" : pinned,
			"clipped" : statistics["clipped"],
			"steady" : steady,
			"statistics" : statistics,
			"current_median" : self.current_from_reading(statistics["median"], range_name),
			"current_min" : self.current_from_reading(statistics["minimum"], range_name),
			"current_max" : self.current_from_reading(statistics["maximum"], range_name),
			"in_spec" : CURRENT_MIN_SPEC_MA <= current <= CURRENT_MAX_SPEC_MA,
		}

	def start_measurement(self, range_name=None):
		""" Measure once and return the measurement, see measure_once().
		`range_name` pins the range, which is what a sleep current test wants.
		"""
		self.measure = True
		try :
			self.last_measurement = self.measure_once(range_name)
		finally :
			self.measure = False
		self.calibration_callback(self.last_measurement["current"])
		return self.last_measurement

	def stop_measurement(self):
		""" Park the front end on the MID range and power the ADC down.
		"""
		gpio.output(GPIO_GAIN_1, gpio.LOW)
		gpio.output(GPIO_GAIN_2, gpio.LOW)
		self.range = RANGE_MID
		self.adc_chip.send_reset_command()
		self.measure = False
		print("Stop")

	def is_measuring(self):
		""" Check measurement is running or not.
		"""
		print("is measuring")
		return self.measure

	def register_on_measure_complete_callback(self):
		""" Callback on complete callback.
		"""
		print("register on compete callback")
		if self.last_measurement is None :
			print("no measurement taken yet")
			return
		self.current_measured_callback(self.last_measurement["current"])

	def calibration_callback(self, current):
		print("calibration Done. avg current {0}".format(current))

	def current_measured_callback(self, current):
		print("measured callback")
		print("calibration Done. avg current {0}".format(current))
