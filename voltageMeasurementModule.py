"""
===========================================
Version 2.0 (Updated for 3-in-1 Jig - Per Board V_charging)
Voltage measurement through a resistive divider on the ADS122C04 inputs.

Everything here talks to the voltage divider board's ADS122C04 at 0x41.
The current sense board is a second chip at 0x40 on the same bus.

Voltage routing per socket (Based on 3-in-1 Jig Schematic):
  Board 1 -> AIN0 (0x41)
  Board 2 -> AIN2 (0x41)
  Board 3 -> AIN3 (0x41)
  (AIN1 is unused for voltage)

The measured voltage represents the charging voltage (V_charging) for the
board currently undergoing test.

Divider on each tap:
  rail -> R1 (10k) -> ADC input -> R2 (4.7k) -> AGND
  ratio = (R1 + R2) / R2 = 14.7k / 4.7k = 3.12766

Each pin carries a calibrated bench drop and zero offset:
  pin_volt  = adc_volt - pin_offset + pin_drop
  rail_volt = pin_volt * ratio - ground_offset_volt

Measurements are single ended, AINx to AVSS, PGA bypassed, against AVDD.
Speed is set by PROFILE (fast, steady, careful).
===========================================
"""

from ADS122C04 import ADS122C04, average_ready_conversions, conversion_period
from ADS122C04 import ADS122C04_MUX_AIN0_AVSS, ADS122C04_MUX_AIN1_AVSS
from ADS122C04 import ADS122C04_MUX_AIN2_AVSS, ADS122C04_MUX_AIN3_AVSS
from ADS122C04 import ADS122C04_PGA_DISABLED
from ADS122C04 import ADS122C04_VREF_EXT_REF_PINS, ADS122C04_VREF_AVDD
from ADS122C04 import ADS122C04_DATA_RATE_20SPS, ADS122C04_DATA_RATE_330SPS
from ADS122C04 import ADS122C04_DATA_RATE_1000SPS
from ADS122C04 import ADS122C04_OP_MODE_NORMAL, ADS122C04_OP_MODE_TURBO
from ADS122C04 import ADS122C04_ADDRESS_VOLTAGE
import time


ADC_ADDRESS = ADS122C04_ADDRESS_VOLTAGE   # 0x41 voltage divider board

VERBOSE = False   # True to print every sample batch and channel as it is measured


def log(*args):
	"""Progress chatter, off unless VERBOSE. Results go back as a dict, not as print."""
	if VERBOSE:
		print(*args)


def divider_ratio(r_top, r_bottom):
	"""Multiply a divider voltage by this to get the voltage at the top of the divider."""
	return (r_top + r_bottom) / r_bottom


CHANNEL_V_CHARGING = "V_charging"

# ------------------------- the fitted dividers -------------------------
R_TOP = 10000.0         # ohm, rail to ADC input
R_BOTTOM = 4700.0       # ohm, ADC input to AGND
RATIO = divider_ratio(R_TOP, R_BOTTOM)

# Backwards compatibility resistor definitions
R_TOP_AIN0 = R_TOP
R_BOTTOM_AIN0 = R_BOTTOM
R_TOP_AIN2 = R_TOP
R_BOTTOM_AIN2 = R_BOTTOM
R_TOP_AIN3 = R_TOP
R_BOTTOM_AIN3 = R_BOTTOM
RATIO_AIN0 = RATIO
RATIO_AIN2 = RATIO
RATIO_AIN3 = RATIO

R_TOP_3V3 = R_TOP
R_BOTTOM_3V3 = R_BOTTOM
R_TOP_5V = R_TOP
R_BOTTOM_5V = R_BOTTOM
R_TOP_VBAT = R_TOP
R_BOTTOM_VBAT = R_BOTTOM
RATIO_3V3 = RATIO
RATIO_5V = RATIO
RATIO_VBAT = RATIO
# -----------------------------------------------------------------------

EXT_REF_VOLT = 2500.0      # mV, the ATL431 on REFP and REFN
AVDD_VOLT = 3296.0         # mV, the 3V3 analog supply
MAX_COUNT = 262144         # counts at full scale
SATURATION_MARGIN_VOLT = 50  # this close to full scale the reading is not trusted
NEGATIVE_LIMIT_VOLT = -50    # reading below this indicates grounding fault

# ------------------------ bench measured pin drops ------------------------
PIN_DROP_AIN0 = 0.0
PIN_DROP_AIN2 = 0.0
PIN_DROP_AIN3 = 0.0

PIN_DROP_3V3 = 0.0
PIN_DROP_5V = 0.0
PIN_DROP_VBAT = 0.0
# --------------------------------------------------------------------------

