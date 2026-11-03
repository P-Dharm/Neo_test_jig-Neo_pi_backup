"""
===========================================
Test jig measurement, current and the supply rails in one pass.

    python3 main.py                     current, then the three rails
    python3 main.py --current           current only
    python3 main.py --voltage           rails only
    python3 main.py --profile careful   both halves slower, mains rejecting, the
                                        profile the current calibration was fitted on
    python3 main.py --verbose           show the module chatter as well

Current comes from currentMeasurementModule, which probes on the MID range and
re measures on the most sensitive range that will hold the reading. Rails come
from voltageMeasurementModule: 3V3 on AIN0, 5V on AIN1, VBAT on AIN2.

These are two separate ADS122C04 on the one I2C bus, and each module opens its own:
0x40 is the current sense board, 0x45 the voltage divider board. AIN0 on 0x40 is the
amplifier output, AIN0 on 0x45 is the 3V3 tap, and they are different pins on
different devices.

Current is measured first, because when the shunt sits in the DUT ground return
every rail reading carries the drop across it, see SHUNT_IN_GROUND_RETURN in
voltageMeasurementModule.

Exit code 0 when everything is inside spec, 1 when it is not, 2 on a usage error.
===========================================
"""
import argparse
import io
import sys
import time
import contextlib

import currentMeasurementModule
import voltageMeasurementModule as voltage
from voltageMeasurementModule import VoltageMeasurementModule
from voltageMeasurementModule import CHANNELS, RAIL_CHANNELS, PROFILES, set_profile
from voltageMeasurementModule import SHUNT_IN_GROUND_RETURN, STATUS_OK
from voltageMeasurementModule import ADC_ADDRESS as VOLTAGE_ADDRESS
import voltageMeasurementModule

# Where the current reading comes from: currentMeasurementModule opens the 0x40 board
# and calls set_continuous_mode() with no arguments, so it reads that chip's default
# mux, AIN0 to AVSS with the PGA in circuit.
CURRENT_ADDRESS = 0x40
CURRENT_INPUT = "AIN0"   # currentMeasurementModule.ADC_INPUT_NAME, repeated here so
                         # the pin check below works without importing RPi.GPIO


def quietly(work):
	"""Run `work` with its printing captured, so the report below stays clean.
	Returns (whatever work returned, the text it printed).
	"""
	captured = io.StringIO()
	with contextlib.redirect_stdout(captured) :
		result = work()
	return result, captured.getvalue()


def shared_pins():
	"""Rails that would collide with the current sense input, which can only happen
	if both are on the same chip. Two boards at two addresses do not collide, the
	same AINx on each is a different pin on a different device.
	"""
	if VOLTAGE_ADDRESS != CURRENT_ADDRESS :
		return []
	return [channel for channel in RAIL_CHANNELS
			if CHANNELS[channel]["pin"] == CURRENT_INPUT]


def print_pin_warning(rails):
	"""Say so when the current sense and a rail divider are on one chip's one pin.
	"""
	print("  WARNING  on 0x{0:02X} the current sense output and {1} are both on {2}.".format(
		CURRENT_ADDRESS, ", ".join(rails), CURRENT_INPUT))
	print("           One pin cannot carry both, so one of these two readings is")
	print("           of the other signal. Move that divider to a free input and")
	print("           update its channel in voltageMeasurementModule.py, or point")
	print("           the current module at the input the amplifier really uses.")
	print()


def print_current(result):
	"""The current, the range it was taken on, and what that range is made of.
	"""
	if result["over_range"] or result["under_range"] :
		reading = "     invalid"
	else :
		reading = "{0:9.4f} mA".format(result["current"])
	print("  current   {0}   on {1} (case {2}), 0x{3:02X} {4}".format(
		reading, result["range"], result["case"], CURRENT_ADDRESS, CURRENT_INPUT))
	print("            ADC {0:.2f} mV, {1:.0f} counts, {2:.4f} mV across Rs".format(
		result["adc_volt"], result["adc_counts"], result["volt_across_rs"]))
	print("            gain {0:.2f}, Rs {1} ohm, offset {2} mV, {3:.2f} mV/mA".format(
		result["gain"], result["resistor"], result["offset"], result["sensitivity"]))
	if result["over_range"] :
		print("            OVER RANGE, the ADC sat at the top of its span")
	if result["under_range"] :
		print("            UNDER RANGE, the reading fell below the range offset")
	print()


