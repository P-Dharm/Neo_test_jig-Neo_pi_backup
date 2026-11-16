#!/usr/bin/env python3
"""
jig_button.py
=============
Physical Start & Stop button daemon for the NEO Automated Test Jig.

Monitors physical push buttons:
  - START Button: Launches 'sudo python3 all_test_mux_remove.py --profile fast'
  - STOP Button : Gracefully terminates and cleans up the running test process.

Buttons are configured as Active-High:
  - Button connected between 3.3V and GPIO pin (with pull-down).
  - Pressing button pulls the GPIO pin HIGH (1).
"""

import os
import signal
import subprocess
import sys
import time

# ══════════════════════════════════════════════════════════════════════════════
#  START / STOP BUTTON GPIO PIN DEFINITIONS (BCM GPIO NUMBERS)
#  Allocate 2 GPIO pins for the physical push buttons.
#  Insert your desired Raspberry Pi BCM GPIO pin numbers below (replace None).
# ══════════════════════════════════════════════════════════════════════════════

PIN_START = 14  # [INSERT GPIO PIN] Start Button (e.g., 18) - Starts test process
PIN_STOP  = 15  # [INSERT GPIO PIN] Stop Button  (e.g., 23) - Quits test process

# ── Button Electrical Wiring Configuration ────────────────────────────────────
# True  = Active-High : Button connects GPIO to 3.3V when pressed (pull-down applied)
# False = Active-Low  : Button connects GPIO to GND when pressed (pull-up applied)
BUTTON_ACTIVE_HIGH = True

# Debounce delay in seconds to filter out switch contact bounce
BUTTON_DEBOUNCE_S = 0.25

# Command to execute when START button is pressed
COMMAND_TEST = ["sudo", "python3", "all_test_mux_remove.py", "--profile", "fast"]

# Directory where all_test_mux_remove.py is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ══════════════════════════════════════════════════════════════════════════════


def run_pinctrl(args):
    """Executes a pinctrl command, returning True on success."""
    try:
        subprocess.run(["pinctrl"] + args, capture_output=True, timeout=2, check=True)
        return True
    except Exception as err:
        return False


def setup_gpio():
    """Configures input direction and pull resistor for Start and Stop pins."""
    pull = "pd" if BUTTON_ACTIVE_HIGH else "pu"
    pins = [("START", PIN_START), ("STOP", PIN_STOP)]

    print("[buttons] Configuring GPIO pins...")
    for name, pin in pins:
        if pin is None:
            print(f"[buttons] WARNING: PIN_{name} is not set (None).")
            continue
        # Set pin as input with pull-down (or pull-up)
        ok = run_pinctrl(["set", str(pin), "ip", pull])
        if ok:
            print(f"[buttons]   PIN_{name} (GPIO {pin}) -> Input with Pull-{pull.upper()} [OK]")
        else:
            print(f"[buttons]   PIN_{name} (GPIO {pin}) -> pinctrl failed! Check permissions or pin number.")


def pin_is_pressed(pin):
    """Returns True if the specified GPIO button is currently pressed."""
    if pin is None:
        return False
    try:
        res = subprocess.run(["pinctrl", "get", str(pin)],
                             capture_output=True, text=True, timeout=1)
        is_high = "hi" in res.stdout.lower()
        return is_high if BUTTON_ACTIVE_HIGH else not is_high
    except Exception:
        return False


