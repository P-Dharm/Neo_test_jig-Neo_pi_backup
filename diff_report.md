# Firmware Diff Report

## 1. `ADS122C04.py`

### 1.1 Added Driver Helper Functions: `read_conversions` and `conversion_statistics`
* **Earlier:**
  Only `average_ready_conversions` and `average_conversions` existed. Neither `read_conversions` nor `conversion_statistics` was defined in the module.

* **Now:**
```python
def read_conversions(adc_chip, count, discard=0, timeout=0.5,
                     poll_interval=0.001, retry_limit=3):
    """Read `count` conversions, taking each one as DRDY says it is ready and
    throwing the first `discard` away, the ones that straddle a settling input.
    Returns (samples, conversions that timed out).
    Raises RuntimeError when nothing ever became ready.
    """
    samples = []
    timeouts = 0
    skipped = 0
    while len(samples) < count and timeouts <= retry_limit :
        value = adc_chip.read_conversion_when_ready(timeout, poll_interval)
        if value is None :
            timeouts = timeouts + 1
        elif skipped < discard :
            skipped = skipped + 1
        else :
            samples.append(value)
    if not samples :
        raise RuntimeError("no ADC conversion became ready, check the I2C link to the ADS122C04")
    return samples, timeouts


def conversion_statistics(samples, clip_counts):
    """Compute summary statistics for a batch of conversion counts."""
    if not samples:
        raise ValueError("samples must not be empty")
    s_sorted = sorted(samples)
    n = len(s_sorted)
    mean = sum(samples) / float(n)
    median = s_sorted[n // 2]
    p10 = s_sorted[int(n * 0.1)]
    p90 = s_sorted[min(int(n * 0.9), n - 1)]
    minimum = s_sorted[0]
    maximum = s_sorted[-1]
    spread = maximum - minimum
    clipped = sum(1 for s in samples if s >= clip_counts)
    return {
        "mean": mean,
        "median": median,
        "p10": p10,
        "p90": p90,
        "minimum": minimum,
        "maximum": maximum,
        "spread": spread,
        "samples": n,
        "clipped": clipped,
    }
```

### 1.2 Refactored `average_ready_conversions`
* **Earlier:**
```python
def average_ready_conversions(adc_chip, count, discard=0, timeout=0.5,
                              poll_interval=0.001, retry_limit=3):
    samples = []
    timeouts = 0
    skipped = 0
    while len(samples) < count and timeouts <= retry_limit :
        value = adc_chip.read_conversion_when_ready(timeout, poll_interval)
        if value is None :
            timeouts = timeouts + 1
        elif skipped < discard :
            skipped = skipped + 1
        else :
            samples.append(value)
    if not samples :
        raise RuntimeError("no ADC conversion became ready, check the I2C link to the ADS122C04")
    return sum(samples)/len(samples), len(samples), timeouts
```

* **Now:**
```python
def average_ready_conversions(adc_chip, count, discard=0, timeout=0.5,
                              poll_interval=0.001, retry_limit=3):
    samples, timeouts = read_conversions(adc_chip, count, discard, timeout,
                                         poll_interval, retry_limit)
    return sum(samples)/len(samples), len(samples), timeouts
```

---

## 2. `currentMeasurementModule.py`

### 2.1 Module Imports
* **Earlier:**
```python
from ADS122C04 import ADS122C04, ADS122C04_MUX_AIN2_AVSS, average_ready_conversions, conversion_period
from ADS122C04 import ADS122C04_ADDRESS_CURRENT
from ADS122C04 import ADS122C04_MUX_AIN0_AVSS, ADS122C04_MUX_AIN1_AVSS, ADS122C04_PGA_ENABLED
from ADS122C04 import ADS122C04_VREF_EXT_REF_PINS
from ADS122C04 import ADS122C04_DATA_RATE_20SPS, ADS122C04_DATA_RATE_330SPS
from ADS122C04 import ADS122C04_DATA_RATE_1000SPS, ADS122C04_OP_MODE_NORMAL
import RPi.GPIO as gpio
import time
```

