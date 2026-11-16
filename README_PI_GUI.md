# NEO Test Jig - Ultra-Lightweight Raspberry Pi Local GUI

A lightweight, high-performance Python/Tkinter GUI designed to run directly on the Raspberry Pi controlling the 3-socket NEO automated test jig.

---

## 🚀 Key Advantages Over Remote GUI (`neo_jig_gui.py`)

1. **Zero SSH & Network Overhead**:
   - Runs directly on the Raspberry Pi.
   - Eliminates all SSH connection setup, Paramiko dependencies, latency, and socket buffers.
2. **Extremely Lightweight (< 25MB RAM)**:
   - Uses standard library Python 3 and Tkinter only.
   - Zero heavy third-party dependencies (no Qt, no Electron, no browser engines).
   - Starts up instantly (< 0.2s) even on low-spec Raspberry Pi boards.
3. **Streamlined Controls (No SSH or Log Options)**:
   - All complex SSH connection settings, log path entry fields, terminal log windows, and file replay pickers are removed.
   - The test script (`all_test_mux_remove.py`) is spawned locally as an unbuffered subprocess and parsed in real time via an internal thread-safe queue.
4. **Full Feature Parity**:
   - **3 Sockets**: Dedicated columns for Socket 1, Socket 2, and Socket 3 with CMSIS-DAP IDs.
   - **11 Real-Time Test Steps**: Real-time pass/fail validation for FICR, test FW, RSSI, BLE, IMU, Mic QC, Active Current, White LED, Amber LED, Sleep Current, and Client FW.
   - **High-Visibility PASS/FAIL Alerts**: Flashing emerald green card on PASS, instant skip handling for already-passed units, and red failure diagnosis.
   - **Local Database Viewer**: Direct instant queries to `jig_results.db` with search, filter (ALL/PASS/FAIL), and CSV export.
   - **Local CMSIS-DAP Check**: One-click local `lsusb` inspection for all 3 debuggers (`2fe3:01ff`, `2fe3:0200`, `2fe3:0204`).
   - **Simulation Mode**: Built-in mock test loop for verifying the UI offline without physical hardware.

---

## 🛠️ How to Launch

From the Raspberry Pi terminal inside the project directory:

```bash
./run_pi_gui.sh
```

Or:

```bash
python3 neo_pi_gui.py
```

> **Note on Permissions**:
> `all_test_mux_remove.py` accesses low-level hardware (GPIO, OpenOCD, HCI Bluetooth) and requires `sudo`.
> If you start `neo_pi_gui.py` as a standard user, clicking **`▶ START`** automatically invokes `sudo python3 -u all_test_mux_remove.py --profile fast`.
> Alternatively, you can run the entire GUI under `sudo`:
> ```bash
> sudo python3 neo_pi_gui.py
> ```

---

## 🖥️ UI Layout & Operations

| Control | Description |
| :--- | :--- |
| **`▶ START`** | Spawns `all_test_mux_remove.py` locally in continuous FCFS mode with `--profile fast`. |
| **`⏹ STOP`** | Sends `SIGINT` to gracefully stop the test sequence and release GPIO/BLE hardware. |
| **`🗑 Clear UI`** | Resets all 3 socket columns to idle waiting state. |
| **`🗄 View DB`** | Opens the local SQLite database viewer for `jig_results.db` with record counts and CSV export. |
| **`🔌 Check CMSIS DAP`** | Runs local `lsusb` to verify that all 3 CMSIS-DAP debuggers are plugged in. |
| **`Simulation Mode`** | Check this box before clicking **`START`** to simulate multi-board test runs offline. |

---

## 🔄 Optional: Auto-Launch on Boot (Factory Kiosk)

To make the GUI automatically start whenever the Raspberry Pi boots into the desktop:

1. Create an autostart directory if it doesn't exist:
   ```bash
   mkdir -p ~/.config/autostart
   ```
2. Create `~/.config/autostart/neo_jig_gui.desktop`:
   ```ini
   [Desktop Entry]
   Type=Application
   Name=NEO Jig GUI
   Exec=/usr/bin/python3 /home/isc/Neo_test_jig-Neo_pi_backup/neo_pi_gui.py
   WorkingDirectory=/home/isc/Neo_test_jig-Neo_pi_backup
   Terminal=false
   ```
