#!/usr/bin/env python3
"""neo_jig.py
===========
Neosapien production test: one module, end to end, no phone in the loop.

    sudo python3 neo_jig.py                 one module, then exit
    sudo python3 neo_jig.py --loop          keep going, Enter between modules
    sudo python3 neo_jig.py --no-program    module is already flashed

The sequence, in order
----------------------
  1. program   flash and verify the test firmware over SWD (this is also the
               "is a module actually fitted" check -- SWD fails if not)
  2. scan      find the module's BLE address by name
  3. connect   one GATT connection, held for everything below
  4. acctest   AT+ACCTEST, the IMU electrostatic self-test
  5. mic       AT+CAPTURE with the speaker driven, plus a silence capture for
               the noise floor, analysed against a calibrated limits file
  6. active    AT+ACTIVE, audio streaming to this host, measure supply current
  7. led       white LED on/off current delta, proves the LED without an operator
  8. sleep     AT+SLEEP, measure supply current again
  9. log       one CSV row with every metric; optional Google Sheet

Why the order is what it is
---------------------------
The mic analysis uses AT+CAPTURE (recorded into module RAM, then transferred
losslessly) rather than the live stream, because a Raspberry Pi 3's controller
has no Data Length Extension and tops out around 2 kB/s -- well under the
32 kB/s the live stream produces. Judging a microphone on audio the link threw
away is not a test. AT+CAPTURE leaves the module in its sleep state, so step 6
re-issues AT+ACTIVE before measuring.

Step 6 deliberately keeps this host subscribed and draining audio, because that
is what the phone does in the field and it is the radio traffic that sets the
current. The measurement runs in a thread so the asyncio loop keeps servicing
notifications -- block the loop and the link congests, the module drops blocks,
and the current you measure is of a different machine than the one you shipped.

READ THIS BEFORE TRUSTING THE ACTIVE CURRENT
--------------------------------------------
Because of that same 2 kB/s ceiling, the active current measured here is NOT
the number a phone produces. On the bench the same firmware read 7.87 mA with
a phone draining the stream and 5.49 mA with far less of it moving. So
ACTIVE_LIMITS below are limits for THIS JIG, established by measuring
known-good modules on THIS JIG. Do not paste the datasheet or phone figure in.
Sleep current has no such problem: nothing is streaming, so it is comparable.
"""

import argparse
import asyncio
import csv
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time

from bleak import BleakClient, BleakScanner

# The BLE session and the audio analysis are imported, never copied: mic_test.py
# and mic_qc.py stay the single source of truth for both.
from mic_test import (
    AUDIO_CHARACTERISTIC_UUID,
    BYTES_PER_SAMPLE,
    CMD_ACTIVE,
    CMD_SLEEP,
    DEVICE_NAME,
    EXIT_ERROR,
    EXIT_FAIL,
    EXIT_PASS,
    RESP_ACTIVE_DONE,
    RESP_SLEEP_DONE,
    SAMPLE_RATE,
    DutSession,
    save_wav,
    start_audio_notify,
)
from mic_qc import (
    DEFAULT_TONES,
    TONE_BANDWIDTH_HZ,
    AmpSelect,
    bluez_kwargs,
    build_stimulus,
    capture_once,
    context_mismatch,
    evaluate,
    measure,
    parse_tones,
    print_rows,
    stimulus_context,
)

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════

# Bumped whenever this file changes. Printed at startup and by --version, so a
# stale copy on the jig is visible immediately rather than showing up as an
# argument that does not exist or a limit that behaves oddly.
VERSION = "2026-09-29.2"

WORK_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Programming ────────────────────────────────────────────────────────────
# Kept beside the scripts so the whole folder can be copied to the jig as one
# unit, with no path on the Pi pointing back at a build tree.
FIRMWARE_HEX = os.path.join(WORK_DIR, "firmware", "neo_test_fw.hex")
OPENOCD_TCL = "/home/isc/openocd/tcl"
OPENOCD_CFG = "/home/isc/nrf52.cfg"
FLASH_ATTEMPTS = 3        # a locked chip needs two passes, so keep this >= 2
FLASH_TIMEOUT = 120       # seconds per OpenOCD invocation
FLASH_RETRY_PAUSE = 2.0

# ── Power bench ────────────────────────────────────────────────────────────
# currentMeasurementModule.py / voltageMeasurementModule.py / main.py live with
# the old power-test scripts rather than beside this one.
BENCH_DIR = "/home/isc/neo_power_test"
BENCH_PROFILE = "careful"   # see the note on ACTIVE_REPEATS

# A single reading on the "fast" profile averages only a ~100 ms window, and a
# module that wakes, transmits and sleeps is partly outside it. Repeats help;
# the careful profile helps far more.
ACTIVE_REPEATS = 5
SLEEP_REPEATS = 3
LED_REPEATS = 3

# Settling. After a mode change the new connection parameters take several
# connection events to come into force, and the sleep interval is 500 ms with
# slave latency 4, so events are ~2.5 s apart -- measure too early and you
# catch the module still on the old interval.
ACTIVE_SETTLE_S = 2.0
SLEEP_SETTLE_S = 5.0
LED_SETTLE_S = 1.0

# ── Pass windows, all mA ───────────────────────────────────────────────────
# CALIBRATE THESE ON THIS JIG. See the warning in the docstring: the active
# figure depends on how much audio this host actually drains.
ACTIVE_LIMITS = (1.50, 4.50)
# Same mode, notifications switched off: mic, PDM, CPU and LED all still
# running, but no radio traffic. Unlike the streaming figure this does not
# depend on how fast the host can drain the link, so it is the one worth a
# tight window. CALIBRATE IT TOO.
ACTIVE_QUIET_LIMITS = (1.20, 3.50)