* **Now:**
```python
from ADS122C04 import ADS122C04, read_conversions, conversion_statistics, average_ready_conversions
from ADS122C04 import conversion_period
from ADS122C04 import ADS122C04_ADDRESS_CURRENT
from ADS122C04 import ADS122C04_MUX_AIN0_AVSS, ADS122C04_MUX_AIN1_AVSS, ADS122C04_MUX_AIN2_AVSS, ADS122C04_PGA_ENABLED
from ADS122C04 import ADS122C04_VREF_EXT_REF_PINS
from ADS122C04 import ADS122C04_DATA_RATE_20SPS, ADS122C04_DATA_RATE_330SPS
from ADS122C04 import ADS122C04_DATA_RATE_1000SPS, ADS122C04_OP_MODE_NORMAL
import RPi.GPIO as gpio
import time
```

---

### 2.2 Calibration Constants, Hardware Offsets, and Range Limits
* **Earlier:**
```python
EXTERNAL_HIGH_GAIN = 308.48
EXTERNAL_LOW_GAIN = 18.96

ADC_REF_VOLT = 2320  #mv
MAX_COUNT = 262144

RS_LOW_CURRENT = 10
RS_HIGH_CURRENT = 1
EQUIVALENT_RS = 0.909

OFFSET_HIGH_GAIN = 54
OFFSET_MID = 119
OFFSET_LOW_RS = 103

SATURATION_LIMIT_VOLT = 1650
RANGE_SELECT_CEILING_VOLT = 1600
```

* **Now:**
```python
STEADY_SPREAD_VOLT = 5.0        # mV, absolute allowance
STEADY_SPREAD_FRACTION = 0.05   # and 5 percent of the signal above the range offset

EXTERNAL_HIGH_GAIN = 224.47619
EXTERNAL_LOW_GAIN = 18.6428

ADC_REF_VOLT = 2393.5  # mV
MAX_COUNT = 262144

RS_LOW_CURRENT = 10.0
RS_HIGH_CURRENT = 1.0
EQUIVALENT_RS = 0.8201

OFFSET_HIGH_GAIN = 117.78
OFFSET_MID = 117.78
OFFSET_LOW_RS = 314.66

SATURATION_LIMIT_VOLT = 2345
CLIP_COUNTS = SATURATION_LIMIT_VOLT*MAX_COUNT/ADC_REF_VOLT
RANGE_SELECT_CEILING_VOLT = 2200
```

---

### 2.3 `CurrentMeasurementModule.__init__`
* **Earlier:**
```python
	def __init__(self, board_num):
		print(f"Initializing Current Module for Board {board_num}")
		self.board_num = board_num
			
		# Restore the correct ADC pin routing!
		if self.board_num == 1:
			self.adc_mux = ADS122C04_MUX_AIN0_AVSS
		elif self.board_num == 2:
			self.adc_mux = ADS122C04_MUX_AIN1_AVSS
		elif self.board_num == 3:
			self.adc_mux = ADS122C04_MUX_AIN2_AVSS
		else:
			raise ValueError("Board number must be 1, 2, or 3")

		# 2. Your original initialization code
		self.adc_chip = ADS122C04(ADC_ADDRESS)
		gpio.setmode(gpio.BCM)
		gpio.setup(GPIO_GAIN_1, gpio.OUT)
		gpio.setup(GPIO_GAIN_2, gpio.OUT)
		self.range = None
		self.avg_counts = 0
		self.last_measurement = None
		self.measure = False
			
		# (This likely uses the MUX, so it must stay at the bottom)
		self.select_range(RANGE_MID)
```

* **Now:**
```python
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
```

---

