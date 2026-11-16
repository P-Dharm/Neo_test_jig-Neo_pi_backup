#!/usr/bin/env python3
"""
================================================================================
  NEO AUTOMATED TEST JIG - LOCAL RASPBERRY PI CONTROLLER GUI
================================================================================
An ultra-lightweight, high-performance Python/Tkinter GUI designed to run
directly on a Raspberry Pi controlling the 3-socket automated test jig.

Features:
  - Local Native Execution: Runs all_test_mux_remove.py locally via unbuffered
    subprocess with zero SSH and zero network overhead.
  - Extremely Lightweight: Pure Python standard library + Tkinter.
    No heavy dependencies (no Paramiko, no Qt, no Electron). Uses < 25MB RAM.
  - High-Precision Real-time Status: 3-column layout (Socket 1, 2, 3) tracking
    all 11 test steps with exact threshold validation (Active, Sleep, RSSI, LEDs).
  - Flashing Green Alert on PASS and instant skip detection for already-passed boards.
  - Built-in Local SQLite Database Viewer (jig_results.db) with search & CSV export.
  - Built-in Local CMSIS-DAP Hardware Check (lsusb query for the 3 debuggers).
  - Offline Simulation Mode for testing UI logic without physical hardware.
================================================================================
"""

import os
import sys
import time
import re
import csv
import signal
import queue
import sqlite3
import threading
import subprocess
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ══════════════════════════════════════════════════════════════════════════════
#  THEME & COLOR CONSTANTS (Workshop High-Contrast Dark Theme)
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

# Resolve script directory dynamically
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_SCRIPT = os.path.join(SCRIPT_DIR, "all_test_mux_remove.py")
LOCAL_DB = os.path.join(SCRIPT_DIR, "jig_results.db")

# Regex to strip ANSI escape codes emitted by terminals
ANSI_ESCAPE_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    """Removes terminal escape codes, cursor escapes, and trailing returns."""
    if not text:
        return ""
    clean = ANSI_ESCAPE_RE.sub('', text)
    return clean.replace('\r', '').strip()