# Advertising signal strength at the jig's fixed geometry. This is the radio
# check: a missing or mistuned antenna costs tens of dB here while barely
# touching the supply current. CALIBRATE IT -- it depends entirely on how far
# the jig's controller sits from the module.
RSSI_LIMITS = (-75.0, -20.0)
SLEEP_LIMITS = (0.02, 0.29)     # 0.29 mA is the product's sleep budget
# The white LED is PWM-dimmed in firmware; flat out it was 5.32 mA, at the
# shipped 20% duty about 1.06 mA. A dead LED shows near zero delta, a wrong
# part or an un-dimmed one shows far too much.
LED_DELTA_LIMITS = (0.40, 2.20)

# Nothing legitimately sits between the two states, so a reading in the middle
# means the module is not in the state we asked for.
STATE_THRESHOLD_MA = 1.0

# ── BLE ────────────────────────────────────────────────────────────────────
SCAN_SECONDS = 8.0
SCAN_AMBIGUITY_DB = 10     # two modules on one name: the winner must lead by this
CONNECT_TIMEOUT = 20.0
CONNECT_ATTEMPTS = 3
CONNECT_RETRY_PAUSE = 2.0
TRANSFER_TIMEOUT = 60.0

# ── Mic test ───────────────────────────────────────────────────────────────
CAPTURE_MS = 1000
STIMULUS = "sequence"      # "sequence" or "multitone"
AMPLITUDE = 0.35
STIMULUS_LEAD = 0.5
LIMITS_FILE = os.path.join(WORK_DIR, "limits_mic.json")
# Named explicitly: under sudo, aplay's default device is root's, which is not
# the MAX98357A, so without this the stimulus silently goes nowhere.
ALSA_DEVICE = "plughw:CARD=sndrpihifiberry,DEV=0"

# ── Logging ────────────────────────────────────────────────────────────────
CSV_FILE = os.path.join(WORK_DIR, "neo_jig_log.csv")
GOOGLE_URL = ""            # paste the Apps Script /exec URL to enable
POST_TIMEOUT = 15
PENDING_FILE = os.path.join(WORK_DIR, "neo_jig_pending.json")

_file_lock = threading.Lock()
VERBOSE = False


def say(message=""):
    print(message, flush=True)


def note(message):
    if VERBOSE:
        print("    " + message, flush=True)


# ══════════════════════════════════════════════════════════════════════════════
#  PROGRAMMING
# ══════════════════════════════════════════════════════════════════════════════

# CTRL-AP is AP index 1 on nRF52.
#   offset 0x00 = RESET   0x04 = ERASEALL   0x08 = ERASEALLSTATUS
# The erase poll is capped at 40 x 50 ms = 2 s. A clean ERASEALL finishes well
# inside that; if it is still busy afterwards, waiting longer in the same
# session does not help and the retry below picks it up.
UNLOCK_SEQUENCE = [
    "-c", "poll off",
    "-c", "nrf52.dap apreg 1 4 0",      # reset the ERASEALL task
    "-c", "nrf52.dap apreg 1 4 1",      # trigger ERASEALL
    "-c", "set i 0",
    "-c", "while {[nrf52.dap apreg 1 8] == 1 && $i < 40} {sleep 50; incr i}",
    "-c", "nrf52.dap apreg 1 0 1",      # assert CTRL-AP reset
    "-c", "nrf52.dap apreg 1 0 0",      # deassert
    "-c", "nrf52.dap apreg 1 4 0",      # re-arm the ERASEALL task
    "-c", "sleep 100",
    "-c", "nrf52.cpu arp_examine",
    "-c", "poll on",
    "-c", "reset halt",
]


def openocd_command(hex_path):
    return [
        "openocd",
        "-s", OPENOCD_TCL,
        "-f", OPENOCD_CFG,
        "-c", "init",
        *UNLOCK_SEQUENCE,
        "-c", "flash probe 0",
        "-c", "flash write_image {0}".format(hex_path),
        "-c", "verify_image {0}".format(hex_path),
        "-c", "reset run",
        "-c", "shutdown",
    ]


def program_module(hex_path):
    """Flash and verify. Returns (ok, message).

    The double-fault and lockup errors OpenOCD prints while unlocking a locked
    chip are expected and do not mean the flash failed. The only thing that
    decides success is the word "verified" in the output.
    """
    if not os.path.isfile(hex_path):
        return False, "firmware not found: {0}".format(hex_path)

    command = openocd_command(hex_path)
    last_output = ""

    for attempt in range(1, FLASH_ATTEMPTS + 1):
        if attempt > 1:
            say("    attempt {0} of {1}...".format(attempt, FLASH_ATTEMPTS))
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=FLASH_TIMEOUT)
            last_output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            last_output = "OpenOCD timed out after {0} s".format(FLASH_TIMEOUT)
        except FileNotFoundError:
            return False, "openocd is not on PATH"
        except Exception as error:                      # noqa: BLE001
            last_output = str(error)

        note(last_output)

        if "verified" in last_output.lower():
            return True, ""
        if attempt < FLASH_ATTEMPTS:
            time.sleep(FLASH_RETRY_PAUSE)

    tail = [line for line in last_output.splitlines() if line.strip()][-6:]
    return False, "\n".join("      " + line for line in tail)


# ══════════════════════════════════════════════════════════════════════════════
#  BLE SCAN
# ══════════════════════════════════════════════════════════════════════════════