# ------------------------ pin side zero offsets ---------------------------
# mV read at the ADC input with the divider input grounded.
PIN_OFFSET_AIN0 = -13.8   # Board 1 (AIN0)
PIN_OFFSET_AIN2 = -3.1    # Board 2 (AIN2)
PIN_OFFSET_AIN3 = -11.0   # Board 3 (AIN3)

PIN_OFFSET_5V = PIN_OFFSET_AIN0
PIN_OFFSET_3V3 = PIN_OFFSET_AIN2
PIN_OFFSET_VBAT = PIN_OFFSET_AIN3
# --------------------------------------------------------------------------

# ---------------------------- accept windows, mV ----------------------------
NOMINAL_V_CHARGING_VOLT = 3600
MIN_V_CHARGING_VOLT = 3000
MAX_V_CHARGING_VOLT = 5500

NOMINAL_3V3_VOLT = 3300
MIN_3V3_VOLT = 3135
MAX_3V3_VOLT = 3465

NOMINAL_5V_VOLT = 5000
MIN_5V_VOLT = 4750
MAX_5V_VOLT = 5250

NOMINAL_VBAT_VOLT = 3700
MIN_VBAT_VOLT = 3000
MAX_VBAT_VOLT = 4200
# ----------------------------------------------------------------------------

SHUNT_IN_GROUND_RETURN = False

# ------------------------------- speed profiles -------------------------------
PROFILES = {
	"fast": {
		"data_rate": ADS122C04_DATA_RATE_1000SPS,
		"op_mode": ADS122C04_OP_MODE_NORMAL,
		"samples": 20,
		"discard": 2,
		"mux_settle": 0.02,
	},
	"steady": {
		"data_rate": ADS122C04_DATA_RATE_330SPS,
		"op_mode": ADS122C04_OP_MODE_NORMAL,
		"samples": 33,
		"discard": 2,
		"mux_settle": 0.05,
	},
	"careful": {
		"data_rate": ADS122C04_DATA_RATE_20SPS,
		"op_mode": ADS122C04_OP_MODE_NORMAL,
		"samples": 100,
		"discard": 2,
		"mux_settle": 0.5,
	},
}
PROFILE_NAME = "fast"
PROFILE = dict(PROFILES[PROFILE_NAME])

POLL_INTERVAL = 0.001
SAMPLE_RETRY_LIMIT = 3
# ------------------------------------------------------------------------------


def set_profile(name):
	"""Swap the speed profile, one of PROFILES. Returns the profile in force."""
	global PROFILE, PROFILE_NAME
	if name not in PROFILES:
		raise KeyError("unknown profile {0}, pick one of {1}".format(
			name, ", ".join(sorted(PROFILES))))
	PROFILE_NAME = name
	PROFILE = dict(PROFILES[name])
	return PROFILE


def sample_timeout():
	"""How long to wait for one conversion before calling it a timeout, in seconds."""
	period = conversion_period(PROFILE["data_rate"], PROFILE["op_mode"])
	return 10 * period + 0.02


def channel_duration():
	"""Roughly how long one measurement takes with the profile in force, in seconds."""
	period = conversion_period(PROFILE["data_rate"], PROFILE["op_mode"])
	return PROFILE["mux_settle"] + (PROFILE["samples"] + PROFILE["discard"]) * period


ABSENT_VOLT = 200
ABSENT_RAW_VOLT = 50

STATUS_OK = "OK"
STATUS_LOW = "LOW"
STATUS_HIGH = "HIGH"
STATUS_ABSENT = "ABSENT"


# Hardware pin mapping per board socket
BOARD_CHANNELS = {
	1: {
		"channel": CHANNEL_V_CHARGING,
		"board": 1,
		"mux": ADS122C04_MUX_AIN0_AVSS,
		"pin": "AIN0",
		"supply": 5000,
		"min": MIN_V_CHARGING_VOLT,
		"max": MAX_V_CHARGING_VOLT,
		"nominal": NOMINAL_V_CHARGING_VOLT,
		"ratio": RATIO_AIN0,
		"pin_drop": PIN_DROP_AIN0,
		"pin_offset": PIN_OFFSET_AIN0,
		"ref_volt": AVDD_VOLT,
		"vref": ADS122C04_VREF_AVDD,
	},
	2: {
		"channel": CHANNEL_V_CHARGING,
		"board": 2,
		"mux": ADS122C04_MUX_AIN2_AVSS,
		"pin": "AIN2",
		"supply": 5000,
		"min": MIN_V_CHARGING_VOLT,
		"max": MAX_V_CHARGING_VOLT,
		"nominal": NOMINAL_V_CHARGING_VOLT,
		"ratio": RATIO_AIN2,
		"pin_drop": PIN_DROP_AIN2,
		"pin_offset": PIN_OFFSET_AIN2,
		"ref_volt": AVDD_VOLT,
		"vref": ADS122C04_VREF_AVDD,
	},
	3: {
		"channel": CHANNEL_V_CHARGING,
		"board": 3,
		"mux": ADS122C04_MUX_AIN3_AVSS,
		"pin": "AIN3",
		"supply": 5000,
		"min": MIN_V_CHARGING_VOLT,
		"max": MAX_V_CHARGING_VOLT,
		"nominal": NOMINAL_V_CHARGING_VOLT,
		"ratio": RATIO_AIN3,
		"pin_drop": PIN_DROP_AIN3,
		"pin_offset": PIN_OFFSET_AIN3,
		"ref_volt": AVDD_VOLT,
		"vref": ADS122C04_VREF_AVDD,
	},
}

