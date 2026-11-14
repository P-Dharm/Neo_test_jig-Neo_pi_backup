#!/usr/bin/env python3
"""
================================================================================
  NEO AUTOMATED TEST JIG - REMOTE CONTROL & MONITOR GUI
================================================================================
A Python/Tkinter GUI for remotely monitoring and controlling the 3-socket
automated test jig running on a Raspberry Pi over SSH.

Key Improvements in this version:
  1. Decoupled Log Streaming (No SSH Execution Bottlenecks):
     - The remote test script (all_test_mux_remove.py) runs detached on the Pi,
       redirecting stdout/stderr directly to a log file (default: jig.log).
     - Execution runs at full hardware speed with zero latency, zero PTY backpressure,
       and zero flow-control stalling from the SSH channel.
     - The GUI reads the log file via `tail -n 200 -F <log_path>` without interfering
       with or controlling the Python process flow.
     - If the GUI disconnects or network jitters, tests proceed on the Pi without
       any interruption. When the GUI reconnects, it immediately restores state.
     - Dedicated "Follow Log" button allows attaching to an already-running script.
     - "Open Log..." allows inspecting / replaying local log files offline.

  2. Reliable GREEN / RED Task Marking:
     - Full support for continuous FCFS mode pipeline:
       * "FCFS STAGE: TESTING BOARD X"
       * "[fcfs] -> Dispatching Board X"
       * "[detect-all] Socket X: detected new module MAC"
       * "[flash-bg] -> Starting test/client firmware flash for Board X"
       * "[flash-bg] <- Board X test/client firmware FLASHED successfully"
     - Independent concurrent tracking: background client flashing on Board X
       does not corrupt or collide with functional testing on Board Y.
     - Exact threshold validation for Active Current, Sleep Current, RSSI,
       and White/Amber LED delta before assigning GREEN (PASS) or RED (FAIL).
     - Full mapping of failure verdicts directly to the offending step box.
     - Automatic reset of socket view when a board is removed or replaced.
     - Large final status card flashes vibrant green on PASS and client flash verified.
================================================================================
"""

import os
import sys
import time
import json
import re
import queue
import sqlite3
import tempfile
import threading
import subprocess
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Optional Paramiko SSH backend with safe exception handling
try:
    import paramiko
    PARAMIKO_OK = True
except (ImportError, Exception):
    paramiko = None
    PARAMIKO_OK = False

# ══════════════════════════════════════════════════════════════════════════════
#  THEME & COLOR CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════
BG_DARK = "#12161f"         # App main background
PANEL_BG = "#1a212d"        # Top bar & containers background
CARD_BG = "#222a38"         # Socket column background
BOX_BG = "#273244"          # Test item default background
TEXT_MAIN = "#f8fafc"       # White text
TEXT_MUTED = "#94a3b8"      # Light grey text

# Status colors - Distinct and unmistakable for workshop visibility
COLOR_PASS_BG = "#10b981"   # Vibrant Emerald Green
COLOR_PASS_FG = "#ffffff"   # Pure white text on green
COLOR_PASS_BADGE = "#064e3b"# Dark contrast badge inside green box

COLOR_RUN_BG = "#b45309"    # Warm Amber
COLOR_RUN_FG = "#ffffff"
COLOR_RUN_BADGE = "#78350f"

COLOR_FAIL_BG = "#dc2626"   # Ruby Red
COLOR_FAIL_FG = "#ffffff"
COLOR_FAIL_BADGE = "#7f1d1d"

GREEN_FLASH_1 = "#00e676"   # Flashing bright green
GREEN_FLASH_2 = "#047857"   # Flashing deep green
ACCENT_BLUE = "#3b82f6"

# Calibrated Limits from all_test_mux_remove.py
RSSI_LIMITS = (-90.0, -20.0)             # dBm
ACTIVE_LIMITS = (3.50, 6.50)             # mA
LED_DELTA_LIMITS = (0.20, 2.20)          # mA (White LED)
AMBER_LED_DELTA_LIMITS = (2.00, 3.50)    # mA (Amber LED)
SLEEP_LIMITS = (0.0070, 2.5000)          # mA

# CMSIS DAP IDs for each socket
CMSIS_DAP_IDS = {
    1: "2fe3:01ff",
    2: "2fe3:0200",
    3: "2fe3:0204"
}

REMOTE_DIR = "/home/isc/Neo_test_jig-Neo_pi_backup"
REMOTE_SCRIPT = "/home/isc/Neo_test_jig-Neo_pi_backup/all_test_mux_remove.py"
REMOTE_LOG = "/home/isc/Neo_test_jig-Neo_pi_backup/jig.log"
REMOTE_DB_NAMES = ["jig_results.db", "jig_result.db"]
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".neo_jig_gui_config.json")

# Regex to strip ANSI escape codes emitted by terminals
ANSI_ESCAPE_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text):
    """Removes terminal escape codes, cursor escapes, and trailing returns."""
    if not text:
        return ""
    clean = ANSI_ESCAPE_RE.sub('', text)
    return clean.replace('\r', '').strip()