def list_adapters():
    """Every local Bluetooth controller and its HCI version.

    Data Length Extension arrived in Bluetooth 4.2. Without it each 244-byte
    notification is fragmented into ten 27-byte link-layer packets, the audio
    stream cannot run at real time, and the streaming current is therefore not
    the one the product draws in the field. A Raspberry Pi 3's built-in
    BCM43438 reports 4.1. Worth stating at startup rather than leaving it to be
    inferred from a throughput number afterwards.
    """
    try:
        output = subprocess.run(["hciconfig"], capture_output=True, text=True,
                                timeout=5).stdout
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return []

    adapters = []
    name = None
    for line in output.splitlines():
        head = re.match(r"^(hci\d+):", line)
        if head:
            name = head.group(1)
            continue
        version = re.search(r"HCI Version:\s*([0-9]+\.[0-9]+)", line)
        if version and name:
            number = float(version.group(1))
            adapters.append((name, number, number >= 4.2,
                             "UP RUNNING" in output.split(name + ":")[1][:400]
                             if name + ":" in output else False))
            name = None
    return adapters


def adapter_line(adapters, chosen):
    """One line describing which controller will be used and what it can do."""
    if not adapters:
        return "adapter   could not read hciconfig; assuming the BlueZ default"
    wanted = chosen or adapters[0][0]
    for name, version, has_dle, up in adapters:
        if name == wanted:
            return ("adapter   {0}, Bluetooth {1}{2}{3}".format(
                name, version,
                "" if up else "  ** NOT UP: run 'sudo hciconfig {0} up' **".format(name),
                "" if has_dle else
                "  ** no Data Length Extension: the audio stream cannot reach "
                "real time, so the streaming current will read low **"))
    return "adapter   {0} not found. Present: {1}".format(
        wanted, ", ".join(a[0] for a in adapters) or "none")


def address_is_stable(mac):
    """A Resolvable or Non-resolvable Private address rotates, so it cannot
    identify a device across a power cycle. Random Static, top two bits set,
    is safe. macOS hands out its own UUIDs, which this correctly rejects --
    the jig is a Linux host.
    """
    try:
        return (int(mac.split(":")[0], 16) >> 6) in (0b10, 0b11)
    except (ValueError, IndexError):
        return False


async def scan_mac(name, seconds, require_stable=True, adapter=None):
    """Returns (address, rssi_dbm, error).

    The RSSI is the production check on the radio path. A missing or badly
    matched antenna barely moves the supply current -- the radio transmits for
    the same time either way -- but it moves RSSI by tens of dB. At a fixed
    jig geometry it is a far sharper test than current, and unlike the
    streaming current it does not depend on how fast this host can drain the
    link.
    """
    seen = {}

    def on_found(device, advert):
        if (advert.local_name or device.name or "").strip() != name:
            return
        seen.setdefault(device.address.upper(), []).append(advert.rssi)

    # adapter=None lets BlueZ pick its default; pass hci1 to use a plugged-in
    # controller instead of the Pi's built-in one.
    scanner = BleakScanner(detection_callback=on_found, **bluez_kwargs(adapter))
    await scanner.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await scanner.stop()

    if not seen:
        return None, None, ("nothing is advertising '{0}'. A module already "
                            "connected to something else has stopped "
                            "advertising.".format(name))

    averages = {mac: statistics.mean(rssi) for mac, rssi in seen.items()}
    ranked = sorted(averages, key=lambda mac: averages[mac], reverse=True)
    best = ranked[0]

    if len(ranked) > 1 and averages[best] - averages[ranked[1]] < SCAN_AMBIGUITY_DB:
        return None, None, ("{0} modules answer to '{1}' and none is clearly closest: "
                      "{2}. Power the others down.".format(
                          len(ranked), name,
                          ", ".join("{0} {1:.0f} dBm".format(m, averages[m])
                                    for m in ranked)))
    if require_stable and not address_is_stable(best):
        return None, None, ("{0} is a private address and rotates, so it "
                            "cannot identify the module".format(best))
    return best, averages[best], ""


# ══════════════════════════════════════════════════════════════════════════════
#  POWER BENCH
# ══════════════════════════════════════════════════════════════════════════════

class Bench:
    """The two ADC boards and the gain GPIOs, opened once for the session.

    Between measurements the front end is parked on LOW_RS, the 0.82 ohm shunt,
    rather than the MID range the modules default to -- otherwise 10 ohm sits
    in series with the DUT for the whole time we are programming, connecting
    and switching states.
    """

    def __init__(self, bench_dir, profile=None):
        if bench_dir and bench_dir not in sys.path:
            sys.path.insert(0, bench_dir)

        import main as report
        import currentMeasurementModule as current_module
        import voltageMeasurementModule as voltage_module
        from currentMeasurementModule import CurrentMeasurementModule
        from voltageMeasurementModule import VoltageMeasurementModule

        self.report = report
        self.current_module = current_module
        self.rail_channels = voltage_module.RAIL_CHANNELS
        self.shunt_in_ground = voltage_module.SHUNT_IN_GROUND_RETURN
        self.status_ok = voltage_module.STATUS_OK

        if profile:
            current_module.set_profile(profile)
            voltage_module.set_profile(profile)
        self.profile_name = current_module.PROFILE_NAME

        self.chip, _ = report.quietly(CurrentMeasurementModule)
        self.rails = VoltageMeasurementModule()
        self.park()
        self.probe()

    def park(self):
        self.report.quietly(
            lambda: self.chip.select_range(self.current_module.RANGE_LOW_RS))

    def probe(self):
        """Read one register from each board, so a dead bus is reported at
        startup instead of midway through a module. Nothing in __init__
        actually transfers over I2C, so without this the first errno 5 does not
        surface until the first measurement.
        """
        for label, chip in (("current 0x40", self.chip.adc_chip),
                            ("voltage 0x45", self.rails.adc_chip)):
            try:
                chip.read_register(0, 1)
            except OSError as error:
                raise OSError("the {0} board did not answer: {1}. Check its "
                              "supply and run i2cdetect -y 1".format(label, error))

    def measure(self, repeats):
        """Average `repeats` current readings, then read the rails once.

        Synchronous and slow (I2C). Callers inside the asyncio loop MUST push
        this to an executor, or audio notifications stop being serviced for the
        duration and the very current being measured changes.
        """
        started = time.monotonic()
        readings = []
        for _ in range(max(1, repeats)):
            result, _ = self.report.quietly(self.chip.start_measurement)
            readings.append(result)
        elapsed = time.monotonic() - started

        valid = [r for r in readings if not (r["over_range"] or r["under_range"])]
        if not valid:
            self.park()
            return {"ok": False, "why": "every current reading was out of range"}

        currents = [r["current"] for r in valid]
        rail_results, _ = self.report.quietly(
            lambda: self.rails.measure_all(
                self.rail_channels,
                statistics.mean(r["volt_across_rs"] for r in valid)
                if self.shunt_in_ground else 0.0))
        self.park()

        return {
            "ok": True,
            "current_ma": statistics.mean(currents),
            "spread_ma": (max(currents) - min(currents)) if len(currents) > 1 else 0.0,
            "readings": len(valid),
            "case": valid[-1]["case"],
            "test_s": elapsed,
            "rails": rail_results,
        }


