"""
===========================================
Verision 0.1
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
Both boards are named: this module opens 0x40, voltageMeasurementModule opens 0x45.
===========================================
"""


from ADS122C04 import ADS122C04, read_conversions, conversion_statistics
from ADS122C04 import conversion_period
from ADS122C04 import ADS122C04_ADDRESS_CURRENT
from ADS122C04 import ADS122C04_MUX_AIN0_AVSS, ADS122C04_PGA_ENABLED
from ADS122C04 import ADS122C04_VREF_EXT_REF_PINS
from ADS122C04 import ADS122C04_DATA_RATE_20SPS, ADS122C04_DATA_RATE_330SPS
from ADS122C04 import ADS122C04_DATA_RATE_1000SPS, ADS122C04_OP_MODE_NORMAL
import RPi.GPIO as gpio
import time

ADC_ADDRESS = ADS122C04_ADDRESS_CURRENT   # the current sense board, not the 0x45
                                          # voltage divider board
ADC_MUX = ADS122C04_MUX_AIN0_AVSS         # the amplifier output, PGA in circuit
ADC_INPUT_NAME = "AIN0"                   # the same pin, for printing

GPIO_GAIN_1 = 12
GPIO_GAIN_2 = 16

# ------------------------------- speed profiles -------------------------------
# v0.4 and earlier took 100 samples at 20 SPS with a fixed 50 ms sleep between reads,
# so one average cost 5.2 s and a measurement, which averages twice, cost 11.4 s.
# Raising the data rate and taking each conversion as DRDY offers it buys all of that
# back: "fast" averages 100 samples at 1000 SPS, so the same sample count over a
# 100 ms window instead of a 5 s one.
#
# Two things to know before trusting a fast reading:
#
#   range_settle is how long the analog front end is given after the gain and shunt
#   GPIOs change. 0.5 s was never measured, it was chosen to be safe. If the shorter
#   value here is too short the second reading of a measurement is taken while the
#   amplifier is still moving, so check it: with a steady load, "careful" and "fast"
#   should agree. If they do not, raise range_settle rather than distrusting the rate.
#
#   the averaging window is what a fluctuating load gets averaged over. 100 ms sees
#   five cycles of 50 Hz mains but only a slice of a DUT that wakes, transmits and
#   sleeps. For a load like that no short window is honest, and "careful" is the one
#   to quote.
#
#   samples       conversions averaged per reading
#   discard       conversions thrown away first, the ones that straddle a switch
#   range_settle  seconds to wait after the gain and shunt GPIOs change
#   adc_settle    seconds to wait after the ADC is configured, on top of the discards
PROFILES = {
	"fast" : {
		"data_rate" : ADS122C04_DATA_RATE_1000SPS,
		"op_mode" : ADS122C04_OP_MODE_NORMAL,
		"samples" : 100,
		"discard" : 4,
		"range_settle" : 0.05,
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
# The floor is well above the ADC's own noise, which is about 0.1 mV RMS a conversion
# at 1000 SPS, so this fires on the load moving rather than on the converter.
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
#
# PROVISIONAL. 2393.5 is not a measured reference voltage. It is the scale that makes
# the HIGH_GAIN and MID ranges agree on the 100 uA bench point of 2026-08-31, given the
# nominal gains above and one offset common to both ranges. It sits between the 2320 of
# v0.2 and the nominal 2500 of v0.3, and it reproduces both of those runs to 0.10000 mA.
# The same 4.5 percent could just as well be the gains or the shunt rather than the
# reference, one load point cannot tell them apart. Measure REFP-REFN with a meter, then
# run calibrate.py, which fits offset and gain per range from a zero and a known load and
# does not care what the reference actually is.
ADC_REF_VOLT = 2393.5  #mv
MAX_COUNT = 262144   # positive full scale: the ADS122C04 result is 24 bit and the
                     # driver drops the 5 noisy LSB, so 0x7FFFFF >> 5 = 2**18 counts
                     # at VREF. Correct as it stands, do not "fix" it to 2**17.

RS_LOW_CURRENT = 10
RS_HIGH_CURRENT = 1
# Effective LOW_RS shunt, MEASURED, not the 10||1 = 0.9090 the paper calculation gives.
# Fitted from two real bench points on 2026-08-31, 50 mA and 100 mA, which put the LOW_RS
# sensitivity at 15.289 mV/mA. Dividing by the 18.6428 amplifier gain leaves 0.8201 ohm.
# The 0.0889 ohm shortfall against the paper value is most likely the on resistance of the
# switch in the parallel leg, which the 10||1 calculation ignores. It could equally be the
# amplifier gain differing on this range; two points cannot tell those apart, and it does
# not matter for accuracy since both multiply the same way.
EQUIVALENT_RS = 0.8201

# Per range ADC offset in mV, subtracted before dividing by the gain.
# PROVISIONAL, same bench point and same caveat as ADC_REF_VOLT. v0.2 carried 127.34 and
# v0.3 carried 111 for all three ranges; both were single numbers trimmed on the HIGH_GAIN
# range only, which is why they survived there and fell apart on MID. Each range now has
# its own entry so calibrate.py can fill them in independently. LOW_RS has never been
# measured, it only inherits the fit, and it is the range where offset error hurts most:
# 12 mV of error there is 708 uA.
OFFSET_HIGH_GAIN = 117.78
OFFSET_MID = 117.78
# MEASURED on 2026-08-31 from the same two points, and nowhere near the 117.78 the other
# two ranges share. This is what made 50 mA read 56 mA: LOW_RS inherited an offset fitted
# on HIGH_GAIN. The -9.8% sensitivity error and the +197 mV offset error happened to cancel
# at 100 mA, so the one high current point that had been tested looked correct.
# STILL UNCONFIRMED: a 197 mV offset step just from switching the shunt is physically odd.
# Read this range at zero current, it should come back near 314.66 mV. If it comes back
# near 118 mV instead then the extra is current dependent, the straight line below is the
# wrong model, and this range needs a third point to find out what it really does.
OFFSET_LOW_RS = 314.66

# Ranges, case numbers are the ones the v0.2 log messages used
RANGE_HIGH_GAIN = "HIGH_GAIN"   # case 3, smallest currents
RANGE_MID = "MID"               # case 2, the range the probe is taken on
RANGE_LOW_RS = "LOW_RS"         # case 1, largest currents

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
# resolution. Derived from RANGES rather than written out, so editing the table above
# is enough. Most sensitive first, which is the order the range search walks.
RANGE_PREFERENCE = tuple(sorted(RANGES, key=lambda name : RANGES[name][2]*RANGES[name][3], reverse=True))

# BOTH MUST STAY BELOW ADC_REF_VOLT, currently 2393.5 mV. adc_volt is
# counts*ADC_REF_VOLT/MAX_COUNT, so a reading can never come out above the span.
# v0.3 had 2450 here against a span it assumed was 2500. Once the span was re fitted to
# 2393.5 that threshold was unreachable, over_range could never fire, and a clipped
# 200 mA load came back as a confident and wrong 134 mA.
# If ADC_REF_VOLT is ever re calibrated, re check both of these numbers by hand.
#
# 2345 is 98 percent of the span. Not derived, a guard band. Two percent rather than zero
# because ADC linearity is worst at the ends of the span, and because the profile's
# readings are averaged: a signal sitting just under full scale gets a few samples
# clipped, which drags the average down, so the result reads low without ever looking
# pegged. To replace the guess with a measurement, raise a load on LOW_RS until the
# reported mV stops tracking it and set this just under where it flattens.
#
# above this the ADC sits at the top of its span and the reading cannot be trusted
SATURATION_LIMIT_VOLT = 2345

# the same limit in counts: a conversion at or past this is the ADC reporting its own
# span rather than the current, so a mean containing any of them is a floor
CLIP_COUNTS = SATURATION_LIMIT_VOLT*MAX_COUNT/ADC_REF_VOLT

# Ceiling the range search keeps the reading under, 92 percent of the span. Held further
# down than SATURATION_LIMIT_VOLT so that a probe estimate which is a few percent
# optimistic still picks a range landing inside it rather than straight into over_range.
# In normal use this is the limit that acts. SATURATION_LIMIT_VOLT only decides anything
# on LOW_RS past about 131 mA, where there is no less sensitive range left to drop to.
RANGE_SELECT_CEILING_VOLT = 2200


class CurrentMeasurementModule :

	def __init__(self):
		print("default constuctor")
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
		runtime calibration step, so this is always False. calibrate.py measures the
		constants, it does not run as part of a measurement.
		"""
		print("calibation is not require")
		return False

	def registration_on_calibration_complete_callback(self):
		""" callback is called when calibation is completed.
		it is for callback registration function
		"""
		print("Calibration")

	def describe_range(self, range_name):
		""" One line saying what this range is made of, for the bench log.
		gain*shunt is the sensitivity, the mV the ADC moves per mA through the shunt, and
		it is the number that actually sets resolution on a range.
		"""
		gain_1, gain_2, gain, resistor, offset = RANGES[range_name]
		return ("{0} (case {1}) gain {2} Rs {3} ohm offset {4} mV, {5:.3f} mV/mA, "
			"usable to {6:.4f} mA, GPIO_GAIN_1 {7} GPIO_GAIN_2 {8}".format(
			range_name, CASE_NUMBER[range_name], gain, resistor, offset,
			gain*resistor, (RANGE_SELECT_CEILING_VOLT - offset)/(gain*resistor),
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

	def current_from_reading(self, adc_volt, range_name):
		""" Current in mA a reading in mV corresponds to on one range.
		"""
		_, _, gain, resistor, offset = RANGES[range_name]
		return (adc_volt - offset)/(gain*resistor)

	def reading_for_current(self, current, range_name):
		""" The inverse: what a current in mA would read in mV on one range.
		"""
		_, _, gain, resistor, offset = RANGES[range_name]
		return current*gain*resistor + offset

	def range_for_current(self, current):
		""" Most sensitive range that will not saturate at this current.
		v0.3 made this decision on the raw ADC voltage, which includes the offset, so the
		thresholds meant something quite different from what they looked like. Working in
		current keeps the two apart.
		"""
		for range_name in RANGE_PREFERENCE :
			if self.reading_for_current(current, range_name) < RANGE_SELECT_CEILING_VOLT :
				return range_name
		return RANGE_PREFERENCE[-1]

	def calculating_avg(self):
		""" Average the profile's conversions and return the result in mV.
		The amplifier output on ADC_MUX, gain 1 with the PGA in circuit, against the
		external reference. Each conversion is taken as DRDY offers it.
		"""
		self.adc_chip.set_continuous_mode(mux=ADC_MUX, gain=1,
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
		anything. A duty cycled supply does not, and the answer to that is not a
		better average, it is measuring the floor and the burst separately, see
		burstScan.py.
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
		stepping down, just one batch on the range asked for. That is what a sleep
		current test wants. Left to itself the probe may land on a burst and choose
		LOW_RS, where one mV of offset error is 65 uA and a 180 uA floor is 3 mV of
		signal, so the floor becomes unreadable. It also reports over_range rather
		than moving, because moving is the thing being avoided.

		The dict:
			current     - measured current in mA
			range       - range the current was measured on
			adc_volt    - averaged ADC reading in mV
			adc_counts  - averaged raw ADC counts
			volt_across_rs - drop across the shunt in mV, the DUT ground sits this far
			              above AVSS when the shunt is in the ground return
			over_range  - True when the ADC sat at the top of its span
			under_range - True when the reading fell below the range offset
			clipped     - conversions in the batch that sat at the top of the span
			steady      - False when the load moved across the batch, which makes
			              every average above a number no instant of the run was
			statistics  - the batch itself in mV: mean, median, p10, p90, minimum,
			              maximum, spread, samples, clipped
			current_median - the median conversion as a current, the estimator to
			              quote for a floor because a burst does not drag it
			current_min / current_max - the extremes as currents
		current is not valid when over_range or under_range is True, and it is an
		average rather than a description when steady is False.
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

		# The probe is taken on MID, where a small current is mostly offset, so its
		# estimate can be off by enough to pick a range that then saturates. Step down
		# until the reading fits or there is nothing less sensitive left.
		while not pinned and adc_volt >= SATURATION_LIMIT_VOLT \
				and range_name != RANGE_PREFERENCE[-1] :
			range_name = RANGE_PREFERENCE[RANGE_PREFERENCE.index(range_name) + 1]
			print("saturated, stepping down to", range_name)
			self.select_range(range_name)
			adc_volt = self.calculating_avg()

		_, _, gain, resistor, offset = RANGES[range_name]
		current = self.calculate_final_current(adc_volt, gain, resistor, offset)
		volt_across_rs = (adc_volt - offset)/gain

		over_range = adc_volt >= SATURATION_LIMIT_VOLT
		under_range = adc_volt < offset
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
		""" check measurement is running or not
		"""
		print("is measuring")
		return self.measure

	def register_on_measure_complete_callback(self):
		""" callback on complete callback
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