CHANNELS = {
	CHANNEL_V_CHARGING: BOARD_CHANNELS[1],
}

RAIL_CHANNELS = (CHANNEL_V_CHARGING,)

CHANNEL_ALIASES = {
	"v_charging": CHANNEL_V_CHARGING,
	"vcharging": CHANNEL_V_CHARGING,
	"v_charg": CHANNEL_V_CHARGING,
	"charging": CHANNEL_V_CHARGING,
	"board_1": CHANNEL_V_CHARGING,
	"board_2": CHANNEL_V_CHARGING,
	"board_3": CHANNEL_V_CHARGING,
	"3v3": CHANNEL_V_CHARGING,
	"rail_3v3": CHANNEL_V_CHARGING,
	"5v": CHANNEL_V_CHARGING,
	"vbus": CHANNEL_V_CHARGING,
	"rail_5v": CHANNEL_V_CHARGING,
	"vbat": CHANNEL_V_CHARGING,
	"bat": CHANNEL_V_CHARGING,
	"rail_vbat": CHANNEL_V_CHARGING,
}


def _get_cfg(target):
	"""Returns the config dict whether given a dict, board number, channel name, or alias."""
	if isinstance(target, dict):
		return target
	if isinstance(target, int) and target in BOARD_CHANNELS:
		return BOARD_CHANNELS[target]
	if target in CHANNELS:
		return CHANNELS[target]
	key = CHANNEL_ALIASES.get(str(target).lower())
	if key and key in CHANNELS:
		return CHANNELS[key]
	return BOARD_CHANNELS[1]


def resolve_channel(name):
	"""Turn alias or channel name into a channel key."""
	if name in CHANNELS:
		return name
	key = CHANNEL_ALIASES.get(str(name).lower())
	if key is not None:
		return key
	return CHANNEL_V_CHARGING


def saturation_volt(target=CHANNEL_V_CHARGING):
	"""Raw pin reading at which this channel stops meaning anything, in mV."""
	cfg = _get_cfg(target)
	return cfg["ref_volt"] - SATURATION_MARGIN_VOLT


def counts_to_volt(target, counts):
	"""Raw averaged counts to millivolts at the ADC pin, against reference."""
	cfg = _get_cfg(target)
	return (counts * cfg["ref_volt"]) / MAX_COUNT


def volt_to_counts(target, adc_volt):
	"""Millivolts at the ADC pin to the raw counts they should produce."""
	cfg = _get_cfg(target)
	return (adc_volt * MAX_COUNT) / cfg["ref_volt"]


def pin_volt_from_raw(target, adc_volt):
	"""Raw reading to divider voltage at the pin: take off the zero, add back the drop."""
	cfg = _get_cfg(target)
	return adc_volt - cfg["pin_offset"] + cfg["pin_drop"]


def rail_volt_from_pin(target, pin_volt):
	"""Divider voltage at the pin to the supply using divider ratio."""
	cfg = _get_cfg(target)
	return pin_volt * cfg["ratio"]


def rail_volt_from_raw(target, adc_volt):
	"""Raw reading straight through to the supply, drop and divider both undone."""
	return rail_volt_from_pin(target, pin_volt_from_raw(target, adc_volt))


def expected_pin_volt(target):
	"""Divider voltage at nominal, in mV."""
	cfg = _get_cfg(target)
	return cfg["nominal"] / cfg["ratio"]


def expected_raw_volt(target):
	"""What the ADC itself should read at nominal, divider voltage less drop + offset."""
	cfg = _get_cfg(target)
	return expected_pin_volt(target) - cfg["pin_drop"] + cfg["pin_offset"]


def rail_volt_limit(target):
	"""Highest supply this channel can read before the raw reading saturates, in mV."""
	return rail_volt_from_raw(target, saturation_volt(target))


def headroom_warnings(channels=RAIL_CHANNELS):
	"""Channels whose reference and divider cannot carry top of accept window."""
	return [(channel, rail_volt_limit(channel))
			for channel in channels
			if _get_cfg(channel)["max"] > rail_volt_limit(channel)]