# ══════════════════════════════════════════════════════════════════════════════
#  DECOUPLED SSH & LOG CONTROLLER
# ══════════════════════════════════════════════════════════════════════════════
class RemoteSSHController:
    """Manages SSH connectivity, detached script execution, and decoupled log streaming."""

    def __init__(self, log_callback=None):
        self.log_callback = log_callback or (lambda msg: None)
        self.host = "192.168.1.100"
        self.port = 22
        self.username = "isc"
        self.password = ""
        self.key_path = ""
        self.log_path = REMOTE_LOG
        self.is_connected = False
        self.sim_mode = False
        self.active_process = None
        self.active_channel = None
        self._paramiko_client = None
        self._stop_requested = False

    def log(self, text):
        self.log_callback(text)

    def load_config(self):
        """Loads saved connection and log settings from disk."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.host = data.get("host", self.host)
                    self.port = int(data.get("port", self.port))
                    self.username = data.get("username", self.username)
                    self.key_path = data.get("key_path", self.key_path)
                    self.log_path = data.get("log_path", self.log_path)
            except Exception:
                pass

    def save_config(self):
        """Persists connection and log configuration."""
        try:
            data = {
                "host": self.host,
                "port": self.port,
                "username": self.username,
                "key_path": self.key_path,
                "log_path": self.log_path
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def connect(self, host, port, username, password="", key_path="", log_path=""):
        """Establishes SSH connectivity to the Raspberry Pi."""
        if self.sim_mode:
            self.is_connected = True
            self.log("[SSH] Connected in SIMULATION / DEMO MODE.")
            return True, "Connected in Simulation Mode"

        self.host = host.strip()
        self.port = int(port)
        self.username = username.strip()
        self.password = password
        self.key_path = key_path.strip()
        if log_path.strip():
            self.log_path = log_path.strip()
        self.save_config()

        if PARAMIKO_OK:
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                key_file = self.key_path if self.key_path and os.path.isfile(self.key_path) else None
                client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password if self.password else None,
                    key_filename=key_file,
                    timeout=8.0,
                    banner_timeout=15.0
                )
                self._paramiko_client = client
                self.is_connected = True
                self.log(f"[SSH] Connected via Paramiko to {self.username}@{self.host}:{self.port}")
                return True, "Connected via Paramiko"
            except Exception as e:
                self.log(f"[SSH] Paramiko connection failed: {e}. Trying OpenSSH fallback...")

        # Fallback using standard OpenSSH client via subprocess
        try:
            cmd = ["ssh", "-p", str(self.port), "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no"]
            if self.key_path and os.path.isfile(self.key_path):
                cmd.extend(["-i", self.key_path])
            cmd.extend([f"{self.username}@{self.host}", "uname -a"])
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
            if res.returncode == 0:
                self.is_connected = True
                self.log(f"[SSH] Connected via OpenSSH CLI: {res.stdout.strip()}")
                return True, "Connected via OpenSSH"
            else:
                err = res.stderr.strip() or res.stdout.strip() or "Connection timed out"
                self.log(f"[SSH] Connection failed: {err}")
                return False, err
        except Exception as e:
            self.log(f"[SSH] Error connecting: {e}")
            return False, str(e)

    def disconnect(self):
        """Disconnects SSH session and stops any active log streaming."""
        self.stop_log_stream()
        if self._paramiko_client:
            try:
                self._paramiko_client.close()
            except Exception:
                pass
            self._paramiko_client = None
        self.is_connected = False
        self.log("[SSH] Disconnected from Raspberry Pi.")

    def run_command_simple(self, cmd_str, timeout=15):
        """Executes a single command remotely and returns (stdout, stderr, exit_code)."""
        if self.sim_mode:
            if "lsusb" in cmd_str:
                mock_lsusb = (
                    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
                    "Bus 001 Device 004: ID 2fe3:01ff CMSIS-DAP v1.0 (Socket 1 Target)\n"
                    "Bus 001 Device 005: ID 2fe3:0200 CMSIS-DAP v1.0 (Socket 2 Target)\n"
                    "Bus 001 Device 006: ID 2fe3:0204 CMSIS-DAP v1.0 (Socket 3 Target)\n"
                )
                return mock_lsusb, "", 0
            if "pgrep" in cmd_str:
                return "1234\n", "", 0
            return "Simulated OK", "", 0

        if self._paramiko_client:
            try:
                stdin, stdout, stderr = self._paramiko_client.exec_command(cmd_str, timeout=timeout)
                out = stdout.read().decode("utf-8", errors="replace")
                err = stderr.read().decode("utf-8", errors="replace")
                code = stdout.channel.recv_exit_status()
                return out, err, code
            except Exception as e:
                return "", str(e), 1

        # CLI fallback
        try:
            ssh_cmd = ["ssh", "-p", str(self.port), "-o", "StrictHostKeyChecking=no"]
            if self.key_path and os.path.isfile(self.key_path):
                ssh_cmd.extend(["-i", self.key_path])
            ssh_cmd.extend([f"{self.username}@{self.host}", cmd_str])
            res = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
            return res.stdout, res.stderr, res.returncode
        except Exception as e:
            return "", str(e), 1

    def is_remote_script_running(self):
        """Checks if all_test_mux_remove.py is currently active on the Raspberry Pi."""
        if self.sim_mode:
            return False
        out, _, _ = self.run_command_simple("pgrep -f all_test_mux_remove.py", timeout=5)
        return bool(out.strip())

    def start_remote_script_detached(self, log_path=None):
        """Launches all_test_mux_remove.py detached in the background on the Pi,

        redirecting stdout and stderr directly to log_path.
        This completely eliminates SSH pipe flow-control latency and backpressure.
        """
        if self.sim_mode:
            return True, "Simulation mode active"

        if not self.is_connected:
            return False, "SSH is not connected"

        log_file = log_path or self.log_path

        # If already running, notify and attach to log directly
        if self.is_remote_script_running():
            self.log(f"[START] all_test_mux_remove.py is ALREADY running on Raspberry Pi. Attaching to log: {log_file}")
            return True, "Already running"

        # Ensure log file exists and start detached with nohup
        launch_cmd = (
            f"sudo touch {log_file} && "
            f"sudo chmod 666 {log_file} && "
            f"nohup sudo python3 -u {REMOTE_SCRIPT} --profile fast > {log_file} 2>&1 &"
        )
        self.log(f"[START] Launching detached on Pi: {launch_cmd}")
        self.run_command_simple(launch_cmd, timeout=8)
        time.sleep(0.5)

        if self.is_remote_script_running():
            self.log("[START] Test script successfully running in background on Pi.")
            return True, "Started successfully"
        else:
            self.log("[START] Verification check: process did not appear in pgrep. Log might still be readable.")
            return True, "Launched"

    def stop_remote_script(self):
        """Sends SIGINT for clean hardware pin release, then stops log reading."""
        self.stop_log_stream()

        if self.sim_mode:
            self._stop_requested = True
            return True

        if self.is_connected:
            self.log("[STOP] Sending SIGINT to all_test_mux_remove.py on Pi...")
            self.run_command_simple("sudo pkill -SIGINT -f all_test_mux_remove.py", timeout=5)
            time.sleep(1.0)
            if self.is_remote_script_running():
                self.log("[STOP] Process still active, sending SIGKILL...")
                self.run_command_simple("sudo pkill -9 -f all_test_mux_remove.py", timeout=5)
            self.log("[STOP] Remote process terminated.")
            return True
        return False

    def start_log_stream(self, line_handler, finished_handler, log_path=None):
        """Tails the log file (tail -n 200 -F <log_file>) over SSH.

        Does NOT control or throttle process execution.
        """
        self._stop_requested = False
        log_file = log_path or self.log_path

        if self.sim_mode:
            t = threading.Thread(target=self._run_simulation, args=(line_handler, finished_handler), daemon=True)
            t.start()
            return True

        if not self.is_connected:
            self.log("[ERROR] Cannot read log: SSH is not connected.")
            finished_handler("SSH Disconnected")
            return False

        t = threading.Thread(target=self._tail_remote_log, args=(log_file, line_handler, finished_handler), daemon=True)
        t.start()
        return True

    def _tail_remote_log(self, log_file, line_handler, finished_handler):
        """Worker thread following the log file via `tail -n 200 -F`."""
        tail_cmd = f"sudo touch {log_file} && sudo chmod 666 {log_file} && tail -n 200 -F {log_file}"
        self.log(f"[LOG] Streaming remote log via: {tail_cmd}")

        if self._paramiko_client:
            try:
                transport = self._paramiko_client.get_transport()
                channel = transport.open_session()
                # We do NOT request a PTY here: prevents PTY backpressure and escape-code noise
                channel.exec_command(tail_cmd)
                self.active_channel = channel

                buffer = ""
                while not self._stop_requested:
                    if channel.recv_ready():
                        chunk = channel.recv(8192).decode("utf-8", errors="replace")
                        if not chunk:
                            break
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            clean = strip_ansi(line)
                            if clean:
                                line_handler(clean)
                    elif channel.exit_status_ready():
                        break
                    time.sleep(0.015)

                if buffer:
                    clean = strip_ansi(buffer)
                    if clean:
                        line_handler(clean)
                self.active_channel = None
                finished_handler("Log streaming ended")
                return
            except Exception as e:
                self.log(f"[ERROR] Log stream exception: {e}")
                finished_handler(str(e))
                return

        # OpenSSH Subprocess CLI fallback
        try:
            ssh_cmd = ["ssh", "-p", str(self.port), "-o", "StrictHostKeyChecking=no"]
            if self.key_path and os.path.isfile(self.key_path):
                ssh_cmd.extend(["-i", self.key_path])
            ssh_cmd.extend([f"{self.username}@{self.host}", tail_cmd])

            proc = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.active_process = proc

            for raw_line in proc.stdout:
                if self._stop_requested:
                    break
                clean = strip_ansi(raw_line)
                if clean:
                    line_handler(clean)

            try:
                proc.terminate()
            except Exception:
                pass
            self.active_process = None
            finished_handler("Log streaming ended")
        except Exception as e:
            self.log(f"[ERROR] Subprocess log stream error: {e}")
            finished_handler(str(e))

    def stop_log_stream(self):
        """Stops reading the remote log."""
        self._stop_requested = True
        if self.active_channel:
            try:
                self.active_channel.close()
            except Exception:
                pass
            self.active_channel = None

        if self.active_process:
            try:
                self.active_process.terminate()
            except Exception:
                pass
            self.active_process = None

    def read_local_log_file(self, file_path, line_handler, finished_handler):
        """Reads a local log file line-by-line for offline inspection / verification."""
        self._stop_requested = False
        def _worker():
            try:
                self.log(f"[FILE] Replaying local log file: {file_path}")
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if self._stop_requested:
                            break
                        clean = strip_ansi(line)
                        if clean:
                            line_handler(clean)
                        time.sleep(0.01)
                finished_handler("Completed local log replay")
            except Exception as e:
                self.log(f"[ERROR] Local log read error: {e}")
                finished_handler(str(e))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def fetch_database(self, local_destination):
        """Fetches jig_results.db / jig_result.db from Raspberry Pi to local PC."""
        if self.sim_mode:
            conn = sqlite3.connect(local_destination)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS test_results (
                    sr INTEGER PRIMARY KEY AUTOINCREMENT,
                    jig_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    mac TEXT NOT NULL,
                    ficr_mac TEXT,
                    rssi_dbm REAL,
                    ble TEXT,
                    imu_acctest TEXT,
                    mic_result TEXT,
                    led_delta_ma REAL,
                    amber_led_delta_ma REAL,
                    active_stream_ma REAL,
                    V_charging REAL,
                    sleep_current_ma REAL,
                    client_firmware TEXT,
                    result TEXT NOT NULL,
                    comments TEXT
                )
            """)
            c.execute("SELECT count(*) FROM test_results")
            if c.fetchone()[0] == 0:
                samples = [
                    ('JIG_01', '2026-11-14 16:10:02', 'E2:8F:A1:E0:43:18', 'E2:8F:A1:E0:43:18', -52.4, 'PASS', 'PASS', 'PASS', 0.85, 2.62, 4.31, 4.15, 0.042, 'PASS', 'PASS', ''),
                    ('JIG_01', '2026-11-14 16:15:30', 'F2:07:CB:79:96:B6', 'F2:07:CB:79:96:B6', -49.1, 'PASS', 'PASS', 'PASS', 0.92, 2.70, 4.45, 4.14, 0.046, 'PASS', 'PASS', ''),
                    ('JIG_01', '2026-11-14 16:21:18', 'DD:30:B3:19:89:B8', 'DD:30:B3:19:89:B8', -95.0, 'FAIL', 'PASS', 'PASS', 0.80, 2.55, 4.30, 4.12, 0.044, 'FAIL', 'FAIL', 'RSSI -95.0 dBm outside (-90.0, -20.0)'),
                    ('JIG_01', '2026-11-14 16:26:45', 'C3:7A:88:99:AA:04', 'C3:7A:88:99:AA:04', -54.0, 'PASS', 'PASS', 'PASS', 0.78, 2.65, 4.29, 4.15, 0.041, 'PASS', 'PASS', '')
                ]
                c.executemany("""
                    INSERT INTO test_results (jig_id, timestamp, mac, ficr_mac, rssi_dbm, ble, imu_acctest, mic_result, led_delta_ma, amber_led_delta_ma, active_stream_ma, V_charging, sleep_current_ma, client_firmware, result, comments)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, samples)
                conn.commit()
            conn.close()
            return True, "Loaded local simulation database."

        if not self.is_connected:
            return False, "SSH is not connected."

        found_remote_db = None
        for name in REMOTE_DB_NAMES:
            path = f"{REMOTE_DIR}/{name}"
            out, err, code = self.run_command_simple(f"test -f {path} && echo EXISTS")
            if "EXISTS" in out:
                found_remote_db = path
                break

        if not found_remote_db:
            found_remote_db = f"{REMOTE_DIR}/jig_results.db"

        self.log(f"[DB] Fetching remote DB: {found_remote_db} -> {local_destination}")

        if self._paramiko_client:
            try:
                sftp = self._paramiko_client.open_sftp()
                sftp.get(found_remote_db, local_destination)
                sftp.close()
                return True, f"Successfully fetched from {found_remote_db}"
            except Exception as e:
                self.log(f"[DB] SFTP fetch failed: {e}. Trying scp...")

        try:
            scp_cmd = ["scp", "-P", str(self.port), "-o", "StrictHostKeyChecking=no"]
            if self.key_path and os.path.isfile(self.key_path):
                scp_cmd.extend(["-i", self.key_path])
            scp_cmd.extend([f"{self.username}@{self.host}:{found_remote_db}", local_destination])
            res = subprocess.run(scp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
            if res.returncode == 0:
                return True, f"Successfully fetched via SCP from {found_remote_db}"
            else:
                return False, res.stderr.strip() or "SCP failed"
        except Exception as e:
            return False, str(e)

    def _run_simulation(self, line_handler, finished_handler):
        """Simulates realistic FCFS continuous multi-board log output with both PASS and FAIL."""
        self.log("[SIM] Starting realistic FCFS multi-board simulation run...")
        line_handler("==============================================================")
        line_handler("  ISC NEO 3-in-1 JIG (Simulation Run - FCFS Continuous)")
        line_handler("  Profile       : CAREFUL")
        line_handler("  Active Stream Limits: 3.50 - 6.50 mA")
        line_handler("  Sleep Current Limits: 0.0070 - 2.5000 mA")
        line_handler("==============================================================")
        time.sleep(0.3)

        # Board 1: Full PASS cycle
        # Board 2: Full PASS cycle
        # Board 3: RSSI Out-of-bounds FAIL cycle
        scenarios = [
            (1, "E2:8F:A1:E0:43:18", True, ""),
            (2, "F2:07:CB:79:96:B6", True, ""),
            (3, "DD:30:B3:19:89:B8", False, "RSSI")
        ]

        for b_num, mac, pass_expected, fail_step in scenarios:
            if self._stop_requested: break

            # Step 1: Detect
            line_handler(f"[detect-all] Socket {b_num}: detected new module {mac} (FICR 03:{mac[3:]})")
            time.sleep(0.4)
            if self._stop_requested: break

            # Step 2: Background Test Flash
            line_handler(f"[detect-all] Socket {b_num} ({mac}) needs test firmware, launching background flash...")
            line_handler(f"[flash-bg] -> Starting test firmware flash for Board {b_num}...")
            time.sleep(0.6)
            line_handler(f"[flash-bg] <- Board {b_num} test firmware FLASHED successfully! Rebooting board...")
            time.sleep(0.4)
            if self._stop_requested: break

            # Queue & Stage Dispatch
            line_handler(f"[fcfs] -> Dispatching Board {b_num}")
            line_handler("\n" + "=" * 62)
            line_handler(f"  FCFS STAGE: TESTING BOARD {b_num}")
            line_handler("=" * 62)
            time.sleep(0.4)
            if self._stop_requested: break

            # Step 3: RSSI
            line_handler("[rssi] scanning on hci0 (Bumble)...")
            time.sleep(0.4)
            if not pass_expected and fail_step == "RSSI":
                line_handler("[rssi] -95.0 dBm")
                line_handler("[result] FAIL  RSSI -95.0 dBm outside (-90.0, -20.0)")
                line_handler(f"[db] saved result SR {b_num:02d} (local SQLite only, Supabase push disabled)")
                line_handler("[ble] disconnected")
                time.sleep(0.8)
                continue

            rssi_val = -52.0 - (b_num * 2.5)
            line_handler(f"[rssi] {rssi_val:.1f} dBm")
            time.sleep(0.3)
            if self._stop_requested: break

            # Step 4: BLE Connection
            line_handler(f"[ble] connected to {mac}")
            time.sleep(0.4)
            if self._stop_requested: break

            # Step 5: IMU
            line_handler("  [test] accelerometer self-test...")
            time.sleep(0.4)
            line_handler("        ACCTEST PASS (IMU axes verified within tolerance)")
            time.sleep(0.3)
            if self._stop_requested: break

            # Step 6: Mic QC
            line_handler("  [test] microphone QC...")
            line_handler("        capturing stimulus via speaker...")
            line_handler("        capturing silence...")
            time.sleep(0.5)
            line_handler("        Mic THD: 1.15%, SNR: 68.4 dB")
            time.sleep(0.3)
            if self._stop_requested: break

            # Step 7: Active Stream
            line_handler("  [test] active mode + streaming current...")
            act_ma = 4.25 + (b_num * 0.12)
            time.sleep(0.4)
            line_handler(f"        Stream {act_ma:.4f} mA (Limits: 3.50 - 6.50 mA)")
            line_handler("        V_charging: 4.15V")
            time.sleep(0.3)
            if self._stop_requested: break

            # Step 8: White LED Delta
            line_handler("  [test] white LED electrical delta...")
            time.sleep(0.4)
            line_handler("        Delta 0.8500 mA")
            time.sleep(0.3)
            if self._stop_requested: break

            # Step 9: Amber LED Delta
            line_handler("  [test] amber LED electrical delta...")
            time.sleep(0.4)
            line_handler("        Amber Delta 2.6200 mA")
            time.sleep(0.3)
            if self._stop_requested: break

            # Step 10: Sleep Current
            line_handler("  [test] sleep mode current...")
            time.sleep(0.5)
            line_handler("        Sleep 0.0450 mA (Limits: 0.0070 - 2.5000 mA)")
            time.sleep(0.3)
            if self._stop_requested: break

            # Step 11: Client FW Flash in Background
            line_handler(f"[board {b_num}] All tests passed! Starting client firmware flash in background...")
            line_handler(f"[flash-bg] -> Starting CLIENT firmware flash for Board {b_num}...")
            time.sleep(0.6)
            line_handler(f"[flash-bg] <- Board {b_num} CLIENT firmware FLASHED successfully! [PASS]")
            line_handler("[result] PASS")
            line_handler(f"[db] saved result SR {b_num:02d} (local SQLite only, Supabase push disabled)")
            line_handler("[ble] disconnected")
            time.sleep(0.8)

        # Step 4: Socket 1 Re-test with an already passed MAC (demonstrates instant Green & blinking PASS)
        if not self._stop_requested:
            time.sleep(0.5)
            line_handler("[detect-all] Socket 1 now empty")
            time.sleep(0.4)
            line_handler("[detect-all] Socket 1: detected new module C3:7A:88:99:AA:04 (FICR 03:C3:7A:88:99:AA:04)")
            time.sleep(0.3)
            line_handler("[skip] Socket 1: MAC C3:7A:88:99:AA:04 is already passed in database, skipping test.")
            time.sleep(0.6)

        finished_handler("Simulation cycle complete.")


# ══════════════════════════════════════════════════════════════════════════════
#  SOCKET UI COLUMN COMPONENT
# ══════════════════════════════════════════════════════════════════════════════
class SocketColumnView:
    """Represents one of the 3 socket test stations with test step boxes and a large pulsing status card."""

    TEST_KEYS = [
        ("ficr", "1. FICR Read / Detect"),
        ("test_fw", "2. Test Firmware Flash"),
        ("rssi", "3. RSSI (dBm)"),
        ("ble", "4. BLE Connection"),
        ("imu", "5. IMU Accelerometer"),
        ("mic", "6. Microphone QC"),
        ("active_curr", "7. Active Stream Current"),
        ("led_white", "8. White LED Delta"),
        ("led_amber", "9. Amber LED Delta"),
        ("sleep_curr", "10. Sleep Current"),
        ("client_fw", "11. Client Firmware Flash"),
    ]

    def __init__(self, parent, socket_num, target_vid_pid):
        self.parent = parent
        self.socket_num = socket_num
        self.target_vid_pid = target_vid_pid
        self.is_flashing = False
        self._flash_toggle = False
        self._flash_job = None
        self.last_mac = ""
        self.last_sr = ""

        self.step_widgets = {}   # key -> (frame, title_lbl, val_lbl)
        self._build_ui()

    def _build_ui(self):
        # Column Container
        self.frame = tk.Frame(self.parent, bg=CARD_BG, bd=1, relief="ridge", padx=8, pady=8)

        # Header Title
        hdr_frame = tk.Frame(self.frame, bg=CARD_BG)
        hdr_frame.pack(fill="x", pady=(0, 6))

        title_lbl = tk.Label(
            hdr_frame,
            text=f"SOCKET {self.socket_num}",
            font=("Segoe UI", 13, "bold"),
            bg=CARD_BG,
            fg=TEXT_MAIN
        )
        title_lbl.pack(side="left")

        dap_lbl = tk.Label(
            hdr_frame,
            text=f"DAP: {self.target_vid_pid}",
            font=("Consolas", 9),
            bg="#2e3848",
            fg="#cbd5e1",
            padx=6,
            pady=2
        )
        dap_lbl.pack(side="right")

        # Subsections container (compact rectangles)
        self.steps_container = tk.Frame(self.frame, bg=CARD_BG)
        self.steps_container.pack(fill="x", pady=(0, 6))

        for key, display_name in self.TEST_KEYS:
            row_frame = tk.Frame(
                self.steps_container,
                bg=BOX_BG,
                bd=1,
                relief="solid",
                padx=6,
                pady=4
            )
            row_frame.pack(fill="x", pady=2)

            t_lbl = tk.Label(
                row_frame,
                text=display_name,
                font=("Segoe UI", 9, "bold"),
                bg=BOX_BG,
                fg=TEXT_MAIN,
                anchor="w"
            )
            t_lbl.pack(side="left", fill="x", expand=True)

            v_lbl = tk.Label(
                row_frame,
                text="WAITING",
                font=("Consolas", 8, "bold"),
                bg="#1a222f",
                fg=TEXT_MUTED,
                padx=5,
                pady=1
            )
            v_lbl.pack(side="right")

            self.step_widgets[key] = (row_frame, t_lbl, v_lbl)

        # ── LARGE FINAL STATUS AREA (Bottom of Column) ───────────────────────
        self.final_status_box = tk.Frame(
            self.frame,
            bg="#1c2432",
            bd=2,
            relief="groove",
            padx=10,
            pady=12
        )
        self.final_status_box.pack(fill="both", expand=True, pady=(6, 0))

        self.final_badge_lbl = tk.Label(
            self.final_status_box,
            text="SOCKET IDLE",
            font=("Segoe UI", 16, "bold"),
            bg="#1c2432",
            fg=TEXT_MUTED
        )
        self.final_badge_lbl.pack(anchor="center", pady=(4, 2))

        self.final_detail_lbl = tk.Label(
            self.final_status_box,
            text="Waiting for DUT / test sequence...",
            font=("Consolas", 10),
            bg="#1c2432",
            fg="#94a3b8",
            justify="center",
            wraplength=220
        )
        self.final_detail_lbl.pack(anchor="center", fill="both", expand=True)

    def set_step_state(self, key, state, value_text=""):
        """Updates a test subsection rectangle with vibrant colors and status text."""
        if key not in self.step_widgets:
            return
        box_frame, t_lbl, v_lbl = self.step_widgets[key]

        if state == "PASS":
            bg = COLOR_PASS_BG
            t_fg = COLOR_PASS_FG
            v_bg = COLOR_PASS_BADGE
            v_fg = "#ffffff"
            text = value_text if value_text else "PASS"
            border = 2
        elif state == "RUNNING":
            bg = COLOR_RUN_BG
            t_fg = COLOR_RUN_FG
            v_bg = COLOR_RUN_BADGE
            v_fg = "#fef3c7"
            text = value_text if value_text else "TESTING..."
            border = 2
        elif state == "FAIL":
            bg = COLOR_FAIL_BG
            t_fg = COLOR_FAIL_FG
            v_bg = COLOR_FAIL_BADGE
            v_fg = "#fecaca"
            text = value_text if value_text else "FAIL"
            border = 2
        else: # IDLE / WAITING
            bg = BOX_BG
            t_fg = TEXT_MAIN
            v_bg = "#1a222f"
            v_fg = TEXT_MUTED
            text = value_text if value_text else "WAITING"
            border = 1

        box_frame.configure(bg=bg, bd=border)
        t_lbl.configure(bg=bg, fg=t_fg)
        v_lbl.configure(text=text, bg=v_bg, fg=v_fg)

    def mark_active(self):
        """Highlights socket as currently undergoing test cycle."""
        self.stop_flashing()
        self.final_status_box.configure(bg="#1e293b", bd=2, relief="solid")
        self.final_badge_lbl.configure(text="TEST IN PROGRESS", bg="#1e293b", fg="#f59e0b")
        mac_info = f"MAC: {self.last_mac}\n" if self.last_mac else ""
        self.final_detail_lbl.configure(text=f"{mac_info}Executing test sequence...", bg="#1e293b", fg=TEXT_MAIN)

    def mark_passed_flashing(self, mac="", sr=""):
        """Turns the large final status area Green and starts flashing animation."""
        self.set_step_state("client_fw", "PASS", "Verified")
        sr_val = sr or self.last_sr
        sr_str = f"SR #{sr_val}" if sr_val else "SAVED"
        mac_val = mac or self.last_mac
        mac_str = f"MAC: {mac_val}" if mac_val else ""

        self.final_badge_lbl.configure(text="✔ PASSED")
        self.final_detail_lbl.configure(
            text=f"✔ CLIENT FW FLASHED\n✔ {sr_str} TO DB\n{mac_str}"
        )
        self.start_flashing()

    def mark_already_passed(self, mac=""):
        """Immediately turns all test steps Green (PASS) and starts the Passed section blinking."""
        if mac:
            self.last_mac = mac
        mac_val = self.last_mac

        # Immediately turn ALL 11 test steps Green (PASS)
        for key, _ in self.TEST_KEYS:
            if key == "ficr" and mac_val:
                self.set_step_state(key, "PASS", mac_val)
            else:
                self.set_step_state(key, "PASS", "PASS")

        mac_str = f"MAC: {mac_val}\n" if mac_val else ""
        self.final_badge_lbl.configure(text="✔ PASSED")
        self.final_detail_lbl.configure(
            text=f"✔ ALREADY PASSED IN DB\n✔ ALL TESTS PASSED\n{mac_str.strip()}"
        )
        self.start_flashing()

    def mark_failed(self, reason=""):
        """Marks the socket cycle as Failed."""
        self.stop_flashing()
        mac_str = f"MAC: {self.last_mac}\n" if self.last_mac else ""
        self.final_status_box.configure(bg="#450a0a", bd=2, relief="solid")
        self.final_badge_lbl.configure(text="✖ TEST FAILED", bg="#450a0a", fg="#fca5a5")
        self.final_detail_lbl.configure(
            text=f"{mac_str}✖ {reason or 'Test checks failed'}",
            bg="#450a0a",
            fg="#fee2e2"
        )
        self.start_flashing() if False else None

    def reset_all(self):
        """Resets all subsections to idle/waiting."""
        self.stop_flashing()
        self.last_mac = ""
        self.last_sr = ""
        for key, _ in self.TEST_KEYS:
            self.set_step_state(key, "IDLE", "WAITING")
        self.final_status_box.configure(bg="#1c2432", bd=2, relief="groove")
        self.final_badge_lbl.configure(text="SOCKET IDLE", bg="#1c2432", fg=TEXT_MUTED)
        self.final_detail_lbl.configure(text="Waiting for DUT / test sequence...", bg="#1c2432", fg="#94a3b8")

    def start_flashing(self):
        """Starts alternating green flashing for high visibility across the room."""
        if self._flash_job:
            self.parent.after_cancel(self._flash_job)
            self._flash_job = None
        self.is_flashing = True
        self._flash_toggle = False
        self._pulse()

    def stop_flashing(self):
        """Halts flashing animation."""
        self.is_flashing = False
        if self._flash_job:
            self.parent.after_cancel(self._flash_job)
            self._flash_job = None

    def _pulse(self):
        if not self.is_flashing:
            return
        self._flash_toggle = not self._flash_toggle
        bg = GREEN_FLASH_1 if self._flash_toggle else GREEN_FLASH_2
        fg = "#000000" if self._flash_toggle else "#ffffff"

        self.final_status_box.configure(bg=bg, bd=3, relief="solid")
        self.final_badge_lbl.configure(bg=bg, fg=fg)
        self.final_detail_lbl.configure(bg=bg, fg=fg)

        # Pulse every 450 milliseconds
        self._flash_job = self.parent.after(450, self._pulse)


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE VIEWER WINDOW ("Fetch dB")
# ══════════════════════════════════════════════════════════════════════════════
class DatabaseViewerWindow(tk.Toplevel):
    """Dedicated viewer window for jig_results.db with latest entries on top and total PASS count."""

    def __init__(self, parent, db_path, on_refresh_callback=None):
        super().__init__(parent)
        self.title("NEO Test Jig - Database Viewer (jig_results.db)")
        self.geometry("1100x640")
        self.configure(bg=BG_DARK)
        self.db_path = db_path
        self.on_refresh_callback = on_refresh_callback

        self._build_ui()
        self.load_data()

    def _build_ui(self):
        stats_frame = tk.Frame(self, bg=PANEL_BG, bd=1, relief="ridge", padx=12, pady=10)
        stats_frame.pack(fill="x", padx=10, pady=(10, 6))

        info_frame = tk.Frame(stats_frame, bg=PANEL_BG)
        info_frame.pack(side="left")
        tk.Label(info_frame, text="DATABASE SNAPSHOT", font=("Segoe UI", 12, "bold"), bg=PANEL_BG, fg=TEXT_MAIN).pack(anchor="w")
        self.db_file_lbl = tk.Label(info_frame, text=f"File: {os.path.basename(self.db_path)}", font=("Consolas", 8), bg=PANEL_BG, fg=TEXT_MUTED)
        self.db_file_lbl.pack(anchor="w")

        cards_frame = tk.Frame(stats_frame, bg=PANEL_BG)
        cards_frame.pack(side="right")

        # Total PASS Count (Prominent green badge)
        pass_card = tk.Frame(cards_frame, bg="#064e3b", bd=2, relief="solid", padx=14, pady=4)
        pass_card.pack(side="right", padx=6)
        tk.Label(pass_card, text="TOTAL PASS", font=("Segoe UI", 9, "bold"), bg="#064e3b", fg="#6ee7b7").pack()
        self.pass_count_lbl = tk.Label(pass_card, text="0", font=("Segoe UI", 16, "bold"), bg="#064e3b", fg="#a7f3d0")
        self.pass_count_lbl.pack()

        # Total FAIL Count
        fail_card = tk.Frame(cards_frame, bg="#7f1d1d", bd=1, relief="solid", padx=12, pady=4)
        fail_card.pack(side="right", padx=6)
        tk.Label(fail_card, text="TOTAL FAIL", font=("Segoe UI", 9, "bold"), bg="#7f1d1d", fg="#fca5a5").pack()
        self.fail_count_lbl = tk.Label(fail_card, text="0", font=("Segoe UI", 16, "bold"), bg="#7f1d1d", fg="#fecaca")
        self.fail_count_lbl.pack()

        # Total Records
        total_card = tk.Frame(cards_frame, bg="#1e293b", bd=1, relief="solid", padx=12, pady=4)
        total_card.pack(side="right", padx=6)
        tk.Label(total_card, text="TOTAL TESTS", font=("Segoe UI", 9, "bold"), bg="#1e293b", fg="#94a3b8").pack()
        self.total_count_lbl = tk.Label(total_card, text="0", font=("Segoe UI", 16, "bold"), bg="#1e293b", fg=TEXT_MAIN)
        self.total_count_lbl.pack()

        # Filter & Action Toolbar
        toolbar = tk.Frame(self, bg=BG_DARK, padx=10, pady=4)
        toolbar.pack(fill="x")

        tk.Label(toolbar, text="Filter:", bg=BG_DARK, fg=TEXT_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 4))
        self.filter_var = tk.StringVar(value="ALL")
        for f in ["ALL", "PASS", "FAIL"]:
            rb = tk.Radiobutton(
                toolbar, text=f, variable=self.filter_var, value=f,
                bg=BG_DARK, fg=TEXT_MAIN, selectcolor="#334155",
                activebackground=BG_DARK, activeforeground=TEXT_MAIN,
                command=self.apply_filter
            )
            rb.pack(side="left", padx=4)

        tk.Label(toolbar, text="Search MAC/Text:", bg=BG_DARK, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(14, 4))
        self.search_entry = tk.Entry(toolbar, bg="#1e293b", fg=TEXT_MAIN, insertbackground=TEXT_MAIN, width=22)
        self.search_entry.pack(side="left", padx=4)
        self.search_entry.bind("<KeyRelease>", lambda e: self.apply_filter())

        if self.on_refresh_callback:
            refresh_btn = tk.Button(
                toolbar, text="🔄 Fetch Latest from Pi",
                command=self.on_refresh_callback,
                bg="#2563eb", fg="#ffffff", font=("Segoe UI", 9, "bold"),
                relief="flat", padx=8, pady=2
            )
            refresh_btn.pack(side="right", padx=4)

        export_btn = tk.Button(
            toolbar, text="💾 Export CSV",
            command=self.export_csv,
            bg="#334155", fg=TEXT_MAIN, font=("Segoe UI", 9),
            relief="flat", padx=8, pady=2
        )
        export_btn.pack(side="right", padx=4)

        table_frame = tk.Frame(self, bg=BG_DARK)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(2, 10))

        columns = ("sr", "timestamp", "result", "mac", "rssi", "ble", "imu", "mic", "active_ma", "sleep_ma", "client_fw", "comments")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        headers = {
            "sr": ("SR", 45),
            "timestamp": ("Timestamp", 140),
            "result": ("Result", 65),
            "mac": ("MAC Address", 140),
            "rssi": ("RSSI (dBm)", 80),
            "ble": ("BLE", 55),
            "imu": ("IMU", 65),
            "mic": ("Mic QC", 75),
            "active_ma": ("Active (mA)", 85),
            "sleep_ma": ("Sleep (mA)", 85),
            "client_fw": ("Client FW", 75),
            "comments": ("Comments / Diagnostics", 200),
        }

        for col, (text, width) in headers.items():
            self.tree.heading(col, text=text, anchor="center")
            self.tree.column(col, width=width, anchor="center")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.tree.tag_configure("pass_row", background="#064e3b", foreground="#ecfdf5")
        self.tree.tag_configure("fail_row", background="#450a0a", foreground="#fef2f2")

        self.all_rows = []

    def load_data(self):
        """Populates rows with the latest record on top."""
        if not os.path.isfile(self.db_path):
            messagebox.showerror("Error", f"Database file not found: {self.db_path}", parent=self)
            return

        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('test_results', 'results')")
            table = c.fetchone()
            tbl_name = table[0] if table else "test_results"

            c.execute(f"SELECT count(*) FROM {tbl_name}")
            total = c.fetchone()[0]

            c.execute(f"SELECT count(*) FROM {tbl_name} WHERE UPPER(result) = 'PASS'")
            passes = c.fetchone()[0]

            fails = total - passes

            self.total_count_lbl.configure(text=str(total))
            self.pass_count_lbl.configure(text=str(passes))
            self.fail_count_lbl.configure(text=str(fails))

            query = f"SELECT * FROM {tbl_name} ORDER BY rowid DESC"
            c.execute(query)
            col_names = [d[0] for d in c.description]
            raw_rows = c.fetchall()
            conn.close()

            self.all_rows = []
            for r in raw_rows:
                row_dict = dict(zip(col_names, r))
                self.all_rows.append(row_dict)

            self.apply_filter()
        except Exception as e:
            messagebox.showerror("Database Read Error", str(e), parent=self)

    def apply_filter(self):
        """Applies PASS/FAIL and text search filters."""
        self.tree.delete(*self.tree.get_children())
        filter_mode = self.filter_var.get()
        search_txt = self.search_entry.get().strip().lower()

        for r in self.all_rows:
            result = str(r.get("result", "")).upper()
            if filter_mode != "ALL" and result != filter_mode:
                continue

            combined_text = " ".join(str(v) for v in r.values()).lower()
            if search_txt and search_txt not in combined_text:
                continue

            tag = "pass_row" if result == "PASS" else "fail_row"
            values = (
                r.get("sr", ""),
                r.get("timestamp", ""),
                result,
                r.get("mac", ""),
                r.get("rssi_dbm", ""),
                r.get("ble", ""),
                r.get("imu_acctest", ""),
                r.get("mic_result", ""),
                r.get("active_stream_ma", ""),
                r.get("sleep_current_ma", ""),
                r.get("client_firmware", ""),
                r.get("comments", "")
            )
            self.tree.insert("", "end", values=values, tags=(tag,))

    def export_csv(self):
        """Exports records to CSV format."""
        if not self.all_rows:
            messagebox.showinfo("Export", "No records to export.", parent=self)
            return
        dest = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save Database as CSV"
        )
        if not dest:
            return
        try:
            import csv
            with open(dest, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(self.all_rows[0].keys()))
                writer.writeheader()
                writer.writerows(self.all_rows)
            messagebox.showinfo("Export Successful", f"Saved {len(self.all_rows)} records to:\n{dest}", parent=self)
        except Exception as e:
            messagebox.showerror("Export Error", str(e), parent=self)


# ══════════════════════════════════════════════════════════════════════════════
#  CMSIS-DAP CHECK WINDOW ("Check CMSIS DAP")
# ══════════════════════════════════════════════════════════════════════════════
class CMSISDAPCheckWindow(tk.Toplevel):
    """Executes lsusb on the Raspberry Pi and validates the 3 CMSIS-DAP debuggers."""

    def __init__(self, parent, ssh_controller):
        super().__init__(parent)
        self.title("CMSIS-DAP USB Verification")
        self.geometry("700x520")
        self.configure(bg=BG_DARK)
        self.ssh_controller = ssh_controller

        self._build_ui()
        self.run_check()

    def _build_ui(self):
        self.hdr_frame = tk.Frame(self, bg=PANEL_BG, bd=1, relief="ridge", padx=12, pady=10)
        self.hdr_frame.pack(fill="x", padx=10, pady=(10, 6))

        self.status_title = tk.Label(
            self.hdr_frame,
            text="Checking USB devices on Raspberry Pi...",
            font=("Segoe UI", 12, "bold"),
            bg=PANEL_BG,
            fg=TEXT_MAIN
        )
        self.status_title.pack(anchor="w")

        cards_frame = tk.Frame(self, bg=BG_DARK, padx=10, pady=4)
        cards_frame.pack(fill="x")

        self.cards = {}
        for s_num in [1, 2, 3]:
            card = tk.Frame(cards_frame, bg=CARD_BG, bd=1, relief="solid", padx=10, pady=8)
            card.pack(side="left", fill="both", expand=True, padx=4)

            s_title = tk.Label(card, text=f"SOCKET {s_num}", font=("Segoe UI", 10, "bold"), bg=CARD_BG, fg=TEXT_MAIN)
            s_title.pack(anchor="w")

            id_lbl = tk.Label(card, text=f"ID: {CMSIS_DAP_IDS[s_num]}", font=("Consolas", 8), bg=CARD_BG, fg=TEXT_MUTED)
            id_lbl.pack(anchor="w", pady=(0, 4))

            state_badge = tk.Label(card, text="CHECKING...", font=("Segoe UI", 9, "bold"), bg="#334155", fg=TEXT_MAIN, padx=6, pady=2)
            state_badge.pack(fill="x")

            self.cards[s_num] = (card, state_badge)

        tk.Label(self, text="Raw `lsusb` Output:", font=("Segoe UI", 9, "bold"), bg=BG_DARK, fg=TEXT_MUTED).pack(anchor="w", padx=14, pady=(8, 2))

        txt_frame = tk.Frame(self, bg=BG_DARK)
        txt_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.raw_text = tk.Text(txt_frame, bg="#0d1117", fg="#c9d1d9", font=("Consolas", 9), relief="solid", bd=1)
        sb = ttk.Scrollbar(txt_frame, orient="vertical", command=self.raw_text.yview)
        self.raw_text.configure(yscrollcommand=sb.set)

        self.raw_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btn_frame = tk.Frame(self, bg=BG_DARK, padx=10, pady=6)
        btn_frame.pack(fill="x")

        recheck_btn = tk.Button(btn_frame, text="🔄 Re-Check", command=self.run_check, bg=ACCENT_BLUE, fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=4)
        recheck_btn.pack(side="right")

    def run_check(self):
        self.status_title.configure(text="Querying `lsusb` over SSH...", fg="#f59e0b")
        self.raw_text.delete("1.0", "end")

        def _worker():
            out, err, code = self.ssh_controller.run_command_simple("lsusb")
            self.after(0, lambda: self._process_result(out, err, code))

        threading.Thread(target=_worker, daemon=True).start()

    def _process_result(self, out, err, code):
        self.raw_text.insert("end", out if out else f"Error running lsusb:\n{err}")

        found_count = 0
        for s_num in [1, 2, 3]:
            card, state_badge = self.cards[s_num]
            target_id = CMSIS_DAP_IDS[s_num].lower()

            if target_id in out.lower():
                found_count += 1
                card.configure(bg="#064e3b")
                state_badge.configure(text="DETECTED (PASS)", bg="#10b981", fg="#ffffff")
            else:
                card.configure(bg="#450a0a")
                state_badge.configure(text="NOT FOUND", bg="#ef4444", fg="#ffffff")

        if found_count == 3:
            self.status_title.configure(
                text="✔ ALL 3 CMSIS-DAP DEBUGGERS DETECTED & READY",
                fg="#10b981"
            )
        else:
            self.status_title.configure(
                text=f"⚠ WARNING: ONLY {found_count} OF 3 CMSIS-DAP DEBUGGERS FOUND",
                fg="#ef4444"
            )


# ══════════════════════════════════════════════════════════════════════════════
#  TERMINAL LOGS VIEWER
# ══════════════════════════════════════════════════════════════════════════════
class TerminalLogViewer(tk.Toplevel):
    """Real-time console log viewer window."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("NEO Test Jig - Live Console Output")
        self.geometry("880x500")
        self.configure(bg="#0a0e14")
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.autoscroll = tk.BooleanVar(value=True)

        self._build_ui()

    def _build_ui(self):
        toolbar = tk.Frame(self, bg="#161b22", padx=8, pady=4)
        toolbar.pack(fill="x")

        tk.Label(
            toolbar, text=">_ LIVE LOG STREAM",
            font=("Consolas", 10, "bold"), bg="#161b22", fg="#58a6ff"
        ).pack(side="left")

        clear_btn = tk.Button(toolbar, text="Clear", command=self.clear, bg="#21262d", fg="#c9d1d9", font=("Segoe UI", 8), relief="flat")
        clear_btn.pack(side="right", padx=4)

        auto_cb = tk.Checkbutton(
            toolbar, text="Auto-Scroll", variable=self.autoscroll,
            bg="#161b22", fg="#8b949e", selectcolor="#21262d",
            activebackground="#161b22", activeforeground="#c9d1d9"
        )
        auto_cb.pack(side="right", padx=6)

        txt_frame = tk.Frame(self, bg="#0a0e14")
        txt_frame.pack(fill="both", expand=True)

        self.text = tk.Text(
            txt_frame, bg="#0a0e14", fg="#7ee787",
            font=("Consolas", 9), relief="flat", padx=8, pady=8
        )
        sb = ttk.Scrollbar(txt_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)

        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def append_line(self, line):
        self.text.insert("end", line + "\n")
        if self.autoscroll.get():
            self.text.see("end")

    def clear(self):
        self.text.delete("1.0", "end")

    def hide(self):
        self.withdraw()

    def show(self):
        self.deiconify()
        self.lift()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION GUI
# ══════════════════════════════════════════════════════════════════════════════
class NeoJigApp:
    """Main Application orchestrating Top Bar, Sockets, and Decoupled Log Streaming."""

    def __init__(self, root):
        self.root = root
        self.root.title("NEO Automated Test Jig - Remote Controller & Monitor")
        self.root.geometry("1220x890")
        self.root.minsize(1080, 760)
        self.root.configure(bg=BG_DARK)

        self.line_queue = queue.Queue()
        self.ssh = RemoteSSHController(log_callback=self._queue_raw_line)
        self.ssh.load_config()

        self.terminal_win = TerminalLogViewer(self.root)
        self.terminal_win.withdraw()

        self.active_socket_num = 1  # Default active functional test socket
        self.pending_pass_board = None
        self.pending_fail_board = None
        self.last_passed_board = None
        self.last_failed_board = None
        self.socket_views = {}
        self.local_temp_db = os.path.join(tempfile.gettempdir(), "fetched_jig_results.db")

        self.is_streaming = False
        self._build_ui()
        self._check_initial_status()

        # Thread-safe queue polling for smooth UI updates
        self.root.after(30, self._poll_line_queue)

    def _build_ui(self):
        # ══════════════════════════════════════════════════════════════════════
        #  TOP BAR (SSH Settings, Log Path, Controls, Fetch dB, Check CMSIS DAP)
        # ══════════════════════════════════════════════════════════════════════
        self.top_frame = tk.Frame(self.root, bg=PANEL_BG, bd=1, relief="ridge", padx=10, pady=8)
        self.top_frame.pack(fill="x", padx=10, pady=(10, 6))

        # Row 1: SSH Connection Parameters, Log Path & Status Pill
        ssh_row = tk.Frame(self.top_frame, bg=PANEL_BG)
        ssh_row.pack(fill="x", pady=(0, 6))

        tk.Label(ssh_row, text="SSH Host:", bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.host_entry = tk.Entry(ssh_row, width=13, bg="#2d3748", fg=TEXT_MAIN, insertbackground=TEXT_MAIN)
        self.host_entry.insert(0, self.ssh.host)
        self.host_entry.pack(side="left", padx=(3, 6))

        tk.Label(ssh_row, text="User:", bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.user_entry = tk.Entry(ssh_row, width=7, bg="#2d3748", fg=TEXT_MAIN, insertbackground=TEXT_MAIN)
        self.user_entry.insert(0, self.ssh.username)
        self.user_entry.pack(side="left", padx=(3, 6))

        tk.Label(ssh_row, text="Pass/Key:", bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.pass_entry = tk.Entry(ssh_row, width=10, show="*", bg="#2d3748", fg=TEXT_MAIN, insertbackground=TEXT_MAIN)
        self.pass_entry.pack(side="left", padx=(3, 6))

        tk.Label(ssh_row, text="Port:", bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.port_entry = tk.Entry(ssh_row, width=4, bg="#2d3748", fg=TEXT_MAIN, insertbackground=TEXT_MAIN)
        self.port_entry.insert(0, str(self.ssh.port))
        self.port_entry.pack(side="left", padx=(3, 8))

        tk.Label(ssh_row, text="Log File:", bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
        self.log_path_entry = tk.Entry(ssh_row, width=28, bg="#2d3748", fg=TEXT_MAIN, insertbackground=TEXT_MAIN)
        self.log_path_entry.insert(0, self.ssh.log_path)
        self.log_path_entry.pack(side="left", padx=(3, 8))

        # Connect / Disconnect Button
        self.connect_btn = tk.Button(
            ssh_row, text="Connect SSH",
            command=self.toggle_ssh_connect,
            bg=ACCENT_BLUE, fg="#ffffff", font=("Segoe UI", 9, "bold"),
            relief="flat", padx=10, pady=2
        )
        self.connect_btn.pack(side="left", padx=(4, 10))

        # Simulation Mode checkbox
        self.sim_var = tk.BooleanVar(value=False)
        sim_cb = tk.Checkbutton(
            ssh_row, text="Simulation Mode", variable=self.sim_var,
            command=self._on_sim_toggle,
            bg=PANEL_BG, fg="#38bdf8", selectcolor="#1e293b",
            activebackground=PANEL_BG, activeforeground="#38bdf8",
            font=("Segoe UI", 9)
        )
        sim_cb.pack(side="left", padx=4)

        # SSH Status Pill
        self.ssh_status_pill = tk.Label(
            ssh_row,
            text="● DISCONNECTED",
            font=("Segoe UI", 9, "bold"),
            bg="#7f1d1d",
            fg="#fee2e2",
            padx=10,
            pady=3,
            relief="solid",
            bd=1
        )
        self.ssh_status_pill.pack(side="right")

        sep = tk.Frame(self.top_frame, height=1, bg="#334155")
        sep.pack(fill="x", pady=6)

        # Row 2: Decoupled START / Follow Log / STOP Controls & Tools
        ctrl_row = tk.Frame(self.top_frame, bg=PANEL_BG)
        ctrl_row.pack(fill="x")

        self.start_btn = tk.Button(
            ctrl_row,
            text="▶  START (Detached)",
            command=self.start_test_detached_and_stream,
            bg="#16a34a",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=14,
            pady=3,
            cursor="hand2"
        )
        self.start_btn.pack(side="left", padx=(0, 6))

        self.follow_log_btn = tk.Button(
            ctrl_row,
            text="📖  Follow Log",
            command=self.attach_and_follow_log,
            bg="#0284c7",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=12,
            pady=3,
            cursor="hand2"
        )
        self.follow_log_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = tk.Button(
            ctrl_row,
            text="⏹  STOP",
            command=self.stop_test_script,
            bg="#dc2626",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=14,
            pady=3,
            cursor="hand2",
            state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(0, 14))

        self.fetch_db_btn = tk.Button(
            ctrl_row,
            text="🗄  Fetch dB",
            command=self.fetch_and_open_db,
            bg="#2563eb",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        self.fetch_db_btn.pack(side="left", padx=(0, 6))

        self.check_dap_btn = tk.Button(
            ctrl_row,
            text="🔌  Check CMSIS DAP",
            command=self.check_cmsis_dap,
            bg="#0d9488",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        self.check_dap_btn.pack(side="left", padx=(0, 6))

        self.open_log_btn = tk.Button(
            ctrl_row,
            text="📂  Open Log...",
            command=self.open_local_log_file,
            bg="#475569",
            fg="#ffffff",
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        self.open_log_btn.pack(side="left", padx=(0, 6))

        self.clear_ui_btn = tk.Button(
            ctrl_row,
            text="🗑  Clear UI",
            command=self.clear_ui_state,
            bg="#334155",
            fg="#cbd5e1",
            font=("Segoe UI", 9),
            relief="flat",
            padx=8,
            pady=3,
            cursor="hand2"
        )
        self.clear_ui_btn.pack(side="left", padx=(0, 10))

        self.status_notification = tk.Label(
            ctrl_row,
            text="Ready. Connect SSH or enable Simulation Mode to begin.",
            font=("Segoe UI", 9),
            bg=PANEL_BG,
            fg=TEXT_MUTED
        )
        self.status_notification.pack(side="left", padx=6)

        # ══════════════════════════════════════════════════════════════════════
        #  MAIN 3-COLUMN SEPARATION FOR SOCKET 1, SOCKET 2, SOCKET 3
        # ══════════════════════════════════════════════════════════════════════
        self.columns_container = tk.Frame(self.root, bg=BG_DARK)
        self.columns_container.pack(fill="both", expand=True, padx=10, pady=6)

        for s_num in [1, 2, 3]:
            col_view = SocketColumnView(
                self.columns_container,
                socket_num=s_num,
                target_vid_pid=CMSIS_DAP_IDS[s_num]
            )
            col_view.frame.pack(side="left", fill="both", expand=True, padx=4)
            self.socket_views[s_num] = col_view

        # ══════════════════════════════════════════════════════════════════════
        #  BOTTOM BAR (Info & Bottom-Right Terminal Symbol)
        # ══════════════════════════════════════════════════════════════════════
        self.bottom_bar = tk.Frame(self.root, bg=PANEL_BG, bd=1, relief="ridge", padx=10, pady=4)
        self.bottom_bar.pack(fill="x", padx=10, pady=(0, 8))

        self.bottom_info_lbl = tk.Label(
            self.bottom_bar,
            text=f"Remote Script: {REMOTE_SCRIPT}  |  Log: {self.ssh.log_path}  |  SSH: {'Paramiko' if PARAMIKO_OK else 'OpenSSH CLI'}",
            font=("Segoe UI", 8),
            bg=PANEL_BG,
            fg=TEXT_MUTED
        )
        self.bottom_info_lbl.pack(side="left")

        self.term_btn = tk.Button(
            self.bottom_bar,
            text=">_ Terminal Logs",
            command=self.toggle_terminal_logs,
            bg="#21262d",
            fg="#58a6ff",
            font=("Consolas", 10, "bold"),
            relief="solid",
            bd=1,
            padx=12,
            pady=2,
            cursor="hand2"
        )
        self.term_btn.pack(side="right")

    # ──────────────────────────────────────────────────────────────────────────
    #  SSH ACTIONS
    # ──────────────────────────────────────────────────────────────────────────
    def _check_initial_status(self):
        self._update_ssh_status_ui()

    def _on_sim_toggle(self):
        self.ssh.sim_mode = self.sim_var.get()
        if self.ssh.sim_mode:
            self.ssh.is_connected = True
            self.status_notification.configure(text="Simulation Mode Active (Offline)", fg="#38bdf8")
        else:
            self.ssh.is_connected = False
            self.status_notification.configure(text="Simulation Mode Off. Connect SSH to proceed.", fg=TEXT_MUTED)
        self._update_ssh_status_ui()

    def toggle_ssh_connect(self):
        if self.ssh.is_connected and not self.ssh.sim_mode:
            self.ssh.disconnect()
            self._update_ssh_status_ui()
            self.status_notification.configure(text="Disconnected from Raspberry Pi.", fg=TEXT_MUTED)
            return

        host = self.host_entry.get().strip()
        user = self.user_entry.get().strip()
        pwd = self.pass_entry.get()
        port = self.port_entry.get().strip()
        log_file = self.log_path_entry.get().strip()

        self.status_notification.configure(text="Connecting over SSH...", fg="#f59e0b")
        self.root.update()

        def _worker():
            ok, msg = self.ssh.connect(host=host, port=port, username=user, password=pwd, log_path=log_file)
            self.root.after(0, lambda: self._on_connected_result(ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_connected_result(self, ok, msg):
        self._update_ssh_status_ui()
        if ok:
            self.bottom_info_lbl.configure(text=f"Remote Script: {REMOTE_SCRIPT}  |  Log: {self.ssh.log_path}  |  SSH: {'Paramiko' if PARAMIKO_OK else 'OpenSSH CLI'}")
            self.status_notification.configure(text=f"Connected: {msg}", fg="#10b981")
            messagebox.showinfo("SSH Connected", f"Successfully connected to {self.ssh.host}!\nReady to follow log.", parent=self.root)
        else:
            self.status_notification.configure(text=f"Connection failed: {msg}", fg="#ef4444")
            messagebox.showerror("SSH Connection Failed", f"Could not connect to {self.ssh.host}:\n{msg}", parent=self.root)

    def _update_ssh_status_ui(self):
        if self.ssh.is_connected:
            self.ssh_status_pill.configure(
                text="● CONNECTED",
                bg="#064e3b",
                fg="#6ee7b7"
            )
            self.connect_btn.configure(text="Disconnect SSH", bg="#475569")
        else:
            self.ssh_status_pill.configure(
                text="● DISCONNECTED",
                bg="#7f1d1d",
                fg="#fee2e2"
            )
            self.connect_btn.configure(text="Connect SSH", bg=ACCENT_BLUE)

    # ──────────────────────────────────────────────────────────────────────────
    #  DECOUPLED LOG STREAMING (START / FOLLOW / STOP)
    # ──────────────────────────────────────────────────────────────────────────
    def start_test_detached_and_stream(self):
        """Starts all_test_mux_remove.py detached in the background on the Pi,

        and streams lines from the log file without controlling or slowing execution.
        """
        if not self.ssh.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to Raspberry Pi via SSH or enable Simulation Mode first.", parent=self.root)
            return

        log_file = self.log_path_entry.get().strip() or self.ssh.log_path
        self.ssh.log_path = log_file
        self.ssh.save_config()

        self.is_streaming = True
        self.start_btn.configure(state="disabled")
        self.follow_log_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_notification.configure(text="Starting detached test on Pi and streaming log...", fg="#f59e0b")

        def _worker():
            ok, msg = self.ssh.start_remote_script_detached(log_path=log_file)
            if not ok:
                self.root.after(0, lambda: self.status_notification.configure(text=f"Start notice: {msg}", fg="#ef4444"))
            # Follow log file
            self.ssh.start_log_stream(
                line_handler=self._queue_raw_line,
                finished_handler=self._handle_stream_finished,
                log_path=log_file
            )

        threading.Thread(target=_worker, daemon=True).start()

    def attach_and_follow_log(self):
        """Follows the remote log file without restarting or altering the running test."""
        if not self.ssh.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to Raspberry Pi via SSH or enable Simulation Mode first.", parent=self.root)
            return

        log_file = self.log_path_entry.get().strip() or self.ssh.log_path
        self.ssh.log_path = log_file
        self.ssh.save_config()

        self.is_streaming = True
        self.start_btn.configure(state="disabled")
        self.follow_log_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_notification.configure(text=f"Reading remote log ({os.path.basename(log_file)})...", fg="#38bdf8")

        self.ssh.start_log_stream(
            line_handler=self._queue_raw_line,
            finished_handler=self._handle_stream_finished,
            log_path=log_file
        )

    def stop_test_script(self):
        """Stops the test script on the Pi and closes log stream."""
        self.status_notification.configure(text="Stopping remote script...", fg="#ef4444")
        self.ssh.stop_remote_script()
        self.is_streaming = False
        self.start_btn.configure(state="normal")
        self.follow_log_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def open_local_log_file(self):
        """Selects and parses a local log file for offline testing / replaying."""
        fpath = filedialog.askopenfilename(
            parent=self.root,
            title="Open Test Log File",
            filetypes=[("Text & Log files", "*.txt *.log"), ("All files", "*.*")]
        )
        if not fpath:
            return

        self.clear_ui_state()
        self.status_notification.configure(text=f"Replaying: {os.path.basename(fpath)}", fg="#38bdf8")
        self.start_btn.configure(state="disabled")
        self.follow_log_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        self.ssh.read_local_log_file(
            file_path=fpath,
            line_handler=self._queue_raw_line,
            finished_handler=self._handle_stream_finished
        )

    def clear_ui_state(self):
        """Clears all 3 socket stations, stops flashing, and clears terminal log view."""
        for v in self.socket_views.values():
            v.reset_all()
        self.terminal_win.clear()
        self.status_notification.configure(text="UI state cleared. Ready.", fg=TEXT_MUTED)

    def _queue_raw_line(self, line):
        """Thread-safe queue dispatch for incoming lines."""
        self.line_queue.put(line)

    def _handle_stream_finished(self, status_msg):
        self.root.after(0, lambda: self._on_stream_ended(status_msg))

    def _on_stream_ended(self, status_msg):
        self.is_streaming = False
        self.start_btn.configure(state="normal")
        self.follow_log_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_notification.configure(text=f"Idle. ({status_msg})", fg=TEXT_MUTED)

    # ──────────────────────────────────────────────────────────────────────────
    #  THREAD-SAFE REAL-TIME QUEUE POLLER & HIGH-PRECISION PARSER
    # ──────────────────────────────────────────────────────────────────────────
    def _poll_line_queue(self):
        """Drains pending lines from the queue and updates Tkinter widgets in the main UI thread."""
        try:
            lines_processed = 0
            while not self.line_queue.empty() and lines_processed < 60:
                raw_line = self.line_queue.get_nowait()
                lines_processed += 1
                self._parse_and_update(raw_line)
        except Exception:
            pass
        finally:
            self.root.after(20, self._poll_line_queue)

    def _parse_and_update(self, line):
        """Parses stdout/stderr log lines and updates socket status and task cards."""
        self.terminal_win.append_line(line)
        clean = line.strip()
        if not clean:
            return
        low = clean.lower()

        # ── 1. ACTIVE BOARD / SOCKET TRACKING ─────────────────────────────────
        m_fcfs_stage = re.search(r"FCFS STAGE:\s*TESTING BOARD\s*(\d)", clean, re.IGNORECASE)
        if m_fcfs_stage:
            b = int(m_fcfs_stage.group(1))
            if b in self.socket_views:
                self.active_socket_num = b
                self.socket_views[b].mark_active()
            return

        m_dispatch = re.search(r"\[fcfs\]\s*->\s*Dispatching Board\s*(\d)", clean, re.IGNORECASE)
        if m_dispatch:
            b = int(m_dispatch.group(1))
            if b in self.socket_views:
                self.active_socket_num = b
                self.socket_views[b].mark_active()
            return

        m_cycle = re.search(r"(?:NEO CYCLE STARTING|TEST RESULTS):\s*BOARD\s*(\d)", clean, re.IGNORECASE)
        if not m_cycle:
            m_cycle = re.search(r"(?:Testing Socket|Target socket configured to: Board|Single-socket override: Socket)\s*(\d)", clean, re.IGNORECASE)
        if m_cycle:
            b = int(m_cycle.group(1))
            if b in self.socket_views:
                self.active_socket_num = b
                self.socket_views[b].mark_active()
            return

        # ── 2. FINAL RESULT PASS / FAIL (Checked early so [result] FAIL isn't swallowed) ─
        if "[result] pass" in low:
            target_b = self.pending_pass_board or self.active_socket_num
            view = self.socket_views.get(target_b, self.socket_views[1])
            view.mark_passed_flashing(mac=view.last_mac, sr=view.last_sr)
            self.last_passed_board = target_b
            self.pending_pass_board = None
            return

        elif "[result] fail" in low:
            reason = re.sub(r"^\[result\]\s*fail\s*", "", clean, flags=re.IGNORECASE).strip()
            target_b = self.pending_fail_board or self.active_socket_num
            view = self.socket_views.get(target_b, self.socket_views[1])
            view.mark_failed(reason)
            self.last_failed_board = target_b
            self.pending_fail_board = None

            # Pinpoint and mark the exact failing task card RED
            r_low = reason.lower()
            if "rssi" in r_low:
                view.set_step_state("rssi", "FAIL", "Out of bounds")
            elif "ble" in r_low or "connect" in r_low:
                view.set_step_state("ble", "FAIL", "Connect Failed")
            elif "imu" in r_low or "acctest" in r_low:
                view.set_step_state("imu", "FAIL", "IMU Failed")
            elif "mic" in r_low:
                view.set_step_state("mic", "FAIL", "Mic Failed")
            elif "active stream" in r_low or "stream" in r_low:
                view.set_step_state("active_curr", "FAIL", "Out of bounds")
            elif "amber" in r_low:
                view.set_step_state("led_amber", "FAIL", "Out of bounds")
            elif "led" in r_low and "amber" not in r_low:
                view.set_step_state("led_white", "FAIL", "Out of bounds")
            elif "sleep" in r_low:
                view.set_step_state("sleep_curr", "FAIL", "Out of bounds")
            elif "client" in r_low or "flash" in r_low:
                view.set_step_state("client_fw", "FAIL", "Flash Error")
            elif "detect" in r_low or "ficr" in r_low or "module" in r_low:
                view.set_step_state("ficr", "FAIL", "No Module")
            return

        # ── 3. DETECT & FICR READ ─────────────────────────────────────────────
        m_detect_all = re.search(r"\[detect-all\]\s*Socket\s*(\d+):\s*detected new module\s*([0-9A-Fa-f:]{11,17})", clean, re.IGNORECASE)
        if m_detect_all:
            b = int(m_detect_all.group(1))
            mac = m_detect_all.group(2)
            if b in self.socket_views:
                self.socket_views[b].reset_all()
                self.socket_views[b].last_mac = mac
                self.socket_views[b].set_step_state("ficr", "PASS", mac)
            return

        m_empty = re.search(r"\[detect-all\]\s*Socket\s*(\d+)\s*now empty", clean, re.IGNORECASE)
        if m_empty:
            b = int(m_empty.group(1))
            if b in self.socket_views:
                self.socket_views[b].reset_all()
            return

        m_replaced = re.search(r"\[detect-all\]\s*Socket\s*(\d+)\s*previously failed board removed/replaced", clean, re.IGNORECASE)
        if m_replaced:
            b = int(m_replaced.group(1))
            if b in self.socket_views:
                self.socket_views[b].reset_all()
            return

        m_detect = re.search(r"(?:identity\s*|module\s+)([0-9A-Fa-f:]{11,17})", clean)
        if m_detect and "[flash" not in low and "[ble" not in low:
            mac = m_detect.group(1)
            view = self.socket_views.get(self.active_socket_num, self.socket_views[1])
            view.last_mac = mac
            view.set_step_state("ficr", "PASS", mac)
            return

        m_no_mod = re.search(r"(?:no module on socket|No module detected in socket)\s*(\d*)", clean, re.IGNORECASE)
        if m_no_mod and ("no module" in low):
            b_str = m_no_mod.group(1)
            b = int(b_str) if b_str else self.active_socket_num
            if b in self.socket_views:
                self.socket_views[b].set_step_state("ficr", "FAIL", "No Module")
                self.socket_views[b].mark_failed("No module detected on socket")
            return

        if "already passed" in low and ("skip" in low or "db" in low):
            m_sk = re.search(r"(?:board\s*|socket\s*)(\d)", clean, re.IGNORECASE)
            m_mac = re.search(r"\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b", clean)
            mac = m_mac.group(1) if m_mac else ""

            b = int(m_sk.group(1)) if m_sk else None
            if b is None and mac:
                for sock_num, sock_view in self.socket_views.items():
                    if sock_view.last_mac and sock_view.last_mac.upper() == mac.upper():
                        b = sock_num
                        break
            if b is None:
                b = self.active_socket_num

            if b in self.socket_views:
                view = self.socket_views[b]
                view.mark_already_passed(mac=mac)
                if hasattr(self, "status_notification") and self.status_notification:
                    self.status_notification.configure(
                        text=f"Socket {b}: MAC {mac or view.last_mac} already passed in DB (Skipped re-test).",
                        fg="#10b981"
                    )
            return

        # ── 4. TEST FIRMWARE FLASH ────────────────────────────────────────────
        m_bg_test_start = re.search(r"\[flash-bg\]\s*->\s*Starting test firmware flash for Board\s*(\d)", clean, re.IGNORECASE)
        if m_bg_test_start:
            b = int(m_bg_test_start.group(1))
            if b in self.socket_views:
                self.socket_views[b].set_step_state("test_fw", "RUNNING", "Flashing...")
            return

        m_bg_test_ok = re.search(r"\[flash-bg\]\s*<-\s*Board\s*(\d)\s*test firmware FLASHED successfully", clean, re.IGNORECASE)
        if m_bg_test_ok:
            b = int(m_bg_test_ok.group(1))
            if b in self.socket_views:
                self.socket_views[b].set_step_state("test_fw", "PASS", "Verified")
            return

        m_bg_test_fail = re.search(r"\[flash-bg\]\s*<-\s*Board\s*(\d)\s*test firmware flash FAILED:\s*(.*)", clean, re.IGNORECASE)
        if m_bg_test_fail:
            b = int(m_bg_test_fail.group(1))
            err = m_bg_test_fail.group(2)
            if b in self.socket_views:
                self.socket_views[b].set_step_state("test_fw", "FAIL", "Flash Error")
                self.socket_views[b].mark_failed(f"Test flash failed: {err}")
            return

        m_retry = re.search(r"\[flash-retry\]\s*Board\s*(\d)\s*\(.*\)\s*flash failed\s*\(attempt\s*(\d+)/3\)", clean, re.IGNORECASE)
        if m_retry:
            b = int(m_retry.group(1))
            attempt = m_retry.group(2)
            if b in self.socket_views:
                self.socket_views[b].set_step_state("test_fw", "RUNNING", f"Retry {attempt}/3")
            return

        m_retry_fail = re.search(r"\[flash-retry\]\s*Board\s*(\d)\s*\(.*\)\s*flash failed 3 times", clean, re.IGNORECASE)
        if m_retry_fail:
            b = int(m_retry_fail.group(1))
            if b in self.socket_views:
                self.socket_views[b].set_step_state("test_fw", "FAIL", "Failed 3x")
                self.socket_views[b].mark_failed("Test FW flash failed 3 times")
            return

        if "flashing test firmware" in low or "test firmware not yet flashed" in low:
            self.socket_views[self.active_socket_num].set_step_state("test_fw", "RUNNING", "Flashing...")
            return
        elif "test firmware verified" in low:
            self.socket_views[self.active_socket_num].set_step_state("test_fw", "PASS", "Verified")
            return

        # ── 5. CLIENT FIRMWARE FLASH ──────────────────────────────────────────
        m_bg_client_start = re.search(r"(?:\[flash-bg\]\s*->\s*Starting CLIENT firmware flash for Board\s*(\d)|\[board\s*(\d)\]\s*All tests passed! Starting client firmware)", clean, re.IGNORECASE)
        if m_bg_client_start:
            b = int(m_bg_client_start.group(1) or m_bg_client_start.group(2))
            if b in self.socket_views:
                self.socket_views[b].set_step_state("client_fw", "RUNNING", "Flashing...")
            return

        m_bg_client_ok = re.search(r"\[flash-bg\]\s*<-\s*Board\s*(\d)\s*CLIENT firmware FLASHED successfully", clean, re.IGNORECASE)
        if m_bg_client_ok:
            b = int(m_bg_client_ok.group(1))
            if b in self.socket_views:
                self.socket_views[b].set_step_state("client_fw", "PASS", "Verified")
                self.pending_pass_board = b
            return

        m_bg_client_fail = re.search(r"\[flash-bg\]\s*<-\s*Board\s*(\d)\s*CLIENT firmware flash FAILED:\s*(.*)", clean, re.IGNORECASE)
        if m_bg_client_fail:
            b = int(m_bg_client_fail.group(1))
            err = m_bg_client_fail.group(2)
            if b in self.socket_views:
                self.socket_views[b].set_step_state("client_fw", "FAIL", "Flash Error")
                self.socket_views[b].mark_failed(f"Client flash failed: {err}")
                self.pending_fail_board = b
            return

        if "flashing client firmware" in low:
            self.socket_views[self.active_socket_num].set_step_state("client_fw", "RUNNING", "Flashing...")
            return
        elif "client firmware verified" in low:
            self.socket_views[self.active_socket_num].set_step_state("client_fw", "PASS", "Verified")
            self.pending_pass_board = self.active_socket_num
            return

        # ── 6. RSSI MEASUREMENT ───────────────────────────────────────────────
        m_rssi = re.search(r"\[rssi\]\s*([-\d\.]+)\s*dBm", clean, re.IGNORECASE)
        if m_rssi:
            rssi_val = float(m_rssi.group(1))
            view = self.socket_views[self.active_socket_num]
            if RSSI_LIMITS[0] <= rssi_val <= RSSI_LIMITS[1]:
                view.set_step_state("rssi", "PASS", f"{rssi_val:.1f} dBm")
            else:
                view.set_step_state("rssi", "FAIL", f"{rssi_val:.1f} dBm (OOB)")
            return
        elif "scanning on" in low and "rssi" in low:
            self.socket_views[self.active_socket_num].set_step_state("rssi", "RUNNING", "Scanning...")
            return
        elif "rssi" in low and ("outside" in low or "first scan failed" in low):
            self.socket_views[self.active_socket_num].set_step_state("rssi", "FAIL", "Out of bounds")
            return

        # ── 7. BLE CONNECTION ─────────────────────────────────────────────────
        if "connected to" in low and ("ble" in low or "bumble" in low or re.search(r"[0-9A-Fa-f:]{11,17}", clean)):
            self.socket_views[self.active_socket_num].set_step_state("ble", "PASS", "Connected")
            return
        elif "connect attempt" in low and "failed" in low:
            m_att = re.search(r"attempt\s*(\d+)", clean, re.IGNORECASE)
            att_txt = f"Retry {m_att.group(1)}" if m_att else "Retrying..."
            self.socket_views[self.active_socket_num].set_step_state("ble", "RUNNING", att_txt)
            return
        elif ("could not connect" in low or "ble stage fault" in low) and "ble" in low:
            self.socket_views[self.active_socket_num].set_step_state("ble", "FAIL", "Connect Failed")
            return

        # ── 8. IMU ACCELEROMETER SELF-TEST ────────────────────────────────────
        if "accelerometer self-test" in low:
            self.socket_views[self.active_socket_num].set_step_state("imu", "RUNNING", "Testing...")
            return
        elif "acctest pass" in low:
            self.socket_views[self.active_socket_num].set_step_state("imu", "PASS", "PASS")
            return
        elif "acctest fail" in low or "imu timeout" in low or ("imu:" in low and "fail" in low):
            self.socket_views[self.active_socket_num].set_step_state("imu", "FAIL", "FAIL")
            return

        # ── 9. MICROPHONE QC ──────────────────────────────────────────────────
        if "microphone qc" in low or "capturing stimulus" in low or "capturing silence" in low:
            self.socket_views[self.active_socket_num].set_step_state("mic", "RUNNING", "Testing...")
            return
        m_mic = re.search(r"Mic THD:\s*([\d\.]+)%,\s*SNR:\s*([\d\.]+)\s*dB", clean, re.IGNORECASE)
        if m_mic:
            thd = m_mic.group(1)
            snr = m_mic.group(2)
            self.socket_views[self.active_socket_num].set_step_state("mic", "PASS", f"THD:{thd}% SNR:{snr}dB")
            return
        elif "mic" in low and ("limits failed" in low or ("capture" in low and "error" in low)):
            self.socket_views[self.active_socket_num].set_step_state("mic", "FAIL", "Limits Failed")
            return

        # ── 10. ACTIVE STREAM CURRENT ─────────────────────────────────────────
        if "active mode + streaming" in low:
            self.socket_views[self.active_socket_num].set_step_state("active_curr", "RUNNING", "Measuring...")
            return
        m_act = re.search(r"Stream\s*([\d\.]+)\s*mA", clean, re.IGNORECASE)
        if m_act:
            val = float(m_act.group(1))
            view = self.socket_views[self.active_socket_num]
            if ACTIVE_LIMITS[0] <= val <= ACTIVE_LIMITS[1]:
                view.set_step_state("active_curr", "PASS", f"{val:.2f} mA")
            else:
                view.set_step_state("active_curr", "FAIL", f"{val:.2f} mA (OOB)")
            return
        elif "active stream" in low and ("bounds" in low or "too low" in low or "error" in low):
            self.socket_views[self.active_socket_num].set_step_state("active_curr", "FAIL", "Out of bounds")
            return

        # ── 11. WHITE LED DELTA ───────────────────────────────────────────────
        if "white led electrical delta" in low:
            self.socket_views[self.active_socket_num].set_step_state("led_white", "RUNNING", "Testing...")
            return
        m_white_delta = re.search(r"(?:^|\s)Delta\s*([\d\.]+)\s*mA", clean, re.IGNORECASE)
        if m_white_delta and "amber" not in low:
            d_val = float(m_white_delta.group(1))
            view = self.socket_views[self.active_socket_num]
            if LED_DELTA_LIMITS[0] <= d_val <= LED_DELTA_LIMITS[1]:
                view.set_step_state("led_white", "PASS", f"{d_val:.2f} mA")
            else:
                view.set_step_state("led_white", "FAIL", f"{d_val:.2f} mA (OOB)")
            return
        elif "led delta" in low and ("out of bounds" in low or "failed" in low) and "amber" not in low:
            self.socket_views[self.active_socket_num].set_step_state("led_white", "FAIL", "Out of bounds")
            return

        # ── 12. AMBER LED DELTA ───────────────────────────────────────────────
        if "amber led electrical delta" in low:
            self.socket_views[self.active_socket_num].set_step_state("led_amber", "RUNNING", "Testing...")
            return
        m_amber_delta = re.search(r"Amber Delta\s*([\d\.]+)\s*mA", clean, re.IGNORECASE)
        if m_amber_delta:
            ad_val = float(m_amber_delta.group(1))
            view = self.socket_views[self.active_socket_num]
            if AMBER_LED_DELTA_LIMITS[0] <= ad_val <= AMBER_LED_DELTA_LIMITS[1]:
                view.set_step_state("led_amber", "PASS", f"{ad_val:.2f} mA")
            else:
                view.set_step_state("led_amber", "FAIL", f"{ad_val:.2f} mA (OOB)")
            return
        elif "amber led" in low and ("out of bounds" in low or "failed" in low or "error" in low):
            self.socket_views[self.active_socket_num].set_step_state("led_amber", "FAIL", "Out of bounds")
            return

        # ── 13. SLEEP CURRENT ─────────────────────────────────────────────────
        if "sleep mode current" in low:
            self.socket_views[self.active_socket_num].set_step_state("sleep_curr", "RUNNING", "Measuring...")
            return
        m_slp = re.search(r"Sleep\s*([\d\.]+)\s*mA", clean, re.IGNORECASE)
        if m_slp:
            slp_val = float(m_slp.group(1))
            view = self.socket_views[self.active_socket_num]
            if SLEEP_LIMITS[0] <= slp_val <= SLEEP_LIMITS[1]:
                view.set_step_state("sleep_curr", "PASS", f"{slp_val:.3f} mA")
            else:
                view.set_step_state("sleep_curr", "FAIL", f"{slp_val:.3f} mA (OOB)")
            return
        elif "sleep" in low and ("out of bounds" in low or "looks like active" in low or "error" in low):
            self.socket_views[self.active_socket_num].set_step_state("sleep_curr", "FAIL", "Out of bounds")
            return

        # ── 14. DATABASE RECORD SR ────────────────────────────────────────────
        m_sr = re.search(r"saved result SR\s*(\d+)", clean, re.IGNORECASE)
        if m_sr:
            sr_val = m_sr.group(1)
            target_b = self.pending_pass_board or self.last_passed_board or self.last_failed_board or self.active_socket_num
            if target_b in self.socket_views:
                view = self.socket_views[target_b]
                view.last_sr = sr_val
                if view.is_flashing:
                    view.mark_passed_flashing(mac=view.last_mac, sr=sr_val)
            self.last_passed_board = None
            self.last_failed_board = None
            return

    # ──────────────────────────────────────────────────────────────────────────
    #  DATABASE & CMSIS DAP TOOLS
    # ──────────────────────────────────────────────────────────────────────────
    def fetch_and_open_db(self):
        """Fetches jig_results.db from Raspberry Pi and opens viewer dialog."""
        self.status_notification.configure(text="Fetching database from Raspberry Pi...", fg="#f59e0b")
        self.root.update()

        def _worker():
            ok, msg = self.ssh.fetch_database(self.local_temp_db)
            self.root.after(0, lambda: self._on_db_fetched(ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_db_fetched(self, ok, msg):
        if not ok:
            for fallback in ["jig_results.db", "jig_result.db"]:
                local_fallback = os.path.join(os.getcwd(), fallback)
                if os.path.isfile(local_fallback):
                    import shutil
                    shutil.copy(local_fallback, self.local_temp_db)
                    ok = True
                    msg = f"Loaded local workspace {fallback}"
                    break

        if ok:
            self.status_notification.configure(text="Database fetched successfully.", fg="#10b981")
            DatabaseViewerWindow(self.root, self.local_temp_db, on_refresh_callback=self.fetch_and_open_db)
        else:
            self.status_notification.configure(text="Database fetch failed.", fg="#ef4444")
            messagebox.showerror("Fetch Database Error", f"Could not retrieve database:\n{msg}", parent=self.root)

    def check_cmsis_dap(self):
        """Opens CMSIS-DAP verification dialog."""
        if not self.ssh.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to Raspberry Pi via SSH or enable Simulation Mode.", parent=self.root)
            return
        CMSISDAPCheckWindow(self.root, self.ssh)

    def toggle_terminal_logs(self):
        """Toggles visibility of the real-time terminal log console."""
        if self.terminal_win.winfo_viewable():
            self.terminal_win.hide()
        else:
            self.terminal_win.show()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    app = NeoJigApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