def vbat_volt(rails):
    """VBAT in volts, or None when the reading is not valid."""
    result = (rails or {}).get("RAIL_VBAT")
    if result is None or result["over_range"] or result["under_range"]:
        return None
    return round(result["rail_volt"] / 1000.0, 4)


def rail_warnings(rails, status_ok):
    """Rails outside their accept window. RAIL_5V is skipped because that tap
    is unpopulated and would warn on every single row.
    """
    return ["{0} {1}".format(channel, result["status"])
            for channel, result in (rails or {}).items()
            if channel != "RAIL_5V" and result["status"] != status_ok]


async def measure_async(bench, repeats):
    """bench.measure() on a worker thread, so notifications keep flowing."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, bench.measure, repeats)


# ══════════════════════════════════════════════════════════════════════════════
#  THE SEQUENCE
# ══════════════════════════════════════════════════════════════════════════════

class Result:
    """Everything one module produced, flat enough to become a CSV row."""

    def __init__(self, mac):
        self.mac = mac
        self.timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.stages = {}        # stage -> "PASS" | "FAIL" | "SKIP"
        self.failures = []
        self.metrics = {}

    def record(self, stage, ok, detail=""):
        self.stages[stage] = "PASS" if ok else "FAIL"
        if not ok:
            self.failures.append("{0}: {1}".format(stage, detail) if detail else stage)
        return ok

    def skip(self, stage):
        self.stages[stage] = "SKIP"

    @property
    def passed(self):
        return not self.failures

    @property
    def verdict(self):
        return "PASS" if self.passed else "FAIL"


def in_window(value, window):
    return window[0] <= value <= window[1]


async def stage_acctest(session, result):
    say("  [4/8] accelerometer self-test...")
    try:
        # session.command() only matches an exact reply; ACCTEST answers
        # "ACCTEST PASS" or "ACCTEST FAIL <reason>", so the prefix match below
        # is needed. Reaching past the public method keeps mic_test.py
        # untouched, which is worth more here than the tidier call.
        await session._send("AT+ACCTEST")
        response = await session._wait_for(
            lambda r: r.startswith("ACCTEST"), 15.0, "ACCTEST <verdict>")
    except (TimeoutError, RuntimeError) as error:
        return result.record("acctest", False, str(error))

    detail = response[len("ACCTEST"):].strip()
    result.metrics["acctest_detail"] = detail
    ok = detail.upper().startswith("PASS")
    say("        {0}".format(response))
    return result.record("acctest", ok, detail)


async def stage_mic(args, session, result):
    say("  [5/8] microphone...")
    tones = args.tones
    stimulus = build_stimulus(args.stimulus, tones,
                              args.capture_ms / 1000.0 + args.stimulus_lead + 1.0,
                              args.amplitude, args.capture_ms, args.tone_gains)

    try:
        with AmpSelect(args.sd_gpio, args.sd_active_low):
            say("        capturing with stimulus...")
            stimulus_raw = await capture_once(
                session, args.capture_ms, args.transfer_timeout, stimulus,
                args.alsa_device, args.stimulus_lead)
            say("        capturing silence (noise floor)...")
            silence_raw = await capture_once(
                session, args.capture_ms, args.transfer_timeout, None,
                args.alsa_device)
    except (TimeoutError, RuntimeError, OSError) as error:
        say("        FAILED: {0}".format(error))
        return result.record("mic", False, str(error))

    if args.wav:
        save_wav(args.wav, stimulus_raw)
        say("        saved {0}".format(args.wav))

    metrics = measure(stimulus_raw, silence_raw, tones, args.stimulus,
                      args.tone_bandwidth)
    result.metrics.update({"mic_" + k: v for k, v in metrics.items()})

    if args.limits is None:
        say("        no limits file: metrics recorded, no verdict")
        for key, value in sorted(metrics.items()):
            say("          {0:<24}{1:>10.2f}".format(key, value))
        result.skip("mic")
        return True

    rows, ok = evaluate(metrics, args.limits)
    print_rows(rows)
    return result.record("mic", ok, "outside limits")


async def stage_active(args, session, bench, result):
    say("  [6/8] active mode + current...")
    try:
        await session.command(CMD_ACTIVE, RESP_ACTIVE_DONE, timeout=10.0)
    except (TimeoutError, RuntimeError) as error:
        return result.record("active", False, str(error))

    if bench is None:
        result.skip("active_current")
        return True

    # Drain audio the way the phone does: it is the radio traffic that sets the
    # current, so a measurement taken with nothing subscribed is meaningless.
    # capture_buffered() leaves `capturing` False, so turn it back on here.
    session.audio = bytearray()
    session.packets = 0
    session.capturing = True
    await asyncio.sleep(args.active_settle)

    started = time.monotonic()
    before_packets = session.packets
    session.audio = bytearray()     # bound the buffer; we only want the rate

    row = await measure_async(bench, ACTIVE_REPEATS)

    elapsed = max(time.monotonic() - started, 1e-6)
    stream_bps = len(session.audio) / elapsed
    packets_per_s = (session.packets - before_packets) / elapsed
    session.capturing = False

    result.metrics["active_stream_bps"] = round(stream_bps, 1)
    result.metrics["active_packets_per_s"] = round(packets_per_s, 1)
    # 32000 B/s is the rate the module produces. How much of it this host
    # actually takes is what decides whether the current above resembles the
    # one a phone produces.
    result.metrics["active_stream_pct_of_realtime"] = round(
        100.0 * stream_bps / (SAMPLE_RATE * BYTES_PER_SAMPLE), 1)

    if not row["ok"]:
        return result.record("active_current", False, row["why"])

    _store_power(result, "active", row)
    say("        {0}".format(_power_line("active", row, bench.status_ok)))
    say("        stream {0:.1f} kB/s = {1:.0f}% of real time, {2:.0f} pkt/s".format(
        stream_bps / 1000.0, result.metrics["active_stream_pct_of_realtime"],
        packets_per_s))

    if stream_bps <= 0:
        return result.record("active_current", False,
                             "no audio arrived, so the module was not streaming")
    if row["current_ma"] < STATE_THRESHOLD_MA:
        return result.record("active_current", False,
                             "{0:.4f} mA looks like sleep".format(row["current_ma"]))

    ok = result.record("active_current", in_window(row["current_ma"], args.active_limits),
                       "{0:.4f} mA outside {1}".format(row["current_ma"],
                                                       args.active_limits))

    # Same mode, radio quiet. Unsubscribing leaves the module in active mode
    # with the mic, PDM, CPU and white LED all running and nothing being
    # transmitted. That figure is identical whatever host is attached, whereas
    # the streaming one above is only as phone-like as this host's throughput.
    # Module-to-module variation lives in the analog parts, not the radio, so
    # this is the number that actually discriminates.
    try:
        await session.client.stop_notify(AUDIO_CHARACTERISTIC_UUID)
        await asyncio.sleep(args.active_settle)
        quiet = await measure_async(bench, ACTIVE_REPEATS)
        await start_audio_notify(session.client, session._on_audio)
    except Exception as error:                          # noqa: BLE001
        return result.record("active_quiet", False, "could not measure quiet: "
                             "{0}".format(error)) and ok

    if not quiet["ok"]:
        return result.record("active_quiet", False, quiet["why"]) and ok

    _store_power(result, "active_quiet", quiet)
    say("        {0}".format(_power_line("quiet", quiet, bench.status_ok)))
    say("        radio contributes {0:+.4f} mA of the streaming figure".format(
        row["current_ma"] - quiet["current_ma"]))
    result.metrics["active_radio_ma"] = round(row["current_ma"] - quiet["current_ma"], 4)

    quiet_ok = result.record(
        "active_quiet", in_window(quiet["current_ma"], args.active_quiet_limits),
        "{0:.4f} mA outside {1}".format(quiet["current_ma"], args.active_quiet_limits))
    return ok and quiet_ok


async def stage_led(args, session, bench, result):
    """Proves the white LED electrically, with no operator looking at it.

    The LED is the largest single load on the board in active mode, so forcing
    it off and on and differencing the supply current says whether it is
    actually there and actually the right part.
    """
    say("  [7/8] white LED...")
    if bench is None:
        result.skip("led")
        return True

    try:
        await session.command("AT+LED=0", "LED DONE", timeout=5.0)
        await asyncio.sleep(args.led_settle)
        dark = await measure_async(bench, LED_REPEATS)

        await session.command("AT+LED=1", "LED DONE", timeout=5.0)
        await asyncio.sleep(args.led_settle)
        lit = await measure_async(bench, LED_REPEATS)

        await session.command("AT+LED=AUTO", "LED DONE", timeout=5.0)
    except (TimeoutError, RuntimeError) as error:
        return result.record("led", False, str(error))

    if not (dark["ok"] and lit["ok"]):
        return result.record("led", False, "current reading out of range")

    delta = lit["current_ma"] - dark["current_ma"]
    result.metrics["led_delta_ma"] = round(delta, 4)
    result.metrics["led_off_ma"] = round(dark["current_ma"], 4)
    result.metrics["led_on_ma"] = round(lit["current_ma"], 4)
    say("        off {0:.4f} mA, on {1:.4f} mA, delta {2:.4f} mA".format(
        dark["current_ma"], lit["current_ma"], delta))

    return result.record("led", in_window(delta, args.led_limits),
                         "delta {0:.4f} mA outside {1}".format(delta, args.led_limits))


async def stage_sleep(args, session, bench, result):
    say("  [8/8] sleep mode + current...")
    try:
        await session.command(CMD_SLEEP, RESP_SLEEP_DONE, timeout=10.0)
    except (TimeoutError, RuntimeError) as error:
        return result.record("sleep", False, str(error))

    if bench is None:
        result.skip("sleep_current")
        return True

    session.capturing = False
    # The sleep link runs at a 500 ms interval with slave latency 4, so
    # connection events land ~2.5 s apart and the parameter update itself takes
    # several events to come into force.
    await asyncio.sleep(args.sleep_settle)

    row = await measure_async(bench, SLEEP_REPEATS)
    if not row["ok"]:
        return result.record("sleep_current", False, row["why"])

    _store_power(result, "sleep", row)
    say("        {0}".format(_power_line("sleep", row, bench.status_ok)))

    if row["current_ma"] > STATE_THRESHOLD_MA:
        return result.record("sleep_current", False,
                             "{0:.4f} mA looks like active".format(row["current_ma"]))
    return result.record("sleep_current", in_window(row["current_ma"], args.sleep_limits),
                         "{0:.4f} mA outside {1}".format(row["current_ma"],
                                                         args.sleep_limits))


def _store_power(result, state, row):
    result.metrics[state + "_ma"] = round(row["current_ma"], 4)
    result.metrics[state + "_spread_ma"] = round(row["spread_ma"], 4)
    result.metrics[state + "_case"] = row["case"]
    result.metrics[state + "_test_s"] = round(row["test_s"], 2)
    result.metrics[state + "_vbat_v"] = vbat_volt(row["rails"])


def _power_line(state, row, status_ok):
    volts = vbat_volt(row["rails"])
    spread = "  +/-{0:.4f}".format(row["spread_ma"] / 2.0) if row["readings"] > 1 else ""
    warnings = rail_warnings(row["rails"], status_ok)
    return "{0:<7}{1:8.4f} mA{2}   case {3}   {4:.2f} s   VBAT {5}{6}".format(
        state, row["current_ma"], spread, row["case"], row["test_s"],
        "{0:.4f} V".format(volts) if volts is not None else "-",
        "   ** {0} **".format(", ".join(warnings)) if warnings else "")


async def connect_with_retry(args, address):
    """A connected BleakClient, or a string saying why there is none.

    Straight after programming the module has only just rebooted, and BlueZ
    often answers the first attempt with "Input/output error" or "le-connection-
    abort-by-local". A second attempt a moment later nearly always works, so a
    single failure is not a verdict on the module -- and it must never escape
    as a traceback, which would end a --loop shift.
    """
    error = None
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        client = BleakClient(address, timeout=args.connect_timeout,
                             **bluez_kwargs(args.adapter))
        try:
            await client.connect()
            if client.is_connected:
                return client
            error = "connected but link reported down"
        except Exception as caught:                     # noqa: BLE001
            # bleak raises BleakError, BleakDBusError, TimeoutError or
            # EOFError depending on the version and where BlueZ gave up.
            error = "{0}: {1}".format(type(caught).__name__, caught)
        if attempt < CONNECT_ATTEMPTS:
            say("        attempt {0} failed ({1}); retrying".format(attempt, error))
            await asyncio.sleep(CONNECT_RETRY_PAUSE)
    return "failed to connect after {0} attempts: {1}".format(CONNECT_ATTEMPTS, error)


async def run_module(args, bench):
    """The whole sequence for one module. Returns a Result, or None if it never
    got far enough to have an address.
    """
    if args.program:
        say("  [1/8] programming...")
        ok, message = program_module(args.hex)
        if not ok:
            say("        FAILED")
            if message:
                say(message)
            return None
        say("        verified")
        # The module reboots into advertising; give it a moment before scanning.
        await asyncio.sleep(1.0)
    else:
        say("  [1/8] programming skipped")

    say("  [2/8] scanning for '{0}'...".format(args.name))
    address, rssi, error = await scan_mac(args.name, args.scan_seconds,
                                          require_stable=not args.allow_private,
                                          adapter=args.adapter)
    if error:
        say("        {0}".format(error))
        return None
    say("        {0} at {1:.0f} dBm".format(address, rssi))

    result = Result(address)
    result.metrics["rssi_dbm"] = round(rssi, 1)
    result.record("rssi", in_window(rssi, args.rssi_limits),
                  "{0:.0f} dBm outside {1} -- check the antenna and that the "
                  "module is seated".format(rssi, args.rssi_limits))

    say("  [3/8] connecting...")
    client = await connect_with_retry(args, address)
    if isinstance(client, str):
        say("        FAILED: {0}".format(client))
        result.record("connect", False, client)
        return result
    result.record("connect", True)
    say("        connected")

    # Not "async with client": that would call connect() a second time.
    try:
        session = DutSession(client)
        await session.start()

        await stage_acctest(session, result)
        if args.mic:
            await stage_mic(args, session, result)
        else:
            result.skip("mic")
        await stage_active(args, session, bench, result)
        if args.led_check:
            await stage_led(args, session, bench, result)
        else:
            result.skip("led")
        await stage_sleep(args, session, bench, result)
    finally:
        try:
            await client.disconnect()
        except Exception:                               # noqa: BLE001
            pass

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════════════

# Fixed leading columns so the file opens sensibly in a spreadsheet; every
# metric a run produced follows, and the header grows if a new metric appears.
BASE_COLUMNS = ["timestamp", "mac", "verdict", "failures",
                "stage_rssi", "stage_connect", "stage_acctest", "stage_mic",
                "stage_active", "stage_active_current", "stage_active_quiet",
                "stage_led", "stage_sleep", "stage_sleep_current"]


def row_for(result):
    row = {
        "timestamp": result.timestamp,
        "mac": result.mac,
        "verdict": result.verdict,
        "failures": "; ".join(result.failures),
    }
    for stage, outcome in result.stages.items():
        row["stage_" + stage] = outcome
    row.update(result.metrics)
    return row


def log_csv(path, result):
    """Append one row, rewriting the header if this run produced a metric the
    file has not seen before. A jig that silently dropped new columns would
    lose exactly the data a new failure mode shows up in.
    """
    row = row_for(result)

    with _file_lock:
        existing = []
        header = []
        if os.path.exists(path):
            with open(path, newline="") as handle:
                reader = csv.DictReader(handle)
                header = list(reader.fieldnames or [])
                existing = list(reader)

        extra = [key for key in row if key not in header]
        if not header:
            header = BASE_COLUMNS + [k for k in extra if k not in BASE_COLUMNS]
            extra = []
        elif extra:
            header = header + extra

        with open(path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            for old in existing:
                writer.writerow(old)
            writer.writerow(row)

    return len(header)


def post_sheet(url, result):
    """Apps Script answers HTTP 200 even when doPost catches an error, so the
    status code proves nothing. Only the "ok" field of the body does.
    """
    if not url:
        return "sheet disabled"
    if not REQUESTS_OK:
        return "sheet skipped, requests is not installed"

    payload = row_for(result)
    payload["key"] = "{0}_{1}".format(result.mac, result.timestamp)

    try:
        response = requests.post(url, data=json.dumps(payload),
                                 headers={"Content-Type": "application/json"},
                                 timeout=POST_TIMEOUT)
        if response.status_code != 200:
            queue_add(payload)
            return "sheet HTTP {0}, queued".format(response.status_code)
        try:
            answer = response.json()
        except ValueError:
            queue_add(payload)
            return ("sheet returned a web page, not JSON: the deployment access "
                    "is not 'Anyone', or the URL is wrong. Queued.")
        if answer.get("ok"):
            return "sheet send done"
        queue_add(payload)
        return "sheet refused: {0}, queued".format(answer.get("error") or answer)
    except Exception as error:                          # noqa: BLE001
        queue_add(payload)
        return "sheet unreachable: {0}, queued".format(error)


def queue_add(payload):
    """Queued rows survive a crash, so a module measured but not sent is never
    silently lost.
    """
    with _file_lock:
        pending = {}
        if os.path.exists(PENDING_FILE):
            try:
                with open(PENDING_FILE) as handle:
                    pending = json.load(handle)
            except (json.JSONDecodeError, OSError):
                pending = {}
        pending[payload["key"]] = payload
        tmp = PENDING_FILE + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(pending, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, PENDING_FILE)


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Neosapien production test: program, BLE, IMU, mic, power.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    stages = parser.add_argument_group("stages")
    stages.add_argument("--no-program", dest="program", action="store_false",
                        help="the module is already flashed")
    stages.add_argument("--no-power", dest="power", action="store_false",
                        help="skip every current measurement (no ADC boards)")
    stages.add_argument("--no-mic", dest="mic", action="store_false",
                        help="skip the microphone test")
    stages.add_argument("--led-check", action="store_true", default=True,
                        help="prove the white LED by its current delta")
    stages.add_argument("--no-led-check", dest="led_check", action="store_false")
    stages.add_argument("--loop", action="store_true",
                        help="keep testing modules, Enter between each")

    ble = parser.add_argument_group("BLE")
    ble.add_argument("--name", default=DEVICE_NAME)
    ble.add_argument("--scan-seconds", type=float, default=SCAN_SECONDS)
    ble.add_argument("--connect-timeout", type=float, default=CONNECT_TIMEOUT)
    ble.add_argument("--transfer-timeout", type=float, default=TRANSFER_TIMEOUT)
    ble.add_argument("--adapter", default=None, metavar="HCI",
                     help="Bluetooth controller to use, e.g. hci1. The Pi 3's built-in "
                          "radio is BT 4.1 and has no Data Length Extension, which caps "
                          "the audio stream well below real time; a BT 5 controller "
                          "lifts that.")
    ble.add_argument("--allow-private", action="store_true",
                     help="accept a rotating BLE address (it cannot identify a module)")

    mic = parser.add_argument_group("microphone")
    mic.add_argument("--limits", default=LIMITS_FILE,
                     help="calibrated limits; without it the metrics are only recorded")
    mic.add_argument("--capture-ms", type=int, default=CAPTURE_MS)
    mic.add_argument("--stimulus", choices=("multitone", "sequence"), default=STIMULUS)
    mic.add_argument("--tones", nargs="+",
                     default=[str(int(f)) for f in DEFAULT_TONES],
                     help="stimulus frequencies in Hz; commas or spaces both work")
    mic.add_argument("--amplitude", type=float, default=AMPLITUDE)
    mic.add_argument("--tone-gains", type=float, nargs="+", default=None, metavar="G",
                     help="per-tone multiplier, one per --tones value; "
                          "mic_qc.py --measure prints the values to use")
    mic.add_argument("--stimulus-lead", type=float, default=STIMULUS_LEAD)
    mic.add_argument("--tone-bandwidth", type=float, default=TONE_BANDWIDTH_HZ)
    mic.add_argument("--alsa-device", default=ALSA_DEVICE,
                     help="speaker output (default {0})".format(ALSA_DEVICE))
    mic.add_argument("--sd-gpio", type=int, default=None,
                     help="BCM pin driving the amplifier's SD line")
    mic.add_argument("--sd-active-low", action="store_true")
    mic.add_argument("--wav", default=None, help="save each stimulus capture here")

    power = parser.add_argument_group("power")
    power.add_argument("--bench-dir", default=BENCH_DIR)
    power.add_argument("--profile", choices=("fast", "steady", "careful"),
                       default=BENCH_PROFILE)
    ble.add_argument("--rssi-limits", type=float, nargs=2, default=list(RSSI_LIMITS),
                     metavar=("LO", "HI"),
                     help="advertising RSSI window in dBm; depends on jig geometry, "
                          "so calibrate it on known-good modules")
    power.add_argument("--active-limits", type=float, nargs=2, default=list(ACTIVE_LIMITS),
                       metavar=("LO", "HI"),
                       help="window for active mode WHILE STREAMING to this host; "
                            "jig-specific, see --active-quiet-limits")
    power.add_argument("--active-quiet-limits", type=float, nargs=2,
                       default=list(ACTIVE_QUIET_LIMITS), metavar=("LO", "HI"),
                       help="window for active mode with notifications off; "
                            "independent of host throughput, so keep this one tight")
    power.add_argument("--sleep-limits", type=float, nargs=2, default=list(SLEEP_LIMITS),
                       metavar=("LO", "HI"))
    power.add_argument("--led-limits", type=float, nargs=2, default=list(LED_DELTA_LIMITS),
                       metavar=("LO", "HI"))
    power.add_argument("--active-settle", type=float, default=ACTIVE_SETTLE_S)
    power.add_argument("--sleep-settle", type=float, default=SLEEP_SETTLE_S)
    power.add_argument("--led-settle", type=float, default=LED_SETTLE_S)

    out = parser.add_argument_group("output")
    out.add_argument("--hex", default=FIRMWARE_HEX)
    out.add_argument("--csv", default=CSV_FILE)
    out.add_argument("--sheet-url", default=GOOGLE_URL)
    out.add_argument("--version", action="version",
                     version="neo_jig.py {0}".format(VERSION))
    out.add_argument("--list-adapters", action="store_true",
                     help="show every Bluetooth controller and whether it supports "
                          "Data Length Extension, then exit")
    out.add_argument("--verbose", action="store_true", help="full OpenOCD output")

    args = parser.parse_args(argv)
    args.tones = parse_tones(args.tones)
    args.active_limits = tuple(args.active_limits)
    args.active_quiet_limits = tuple(args.active_quiet_limits)
    args.rssi_limits = tuple(args.rssi_limits)
    args.sleep_limits = tuple(args.sleep_limits)
    args.led_limits = tuple(args.led_limits)
    return args


def load_limits(path):
    """Returns (limits, message). None means run without a verdict."""
    if not path:
        return None, "mic limits disabled"
    if not os.path.exists(path):
        return None, ("{0} not found -- the mic metrics will be recorded but "
                      "not judged. Build it with:\n"
                      "      python3 mic_qc.py --calibrate --runs 10 "
                      "--limits {0}".format(path))
    with open(path) as handle:
        return json.load(handle), "mic limits from {0}".format(path)


def report(result, args):
    say()
    say("  " + "=" * 58)
    say("  RESULT: {0}   {1}".format(result.verdict, result.mac))
    for stage, outcome in result.stages.items():
        say("      {0:<18}{1}".format(stage, outcome))
    for failure in result.failures:
        say("      ! {0}".format(failure))
    say("  " + "=" * 58)

    try:
        columns = log_csv(args.csv, result)
        say("  logged to {0} ({1} columns)".format(args.csv, columns))
    except OSError as error:
        say("  could not write {0}: {1}".format(args.csv, error))

    if args.sheet_url:
        say("  {0}".format(post_sheet(args.sheet_url, result)))


async def amain(args):
    global VERBOSE
    VERBOSE = args.verbose

    limits, message = load_limits(args.limits if args.mic else None)
    if limits is not None:
        # A limits file built for a different stimulus gives confident,
        # meaningless verdicts. Refuse rather than ship bad modules.
        differences = context_mismatch(limits, stimulus_context(args, args.tones))
        if differences:
            say("the mic limits do not apply to these options:")
            for difference in differences:
                say("  " + difference)
            say("Re-calibrate with these exact options, or pass the ones the "
                "limits were built with.")
            return EXIT_ERROR
    args.limits = limits

    bench = None
    if args.power:
        try:
            bench = Bench(args.bench_dir, args.profile)
        except ImportError as error:
            say("cannot import the measurement modules from {0}: {1}".format(
                args.bench_dir, error))
            return EXIT_ERROR
        except (OSError, RuntimeError) as error:
            say("the instrument did not come up: {0}".format(error))
            return EXIT_ERROR

    say()
    say("  NEO production jig   v{0}".format(VERSION))
    say("  name       '{0}'".format(args.name))
    say("  " + adapter_line(list_adapters(), args.adapter))
    say("  firmware   {0}".format(os.path.basename(args.hex) if args.program else "not flashed"))
    say("  power      {0}".format(
        "profile {0}, active x{1}, sleep x{2}".format(bench.profile_name,
                                                      ACTIVE_REPEATS, SLEEP_REPEATS)
        if bench else "disabled"))
    say("  {0}".format(message))
    if bench:
        say("  active window {0} mA, sleep window {1} mA".format(
            args.active_limits, args.sleep_limits))
        say("  NOTE: the active window is a limit for THIS JIG. This host drains")
        say("        far less of the audio stream than a phone, so the number is")
        say("        lower than the field figure. Calibrate on known-good units.")
    say()

    worst = EXIT_PASS
    try:
        while True:
            say("  " + "-" * 58)
            result = await run_module(args, bench)
            if result is None:
                say("  RESULT: ERROR   the module never got far enough to test")
                worst = max(worst, EXIT_ERROR)
            else:
                report(result, args)
                if not result.passed:
                    worst = max(worst, EXIT_FAIL)

            if not args.loop:
                return worst
            say()
            try:
                input("  fit the next module and press Enter (Ctrl-C to stop)... ")
            except EOFError:
                return worst
    finally:
        if bench:
            try:
                bench.park()
            except Exception:                           # noqa: BLE001
                pass


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    if args.list_adapters:
        adapters = list_adapters()
        if not adapters:
            say("no controllers found (is hciconfig installed?)")
            return EXIT_ERROR
        say("\n  controller   Bluetooth   state       Data Length Extension")
        for name, version, has_dle, up in adapters:
            say("  {0:<13}{1:<12}{2:<12}{3}".format(
                name, version, "up" if up else "down",
                "yes" if has_dle else "NO -- stream cannot reach real time"))
        say("\n  neo_jig.py {0}".format(VERSION))
        say("  Use the best one with:  --adapter <name>\n")
        return EXIT_PASS

    needs_root = args.program or args.power or args.sd_gpio is not None
    if needs_root and hasattr(os, "geteuid") and os.geteuid() != 0:
        say("run this with sudo: it needs OpenOCD, I2C and the GPIOs")
        return EXIT_ERROR

    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        say("\n  stopped")
        return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