### 2.4 `CurrentMeasurementModule.calculating_avg`
* **Earlier:**
```python
	def calculating_avg(self):
		self.adc_chip.set_continuous_mode(mux=self.adc_mux, gain=1,
                                  pga_bypass=ADS122C04_PGA_ENABLED,
                                  vref=ADS122C04_VREF_EXT_REF_PINS,
                                  data_rate=PROFILE["data_rate"],
                                  op_mode=PROFILE["op_mode"])
		time.sleep(PROFILE["adc_settle"])
		self.avg_counts, used, timeouts = average_ready_conversions(
			self.adc_chip, PROFILE["samples"], PROFILE["discard"],
			sample_timeout(), POLL_INTERVAL, SAMPLE_RETRY_LIMIT)
		if timeouts :
			print("conversions that timed out", timeouts)
		adc_volt = (self.avg_counts*ADC_REF_VOLT)/MAX_COUNT
		print("range", self.range, "counts", self.avg_counts, "->", adc_volt, "mV")
		return adc_volt
```

* **Now:**
```python
	def calculating_avg(self):
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
```

---

### 2.5 Added Method: `CurrentMeasurementModule.is_steady`
* **Earlier:**
Method did not exist in `currentMeasurementModule.py`.

* **Now:**
```python
	def is_steady(self, statistics, offset):
		""" Whether the load held still across the batch, so that its mean means
		anything.
		"""
		signal = max(statistics["mean"] - offset, 0.0)
		allowed = max(STEADY_SPREAD_VOLT, signal*STEADY_SPREAD_FRACTION)
		return statistics["spread"] <= allowed
```

---

### 2.6 `CurrentMeasurementModule.measure_once`
* **Earlier:**
```python
	def measure_once(self):
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

		while adc_volt >= SATURATION_LIMIT_VOLT and range_name != RANGE_PREFERENCE[-1] :
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
		}
```

* **Now:**
```python
	def measure_once(self, range_name=None):
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

		# Step down if the reading saturated on the selected range
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
```

---

### 2.7 `CurrentMeasurementModule.start_measurement`
* **Earlier:**
```python
	def start_measurement(self):
		self.measure = True
		try :
			self.last_measurement = self.measure_once()
		finally :
			self.measure = False
		self.calibration_callback(self.last_measurement["current"])
		return self.last_measurement
```

* **Now:**
```python
	def start_measurement(self, range_name=None):
		self.measure = True
		try :
			self.last_measurement = self.measure_once(range_name)
		finally :
			self.measure = False
		self.calibration_callback(self.last_measurement["current"])
		return self.last_measurement
```

---

## 3. `all_test_mux_remove.py`

### 3.1 `Bench.measure`
* **Earlier:**
```python
    def measure(self, repeats):
        # Skip GPIO toggle if in manual mode
        if not self.manual_power:
            pin_set(PIN_PWR[self.board_num], False)
            time.sleep(0.1)
        
        started = time.monotonic()
        readings = []
        for _ in range(max(1, repeats)):
            result, _ = self.report.quietly(self.chip.start_measurement)
            readings.append(result)
        elapsed = time.monotonic() - started
```

* **Now:**
```python
    def measure(self, repeats, range_name=None):
        # Skip GPIO toggle if in manual mode
        if not self.manual_power:
            pin_set(PIN_PWR[self.board_num], False)
            time.sleep(0.1)
        
        started = time.monotonic()
        readings = []
        for _ in range(max(1, repeats)):
            result, _ = self.report.quietly(lambda: self.chip.start_measurement(range_name))
            readings.append(result)
        elapsed = time.monotonic() - started
```

---

### 3.2 `measure_async`
* **Earlier:**
```python
async def measure_async(bench, repeats):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, bench.measure, repeats)
```

* **Now:**
```python
async def measure_async(bench, repeats, range_name=None):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, bench.measure, repeats, range_name)
```

---

### 3.3 Sleep Current Test in `_ble_stage`
* **Earlier:**
```python
            row = await measure_async(bench, SLEEP_REPEATS)
```

* **Now:**
```python
            row = await measure_async(bench, SLEEP_REPEATS, bench.current_module.RANGE_HIGH_GAIN)
```