def print_rails(results):
	"""One row per rail, raw reading through to the supply, against its window.
	"""
	print("  rails on 0x{0:02X}".format(VOLTAGE_ADDRESS))
	print("  rail        pin     raw ADC     at pin     supply     window            status")
	for channel in RAIL_CHANNELS :
		if channel not in results :
			continue
		cfg = CHANNELS[channel]
		result = results[channel]
		if result["over_range"] or result["under_range"] :
			pin_volt, supply = "         -", "         -"
		else :
			pin_volt = "{0:7.1f} mV".format(result["pin_volt"])
			supply = "{0:7.1f} mV".format(result["rail_volt"])
		print("  {0:<11} {1:<6} {2:7.1f} mV  {3}  {4}  {5:4.0f} to {6:4.0f} mV   {7}".format(
			channel, result["pin"], result["adc_volt"], pin_volt, supply,
			float(cfg["min"]), float(cfg["max"]), result["status"]))
	print()


def current_faults(result):
	"""What is wrong with the current reading, empty when nothing is.
	"""
	if result is None :
		return []
	if result["over_range"] :
		return ["current over range"]
	if result["under_range"] :
		return ["current under range"]
	return []


def rail_faults(results):
	"""One entry per rail that is not inside its window.
	"""
	faults = []
	for channel, result in results.items() :
		# status first, so a fault reads the same here as in the table above
		if result["status"] != STATUS_OK :
			faults.append("{0} {1}".format(channel, result["status"]))
		elif result["over_range"] :
			faults.append("{0} pin at full scale".format(channel))
		elif result["under_range"] :
			faults.append("{0} pin below AVSS".format(channel))
	return faults


def main():
	args = sys.argv[1:]
	verbose = "--verbose" in args
	args = [arg for arg in args if arg != "--verbose"]

	profile = None
	if "--profile" in args :
		index = args.index("--profile")
		if index + 1 >= len(args) or args[index + 1] not in PROFILES :
			print("--profile wants one of {0}".format(", ".join(sorted(PROFILES))))
			return 2
		profile = args[index + 1]
		set_profile(profile)
		args = args[:index] + args[index + 2:]

	do_current = "--voltage" not in args
	do_voltage = "--current" not in args
	args = [arg for arg in args if arg not in ("--current", "--voltage")]
	if args :
		print("do not know what to do with {0}, see the docstring".format(" ".join(args)))
		return 2
	if not do_current and not do_voltage :
		print("--current and --voltage together leave nothing to measure")
		return 2

	print()
	shared = shared_pins() if do_current and do_voltage else []
	if shared :
		print_pin_warning(shared)

	current_module = None
	current_result = None
	current_seconds = 0.0
	ground_offset = 0.0

	if do_current :
		# imported here so a rails only run works on a machine without RPi.GPIO
		import currentMeasurementModule as current
		from currentMeasurementModule import CurrentMeasurementModule
		if profile :
			current.set_profile(profile)
		started = time.monotonic()
		(current_module, chatter) = quietly(CurrentMeasurementModule)
		current_result, measure_chatter = quietly(current_module.start_measurement)
		current_seconds = time.monotonic() - started
		if verbose or current_faults(current_result) :
			print(chatter + measure_chatter)
		print_current(current_result)
		if SHUNT_IN_GROUND_RETURN :
			ground_offset = current_result["volt_across_rs"]
			print("  DUT ground sits {0:.3f} mV above AVSS, taken off every rail".format(
				ground_offset))
			print()

	rail_results = {}
	rail_seconds = 0.0
	if do_voltage :
		# its own chip at 0x45, nothing to do with the current module's at 0x40
		voltage_module = VoltageMeasurementModule()
		started = time.monotonic()
		rail_results, chatter = quietly(
			lambda : voltage_module.measure_all(RAIL_CHANNELS, ground_offset))
		rail_seconds = time.monotonic() - started
		if verbose :
			print(chatter)
		print_rails(rail_results)

	timing = []
	profiles = set()
	if do_current :
		timing.append("current {0:.2f} s".format(current_seconds))
		profiles.add(current.PROFILE_NAME)
	if do_voltage :
		timing.append("{0} rails {1:.2f} s".format(len(rail_results), rail_seconds))
		profiles.add(voltage.PROFILE_NAME)
	if len(profiles) == 1 :
		timing.append("{0} profile".format(profiles.pop()))
	else :
		timing.append("current on {0}, rails on {1}".format(
			current.PROFILE_NAME, voltage.PROFILE_NAME))

	faults = current_faults(current_result) + rail_faults(rail_results)
	if faults :
		print("  FAIL  {0}".format(", ".join(faults)))
	elif do_current and do_voltage :
		print("  PASS  {0:.4f} mA, {1} rails inside their windows".format(
			current_result["current"], len(rail_results)))
	elif do_current :
		print("  PASS  {0:.4f} mA".format(current_result["current"]))
	else :
		print("  PASS  {0} rails inside their windows".format(len(rail_results)))
	print("        {0}".format(", ".join(timing)))
	return 1 if faults else 0

