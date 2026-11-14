# NEO Automated Test Jig - Remote Control & Monitor GUI

A Python GUI application designed to run on a remote PC (Windows, macOS, or Linux) to monitor and control the 3-socket automated test jig running on a Raspberry Pi over SSH.

---

## 🌟 Key Architecture & Upgrades

### 1. Decoupled Log Streaming (Zero SSH Bottlenecks)
* **Problem Solved**: Running `all_test_mux_remove.py` inside an interactive SSH pseudo-terminal (PTY) previously slowed down execution because of PTY buffer backpressure, SSH network latency, and synchronous pipe stalls during heavy OpenOCD flashing and Bumble BLE operations.
* **New Decoupled Design**:
  * **Independent Pi Execution**: When **`▶ START (Detached)`** is clicked, the script runs detached in the background on the Pi (`nohup sudo python3 -u all_test_mux_remove.py > jig.log 2>&1 &`). The Raspberry Pi executes tests at 100% full native hardware speed writing to local file cache.
  * **Decoupled Log Reading**: The GUI reads the log file via `tail -n 200 -F <log_path>`. The GUI acts as a decoupled reader; neither network latency nor SSH window buffering can slow down the jig!
  * **📖 Follow Log**: Allows operators to attach to and monitor an ongoing test script on the Pi at any time without restarting the script.
  * **Resilience**: If the SSH connection is interrupted, the test sequence on the Pi continues uninterrupted. Reconnecting instantly restores the GUI display from the last 200 lines.
  * **📂 Open Log...**: Allows loading and replaying any local or archived log file (e.g. `oct-26-10-26_test_log.txt`) directly in the GUI.

---

### 2. High-Precision Task Status Marking (GREEN / RED)
* **FCFS Continuous Pipeline Tracking**:
  * Real-time tracking of FCFS stages: `FCFS STAGE: TESTING BOARD X` and `[fcfs] -> Dispatching Board X`.
  * Dynamic detection of new modules (`[detect-all] Socket X: detected new module <MAC>`) and automatic reset on board removal (`[detect-all] Socket X now empty`).
  * Concurrent background flashing: Background client firmware flashing on Board X does not collide with or corrupt functional testing on Board Y.
* **Calibrated Limit Checks**:
  * **Active Stream Current**: 3.50 - 6.50 mA
  * **White LED Delta**: 0.20 - 2.20 mA
  * **Amber LED Delta**: 2.00 - 3.50 mA
  * **Sleep Current**: 0.0070 - 2.5000 mA
  * **RSSI**: -90.0 to -20.0 dBm
  * Values are validated against limits before marking **GREEN (PASS)**. Out-of-bounds readings turn **RED (FAIL)**.
* **Failure Verdict Mapping**:
  * If a test fails (`[result] FAIL <reason>`), the exact failing step is pinpointed and marked **RED (FAIL)**, and the bottom card turns solid red with the diagnosis.
* **Large Flashing Alert on PASS & Instant Skip for Already-Passed Modules**:
  * When Client Firmware is verified and the record is saved to the DB, the large status box turns **Vibrant Green and STARTS FLASHING / PULSING** with `✔ PASSED`, `✔ CLIENT FW FLASHED`, `✔ SR #XX TO DB`, and `MAC: XX:XX:XX:XX:XX:XX`.
  * **Database Re-test Skip**: If a module MAC is already found in `jig_results.db` with status `PASS`, `all_test_mux_remove.py` logs `[skip] Socket X: MAC <MAC> is already passed in database, skipping test.` and skips redundant re-testing. The GUI immediately turns all 11 test steps **Green (PASS)** and the Passed section **starts blinking green** with `✔ PASSED` and `✔ ALREADY PASSED IN DB`.

---

## 🌟 Controls & Top Bar

* **SSH Connection Setup**:
  * Inputs for Host IP (default `192.168.1.100`), Username (default `isc`), Password / Key Path, and Port (`22`).
  * **Log File Path**: Configurable remote log path (default `/home/isc/Neo_test_jig-Neo_pi_backup/jig.log`), persisted in `~/.neo_jig_gui_config.json`.
  * Connect / Disconnect button with live indicator (**`● CONNECTED`** / **`● DISCONNECTED`**).
  * **Simulation Mode**: Checkbox for testing the GUI offline without Raspberry Pi hardware.
* **Control Buttons**:
  * **`▶ START (Detached)`**: Starts `all_test_mux_remove.py` detached in the background on the Pi writing to `jig.log`, and follows the log.
  * **`📖 Follow Log`**: Attaches to the log stream without starting or restarting the script.
  * **`⏹ STOP`**: Gracefully terminates the script on the Pi (`sudo pkill -SIGINT -f all_test_mux_remove.py`).
  * **`🗄 Fetch dB`**: Fetches `jig_results.db` via SFTP/SCP and opens the Database Viewer (latest on top, Total PASS badge, search, CSV export).
  * **`🔌 Check CMSIS DAP`**: Queries `lsusb` over SSH and checks the 3 CMSIS-DAP debuggers (`2fe3:01ff`, `2fe3:0200`, `2fe3:0204`).
  * **`📂 Open Log...`**: Opens a local log file for offline testing or replaying past runs.
  * **`🗑 Clear UI`**: Clears socket cards and terminal log.

---

## 🚀 How to Run the GUI

### Prerequisites
* Python 3.8 or higher.
* Tkinter (installed with Python; on Linux run `sudo apt install python3-tk`).
* *(Optional)* `paramiko` for enhanced SSH performance (`pip install paramiko`).
  *(If paramiko is unavailable, OpenSSH `ssh` / `scp` CLI is used automatically)*.

### Launching the Application
From the repository folder:
```bash
python3 neo_jig_gui.py
```
Or on Windows:
```cmd
python neo_jig_gui.py
```

### Quick Verification using Simulation Mode
1. Launch `python3 neo_jig_gui.py`.
2. Check the **Simulation Mode** checkbox at the top.
3. Click **`▶ START (Detached)`** or **`📖 Follow Log`**.
4. Observe:
   * Socket 1: Completes full cycle, all 11 rectangles turn **Green**, bottom card turns **Green and Flashes** upon DB save.
   * Socket 2: Completes full cycle and turns **Green and Flashes**.
   * Socket 3: Simulates RSSI out-of-bounds failure, turns RSSI rectangle **Red**, bottom card turns **Solid Red** with failure diagnosis.
   * Socket 1 (Already-Passed Check): Simulates insertion of an already passed module from the database (`C3:7A:88:99:AA:04`). All 11 test rectangles immediately turn **Green** and the bottom card **starts blinking green** with `✔ PASSED`.
5. Click **`🗄 Fetch dB`** and **`🔌 Check CMSIS DAP`** to inspect the tools.
6. Click **`>_ Terminal Logs`** in the bottom right corner to view the real-time logs.