# ══════════════════════════════════════════════════════════════════════════════
#  SOCKET COLUMN VIEW (UI Component for each Socket)
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

    def __init__(self, parent, socket_num: int, target_vid_pid: str):
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

        # Subsections container (compact step rectangles)
        self.steps_container = tk.Frame(self.frame, bg=CARD_BG)
        self.steps_container.pack(fill="x", pady=(0, 6))

        for key, display_name in self.TEST_KEYS:
            row_frame = tk.Frame(
                self.steps_container,
                bg=BOX_BG,
                bd=1,
                relief="solid",
                padx=6,
                pady=3
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
            pady=10
        )
        self.final_status_box.pack(fill="both", expand=True, pady=(4, 0))

        self.final_badge_lbl = tk.Label(
            self.final_status_box,
            text="SOCKET IDLE",
            font=("Segoe UI", 15, "bold"),
            bg="#1c2432",
            fg=TEXT_MUTED
        )
        self.final_badge_lbl.pack(anchor="center", pady=(4, 2))

        self.final_detail_lbl = tk.Label(
            self.final_status_box,
            text="Waiting for DUT / test sequence...",
            font=("Consolas", 9),
            bg="#1c2432",
            fg="#94a3b8",
            justify="center",
            wraplength=220
        )
        self.final_detail_lbl.pack(anchor="center", fill="both", expand=True)

    def set_step_state(self, key: str, state: str, value_text: str = ""):
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
        else:  # IDLE / WAITING
            bg = BOX_BG
            t_fg = TEXT_MAIN
            v_bg = "#1a222f"
            v_fg = TEXT_MUTED
            text = value_text if value_text else "WAITING"
            border = 1

        box_frame.configure(bg=bg, bd=border)
        t_lbl.configure(bg=bg, fg=t_fg)
        v_lbl.configure(bg=v_bg, fg=v_fg, text=text)

    def mark_active(self):
        """Highlights the socket card indicating it is currently being actively tested."""
        self.stop_flashing()
        self.final_status_box.configure(bg="#2a2415", bd=2, relief="solid")
        self.final_badge_lbl.configure(text="TESTING...", bg="#2a2415", fg="#fbbf24")
        self.final_detail_lbl.configure(
            text=f"Active MAC: {self.last_mac or 'Detecting...'}\nRunning automated test chain...",
            bg="#2a2415",
            fg="#fde68a"
        )

    def mark_passed_flashing(self, mac: str = "", sr: str = ""):
        """Turns the bottom status area vibrant green and initiates high-visibility flashing."""
        if mac:
            self.last_mac = mac
        if sr:
            self.last_sr = sr

        self.is_flashing = True
        self._flash_toggle = False
        self._do_flash()

    def _do_flash(self):
        if not self.is_flashing:
            return

        self._flash_toggle = not self._flash_toggle
        bg_col = GREEN_FLASH_1 if self._flash_toggle else GREEN_FLASH_2
        txt_col = "#064e3b" if self._flash_toggle else "#ffffff"

        self.final_status_box.configure(bg=bg_col, bd=3, relief="solid")
        self.final_badge_lbl.configure(
            text="✔  PASSED",
            bg=bg_col,
            fg=txt_col,
            font=("Segoe UI", 16, "bold")
        )

        detail_lines = ["✔ CLIENT FW FLASHED"]
        if self.last_sr:
            detail_lines.append(f"✔ SR #{self.last_sr} SAVED TO DB")
        if self.last_mac:
            detail_lines.append(f"MAC: {self.last_mac}")

        self.final_detail_lbl.configure(
            text="\n".join(detail_lines),
            bg=bg_col,
            fg=txt_col,
            font=("Consolas", 10, "bold")
        )

        self._flash_job = self.parent.after(400, self._do_flash)

    def mark_already_passed(self, mac: str = ""):
        """Instantly turns all 11 test boxes green and pulses green for already-passed module."""
        if mac:
            self.last_mac = mac

        for key, _ in self.TEST_KEYS:
            self.set_step_state(key, "PASS", "PASS (DB)")

        if self.last_mac:
            self.set_step_state("ficr", "PASS", self.last_mac)

        self.is_flashing = True
        self._flash_toggle = False
        self._do_already_passed_flash()

    def _do_already_passed_flash(self):
        if not self.is_flashing:
            return

        self._flash_toggle = not self._flash_toggle
        bg_col = "#059669" if self._flash_toggle else "#047857"
        badge_fg = "#a7f3d0" if self._flash_toggle else "#ffffff"

        self.final_status_box.configure(bg=bg_col, bd=3, relief="solid")
        self.final_badge_lbl.configure(
            text="✔ PASSED",
            bg=bg_col,
            fg=badge_fg,
            font=("Segoe UI", 16, "bold")
        )
        self.final_detail_lbl.configure(
            text=f"✔ ALREADY PASSED IN DB\nMAC: {self.last_mac}\n(Skipped redundant re-test)",
            bg=bg_col,
            fg="#ecfdf5",
            font=("Consolas", 10, "bold")
        )
        self._flash_job = self.parent.after(500, self._do_already_passed_flash)

    def mark_failed(self, reason: str = ""):
        """Sets the bottom card to a solid red failure diagnosis."""
        self.stop_flashing()
        self.final_status_box.configure(bg=COLOR_FAIL_BG, bd=2, relief="solid")
        self.final_badge_lbl.configure(text="✖  FAILED", bg=COLOR_FAIL_BG, fg="#ffffff")

        err_msg = reason if reason else "Failed test validation"
        if len(err_msg) > 65:
            err_msg = err_msg[:62] + "..."

        self.final_detail_lbl.configure(
            text=f"MAC: {self.last_mac or 'Unknown'}\n{err_msg}",
            bg=COLOR_FAIL_BG,
            fg="#fee2e2",
            font=("Consolas", 9, "bold")
        )

    def stop_flashing(self):
        """Cancels any ongoing pulsing animation."""
        self.is_flashing = False
        if self._flash_job is not None:
            try:
                self.parent.after_cancel(self._flash_job)
            except Exception:
                pass
            self._flash_job = None

    def reset_all(self):
        """Clears all test step badges and returns the socket to idle state."""
        self.stop_flashing()
        self.last_mac = ""
        self.last_sr = ""
        for key, _ in self.TEST_KEYS:
            self.set_step_state(key, "IDLE", "WAITING")

        self.final_status_box.configure(bg="#1c2432", bd=2, relief="groove")
        self.final_badge_lbl.configure(text="SOCKET IDLE", bg="#1c2432", fg=TEXT_MUTED)
        self.final_detail_lbl.configure(
            text="Waiting for DUT / test sequence...",
            bg="#1c2432",
            fg="#94a3b8"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  LOCAL PROCESS CONTROLLER (Runs all_test_mux_remove.py locally)
# ══════════════════════════════════════════════════════════════════════════════
class LocalProcessController:
    """Manages local unbuffered subprocess execution of all_test_mux_remove.py on Raspberry Pi."""

    def __init__(self, script_path: str = LOCAL_SCRIPT):
        self.script_path = script_path
        self.process = None
        self.sim_thread = None
        self._stop_requested = False
        self.is_sim_mode = False

    def is_running(self) -> bool:
        """Checks if all_test_mux_remove.py or simulation is actively running."""
        if self.is_sim_mode:
            return self.sim_thread is not None and self.sim_thread.is_alive()

        if self.process is not None:
            if self.process.poll() is None:
                return True

        # System check via pgrep
        try:
            res = subprocess.run(["pgrep", "-f", "all_test_mux_remove.py"], capture_output=True, text=True)
            return bool(res.stdout.strip())
        except Exception:
            return False

    def start(self, line_callback, finished_callback, profile: str = "fast", sim_mode: bool = False):
        """Launches the test script locally with unbuffered output streaming."""
        self._stop_requested = False
        self.is_sim_mode = sim_mode

        if sim_mode:
            self.sim_thread = threading.Thread(
                target=self._run_simulation,
                args=(line_callback, finished_callback),
                daemon=True
            )
            self.sim_thread.start()
            return True, "Simulation started"

        if not os.path.exists(self.script_path):
            return False, f"Script not found: {self.script_path}"

        if self.is_running():
            return False, "all_test_mux_remove.py is already running!"

        # Determine execution command: if already running as root, no sudo needed; otherwise prepend sudo
        if os.geteuid() == 0:
            cmd = [sys.executable, "-u", self.script_path, "--profile", profile]
        else:
            cmd = ["sudo", sys.executable, "-u", self.script_path, "--profile", profile]

        try:
            # Launch unbuffered process with piped stdout/stderr
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
        except Exception as e:
            return False, f"Failed to spawn process: {e}"

        # Background thread to read stdout line by line
        def _stdout_reader():
            try:
                for line in iter(self.process.stdout.readline, ''):
                    if not line:
                        break
                    line_callback(line)
            except Exception:
                pass
            finally:
                if self.process:
                    self.process.stdout.close()
                    self.process.wait()
                finished_callback()

        reader_thread = threading.Thread(target=_stdout_reader, daemon=True)
        reader_thread.start()
        return True, "Started successfully"

    def stop(self):
        """Sends SIGINT cleanly so hardware GPIOs/BLE disconnect cleanly, then force kills if needed."""
        self._stop_requested = True

        if self.is_sim_mode:
            return

        # Attempt graceful SIGINT via sudo pkill
        try:
            subprocess.run(["sudo", "pkill", "-SIGINT", "-f", "all_test_mux_remove.py"], timeout=3)
        except Exception:
            pass

        if self.process and self.process.poll() is None:
            try:
                self.process.send_signal(signal.SIGINT)
            except Exception:
                pass

        # Give it up to 2 seconds to cleanly exit, then force kill
        def _enforce_kill():
            time.sleep(2.0)
            try:
                res = subprocess.run(["pgrep", "-f", "all_test_mux_remove.py"], capture_output=True, text=True)
                if res.stdout.strip():
                    subprocess.run(["sudo", "pkill", "-9", "-f", "all_test_mux_remove.py"], timeout=3)
            except Exception:
                pass

        threading.Thread(target=_enforce_kill, daemon=True).start()

    def _run_simulation(self, line_handler, finished_handler):
        """Simulates realistic FCFS multi-board run with PASS, FAIL, and DB Skip."""
        line_handler("==============================================================")
        line_handler("  ISC NEO 3-in-1 JIG (Simulation Run - Local Pi)")
        line_handler("  Profile       : FAST")
        line_handler("  Active Stream Limits: 3.50 - 6.50 mA")
        line_handler("  Sleep Current Limits: 0.0070 - 2.5000 mA")
        line_handler("==============================================================")
        time.sleep(0.3)

        scenarios = [
            (1, "E2:8F:A1:E0:43:18", True, ""),
            (2, "F2:07:CB:79:96:B6", True, ""),
            (3, "DD:30:B3:19:89:B8", False, "RSSI")
        ]

        for b_num, mac, pass_expected, fail_step in scenarios:
            if self._stop_requested:
                break

            line_handler(f"[detect-all] Socket {b_num}: detected new module {mac}")
            time.sleep(0.35)
            if self._stop_requested:
                break

            line_handler(f"[flash-bg] -> Starting test firmware flash for Board {b_num}...")
            time.sleep(0.4)
            line_handler(f"[flash-bg] <- Board {b_num} test firmware FLASHED successfully")
            time.sleep(0.3)
            if self._stop_requested:
                break

            line_handler(f"FCFS STAGE: TESTING BOARD {b_num} (MAC: {mac})")
            time.sleep(0.3)

            # RSSI
            line_handler(f"Scanning on hci0 for {mac}...")
            time.sleep(0.3)
            if fail_step == "RSSI":
                line_handler("[rssi] -94.5 dBm (too weak)")
                line_handler("[result] FAIL RSSI -94.5 dBm outside bounds (-90.0, -20.0)")
                time.sleep(0.5)
                continue
            else:
                line_handler("[rssi] -48.2 dBm")

            # BLE
            time.sleep(0.25)
            line_handler(f"Connected to {mac} via BLE")

            # IMU
            time.sleep(0.25)
            line_handler("IMU: Accelerometer self-test running...")
            time.sleep(0.25)
            line_handler("ACCTEST PASS")

            # Mic
            time.sleep(0.25)
            line_handler("Microphone QC: capturing stimulus...")
            time.sleep(0.35)
            line_handler("Mic THD: 0.85%, SNR: 68.2 dB")

            # Active Stream
            time.sleep(0.25)
            line_handler("Active mode + streaming audio...")
            time.sleep(0.25)
            line_handler("Stream 4.82 mA")

            # White LED
            time.sleep(0.25)
            line_handler("White LED electrical delta...")
            time.sleep(0.25)
            line_handler("Delta 1.45 mA")

            # Amber LED
            time.sleep(0.25)
            line_handler("Amber LED electrical delta...")
            time.sleep(0.25)
            line_handler("Amber Delta 2.68 mA")

            # Sleep
            time.sleep(0.25)
            line_handler("Sleep mode current measurement...")
            time.sleep(0.25)
            line_handler("Sleep 0.045 mA")

            # Client FW
            time.sleep(0.25)
            line_handler(f"[board {b_num}] All tests passed! Starting client firmware flash...")
            line_handler(f"[flash-bg] -> Starting CLIENT firmware flash for Board {b_num}...")
            time.sleep(0.5)
            line_handler(f"[flash-bg] <- Board {b_num} CLIENT firmware FLASHED successfully")

            sr_num = 100 + b_num
            line_handler(f"Saved result SR {sr_num} to local database")
            line_handler("[result] PASS")
            time.sleep(0.5)

        # Re-test skip simulation on Socket 1
        if not self._stop_requested:
            time.sleep(0.5)
            line_handler("[detect-all] Socket 1: detected new module C3:7A:88:99:AA:04")
            time.sleep(0.4)
            line_handler("[skip] Socket 1: MAC C3:7A:88:99:AA:04 is already passed in database, skipping test.")

        finished_handler()


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE VIEWER WINDOW (Direct Local SQLite Access)
# ══════════════════════════════════════════════════════════════════════════════
class DatabaseViewerWindow(tk.Toplevel):
    """Direct local viewer for jig_results.db with search, stats, and CSV export."""

    def __init__(self, parent, db_path: str = LOCAL_DB):
        super().__init__(parent)
        self.title("NEO Test Jig - Database Viewer (Local)")
        self.geometry("1024x600")
        self.configure(bg=BG_DARK)
        self.db_path = db_path

        self._build_ui()
        self.load_data()

    def _build_ui(self):
        # Stats Top Bar
        stats_frame = tk.Frame(self, bg=PANEL_BG, bd=1, relief="ridge", padx=12, pady=8)
        stats_frame.pack(fill="x", padx=10, pady=(10, 6))

        info_frame = tk.Frame(stats_frame, bg=PANEL_BG)
        info_frame.pack(side="left")
        tk.Label(
            info_frame,
            text="LOCAL DATABASE RECORDS",
            font=("Segoe UI", 11, "bold"),
            bg=PANEL_BG,
            fg=TEXT_MAIN
        ).pack(anchor="w")

        self.db_file_lbl = tk.Label(
            info_frame,
            text=f"File: {os.path.basename(self.db_path)}",
            font=("Consolas", 8),
            bg=PANEL_BG,
            fg=TEXT_MUTED
        )
        self.db_file_lbl.pack(anchor="w")

        cards_frame = tk.Frame(stats_frame, bg=PANEL_BG)
        cards_frame.pack(side="right")

        # Total PASS Count (Green)
        pass_card = tk.Frame(cards_frame, bg="#064e3b", bd=2, relief="solid", padx=12, pady=2)
        pass_card.pack(side="right", padx=6)
        tk.Label(pass_card, text="TOTAL PASS", font=("Segoe UI", 8, "bold"), bg="#064e3b", fg="#6ee7b7").pack()
        self.pass_count_lbl = tk.Label(pass_card, text="0", font=("Segoe UI", 15, "bold"), bg="#064e3b", fg="#a7f3d0")
        self.pass_count_lbl.pack()

        # Total FAIL Count (Red)
        fail_card = tk.Frame(cards_frame, bg="#7f1d1d", bd=1, relief="solid", padx=12, pady=2)
        fail_card.pack(side="right", padx=6)
        tk.Label(fail_card, text="TOTAL FAIL", font=("Segoe UI", 8, "bold"), bg="#7f1d1d", fg="#fca5a5").pack()
        self.fail_count_lbl = tk.Label(fail_card, text="0", font=("Segoe UI", 15, "bold"), bg="#7f1d1d", fg="#fecaca")
        self.fail_count_lbl.pack()

        # Total Records
        total_card = tk.Frame(cards_frame, bg="#1e293b", bd=1, relief="solid", padx=12, pady=2)
        total_card.pack(side="right", padx=6)
        tk.Label(total_card, text="TOTAL TESTS", font=("Segoe UI", 8, "bold"), bg="#1e293b", fg="#94a3b8").pack()
        self.total_count_lbl = tk.Label(total_card, text="0", font=("Segoe UI", 15, "bold"), bg="#1e293b", fg=TEXT_MAIN)
        self.total_count_lbl.pack()

        # Toolbar Frame
        toolbar = tk.Frame(self, bg=BG_DARK, padx=10, pady=4)
        toolbar.pack(fill="x")

        tk.Label(toolbar, text="Filter:", bg=BG_DARK, fg=TEXT_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 4))
        self.filter_var = tk.StringVar(value="ALL")
        for f in ["ALL", "PASS", "FAIL"]:
            rb = tk.Radiobutton(
                toolbar,
                text=f,
                variable=self.filter_var,
                value=f,
                bg=BG_DARK,
                fg=TEXT_MAIN,
                selectcolor="#334155",
                activebackground=BG_DARK,
                activeforeground=TEXT_MAIN,
                command=self.apply_filter
            )
            rb.pack(side="left", padx=4)

        tk.Label(toolbar, text="Search MAC/Text:", bg=BG_DARK, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(14, 4))
        self.search_entry = tk.Entry(toolbar, bg="#1e293b", fg=TEXT_MAIN, insertbackground=TEXT_MAIN, width=22)
        self.search_entry.pack(side="left", padx=4)
        self.search_entry.bind("<KeyRelease>", lambda e: self.apply_filter())

        refresh_btn = tk.Button(
            toolbar,
            text="🔄 Refresh",
            command=self.load_data,
            bg="#2563eb",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=8,
            pady=2,
            cursor="hand2"
        )
        refresh_btn.pack(side="right", padx=4)

        export_btn = tk.Button(
            toolbar,
            text="💾 Export CSV",
            command=self.export_csv,
            bg="#334155",
            fg=TEXT_MAIN,
            font=("Segoe UI", 9),
            relief="flat",
            padx=8,
            pady=2,
            cursor="hand2"
        )
        export_btn.pack(side="right", padx=4)

        # Table Frame
        table_frame = tk.Frame(self, bg=BG_DARK)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(2, 10))

        columns = ("sr", "timestamp", "result", "mac", "rssi", "ble", "imu", "mic", "active_ma", "sleep_ma", "client_fw", "comments")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        headers = {
            "sr": ("SR", 45),
            "timestamp": ("Timestamp", 140),
            "result": ("Result", 65),
            "mac": ("MAC Address", 130),
            "rssi": ("RSSI (dBm)", 75),
            "ble": ("BLE", 50),
            "imu": ("IMU", 50),
            "mic": ("Mic", 55),
            "active_ma": ("Active mA", 75),
            "sleep_ma": ("Sleep mA", 75),
            "client_fw": ("Client FW", 75),
            "comments": ("Comments / Failure Reason", 220)
        }

        for col, (title, width) in headers.items():
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="center" if col != "comments" else "w")

        v_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        h_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")

        # Treeview row styling
        self.tree.tag_configure("PASS", background="#064e3b", foreground="#a7f3d0")
        self.tree.tag_configure("FAIL", background="#450a0a", foreground="#fca5a5")

        self.all_rows = []

    def load_data(self):
        """Reads test_results directly from local SQLite database."""
        if not os.path.exists(self.db_path):
            self.db_file_lbl.configure(text=f"Database file not found: {self.db_path}", fg="#ef4444")
            return

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT sr, timestamp, result, mac, rssi_dbm, ble, imu_acctest,
                       mic_result, active_stream_ma, sleep_current_ma, client_firmware, comments
                FROM test_results
                ORDER BY sr DESC
            """)
            self.all_rows = cursor.fetchall()
            conn.close()

            # Calculate metrics
            total = len(self.all_rows)
            pass_cnt = sum(1 for r in self.all_rows if str(r["result"]).upper() == "PASS")
            fail_cnt = sum(1 for r in self.all_rows if str(r["result"]).upper() == "FAIL")

            self.total_count_lbl.configure(text=str(total))
            self.pass_count_lbl.configure(text=str(pass_cnt))
            self.fail_count_lbl.configure(text=str(fail_cnt))
            self.db_file_lbl.configure(text=f"File: {os.path.basename(self.db_path)} ({total} records loaded)", fg=TEXT_MUTED)

            self.apply_filter()
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed reading database:\n{e}", parent=self)

    def apply_filter(self):
        """Filters rows according to selected status and search keyword."""
        self.tree.delete(*self.tree.get_children())
        filter_mode = self.filter_var.get().upper()
        search_term = self.search_entry.get().strip().lower()

        for r in self.all_rows:
            res = str(r["result"]).upper()
            if filter_mode != "ALL" and res != filter_mode:
                continue

            row_str = " ".join(str(val) for val in r).lower()
            if search_term and search_term not in row_str:
                continue

            sr_val = r["sr"]
            ts = r["timestamp"]
            mac = r["mac"]
            rssi = f"{r['rssi_dbm']:.1f}" if r['rssi_dbm'] is not None else "-"
            ble = r["ble"] or "-"
            imu = r["imu_acctest"] or "-"
            mic = r["mic_result"] or "-"
            act = f"{r['active_stream_ma']:.2f}" if r['active_stream_ma'] is not None else "-"
            slp = f"{r['sleep_current_ma']:.3f}" if r['sleep_current_ma'] is not None else "-"
            cfw = r["client_firmware"] or "-"
            comm = r["comments"] or ""

            values = (sr_val, ts, res, mac, rssi, ble, imu, mic, act, slp, cfw, comm)
            tag = "PASS" if res == "PASS" else ("FAIL" if res == "FAIL" else "")
            self.tree.insert("", "end", values=values, tags=(tag,))

    def export_csv(self):
        """Exports the loaded database entries directly to a CSV file."""
        if not self.all_rows:
            messagebox.showwarning("Export CSV", "No records to export.", parent=self)
            return

        out_path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialfile=f"NEO_JIG_EXPORT_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not out_path:
            return

        try:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "SR", "Timestamp", "Result", "MAC", "RSSI (dBm)", "BLE", "IMU",
                    "Mic", "Active Current (mA)", "Sleep Current (mA)", "Client FW", "Comments"
                ])
                for r in self.all_rows:
                    writer.writerow([
                        r["sr"], r["timestamp"], r["result"], r["mac"],
                        r["rssi_dbm"], r["ble"], r["imu_acctest"], r["mic_result"],
                        r["active_stream_ma"], r["sleep_current_ma"], r["client_firmware"], r["comments"]
                    ])
            messagebox.showinfo("Export Successful", f"Database records exported successfully to:\n{out_path}", parent=self)
        except Exception as e:
            messagebox.showerror("Export Failed", f"Error exporting CSV:\n{e}", parent=self)


# ══════════════════════════════════════════════════════════════════════════════
#  CMSIS-DAP HARDWARE CHECK WINDOW (Local `lsusb` execution)
# ══════════════════════════════════════════════════════════════════════════════
class CMSISDAPCheckWindow(tk.Toplevel):
    """Executes local `lsusb` and verifies presence of the 3 CMSIS-DAP debuggers."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("CMSIS-DAP USB Verification (Local)")
        self.geometry("680x480")
        self.configure(bg=BG_DARK)

        self._build_ui()
        self.run_check()

    def _build_ui(self):
        self.hdr_frame = tk.Frame(self, bg=PANEL_BG, bd=1, relief="ridge", padx=12, pady=10)
        self.hdr_frame.pack(fill="x", padx=10, pady=(10, 6))

        self.status_title = tk.Label(
            self.hdr_frame,
            text="Checking USB devices on Raspberry Pi...",
            font=("Segoe UI", 11, "bold"),
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

        recheck_btn = tk.Button(
            btn_frame,
            text="🔄 Re-Check USB",
            command=self.run_check,
            bg=ACCENT_BLUE,
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2"
        )
        recheck_btn.pack(side="right")

    def run_check(self):
        """Runs local lsusb and updates the badge indicators."""
        self.status_title.configure(text="Querying `lsusb` locally...", fg="#f59e0b")
        self.raw_text.delete("1.0", "end")

        def _worker():
            try:
                res = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
                out = res.stdout
                err = res.stderr
                code = res.returncode
            except Exception as e:
                out = ""
                err = str(e)
                code = 1
            self.after(0, lambda: self._process_result(out, err, code))

        threading.Thread(target=_worker, daemon=True).start()

    def _process_result(self, out: str, err: str, code: int):
        self.raw_text.insert("end", out if out else f"Error running lsusb:\n{err}")

        found_count = 0
        out_low = out.lower()
        for s_num in [1, 2, 3]:
            card, state_badge = self.cards[s_num]
            target_id = CMSIS_DAP_IDS[s_num].lower()

            if target_id in out_low:
                found_count += 1
                card.configure(bg="#064e3b")
                state_badge.configure(text="DETECTED (PASS)", bg="#10b981", fg="#ffffff")
            else:
                card.configure(bg="#450a0a")
                state_badge.configure(text="NOT FOUND", bg="#ef4444", fg="#ffffff")

        if found_count == 3:
            self.status_title.configure(text="✔ ALL 3 CMSIS-DAP DEBUGGERS DETECTED & READY", fg="#10b981")
        else:
            self.status_title.configure(text=f"⚠ WARNING: ONLY {found_count} OF 3 CMSIS-DAP DEBUGGERS FOUND", fg="#ef4444")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION CLASS (NeoPiApp)
# ══════════════════════════════════════════════════════════════════════════════
class NeoPiApp:
    """Main Application Controller for the Raspberry Pi GUI."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("NEO Test Jig - Raspberry Pi Controller")
        self.root.geometry("1024x720")
        self.root.minsize(960, 600)
        self.root.configure(bg=BG_DARK)

        # Process controller and thread-safe line queue
        self.controller = LocalProcessController(LOCAL_SCRIPT)
        self.line_queue = queue.Queue()

        # State tracking
        self.socket_views = {}
        self.active_socket_num = 1
        self.pending_pass_board = None
        self.pending_fail_board = None
        self.last_passed_board = None
        self.last_failed_board = None

        self._build_ui()

        # Start periodic queue polling
        self.root.after(40, self._poll_line_queue)

        # Graceful exit hook
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # ══════════════════════════════════════════════════════════════════════
        #  TOP CONTROL BAR
        # ══════════════════════════════════════════════════════════════════════
        self.top_frame = tk.Frame(self.root, bg=PANEL_BG, bd=1, relief="ridge", padx=12, pady=8)
        self.top_frame.pack(fill="x", padx=10, pady=(8, 4))

        # Row 1: Title and Status Badge
        r1 = tk.Frame(self.top_frame, bg=PANEL_BG)
        r1.pack(fill="x", pady=(0, 6))

        tk.Label(
            r1,
            text="NEO AUTOMATED TEST JIG",
            font=("Segoe UI", 13, "bold"),
            bg=PANEL_BG,
            fg=TEXT_MAIN
        ).pack(side="left")

        tk.Label(
            r1,
            text="(Raspberry Pi Local Controller)",
            font=("Segoe UI", 9),
            bg=PANEL_BG,
            fg=TEXT_MUTED
        ).pack(side="left", padx=8)

        self.sim_var = tk.BooleanVar(value=False)
        sim_chk = tk.Checkbutton(
            r1,
            text="Simulation Mode",
            variable=self.sim_var,
            bg=PANEL_BG,
            fg="#f59e0b",
            selectcolor="#1e293b",
            activebackground=PANEL_BG,
            activeforeground="#fbbf24",
            font=("Segoe UI", 9, "bold")
        )
        sim_chk.pack(side="right", padx=(8, 0))

        self.status_badge = tk.Label(
            r1,
            text="●  IDLE",
            font=("Segoe UI", 10, "bold"),
            bg="#2e3848",
            fg=TEXT_MUTED,
            padx=10,
            pady=2
        )
        self.status_badge.pack(side="right", padx=6)

        sep = tk.Frame(self.top_frame, height=1, bg="#334155")
        sep.pack(fill="x", pady=4)

        # Row 2: Control Buttons & Notification
        r2 = tk.Frame(self.top_frame, bg=PANEL_BG)
        r2.pack(fill="x")

        self.start_btn = tk.Button(
            r2,
            text="▶  START",
            command=self.start_test,
            bg="#16a34a",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=14,
            pady=3,
            cursor="hand2"
        )
        self.start_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = tk.Button(
            r2,
            text="⏹  STOP",
            command=self.stop_test,
            bg="#dc2626",
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=14,
            pady=3,
            cursor="hand2",
            state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(0, 10))

        self.clear_ui_btn = tk.Button(
            r2,
            text="🗑  Clear UI",
            command=self.clear_ui,
            bg="#334155",
            fg="#cbd5e1",
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        self.clear_ui_btn.pack(side="left", padx=(0, 6))

        self.view_db_btn = tk.Button(
            r2,
            text="🗄  View DB",
            command=self.open_db_viewer,
            bg="#2563eb",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        self.view_db_btn.pack(side="left", padx=(0, 6))

        self.check_dap_btn = tk.Button(
            r2,
            text="🔌  Check CMSIS DAP",
            command=self.open_dap_checker,
            bg="#0d9488",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        self.check_dap_btn.pack(side="left", padx=(0, 10))

        self.notification_lbl = tk.Label(
            r2,
            text="Ready. Click START to begin automated tests.",
            font=("Segoe UI", 9),
            bg=PANEL_BG,
            fg=TEXT_MUTED
        )
        self.notification_lbl.pack(side="left", padx=6)

        # ══════════════════════════════════════════════════════════════════════
        #  MAIN 3-COLUMN SEPARATION FOR SOCKET 1, SOCKET 2, SOCKET 3
        # ══════════════════════════════════════════════════════════════════════
        self.columns_container = tk.Frame(self.root, bg=BG_DARK)
        self.columns_container.pack(fill="both", expand=True, padx=10, pady=4)

        for s_num in [1, 2, 3]:
            col_view = SocketColumnView(
                self.columns_container,
                socket_num=s_num,
                target_vid_pid=CMSIS_DAP_IDS[s_num]
            )
            col_view.frame.pack(side="left", fill="both", expand=True, padx=4)
            self.socket_views[s_num] = col_view

        # ══════════════════════════════════════════════════════════════════════
        #  BOTTOM BAR
        # ══════════════════════════════════════════════════════════════════════
        self.bottom_bar = tk.Frame(self.root, bg=PANEL_BG, bd=1, relief="ridge", padx=10, pady=4)
        self.bottom_bar.pack(fill="x", padx=10, pady=(2, 6))

        self.bottom_info_lbl = tk.Label(
            self.bottom_bar,
            text=f"Script: {os.path.basename(LOCAL_SCRIPT)}  |  Database: {os.path.basename(LOCAL_DB)}",
            font=("Segoe UI", 8),
            bg=PANEL_BG,
            fg=TEXT_MUTED
        )
        self.bottom_info_lbl.pack(side="left")

        self.proc_state_lbl = tk.Label(
            self.bottom_bar,
            text="Status: Stopped",
            font=("Consolas", 8),
            bg=PANEL_BG,
            fg="#94a3b8"
        )
        self.proc_state_lbl.pack(side="right")

    # ══════════════════════════════════════════════════════════════════════════
    #  EXECUTION CONTROLS
    # ══════════════════════════════════════════════════════════════════════════
    def start_test(self):
        """Launches all_test_mux_remove.py locally in continuous FCFS mode."""
        sim = self.sim_var.get()
        self.notification_lbl.configure(text="Launching test sequence...", fg="#fbbf24")

        success, msg = self.controller.start(
            line_callback=self._queue_raw_line,
            finished_callback=self._on_process_finished,
            profile="fast",
            sim_mode=sim
        )

        if not success:
            messagebox.showerror("Execution Error", msg, parent=self.root)
            self.notification_lbl.configure(text=f"Error: {msg}", fg="#ef4444")
            return

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_badge.configure(text="●  RUNNING", bg="#16a34a", fg="#ffffff")
        self.proc_state_lbl.configure(text="Status: Running", fg="#10b981")
        self.notification_lbl.configure(text="Tests running in continuous FCFS mode.", fg="#10b981")

    def stop_test(self):
        """Gracefully stops the active testing script."""
        self.notification_lbl.configure(text="Stopping tests cleanly...", fg="#f59e0b")
        self.controller.stop()
        self.stop_btn.configure(state="disabled")

    def _on_process_finished(self):
        """Callback invoked when the subprocess or simulation finishes."""
        def _ui_finish():
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.status_badge.configure(text="●  IDLE", bg="#2e3848", fg=TEXT_MUTED)
            self.proc_state_lbl.configure(text="Status: Stopped", fg="#94a3b8")
            self.notification_lbl.configure(text="Test script stopped.", fg=TEXT_MUTED)

        self.root.after(0, _ui_finish)

    def clear_ui(self):
        """Resets all 3 socket views to their initial idle states."""
        for view in self.socket_views.values():
            view.reset_all()
        self.notification_lbl.configure(text="UI state cleared.", fg=TEXT_MUTED)

    def open_db_viewer(self):
        """Opens the local SQLite Database Viewer."""
        DatabaseViewerWindow(self.root, LOCAL_DB)

    def open_dap_checker(self):
        """Opens the CMSIS DAP local verification window."""
        CMSISDAPCheckWindow(self.root)

    def _on_close(self):
        """Handles application window close with safety confirmation."""
        if self.controller.is_running():
            if messagebox.askyesno("Exit Confirmation", "A test process is currently running. Stop tests and exit?"):
                self.controller.stop()
                self.root.destroy()
        else:
            self.root.destroy()

    # ══════════════════════════════════════════════════════════════════════════
    #  LINE QUEUE & HIGH-PRECISION REGEX PARSER
    # ══════════════════════════════════════════════════════════════════════════
    def _queue_raw_line(self, line: str):
        """Receives a line from background reader and enqueues for UI processing."""
        clean = strip_ansi(line)
        if clean:
            self.line_queue.put(clean)

    def _poll_line_queue(self):
        """Pulls lines from thread-safe queue and passes to parser."""
        count = 0
        while not self.line_queue.empty() and count < 60:
            try:
                line = self.line_queue.get_nowait()
                self._parse_and_update(line)
                count += 1
            except queue.Empty:
                break
        self.root.after(40, self._poll_line_queue)

    def _parse_and_update(self, line: str):
        """Parses stdout log lines and updates socket status and task cards."""
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

        # ── 2. FINAL RESULT PASS / FAIL ───────────────────────────────────────
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
                self.notification_lbl.configure(
                    text=f"Socket {b}: MAC {mac or view.last_mac} already passed in DB (Skipped).",
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


# ══════════════════════════════════════════════════════════════════════════════
#  APPLICATION ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main():
    root = tk.Tk()
    app = NeoPiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
