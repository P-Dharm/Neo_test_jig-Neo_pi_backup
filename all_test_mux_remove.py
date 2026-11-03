#!/usr/bin/env python3
"""neo.py
======
python3 mic_qc.py --calibrate --runs 10 --limits limits_mic.json   // run this to generate limits_mic.json

NEO (nRF52840) module test jig, 3-in-1 automated production testing script.

Mode Selection & CLI Reference
------------------------------
  1. python3 neo.py --once --board x : Immediate one-shot test on Socket X.
  2. python3 neo.py --board x        : Button-driven continuous loop on Socket X.
  3. python3 neo.py                  : Default floor mode. Press Start to execute 
                                       based on GPIO 9 (HIGH = Multi, LOW = Single).
  4. python3 neo.py --once           : Immediate sweep evaluating GPIO 9.

Test Chain
----------
  1. DETECT: Apply power to target board, OpenOCD reads FICR MAC (Random Static applied).
  2. SKIP CHECK: If identity MAC is marked PASS in the CSV, skip re-testing.
  3. FLASH TEST FW: Erase chip, flash AT test build via OpenOCD. Hard power cycle.
  4. RSSI & BLE CONNECT: HCI RSSI scan, GATT connection via bleak.
  5. IMU: Send AT+ACCTEST.
  6. MIC: Send AT+CAPTURE, play stimulus, transfer audio, check THD/SNR against limits.
  7. ACTIVE STREAM: Send AT+ACTIVE. Measure current with live audio traffic.
  8. ACTIVE QUIET: Stop notifications. Measure current with radio idle.
  9. LED TEST: Send AT+LED=0, measure. Send AT+LED=1, measure. Check delta.
 10. SLEEP TEST: Send AT+SLEEP. Measure deep sleep current.
 11. FLASH CLIENT FW: If all criteria pass, program final production firmware.
 12. LOG & CLEANUP: Write 23-column row to CSV and Google Sheets. Power off.
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

try:
    import aioblescan as aiobs
    AIOBLESCAN_OK = True
except ImportError:
    AIOBLESCAN_OK = False

try:
    from bleak import BleakClient
    BLEAK_OK = True
except ImportError:
    BLEAK_OK = False

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# Import test cases from the supplementary files
try:
    from mic_test import (
        CMD_ACTIVE, CMD_SLEEP, RESP_ACTIVE_DONE, RESP_SLEEP_DONE,
        AUDIO_CHARACTERISTIC_UUID, DutSession, start_audio_notify,
        SAMPLE_RATE, BYTES_PER_SAMPLE
    )
    from mic_qc import (
        DEFAULT_TONES, TONE_BANDWIDTH_HZ, AmpSelect, build_stimulus,
        capture_once, evaluate, measure, parse_tones
    )
    EXTERNAL_TESTS_OK = True
except ImportError as e:
    EXTERNAL_TESTS_OK = False
    print(f"WARNING: Missing external test file - {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════

WORK_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Files ──────────────────────────────────────────────────────────────────
CSV_FILE = os.path.join(WORK_DIR, "NEO_JIG_RESULTS.csv")
LIMITS_FILE = os.path.join(WORK_DIR, "limits_mic.json")
TEST_HEX = "/home/isc/jig_pi/firmware/neo_test_fw.hex"
CLIENT_HEX = "/home/isc/jig_pi/firmware/neo_test_fw.hex"

# ── Power bench ────────────────────────────────────────────────────────────
BENCH_DIR = "/home/isc/Power_gem"
ACTIVE_REPEATS = 5
SLEEP_REPEATS = 3
LED_REPEATS = 3
ACTIVE_SETTLE_S = 2.0
SLEEP_SETTLE_S = 5.0
LED_SETTLE_S = 1.0

# Calibrated Limits (mA and dBm)
ACTIVE_LIMITS = (1.50, 4.50)
ACTIVE_QUIET_LIMITS = (1.20, 3.50)
SLEEP_LIMITS = (0.02, 0.29)
LED_DELTA_LIMITS = (0.20, 2.20)
RSSI_LIMITS = (-75.0, -20.0)
STATE_THRESHOLD_MA = 1.0

# ── Programming ────────────────────────────────────────────────────────────
OPENOCD_TCL = "/home/isc/openocd/tcl"
OPENOCD_CFG = "/home/isc/nrf52.cfg"
FLASH_ATTEMPTS = 3
FLASH_TIMEOUT = 120
FLASH_RETRY_PAUSE = 2.0
FICR_DEVICEADDRTYPE = 0x100000A0
FICR_DEVICEADDR0 = 0x100000A4
FICR_DEVICEADDR1 = 0x100000A8
FORCE_RANDOM_STATIC = True

DETECT_RETRIES = 3
DETECT_RETRY_PAUSE = 1.0

# ── BLE ────────────────────────────────────────────────────────────────────
BLE_NAME = "SPH0141_Audio"
HCI_DEVICE_INDEX = 0
RSSI_COUNT = 10
RSSI_SCAN_TIMEOUT = 15.0
RSSI_MIN_READINGS = 3
CONNECT_TIMEOUT = 20.0
CONNECT_ATTEMPTS = 3
CONNECT_RETRY_PAUSE = 2.0
BLE_ADAPTER = "hci0"

# ── Mic test ───────────────────────────────────────────────────────────────
CAPTURE_MS = 1000
STIMULUS = "sequence"
AMPLITUDE = 0.35
STIMULUS_LEAD = 0.5
ALSA_DEVICE = "plughw:CARD=sndrpihifiberry,DEV=0"

# ── Pins & Hardware Routing ────────────────────────────────────────────────
PIN_START = 18
PIN_STOP = 23
PIN_MODE_SWITCH = 9  

PIN_PWR = {1: 13, 2: 27, 3: 26}
USE_BUTTONS = False  # Set to True to use physical Start/Stop pins, False to run automatically

# ── CSV Schema ─────────────────────────────────────────────────────────────
HEADERS = [
    "SR", "TIMESTAMP", "MAC", "FICR MAC", "RSSI (dBm)", "BLE",
    "IMU ACCTEST", "MIC RESULT", "MIC THD (%)", "MIC SNR (dB)",
    "LED DELTA (mA)", "ACTIVE STREAM (mA)", "ACTIVE QUIET (mA)", 
    "ACTIVE 3V3", "ACTIVE 5V", "ACTIVE VBAT",
    "SLEEP CURRENT (mA)", "SLEEP 3V3", "SLEEP 5V", "SLEEP VBAT",
    "CLIENT FIRMWARE", "RESULT", "COMMENTS"
]

# ── Google Sheet ───────────────────────────────────────────────────────────
GOOGLE_URL = ""
ENABLE_GOOGLE = False
POST_TIMEOUT = 15
SHEET_TAB = "NEO TEST"

NA = "NA"
stop_event = threading.Event()
_file_lock = threading.Lock()
VERBOSE = False


# ══════════════════════════════════════════════════════════════════════════════
#  GPIO & ROUTING
# ══════════════════════════════════════════════════════════════════════════════

def pin_set(gpio, high):
    try:
        subprocess.run(["pinctrl", "set", str(gpio), "op", "dh" if high else "dl"],
                       capture_output=True, timeout=2)
    except Exception as error:
        print(f"[gpio] set {gpio} failed: {error}")

def pin_get(gpio):
    try:
        result = subprocess.run(["pinctrl", "get", str(gpio)],
                                capture_output=True, text=True, timeout=2)
        return "hi" in result.stdout.lower()
    except Exception:
        return False

def route_hardware(board_num):
    for p_pin in PIN_PWR.values():
        pin_set(p_pin, False)
    time.sleep(0.1)
    
    pin_set(PIN_PWR[board_num], True)
    time.sleep(0.5) 

def clean_all_pins():
    for p_pin in PIN_PWR.values():
        pin_set(p_pin, False)


# ══════════════════════════════════════════════════════════════════════════════
#  SHEET POSTING
# ══════════════════════════════════════════════════════════════════════════════

def post_once(payload, quiet=False):
    if not (REQUESTS_OK and GOOGLE_URL):
        return False
    try:
        response = requests.post(
            GOOGLE_URL, data=json.dumps(payload),
            headers={"Content-Type": "application/json"}, timeout=POST_TIMEOUT)
        if response.status_code != 200:
            return False
        if response.json().get("ok"):
            return True
        return False
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  CSV WORKBOOK LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def check_mac_result_csv(mac):
    """Reads the CSV file line-by-line to check if the module already passed."""
    if not os.path.exists(CSV_FILE):
        return None
    with _file_lock:
        try:
            latest = None
            with open(CSV_FILE, "r", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if row.get("MAC", "").strip().upper() == mac.upper():
                        res = row.get("RESULT", "").strip().upper()
                        if res:
                            latest = res
            return latest
        except Exception as error:
            print(f"[csv] read error: {error}")
            return None

def write_csv_row(record):
    """Appends the result to the CSV file and determines the next SR number."""
    with _file_lock:
        file_exists = os.path.exists(CSV_FILE)
        highest_sr = 0
        
        if file_exists:
            try:
                with open(CSV_FILE, "r", newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        try:
                            sr = int(row.get("SR", 0))
                            if sr > highest_sr:
                                highest_sr = sr
                        except ValueError:
                            pass
            except Exception:
                pass
                
        serial = f"{highest_sr + 1:02d}"
        record["sr"] = serial
        
        row_dict = {
            "SR": serial,
            "TIMESTAMP": record["timestamp"],
            "MAC": record["mac"],
            "FICR MAC": record["ficr_mac"],
            "RSSI (dBm)": record["rssi_dbm"],
            "BLE": record["ble"],
            "IMU ACCTEST": record["imu_acctest"],
            "MIC RESULT": record["mic_result"],
            "MIC THD (%)": record["mic_thd_pct"],
            "MIC SNR (dB)": record["mic_snr_db"],
            "LED DELTA (mA)": record["led_delta_ma"],
            "ACTIVE STREAM (mA)": record["active_stream_ma"],
            "ACTIVE QUIET (mA)": record["active_quiet_ma"],
            "ACTIVE 3V3": record["active_3v3"],
            "ACTIVE 5V": record["active_5v"],
            "ACTIVE VBAT": record["active_vbat"],
            "SLEEP CURRENT (mA)": record["sleep_current_ma"],
            "SLEEP 3V3": record["sleep_3v3"],
            "SLEEP 5V": record["sleep_5v"],
            "SLEEP VBAT": record["sleep_vbat"],
            "CLIENT FIRMWARE": record["client_firmware"],
            "RESULT": record["result"],
            "COMMENTS": record["comments"]
        }
        
        with open(CSV_FILE, "a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row_dict)
            
    return serial

def send_row(serial, record):
    payload = dict(record)
    payload["sheet_name"] = SHEET_TAB
    payload["key"] = f"{record['mac']}_{record['timestamp']}"
    payload["sr"] = serial

    if not ENABLE_GOOGLE:
        return "local only, sending disabled"
    
    if post_once(payload):
        return "sheet send done"
    
    return "sheet send failed (no retry)"

def blank_record():
    return {
        "timestamp": "", "mac": "", "ficr_mac": "", "rssi_dbm": NA, "ble": NA,
        "imu_acctest": NA, "mic_result": NA, "mic_thd_pct": NA, "mic_snr_db": NA,
        "led_delta_ma": NA, 
        "active_stream_ma": NA, "active_quiet_ma": NA, 
        "active_3v3": NA, "active_5v": NA, "active_vbat": NA,
        "sleep_current_ma": NA, "sleep_3v3": NA, "sleep_5v": NA, "sleep_vbat": NA,
        "client_firmware": NA, "result": "FAIL", "comments": ""
    }


# ══════════════════════════════════════════════════════════════════════════════
#  OPENOCD: DETECT, FICR MAC, FLASH
# ══════════════════════════════════════════════════════════════════════════════

UNLOCK_SEQUENCE = [
    "-c", "poll off",
    "-c", "nrf52.dap apreg 1 4 0",
    "-c", "nrf52.dap apreg 1 4 1",
    "-c", "set i 0",
    "-c", "while {[nrf52.dap apreg 1 8] == 1 && $i < 40} {sleep 50; incr i}",
    "-c", "nrf52.dap apreg 1 0 1",
    "-c", "nrf52.dap apreg 1 0 0",
    "-c", "nrf52.dap apreg 1 4 0",
    "-c", "sleep 100",
    "-c", "nrf52.cpu arp_examine",
    "-c", "poll on",
    "-c", "reset halt",
]

_MDW_RE = re.compile(r"(0x[0-9a-fA-F]+):\s*([0-9a-fA-F]{8})")

def _openocd(extra, timeout):
    command = ["openocd", "-s", OPENOCD_TCL, "-f", OPENOCD_CFG, "-c", "init"] + extra + ["-c", "shutdown"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout} s"
    except Exception as error:
        return str(error)

def read_ficr_mac():
    output = _openocd([
        "-c", "poll off",
        "-c", "nrf52.cpu arp_examine",
        "-c", "halt",
        f"-c", f"mdw {hex(FICR_DEVICEADDRTYPE)} 1",
        f"-c", f"mdw {hex(FICR_DEVICEADDR0)} 1",
        f"-c", f"mdw {hex(FICR_DEVICEADDR1)} 1",
    ], 20)

    if VERBOSE: print(output)

    low = output.lower()
    if "ap lock engaged" in low or "could not find mem-ap" in low:
        return None, None, "chip APPROTECT-locked, FICR unreadable."
    if "0x00000000" in output and "dpidr" in low:
        return None, None, "SWD connected but chip not powered"

    values = {}
    for match in _MDW_RE.finditer(output):
        values[int(match.group(1), 16)] = int(match.group(2), 16)

    try:
        addr0 = values[FICR_DEVICEADDR0]
        addr1 = values[FICR_DEVICEADDR1]
    except KeyError:
        return None, None, "no FICR words in output"

    if addr0 == 0xFFFFFFFF or (addr1 & 0xFFFF) == 0xFFFF:
        return None, None, "FICR is blank"

    octets = [
        (addr0 >> 0) & 0xFF, (addr0 >> 8) & 0xFF, (addr0 >> 16) & 0xFF, (addr0 >> 24) & 0xFF,
        (addr1 >> 0) & 0xFF, (addr1 >> 8) & 0xFF,
    ]
    raw = "%02X:%02X:%02X:%02X:%02X:%02X" % (octets[5], octets[4], octets[3], octets[2], octets[1], octets[0])
    if FORCE_RANDOM_STATIC:
        octets[5] = octets[5] | 0xC0
    identity = "%02X:%02X:%02X:%02X:%02X:%02X" % (octets[5], octets[4], octets[3], octets[2], octets[1], octets[0])
    return identity, raw, ""

def flash_hex(path, label):
    if not os.path.isfile(path):
        return False, f"{label} firmware not found: {path}"

    extra = UNLOCK_SEQUENCE + [
        "-c", "flash probe 0",
        f"-c", f"flash write_image {path}",
        f"-c", f"verify_image {path}",
        "-c", "reset run",
    ]
    output = ""
    for attempt in range(1, FLASH_ATTEMPTS + 1):
        if stop_event.is_set():
            return False, "stopped"
        output = _openocd(extra, FLASH_TIMEOUT)
        if VERBOSE: print(output)
        if "verified" in output.lower():
            return True, ""
        if attempt < FLASH_ATTEMPTS:
            print(f"[flash] {label} attempt {attempt} failed, retrying")
            time.sleep(FLASH_RETRY_PAUSE)

    tail = [line for line in output.splitlines() if line.strip()][-4:]
    return False, f"{label} flash failed: {' | '.join(tail)}"


# ══════════════════════════════════════════════════════════════════════════════
#  BLE RSSI SCANNER
# ══════════════════════════════════════════════════════════════════════════════

def _bt_is_on():
    try:
        result = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5)
        return "Powered: yes" in result.stdout
    except Exception:
        return False

def _bt_turn_on():
    try:
        subprocess.run(["rfkill", "unblock", "bluetooth"], capture_output=True, text=True, timeout=5)
        time.sleep(0.5)
        subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, text=True, timeout=5)
        time.sleep(2)
        return _bt_is_on()
    except Exception:
        return False

async def _collect_rssi(name, mac, count, timeout):
    readings = []
    target = mac.strip().upper()

    def _process(data):
        event = aiobs.HCI_Event()
        try: event.decode(data)
        except Exception: return
        peers = event.retrieve("peer")
        if not peers or str(peers[0].val).strip().upper().replace("-", ":") != target:
            return
        rssis = event.retrieve("rssi")
        if rssis: readings.append(rssis[0].val)

    loop = asyncio.get_running_loop()
    try: btsocket = aiobs.create_bt_socket(HCI_DEVICE_INDEX)
    except OSError as error: return [], f"hci{HCI_DEVICE_INDEX} error: {error}"

    conn, btctrl = await loop._create_connection_transport(btsocket, aiobs.BLEScanRequester, None, None)
    btctrl.process = _process
    try:
        await asyncio.wait_for(btctrl.send_scan_request(isactivescan=True), timeout=5.0)
    except asyncio.TimeoutError:
        conn.close()
        return [], "scan request timeout"

    elapsed = 0.0
    try:
        while elapsed < timeout and len(readings) < count:
            if stop_event.is_set(): break
            await asyncio.sleep(0.1)
            elapsed += 0.1
    finally:
        try: await btctrl.stop_scan_request()
        except Exception: pass
        conn.close()
        await asyncio.sleep(0.5)

    return readings[:count], ""

def scan_rssi(name, mac):
    if not AIOBLESCAN_OK: return None, "aioblescan not installed"
    if not _bt_is_on() and not _bt_turn_on(): return None, "BT adapter off"
    try:
        readings, error = asyncio.run(_collect_rssi(name, mac, RSSI_COUNT, RSSI_SCAN_TIMEOUT))
    except Exception as failure:
        return None, f"scan failed: {failure}"
    if error: return None, error
    if len(readings) < RSSI_MIN_READINGS:
        return None, f"only {len(readings)} reports from {mac}"
    return round(statistics.mean(readings)), ""


# ══════════════════════════════════════════════════════════════════════════════
#  POWER BENCH
# ══════════════════════════════════════════════════════════════════════════════
class Bench:
    def __init__(self, board_num, profile=None, manual_power=False):
        self.board_num = board_num
        self.manual_power = manual_power
        
        if BENCH_DIR and BENCH_DIR not in sys.path:
            sys.path.insert(0, BENCH_DIR)

        import main as report
        import currentMeasurementModule as current_module
        import voltageMeasurementModule as voltage_module

        self.report = report
        self.current_module = current_module
        self.rail_channels = voltage_module.RAIL_CHANNELS
        self.shunt_in_ground = voltage_module.SHUNT_IN_GROUND_RETURN
        self.status_ok = voltage_module.STATUS_OK

        if profile:
            current_module.set_profile(profile)
            voltage_module.set_profile(profile)

        self.chip, _ = report.quietly(lambda: current_module.CurrentMeasurementModule(board_num))
        self.rails, _ = report.quietly(lambda: voltage_module.VoltageMeasurementModule(board_num))
        self.park()

    def park(self):
        self.report.quietly(lambda: self.chip.select_range(self.current_module.RANGE_LOW_RS))

    def measure(self, repeats, range_name=None):
        # Skip GPIO toggle if in manual mode
        # if not self.manual_power:
        #     pin_set(PIN_PWR[self.board_num], False)
            # time.sleep(0.1)
        
        started = time.monotonic()
        readings = []
        for _ in range(max(1, repeats)):
            result, _ = self.report.quietly(lambda: self.chip.start_measurement(range_name))
            readings.append(result)
        elapsed = time.monotonic() - started

        valid = [r for r in readings if not (r["over_range"] or r["under_range"])]
        if not valid:
            self.park()
            # if not self.manual_power:
                # pin_set(PIN_PWR[self.board_num], True)
            raw = readings[-1]
            return {"ok": False, "why": f"out of range! ADC={raw['adc_volt']:.2f}mV (Under={raw['under_range']}, Over={raw['over_range']})"}

        currents = [r["current"] for r in valid]
        across_rs = statistics.mean(r["volt_across_rs"] for r in valid)
        ground_offset = across_rs if self.shunt_in_ground else 0.0
        
        rails = {}
        try:
            rails, _ = self.report.quietly(lambda: self.rails.measure_all(self.rail_channels, ground_offset))
        except OSError:
            pass 
        
        self.park()
        
        # Skip GPIO toggle if in manual mode
        if not self.manual_power:
            pin_set(PIN_PWR[self.board_num], True)

        return {
            "ok": True,
            "current_ma": statistics.mean(currents),
            "spread_ma": (max(currents) - min(currents)) if len(currents) > 1 else 0.0,
            "case": valid[-1]["case"],
            "test_s": elapsed,
            "rails": rails,
        }

async def measure_async(bench, repeats, range_name=None):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, bench.measure, repeats, range_name)

def parse_rail_volt(rails, key):
    result = rails.get(key)
    if result is None or result.get("over_range") or result.get("under_range"):
        return NA
    return round(result["rail_volt"] / 1000.0, 4)

def check_window(value, window):
    return window[0] <= value <= window[1]


# ══════════════════════════════════════════════════════════════════════════════
#  THE TEST STAGES
# ══════════════════════════════════════════════════════════════════════════════

async def _ble_stage(mac, bench, record):
    """Executes the complete connection, IMU, Mic, LED, and Power sequence."""
    
    # 1. Connect
    # client = BleakClient(mac, adapter=BLE_ADAPTER, timeout=CONNECT_TIMEOUT)
    client = BleakClient(mac, bluez={"adapter": BLE_ADAPTER}, timeout=CONNECT_TIMEOUT)
    connected = False
    last_error = ""
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        if stop_event.is_set(): return "stopped"
        try:
            await client.connect()
            connected = True
            break
        except Exception as error:
            last_error = str(error)
            print(f"[ble] connect attempt {attempt} failed: {error}")
            await asyncio.sleep(CONNECT_RETRY_PAUSE)

    if not connected:
        record["ble"] = "FAIL"
        return f"BLE: could not connect ({last_error})"

    record["ble"] = "PASS"
    print(f"[ble] connected to {mac}")

    try:
        session = DutSession(client)
        await session.start()
        
        # 2. IMU ACCTEST
        print("  [test] accelerometer self-test...")
        try:
            await session._send("AT+ACCTEST")
            response = await session._wait_for(lambda r: r.startswith("ACCTEST"), 15.0, "ACCTEST <verdict>")
            detail = response[len("ACCTEST"):].strip()
            ok = detail.upper().startswith("PASS")
            record["imu_acctest"] = "PASS" if ok else f"FAIL ({detail})"
            print(f"        {response}")
            if not ok: return f"IMU: {detail}"
        except Exception as e:
            record["imu_acctest"] = "FAIL"
            return f"IMU Timeout/Error: {e}"

        # 3. MIC TEST
        print("  [test] microphone QC...")
        tones = parse_tones([str(int(f)) for f in DEFAULT_TONES])
        stimulus = build_stimulus(STIMULUS, tones, CAPTURE_MS / 1000.0 + STIMULUS_LEAD + 1.0, AMPLITUDE, CAPTURE_MS, None)

        try:
            print("        capturing stimulus via speaker...")
            stimulus_raw = await capture_once(session, CAPTURE_MS, 60.0, stimulus, ALSA_DEVICE, STIMULUS_LEAD)
            print("        capturing silence...")
            silence_raw = await capture_once(session, CAPTURE_MS, 60.0, None, ALSA_DEVICE)
        except Exception as error:
            record["mic_result"] = "FAIL"
            return f"Mic Capture: {error}"

        metrics = measure(stimulus_raw, silence_raw, tones, STIMULUS, TONE_BANDWIDTH_HZ)
        record["mic_thd_pct"] = round(metrics.get("thd_pct", 0), 2)
        
        primary_tone = min(tones)
        record["mic_snr_db"] = round(metrics.get(f"snr_{int(primary_tone)}hz_db", 0), 1)

        if os.path.exists(LIMITS_FILE):
            with open(LIMITS_FILE) as handle:
                limits = json.load(handle)
            rows, ok = evaluate(metrics, limits)
            record["mic_result"] = "PASS" if ok else "FAIL"
            if not ok: return "Mic: Limits failed"
        else:
            record["mic_result"] = "PASS (No limits file)"
        
        # 3. MIC TEST (SKIPPED TO AVOID GPIO 19 CONFLICT)
        # print("  [test] microphone QC... SKIPPED")
        # record["mic_result"] = "SKIPPED"
        # record["mic_thd_pct"] = "NA"
        # record["mic_snr_db"] = "NA"

        # 4. ACTIVE CURRENT (STREAMING)
        print("  [test] active mode + streaming current...")
        try:
            await session.command(CMD_ACTIVE, RESP_ACTIVE_DONE, timeout=10.0)
            session.audio = bytearray()
            session.capturing = True
            await asyncio.sleep(ACTIVE_SETTLE_S)
            row = await measure_async(bench, ACTIVE_REPEATS)
            session.capturing = False
            
            if not row["ok"]: return f"Active Stream: {row['why']}"
            if row["current_ma"] < STATE_THRESHOLD_MA: return f"Active Stream: {row['current_ma']:.4f} mA is too low."
            
            record["active_stream_ma"] = round(row["current_ma"], 4)
            record["active_3v3"] = parse_rail_volt(row["rails"], "RAIL_3V3")
            record["active_5v"] = parse_rail_volt(row["rails"], "RAIL_5V")
            record["active_vbat"] = parse_rail_volt(row["rails"], "RAIL_VBAT")
            
            print(f"        Stream {row['current_ma']:.4f} mA")
            print(f"        3V3: {record['active_3v3']}V | 5V: {record['active_5v']}V | VBAT: {record['active_vbat']}V")
            if not check_window(row["current_ma"], ACTIVE_LIMITS): return f"Active Stream {row['current_ma']:.4f} mA out of bounds"
        except Exception as e:
            return f"Active Stream Error: {e}"

        # 5. ACTIVE CURRENT (QUIET)
        # print("  [test] active mode quiet (radio idle)...")
        # try:
        #     await session.client.stop_notify(AUDIO_CHARACTERISTIC_UUID)
        #     await asyncio.sleep(ACTIVE_SETTLE_S)
        #     quiet = await measure_async(bench, ACTIVE_REPEATS)
        #     await start_audio_notify(session.client, session._on_audio)
            
        #     if not quiet["ok"]: return f"Active Quiet: {quiet['why']}"
        #     record["active_quiet_ma"] = round(quiet["current_ma"], 4)
        #     print(f"        Quiet {quiet['current_ma']:.4f} mA")
        #     if not check_window(quiet["current_ma"], ACTIVE_QUIET_LIMITS): return f"Active Quiet {quiet['current_ma']:.4f} mA out of bounds"
        # except Exception as e:
        #     return f"Active Quiet Error: {e}"
        
        # print("  [test] active mode quiet (radio idle)...")
        # try:
        #     # await session.client.stop_notify(AUDIO_CHARACTERISTIC_UUID)
        #     await asyncio.sleep(ACTIVE_SETTLE_S)
        #     quiet = await measure_async(bench, ACTIVE_REPEATS)
        #     # await start_audio_notify(session.client, session._on_audio)
            
        #     if not quiet["ok"]: return f"Active Quiet: {quiet['why']}"
        #     record["active_quiet_ma"] = round(quiet["current_ma"], 4)
        #     print(f"        Quiet {quiet['current_ma']:.4f} mA")
        #     if not check_window(quiet["current_ma"], ACTIVE_QUIET_LIMITS): return f"Active Quiet {quiet['current_ma']:.4f} mA out of bounds"
        # except Exception as e:
        #     return f"Active Quiet Error: {e}"

        # 6. LED VERIFICATION
        print("  [test] white LED electrical delta...")
        try:
            await session.command("AT+LED=0", "LED DONE", timeout=5.0)
            await asyncio.sleep(LED_SETTLE_S)
            dark = await measure_async(bench, LED_REPEATS)

            await session.command("AT+LED=1", "LED DONE", timeout=5.0)
            await asyncio.sleep(LED_SETTLE_S)
            lit = await measure_async(bench, LED_REPEATS)

            await session.command("AT+LED=AUTO", "LED DONE", timeout=5.0)
            
            if not (dark["ok"] and lit["ok"]): return "LED: Current reading failed"
            delta = lit["current_ma"] - dark["current_ma"]
            record["led_delta_ma"] = round(delta, 4)
            print(f"        Delta {delta:.4f} mA")
            if not check_window(delta, LED_DELTA_LIMITS): return f"LED delta {delta:.4f} mA out of bounds"
        except Exception as e:
            return f"LED Test Error: {e}"

        # 7. SLEEP CURRENT
        print("  [test] sleep mode current...")
        try:
            await session.command(CMD_SLEEP, RESP_SLEEP_DONE, timeout=10.0)
            session.capturing = False
            await asyncio.sleep(SLEEP_SETTLE_S)
            
            row = await measure_async(bench, SLEEP_REPEATS, bench.current_module.RANGE_HIGH_GAIN)
            if not row["ok"]: return f"Sleep: {row['why']}"
            
            record["sleep_current_ma"] = round(row["current_ma"], 4)
            record["sleep_3v3"] = parse_rail_volt(row["rails"], "RAIL_3V3")
            record["sleep_5v"] = parse_rail_volt(row["rails"], "RAIL_5V")
            record["sleep_vbat"] = parse_rail_volt(row["rails"], "RAIL_VBAT")
            
            print(f"        Sleep {row['current_ma']:.4f} mA")
            print(f"        3V3: {record['sleep_3v3']}V | 5V: {record['sleep_5v']}V | VBAT: {record['sleep_vbat']}V")
            if row["current_ma"] > STATE_THRESHOLD_MA: return f"Sleep: {row['current_ma']:.4f} mA looks like active."
            if not check_window(row["current_ma"], SLEEP_LIMITS): return f"Sleep {row['current_ma']:.4f} mA out of bounds"
        except Exception as e:
            return f"Sleep Test Error: {e}"

        return ""
    finally:
        await client.disconnect()
        print("[ble] disconnected")


def run_cycle(board_num, profile):
    """Executes the test sequence on the target board. Returns True if module detected."""
    print("\n" + "=" * 62)
    print(f"  NEO CYCLE STARTING: BOARD {board_num}")
    print("=" * 62)

    record = blank_record()
    route_hardware(board_num)

    # 1. Detect
    mac = raw_mac = None
    error = "not attempted"
    for attempt in range(1, DETECT_RETRIES + 1):
        if stop_event.is_set():
            clean_all_pins()
            return False
        mac, raw_mac, error = read_ficr_mac()
        if mac: break
        print(f"[detect] {error} (attempt {attempt}/{DETECT_RETRIES})")
        time.sleep(DETECT_RETRY_PAUSE)

    if not mac:
        print(f"[detect] no module on socket {board_num}: {error}")
        clean_all_pins()
        return False

    print(f"[detect] identity {mac}   FICR {raw_mac}")
    record["mac"] = mac
    record["ficr_mac"] = raw_mac

    # 2. Skip Check via CSV
    if check_mac_result_csv(mac) == "PASS":
        print(f"[skip] {mac} already passed, not re-testing")
        clean_all_pins()
        return True

    # 3. Flash Test Firmware & Power Cycle
    ok, message = flash_hex(TEST_HEX, "test")
    if not ok:
        finish(record, message)
        return True
    print("[flash] test firmware verified. Power-cycling module...")
    
    pin_set(PIN_PWR[board_num], False)
    time.sleep(0.5)
    pin_set(PIN_PWR[board_num], True)
    time.sleep(2.0)

    # 4. RSSI
    rssi, message = scan_rssi(BLE_NAME, mac)
    if message:
        record["ble"] = "FAIL"
        finish(record, f"RSSI: {message}")
        return True
        
    record["rssi_dbm"] = rssi
    print(f"[rssi] {rssi} dBm")
    if not check_window(rssi, RSSI_LIMITS):
        finish(record, f"RSSI {rssi} dBm outside {RSSI_LIMITS}")
        return True

    # 5. Advanced Component & Power Tests
    if not EXTERNAL_TESTS_OK:
        finish(record, "Missing mic_test.py or mic_qc.py dependencies.")
        return True
        
    try:
        bench = Bench(board_num, profile)
    except Exception as error:
        finish(record, f"ADC initialization fault: {error}")
        return True

    try:
        message = asyncio.run(_ble_stage(mac, bench, record))
    except Exception as error:
        message = f"BLE stage fault: {error}"
        
    if message:
        finish(record, message)
        return True

    # 6. Client Firmware
    ok, message = flash_hex(CLIENT_HEX, "client")
    if not ok:
        record["client_firmware"] = "FAIL"
        finish(record, message)
        return True
        
    record["client_firmware"] = "PASS"
    print("[flash] client firmware verified")

    record["result"] = "PASS"
    finish(record, "")
    return True


def finish(record, comment):
    clean_all_pins()
    record["timestamp"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record["comments"] = comment
    if comment:
        record["result"] = "FAIL"
        print(f"[result] FAIL  {comment}")
    else:
        print("[result] PASS")

    try:
        serial = write_csv_row(record)
    except Exception as error:
        print(f"[csv] could not write: {error}")
        return
    print(f"[sheet] {send_row(serial, record)}   SR {serial}")


# ══════════════════════════════════════════════════════════════════════════════
#  BUTTONS & EXECUTION CONTROLLER
# ══════════════════════════════════════════════════════════════════════════════

def watch_buttons(profile, target_board=1):
    busy = threading.Event()
    previous_start = False
    previous_stop = False

    print(f"\n[buttons] Start GPIO{PIN_START}, Stop GPIO{PIN_STOP}.")
    print(f"[buttons] Mode Switch GPIO{PIN_MODE_SWITCH} (HIGH = Multi, LOW = Single).")
    if target_board != 1:
        print(f"[buttons] Single-board target socket configured to: Board {target_board}")
    print("[buttons] waiting...\n")

    while True:
        start_now = pin_get(PIN_START)
        stop_now = pin_get(PIN_STOP)

        if start_now and not previous_start and not busy.is_set():
            busy.set()
            stop_event.clear()
            
            is_multi_mode = pin_get(PIN_MODE_SWITCH)
            
            if is_multi_mode:
                print("\n[mode] MULTI-BOARD: Round-robin testing Sockets 1, 2, and 3")
            else:
                print(f"\n[mode] SINGLE-BOARD: Continuous detection loop on Socket {target_board}")

            def _run():
                try:
                    if is_multi_mode:
                        while not stop_event.is_set():
                            for b_num in [1, 2, 3]:
                                if stop_event.is_set(): break
                                run_cycle(b_num, profile)
                                clean_all_pins()
                                time.sleep(0.5)
                    else:
                        while not stop_event.is_set():
                            run_cycle(target_board, profile)
                            clean_all_pins()
                            # Safe swap window
                            waited = 0.0
                            while waited < 2.0 and not stop_event.is_set():
                                time.sleep(0.1)
                                waited += 0.1
                finally:
                    clean_all_pins()
                    busy.clear()
                    print("\n[buttons] stopped, pins cleaned. Press Start for next run.\n")

            threading.Thread(target=_run, daemon=True).start()

        if stop_now and not previous_stop:
            print("\n[buttons] Stop pressed")
            stop_event.set()

        previous_start = start_now
        previous_stop = stop_now
        time.sleep(0.05)


def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="NEO 3-in-1 automated test jig")
    parser.add_argument("--once", action="store_true", help="run one cycle immediately")
    parser.add_argument("--board", type=int, choices=[1, 2, 3], help="target socket (1, 2, or 3)")
    parser.add_argument("--profile", choices=("fast", "steady", "careful"), default="careful")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--adapter", type=str, default="hci0", help="Bluetooth adapter (e.g., hci1)")
    parser.add_argument("--current", action="store_true", help="Only measure default boot current and exit")
    parser.add_argument("--voltage", action="store_true", help="Only measure rail voltages and exit")
    args = parser.parse_args()
    VERBOSE = args.verbose
    global HCI_DEVICE_INDEX, BLE_ADAPTER
    BLE_ADAPTER = args.adapter
    try:
        HCI_DEVICE_INDEX = int(args.adapter.replace("hci", ""))
    except ValueError:
        HCI_DEVICE_INDEX = 0

    try: sys.stdout.reconfigure(line_buffering=True)
    except AttributeError: pass

    if os.geteuid() != 0:
        print("run this with sudo")
        return 2

    print("=" * 62)
    print("  ISC NEO 3-in-1 JIG (Integrated CSV Logging)")
    print("=" * 62)

    if not BLEAK_OK: print("  bleak is missing: pip3 install bleak --break-system-packages")
    if not EXTERNAL_TESTS_OK: print("  mic_test.py or mic_qc.py is missing from the directory.")
    
    for label, path in (("test", TEST_HEX), ("client", CLIENT_HEX)):
        if not os.path.isfile(path): print(f"  {label} firmware missing: {path}")

    try:
        # --- NEW MEASUREMENT INTERCEPT BLOCK ---
        if args.current or args.voltage:
            target_board = args.board if args.board else 1
            print(f"\n[power] Manual mode: reading Socket {target_board} without changing GPIOs...")
            
            bench = Bench(target_board, args.profile, manual_power=True)
            row = asyncio.run(measure_async(bench, 3))
            
            if not row["ok"]:
                print(f"[power] Measurement failed: {row['why']}")
            else:
                if args.current:
                    print(f"  -> Default Boot Current: {row['current_ma']:.4f} mA")
                if args.voltage:
                    print(f"  -> 3V3 Rail: {parse_rail_volt(row['rails'], 'RAIL_3V3')} V")
                    print(f"  -> 5V Rail: {parse_rail_volt(row['rails'], 'RAIL_5V')} V")
                    print(f"  -> VBAT Rail: {parse_rail_volt(row['rails'], 'RAIL_VBAT')} V")
                    
            return 0
        # ---------------------------------------
        # ---------------------------------------

        if args.once:
            if args.board:
                print(f"\n[mode] ONE-SHOT: Testing Socket {args.board}")
                run_cycle(args.board, args.profile)
            else:
                is_multi_mode = pin_get(PIN_MODE_SWITCH)
                if is_multi_mode:
                    print("\n[mode] ONE-SHOT MULTI: Testing Sockets 1, 2, and 3 once")
                    for b_num in [1, 2, 3]:
                        if stop_event.is_set(): break
                        run_cycle(b_num, args.profile)
                        clean_all_pins()
                        time.sleep(0.5)
                else:
                    print("\n[mode] ONE-SHOT SINGLE: Testing default Socket 1 once")
                    run_cycle(1, args.profile)
            clean_all_pins()
        # --- NEW HARDCODED AUTO BLOCK ---
        elif not USE_BUTTONS:
            clean_all_pins()
            target_board = args.board if args.board else 1
            print("\n[mode] AUTO: Start button disabled in config. Running continuously.")
            while not stop_event.is_set():
                is_multi_mode = pin_get(PIN_MODE_SWITCH)
                if is_multi_mode:
                    for b_num in [1, 2, 3]:
                        if stop_event.is_set(): break
                        run_cycle(b_num, args.profile)
                        clean_all_pins()
                        time.sleep(1.0) # Brief pause before testing the next board
                else:
                    run_cycle(target_board, args.profile)
                    clean_all_pins()
                    time.sleep(2.0) # Safe window to swap the board before it restarts
        # --------------------------------
        
        else:
            clean_all_pins()
            target_board = args.board if args.board else 1
            watch_buttons(args.profile, target_board=target_board)
    except KeyboardInterrupt:
        print("\n[quit] stopping")
        stop_event.set()
        time.sleep(0.5)
    finally:
        # Do not clean up pins if we are in manual diagnostic mode
        if not (args.current or args.voltage):
            clean_all_pins()
        print("[quit] done")
    return 0

if __name__ == "__main__":
    sys.exit(main())