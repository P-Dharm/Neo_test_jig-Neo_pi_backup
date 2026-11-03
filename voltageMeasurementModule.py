"""
===========================================
Version 1.0 (Updated for 3-in-1 Jig)
Voltage measurement through a resistive divider on the ADS122C04 inputs.

Everything here talks to the voltage divider board's ADS122C04 at 0x41(this address has bean changes from old pcb test which was 0x45). The current
sense board is a second chip at 0x40 on the same bus, so its AIN0 is a different pin
on a different device.

One supply rail per analog input of 0x41 (Based on 3-in-1 Jig Schematic):

  RAIL_5V    AIN0, the 5V / VBUS rail
  RAIL_3V3   AIN2, the 3.3V rail
  RAIL_VBAT  AIN3, the battery rail
  (AIN1 is unused for voltage)

Every tap has its own divider:

  rail -> R1 -> ADC input -> R2 -> AGND
  ratio (R1 + R2)/R2

so the divider voltage column of the hardware sheet is what a pin should sit at,
and multiplying it back by that tap's ratio gives the rail: on a 9k88 over 11k91
tap, ratio 1.8296, 2022.3 mV on the pin is 3700.0 mV at the supply.

Measure a tap with
    python3 dividerTest.py <rail> --calibrate <volts> [<second volts>]
rather than trusting the sheet, see the resistor block below.

Each pin also carries a drop, measured on the bench, the millivolts the ADC input
reads below the divider voltage. It is added back to the raw reading first, so

  pin_volt  = adc_volt - pin_offset + pin_drop     compare with the sheet
  rail_volt = pin_volt * ratio                     the supply

The 2.5 V external reference cannot reach the 5V rail through this divider,
5000/1.8296 = 2733 mV of divider voltage against a 2500 mV full scale, so
RAIL_5V reads against AVDD instead. See the RAIL_5V block below.

Measurements are single ended, AINx to AVSS, PGA bypassed.

Speed is set by PROFILE, see the profile block. The default reads all three rails
in about a fifth of a second.
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


ADC_ADDRESS = ADS122C04_ADDRESS_VOLTAGE   # the voltage divider board, not the 0x40
										  # current sense board

VERBOSE = False   # True to print every sample batch and channel as it is measured


def log(*args):
	"""Progress chatter, off unless VERBOSE. Results go back as a dict, not as print.
	"""
	if VERBOSE :
		print(*args)


def divider_ratio(r_top, r_bottom):
	"""Multiply a divider voltage by this to get the voltage at the top of the divider.
	"""
	return (r_top + r_bottom)/r_bottom


CHANNEL_RAIL_3V3 = "RAIL_3V3"
CHANNEL_RAIL_5V = "RAIL_5V"
CHANNEL_RAIL_VBAT = "RAIL_VBAT"

# ------------------------- the fitted dividers, per tap -------------------------
# Sheet values are 9.88k over 11.91k, ratio 1.8296. Each tap gets its own pair
# because they need not match, and on this board they do not: 3.700 V into VBAT
# put 392.7 mV on AIN2, which is a ratio near 9.4, so the top resistor on that tap
# is around 100k rather than 9k88. Run
#     python3 dividerTest.py vbat --calibrate 3.7 3.0
# to measure a tap and get the two numbers to paste back here.
R_TOP_3V3 = 10000.0         # ohm, rail to ADC input
R_BOTTOM_3V3 = 4700.0       # ohm, ADC input to AGND
R_TOP_5V = 10000.0
R_BOTTOM_5V = 4700.0
R_TOP_VBAT = 10000.0
R_BOTTOM_VBAT = 4700.0

RATIO_3V3 = divider_ratio(R_TOP_3V3, R_BOTTOM_3V3)
RATIO_5V = divider_ratio(R_TOP_5V, R_BOTTOM_5V)
RATIO_VBAT = divider_ratio(R_TOP_VBAT, R_BOTTOM_VBAT)
# --------------------------------------------------------------------------------

EXT_REF_VOLT = 2500.0      # mV, the ATL431 on REFP and REFN
AVDD_VOLT = 3296.0         # mV, the 3V3 analog supply, full scale when vref is AVDD.
						   # Only as good as that supply, measure it and put the real
						   # number here if the 5V rail has to be accurate.
MAX_COUNT = 262144         # counts at full scale, the driver shifts the 24 bit code
						   # right by 5, see read_conversion_register
SATURATION_MARGIN_VOLT = 50  # this close to full scale the reading is not trusted
NEGATIVE_LIMIT_VOLT = -50    # a pin cannot really sit this far below AVSS, a reading
							 # here is an offset or grounding fault, not a low rail

# ------------------------ bench measured pin drops ------------------------
# Millivolts the ADC input reads below the divider voltage it should see, measured
# at the pin. Added back to the raw reading before the divider is undone, so the
# pin figure lines up with the divider voltage column of the hardware sheet.
# A loss on the rail side of the divider is a different thing and is not this.
# PIN_DROP_3V3 = 33.0        # mV, AIN0, measured 0.033 V
# PIN_DROP_5V = 140.0        # mV, AIN1, measured 0.14 V
# PIN_DROP_VBAT = 30.0       # mV, AIN2, measured 0.03 V

PIN_DROP_3V3 = 0.0
PIN_DROP_5V = 0.0
PIN_DROP_VBAT = 0.0

# --------------------------------------------------------------------------

# ------------------------ pin side zero, bench step 1 ------------------------
# mV read at the ADC input with the divider input grounded. Leave at 0 until it
# has been measured, it is subtracted from the raw reading.
# PIN_OFFSET_3V3 = 0.0
# PIN_OFFSET_5V = 0.0
# PIN_OFFSET_VBAT = 0.0
PIN_OFFSET_3V3 = -3.1
PIN_OFFSET_5V = -13.8
PIN_OFFSET_VBAT = -11.0
# ----------------------------------------------------------------------------

# ---------------------------- accept windows, mV ----------------------------
NOMINAL_3V3_VOLT = 3300
MIN_3V3_VOLT = 3135       # nominal -5 %
MAX_3V3_VOLT = 3465       # nominal +5 %

NOMINAL_5V_VOLT = 5000
MIN_5V_VOLT = 4750        # the USB floor at the connector
MAX_5V_VOLT = 5250

# one Li-ion cell, 3.0 V empty to 4.2 V full. Change these for a different pack.
NOMINAL_VBAT_VOLT = 3700
MIN_VBAT_VOLT = 3000
MAX_VBAT_VOLT = 4200
# ----------------------------------------------------------------------------

SHUNT_IN_GROUND_RETURN = False   # True when the current shunt sits in the DUT ground
								 # return, the rail readings are then referenced to a
								 # DUT ground that floats above AVSS

# ------------------------------- speed profiles -------------------------------
# A jig wants the answer now, so "fast" runs the ADC at 1000 SPS and averages 20
# conversions, about 20 ms of signal per rail instead of five seconds. Averaging
# 20 samples at 1000 SPS leaves under 30 uV of noise, well under the 9.5 uV per
# count the driver resolves, so nothing useful is lost on a rail check.
#
# What the rate does cost is the 20 SPS notch that rejects 50 and 60 Hz mains, so
# a rail with real hum on it reads steadier on "careful", which is the old
# behaviour: 20 SPS, 100 samples, half a second of settling.
#
#   samples      conversions averaged per rail
#   discard      conversions thrown away first, the ones that straddle the switch
#   mux_settle   seconds to wait after pointing the MUX at a new rail
PROFILES = {
	"fast" : {
		"data_rate" : ADS122C04_DATA_RATE_1000SPS,
		"op_mode" : ADS122C04_OP_MODE_NORMAL,
		"samples" : 20,
		"discard" : 2,
		"mux_settle" : 0.02,
	},
	"steady" : {
		"data_rate" : ADS122C04_DATA_RATE_330SPS,
		"op_mode" : ADS122C04_OP_MODE_NORMAL,
		"samples" : 33,
		"discard" : 2,
		"mux_settle" : 0.05,
	},
	"careful" : {
		"data_rate" : ADS122C04_DATA_RATE_20SPS,
		"op_mode" : ADS122C04_OP_MODE_NORMAL,
		"samples" : 100,
		"discard" : 2,
		"mux_settle" : 0.5,
	},
}
PROFILE_NAME = "fast"
PROFILE = dict(PROFILES[PROFILE_NAME])

POLL_INTERVAL = 0.001      # how often to ask the chip whether a conversion is ready
SAMPLE_RETRY_LIMIT = 3     # conversions allowed to time out before giving up
# ------------------------------------------------------------------------------


def set_profile(name):
	"""Swap the speed profile, one of PROFILES. Returns the profile in force.
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
	Ten conversion periods plus a fixed allowance for the I2C round trips.
	"""
	period = conversion_period(PROFILE["data_rate"], PROFILE["op_mode"])
	return 10*period + 0.02


def channel_duration():
	"""Roughly how long one rail takes with the profile in force, in seconds.
	"""
	period = conversion_period(PROFILE["data_rate"], PROFILE["op_mode"])
	return PROFILE["mux_settle"] + (PROFILE["samples"] + PROFILE["discard"])*period

# below this a rail counts as switched off or unplugged rather than merely low
ABSENT_VOLT = 200
# and below this many raw millivolts the tap is dead, judged before the pin drop is
# added back, so a large drop cannot make an unpowered rail look merely low
ABSENT_RAW_VOLT = 50

STATUS_OK = "OK"
STATUS_LOW = "LOW"
STATUS_HIGH = "HIGH"
STATUS_ABSENT = "ABSENT"

CHANNELS = {
    CHANNEL_RAIL_5V : {
        "mux" : ADS122C04_MUX_AIN0_AVSS,
        "pin" : "AIN0",
        "supply" : 5000,
        "min" : 4750,
        "max" : 5250,
        "nominal" : NOMINAL_5V_VOLT,
        "ratio" : RATIO_5V,
        "pin_drop" : PIN_DROP_5V,
        "pin_offset" : PIN_OFFSET_5V,
        "ref_volt" : AVDD_VOLT,
        "vref" : ADS122C04_VREF_AVDD
    },
    CHANNEL_RAIL_VBAT : {
        "mux" : ADS122C04_MUX_AIN3_AVSS,
        "pin" : "AIN3",
        "supply" : 4200,
        "min" : 3000,
        "max" : 4200,
        "nominal" : NOMINAL_VBAT_VOLT,
        "ratio" : RATIO_VBAT,
        "pin_drop" : PIN_DROP_VBAT,
        "pin_offset" : PIN_OFFSET_VBAT,
        # "ref_volt" : EXT_REF_VOLT,
        # "vref" : ADS122C04_VREF_EXT_REF_PINS
		"ref_volt" : AVDD_VOLT,
		"vref" : ADS122C04_VREF_AVDD
    },
    CHANNEL_RAIL_3V3 : {
        "mux" : ADS122C04_MUX_AIN2_AVSS,
        "pin" : "AIN2",
        "supply" : 3300,
        "min" : 3135,
        "max" : 3465,
        "nominal" : NOMINAL_3V3_VOLT,
        "ratio" : RATIO_3V3,
        "pin_drop" : PIN_DROP_3V3,
        "pin_offset" : PIN_OFFSET_3V3,
        # "ref_volt" : EXT_REF_VOLT,
        # "vref" : ADS122C04_VREF_EXT_REF_PINS
		"ref_volt" : AVDD_VOLT,
		"vref" : ADS122C04_VREF_AVDD
    }
}

RAIL_CHANNELS = (CHANNEL_RAIL_3V3, CHANNEL_RAIL_5V, CHANNEL_RAIL_VBAT)

# short forms accepted on the command line, "5v" is easier to type than "RAIL_5V"
CHANNEL_ALIASES = {
	"3v3" : CHANNEL_RAIL_3V3,
	"rail_3v3" : CHANNEL_RAIL_3V3,
	"5v" : CHANNEL_RAIL_5V,
	"vbus" : CHANNEL_RAIL_5V,
	"rail_5v" : CHANNEL_RAIL_5V,
	"vbat" : CHANNEL_RAIL_VBAT,
	"bat" : CHANNEL_RAIL_VBAT,
	"rail_vbat" : CHANNEL_RAIL_VBAT,
}


def resolve_channel(name):
	"""Turn "5v", "vbat", "RAIL_3V3" and friends into a channel key, or raise KeyError.
	"""
	if name in CHANNELS :
		return name
	key = CHANNEL_ALIASES.get(name.lower())
	if key is None :
		raise KeyError("unknown rail {0}, pick one of {1}".format(name, ", ".join(RAIL_CHANNELS)))
	return key


def saturation_volt(channel):
	"""Raw pin reading at which this channel stops meaning anything, in mV.
	"""
	return CHANNELS[channel]["ref_volt"] - SATURATION_MARGIN_VOLT


def counts_to_volt(channel, counts):
	"""Raw averaged counts to millivolts at the ADC pin, against that channel's reference.
	"""
	return (counts * CHANNELS[channel]["ref_volt"])/MAX_COUNT


def volt_to_counts(channel, adc_volt):
	"""Millivolts at the ADC pin to the raw counts they should produce.
	"""
	return (adc_volt * MAX_COUNT)/CHANNELS[channel]["ref_volt"]


def pin_volt_from_raw(channel, adc_volt):
	"""Raw reading to divider voltage at the pin: take off the zero, add back the drop.
	This is the figure to compare with the divider voltage column of the sheet.
	"""
	cfg = CHANNELS[channel]
	return adc_volt - cfg["pin_offset"] + cfg["pin_drop"]


def rail_volt_from_pin(channel, pin_volt):
	"""Divider voltage at the pin to the supply. 2022.3 mV gives 3700.0 mV.
	"""
	return pin_volt * CHANNELS[channel]["ratio"]


def rail_volt_from_raw(channel, adc_volt):
	"""Raw reading straight through to the supply, drop and divider both undone.
	"""
	return rail_volt_from_pin(channel, pin_volt_from_raw(channel, adc_volt))


def expected_pin_volt(channel):
	"""Divider voltage at nominal, the sheet's figure for this rail, in mV.
	"""
	cfg = CHANNELS[channel]
	return cfg["nominal"]/cfg["ratio"]


def expected_raw_volt(channel):
	"""What the ADC itself should read at nominal, the divider voltage less the drop.
	"""
	cfg = CHANNELS[channel]
	return expected_pin_volt(channel) - cfg["pin_drop"] + cfg["pin_offset"]


def rail_volt_limit(channel):
	"""Highest supply this channel can read before the raw reading saturates, in mV.
	"""
	return rail_volt_from_raw(channel, saturation_volt(channel))


def headroom_warnings(channels=RAIL_CHANNELS):
	"""Channels whose reference and divider cannot carry the top of their own accept
	window. Returns [(channel, ceiling in mV)] so a caller can print or fail on it.
	"""
	return [(channel, rail_volt_limit(channel))
			for channel in channels
			if CHANNELS[channel]["max"] > rail_volt_limit(channel)]


def classify_rail(channel, rail_volt, adc_volt=None):
	"""Say whether a supply sits in its window, under it, over it, or is not there.
	Pass the raw reading as well and a dead tap is called ABSENT on that alone.
	"""
	cfg = CHANNELS[channel]
	if adc_volt is not None and adc_volt < ABSENT_RAW_VOLT :
		return STATUS_ABSENT
	if rail_volt < ABSENT_VOLT :
		return STATUS_ABSENT
	elif rail_volt < cfg["min"] :
		return STATUS_LOW
	elif rail_volt > cfg["max"] :
		return STATUS_HIGH
	return STATUS_OK


class VoltageMeasurementModule :

	def __init__(self, board_num, adc_chip=None, address=ADC_ADDRESS):
			"""Opens the voltage divider board's own ADS122C04 at `address`, 0x45.
			Pass adc_chip only to hand in a chip that is already open, and only if it is
			that board: the current module's chip is a different device at 0x40 and its
			AIN0 is the current sense amplifier, not the 3V3 tap.
			"""
			print(f"Initializing Voltage Module for Board {board_num}")
			self.board_num = board_num
			
			if adc_chip is None :
				self.adc_chip = ADS122C04(address)
			else :
				self.adc_chip = adc_chip
			self.channel = None
			self.avg_counts = 0
			self.last_measurement = None

	def select_channel(self, channel):
		"""Point the ADC MUX at one rail, on that rail's reference, and let it settle.
		The PGA is bypassed, these are single ended AINx to AVSS measurements.
		"""
		cfg = CHANNELS[channel]
		self.adc_chip.set_continuous_mode(mux=cfg["mux"], gain=1,
										  pga_bypass=ADS122C04_PGA_DISABLED,
										  vref=cfg["vref"],
										  data_rate=PROFILE["data_rate"],
										  op_mode=PROFILE["op_mode"])
		self.channel = channel
		time.sleep(PROFILE["mux_settle"])

	def calculating_avg(self, channel):
		"""Average the profile's conversions, returns the raw millivolts at the ADC pin.
		Each one is taken as DRDY offers it, so this costs one conversion period per
		sample rather than a fixed sleep.
		"""
		self.avg_counts, used, timeouts = average_ready_conversions(
			self.adc_chip, PROFILE["samples"], PROFILE["discard"],
			sample_timeout(), POLL_INTERVAL, SAMPLE_RETRY_LIMIT)
		adc_volt = counts_to_volt(channel, self.avg_counts)
		log("{0} {1} samples, {2} timed out, {3:.0f} counts, {4:.2f} mV raw".format(
			channel, used, timeouts, self.avg_counts, adc_volt))
		return adc_volt

	def measure_channel(self, channel, ground_offset_volt=0.0):
		"""Measure one rail and return the result as a dict:
			channel      - CHANNEL_RAIL_3V3, CHANNEL_RAIL_5V or CHANNEL_RAIL_VBAT
			pin          - the analog input it is wired to, for printing
			adc_counts   - averaged raw counts
			adc_volt     - raw millivolts at the ADC pin
			pin_volt     - divider voltage at the pin, zero and drop applied
			rail_volt    - the supply, pin_volt times the divider ratio
			pin_drop     - the drop added back at the pin, in mV
			ratio        - the divider ratio used
			ref_volt     - full scale for this channel, in mV
			status       - OK, LOW, HIGH or ABSENT
			over_range   - True when the raw reading sat at the top of the span
			under_range  - True when it came back implausibly negative
		pin_volt and rail_volt mean nothing when over_range or under_range is True.

		ground_offset_volt is the drop across the current shunt in mV, taken from the
		current module's volt_across_rs. Pass it when SHUNT_IN_GROUND_RETURN is True,
		the DUT ground then floats that far above AVSS and every reading carries it.
		"""
		cfg = CHANNELS[channel]
		self.select_channel(channel)
		adc_volt = self.calculating_avg(channel)

		pin_volt = pin_volt_from_raw(channel, adc_volt)
		rail_volt = rail_volt_from_pin(channel, pin_volt) - ground_offset_volt
		status = classify_rail(channel, rail_volt, adc_volt)

		result = {
			"channel" : channel,
			"pin" : cfg["pin"],
			"adc_counts" : self.avg_counts,
			"adc_volt" : adc_volt,
			"pin_volt" : pin_volt,
			"rail_volt" : rail_volt,
			"pin_drop" : cfg["pin_drop"],
			"ratio" : cfg["ratio"],
			"ref_volt" : cfg["ref_volt"],
			"status" : status,
			"over_range" : adc_volt >= saturation_volt(channel),
			"under_range" : adc_volt < NEGATIVE_LIMIT_VOLT,
		}
		log("{0} on {1}, raw {2:.2f} mV, pin {3:.2f} mV, supply {4:.1f} mV, {5}".format(
			channel, cfg["pin"], adc_volt, pin_volt, rail_volt, status))
		return result

	def measure_all(self, channels=RAIL_CHANNELS, ground_offset_volt=0.0):
		"""Measure each rail in turn, returns {channel: measurement dict}.
		"""
		results = {}
		for channel in channels :
			results[channel] = self.measure_channel(channel, ground_offset_volt)
		self.last_measurement = results
		return results