def classify_rail(target, rail_volt, adc_volt=None):
	"""Say whether a supply sits in its window, under it, over it, or is not there."""
	cfg = _get_cfg(target)
	if adc_volt is not None and adc_volt < ABSENT_RAW_VOLT:
		return STATUS_ABSENT
	if rail_volt < ABSENT_VOLT:
		return STATUS_ABSENT
	elif rail_volt < cfg["min"]:
		return STATUS_LOW
	elif rail_volt > cfg["max"]:
		return STATUS_HIGH
	return STATUS_OK


class VoltageMeasurementModule:

	def __init__(self, board_num=1, adc_chip=None, address=ADC_ADDRESS):
		"""Opens the voltage divider board's ADS122C04 at `address` (0x41).
		board_num selects the ADC input pin:
		  Board 1 -> AIN0
		  Board 2 -> AIN2
		  Board 3 -> AIN3
		"""
		if board_num not in (1, 2, 3):
			raise ValueError("Board number must be 1, 2, or 3 (got {0})".format(board_num))

		print(f"Initializing Voltage Module for Board {board_num}")
		self.board_num = board_num
		self.cfg = BOARD_CHANNELS[board_num]

		if adc_chip is None:
			self.adc_chip = ADS122C04(address)
		else:
			self.adc_chip = adc_chip

		self.channel = CHANNEL_V_CHARGING
		self.avg_counts = 0
		self.last_measurement = None

	def select_channel(self, channel=None):
		"""Point ADC MUX at the board's designated charging pin and let it settle."""
		cfg = self.cfg if (channel is None or channel == CHANNEL_V_CHARGING) else _get_cfg(channel)
		self.adc_chip.set_continuous_mode(mux=cfg["mux"], gain=1,
										  pga_bypass=ADS122C04_PGA_DISABLED,
										  vref=cfg["vref"],
										  data_rate=PROFILE["data_rate"],
										  op_mode=PROFILE["op_mode"])
		self.channel = cfg["channel"]
		time.sleep(PROFILE["mux_settle"])

	def calculating_avg(self, channel=None):
		"""Average the profile conversions and return raw millivolts at the ADC pin."""
		cfg = self.cfg if (channel is None or channel == CHANNEL_V_CHARGING) else _get_cfg(channel)
		self.avg_counts, used, timeouts = average_ready_conversions(
			self.adc_chip, PROFILE["samples"], PROFILE["discard"],
			sample_timeout(), POLL_INTERVAL, SAMPLE_RETRY_LIMIT)
		adc_volt = counts_to_volt(cfg, self.avg_counts)
		log("{0} (Board {1} on {2}) {3} samples, {4} timed out, {5:.0f} counts, {6:.2f} mV raw".format(
			cfg["channel"], self.board_num, cfg["pin"], used, timeouts, self.avg_counts, adc_volt))
		return adc_volt

	def measure_channel(self, channel=None, ground_offset_volt=0.0):
		"""Measure charging voltage for the configured board socket.
		Returns result dict containing channel, pin, adc_volt, pin_volt, rail_volt, status, etc.
		"""
		cfg = self.cfg if (channel is None or channel == CHANNEL_V_CHARGING) else _get_cfg(channel)
		self.select_channel(channel)
		adc_volt = self.calculating_avg(channel)

		pin_volt = pin_volt_from_raw(cfg, adc_volt)
		rail_volt = rail_volt_from_pin(cfg, pin_volt) - ground_offset_volt
		status = classify_rail(cfg, rail_volt, adc_volt)

		result = {
			"channel": cfg["channel"],
			"board_num": self.board_num,
			"pin": cfg["pin"],
			"adc_counts": self.avg_counts,
			"adc_volt": adc_volt,
			"pin_volt": pin_volt,
			"rail_volt": rail_volt,
			"pin_drop": cfg["pin_drop"],
			"ratio": cfg["ratio"],
			"ref_volt": cfg["ref_volt"],
			"status": status,
			"over_range": adc_volt >= saturation_volt(cfg),
			"under_range": adc_volt < NEGATIVE_LIMIT_VOLT,
		}
		log("{0} on {1} (Board {2}), raw {3:.2f} mV, pin {4:.2f} mV, supply {5:.1f} mV, {6}".format(
			cfg["channel"], cfg["pin"], self.board_num, adc_volt, pin_volt, rail_volt, status))
		self.last_measurement = result
		return result

	def measure_voltage(self, ground_offset_volt=0.0):
		"""Measure voltage for the configured board socket."""
		return self.measure_channel(self.channel, ground_offset_volt)

	def measure_all(self, channels=None, ground_offset_volt=0.0):
		"""Measures the board's charging voltage and returns {CHANNEL_V_CHARGING: measurement dict}."""
		res = self.measure_channel(self.channel, ground_offset_volt)
		self.last_measurement = {CHANNEL_V_CHARGING: res}
		return self.last_measurement