class JigProcessManager:
    """Manages spawning, monitoring, and cleanly killing the test script."""

    def __init__(self):
        self.proc = None

    def is_running(self):
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def start(self):
        if self.is_running():
            print(f"[buttons] START ignored: Test process is already running (PID: {self.proc.pid}).")
            return

        print("\n" + "=" * 65)
        print(f"[buttons] START PRESSED -> Launching: {' '.join(COMMAND_TEST)}")
        print("=" * 65 + "\n")

        try:
            # os.setsid creates a new process group so SIGINT/SIGTERM can be delivered
            # to sudo and all of its spawned child processes simultaneously.
            self.proc = subprocess.Popen(
                COMMAND_TEST,
                cwd=SCRIPT_DIR,
                preexec_fn=os.setsid
            )
            print(f"[buttons] Test process launched successfully (PID: {self.proc.pid}).\n")
        except Exception as err:
            print(f"[buttons] ERROR: Failed to launch test process: {err}")
            self.proc = None

    def stop(self):
        if not self.is_running():
            print("[buttons] STOP pressed: No test process is currently running.")
            return

        pid = self.proc.pid
        print("\n" + "=" * 65)
        print(f"[buttons] STOP PRESSED -> Gracefully stopping test process (PID: {pid})...")
        print("=" * 65)

        try:
            pgid = os.getpgid(pid)
            # Send SIGINT (Ctrl+C) to process group so Python cleans up GPIOs, LEDs, and DB
            os.killpg(pgid, signal.SIGINT)

            # Wait up to 5 seconds for clean exit
            try:
                self.proc.wait(timeout=5.0)
                print(f"[buttons] Test process (PID: {pid}) exited cleanly.")
            except subprocess.TimeoutExpired:
                print(f"[buttons] Process did not terminate within 5s. Sending SIGTERM...")
                os.killpg(pgid, signal.SIGTERM)
                try:
                    self.proc.wait(timeout=2.0)
                    print(f"[buttons] Test process (PID: {pid}) terminated via SIGTERM.")
                except subprocess.TimeoutExpired:
                    print(f"[buttons] Force-killing with SIGKILL...")
                    os.killpg(pgid, signal.SIGKILL)
                    self.proc.wait()
                    print(f"[buttons] Test process (PID: {pid}) killed.")
        except ProcessLookupError:
            print(f"[buttons] Process (PID: {pid}) was already stopped.")
        except Exception as err:
            print(f"[buttons] Error stopping process: {err}")
        finally:
            self.proc = None
            print("[buttons] Ready for next START command.\n")


def main():
    print("=" * 65)
    print("  NEO TEST JIG - START/STOP BUTTON DAEMON")
    print(f"  Start Pin   : GPIO {PIN_START}")
    print(f"  Stop Pin    : GPIO {PIN_STOP}")
    print(f"  Polarity    : {'Active-High (Pressed = 3.3V / HIGH)' if BUTTON_ACTIVE_HIGH else 'Active-Low (Pressed = GND / LOW)'}")
    print(f"  Command     : {' '.join(COMMAND_TEST)}")
    print("=" * 65)

    if PIN_START is None or PIN_STOP is None:
        print("\n[!] ATTENTION: GPIO pin numbers for PIN_START and/or PIN_STOP are not set!")
        print("    Please open 'jig_button.py' and fill in your GPIO pin numbers at the top:")
        print("        PIN_START = <gpio_number>   # e.g., 18")
        print("        PIN_STOP  = <gpio_number>   # e.g., 23\n")
        print("Daemon exiting. Please configure pin numbers and re-run.")
        return 1

    setup_gpio()
    manager = JigProcessManager()

    # Graceful exit handler on Ctrl+C of jig_button.py itself
    def _sig_handler(sig, frame):
        print("\n[buttons] jig_button.py shutting down...")
        if manager.is_running():
            manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    print("\n[buttons] Daemon active and listening for button presses...")
    print("  - Press START to run test suite")
    print("  - Press STOP to abort/quit test suite")
    print("  - Press Ctrl+C in this terminal to exit daemon.\n")

    prev_start = False
    prev_stop = False

    while True:
        # Check if the child process exited on its own
        if manager.proc is not None and manager.proc.poll() is not None:
            code = manager.proc.poll()
            print(f"\n[buttons] Test process exited (Return Code: {code}). Ready for next run.")
            manager.proc = None

        start_now = pin_is_pressed(PIN_START)
        stop_now = pin_is_pressed(PIN_STOP)

        # Detect rising edge (button pressed)
        if start_now and not prev_start:
            time.sleep(BUTTON_DEBOUNCE_S)
            if pin_is_pressed(PIN_START):
                manager.start()

        if stop_now and not prev_stop:
            time.sleep(BUTTON_DEBOUNCE_S)
            if pin_is_pressed(PIN_STOP):
                manager.stop()

        prev_start = start_now
        prev_stop = stop_now
        time.sleep(0.05)


if __name__ == "__main__":
    sys.exit(main())