# if __name__ == "__main__" :
# 	sys.exit(main())
if __name__ == "__main__":
    import time
    
    parser = argparse.ArgumentParser(description="Test jig manual validation")
    parser.add_argument("--board", type=int, choices=[1, 2, 3], required=True, 
                        help="Board number to test (1, 2, or 3)")
    parser.add_argument("--current", action="store_true", help="Test current only")
    parser.add_argument("--voltage", action="store_true", help="Test voltage only")
    args = parser.parse_args()

    board_num = args.board
    run_both = not args.current and not args.voltage
    do_current = args.current or run_both
    do_voltage = args.voltage or run_both

    print(f"\n======================================")
    print(f"       TEST RESULTS: BOARD {board_num}")
    print(f"======================================\n")

    total_start = time.monotonic()

    # 1. Initialize and Run Current Measurement
    if do_current:
        try:
            curr_start = time.monotonic()
            current_module, _ = quietly(lambda: currentMeasurementModule.CurrentMeasurementModule(board_num=board_num))
            current_result, _ = quietly(current_module.start_measurement)
            curr_time = time.monotonic() - curr_start
            
            print("  CURRENT MEASUREMENT:")
            print("  ------------------------------------------------")
            print(f"  Final Current   : {current_result['current']:.4f} mA")
            print(f"  Active Range    : {current_result['range']} (Case {current_result['case']})")
            print(f"  Raw ADC Counts  : {current_result['adc_counts']:.0f}")
            print(f"  Raw ADC Voltage : {current_result['adc_volt']:.3f} mV")
            print(f"  Drop Across Rs  : {current_result['volt_across_rs']:.4f} mV")
            print(f"  Hardware Config : Gain = {current_result['gain']:.2f}, Rs = {current_result['resistor']} Ω, Offset = {current_result['offset']} mV")
            print(f"  Execution Time  : {curr_time:.2f} seconds")
            print("  ------------------------------------------------\n")
            
        except Exception as e:
            print(f"  [❌] CURRENT ERROR: {e}\n")

    # 2. Initialize and Run Voltage Measurement
    if do_voltage:
        try:
            volt_start = time.monotonic()
            voltage_module, _ = quietly(lambda: voltageMeasurementModule.VoltageMeasurementModule(board_num=board_num))
            voltage_result, _ = quietly(voltage_module.measure_all)
            volt_time = time.monotonic() - volt_start
            
            print("  VOLTAGE MEASUREMENT:")
            print("  -----------------------------------------------------------------")
            print("  Rail       | Pin  | Raw ADC  | Pin Volt | Supply   | Status")
            print("  -----------------------------------------------------------------")
            for rail, data in voltage_result.items():
                print(f"  {rail:<10} | {data['pin']:<4} | {data['adc_volt']:>6.1f}mV | {data['pin_volt']:>6.1f}mV | {data['rail_volt']:>6.1f}mV | {data['status']}")
            print("  -----------------------------------------------------------------")
            print(f"  Execution Time  : {volt_time:.2f} seconds")
            print("  -----------------------------------------------------------------\n")
            
        except Exception as e:
            print(f"  [❌] VOLTAGE ERROR: {e}\n")

    total_time = time.monotonic() - total_start
    print(f"  Total Test Time : {total_time:.2f} seconds")
    print(f"======================================\n")

