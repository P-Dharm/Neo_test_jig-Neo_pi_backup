#!/usr/bin/env bash
# ==============================================================================
#  recover_all.sh
#
#  Recovers (unlocks and erases) nRF52 chips across all 3 test jig sockets using
#  CMSIS-DAP debuggers and OpenOCD.
#
#  Matches the CMSIS-DAP configuration and recovery logic from all_test_mux_remove.py:
#    - Socket 1: Power GPIO 13 | VID/PID 0x2fe3 0x01ff | Serial 4DA6E205F220EC8E
#    - Socket 2: Power GPIO 27 | VID/PID 0x2fe3 0x0200 | Serial E2E5D9AED9D33D64
#    - Socket 3: Power GPIO 26 | VID/PID 0x2fe3 0x0204 | Serial E35E8B7E733FD598
#
#  Board power is turned ON before recovery and turned OFF after recovery.
# ==============================================================================

set -u

# --- Locate OpenOCD Binary and TCL Scripts ---
if [ -x "/home/isc/openocd/src/openocd" ]; then
    OPENOCD_BIN="/home/isc/openocd/src/openocd"
elif command -v openocd >/dev/null 2>&1; then
    OPENOCD_BIN="$(command -v openocd)"
else
    OPENOCD_BIN="/home/isc/openocd/src/openocd"
fi

OPENOCD_TCL=""
if [ -d "/home/isc/openocd/tcl" ]; then
    OPENOCD_TCL="/home/isc/openocd/tcl"
elif [ -d "/usr/local/share/openocd/scripts" ]; then
    OPENOCD_TCL="/usr/local/share/openocd/scripts"
elif [ -d "/usr/share/openocd/scripts" ]; then
    OPENOCD_TCL="/usr/share/openocd/scripts"
fi

# --- Hardware Mapping ---
declare -A SOCKET_PWR=(
    [1]=13
    [2]=27
    [3]=26
)

declare -A CMSIS_VID_PID=(
    [1]="0x2fe3 0x01ff"
    [2]="0x2fe3 0x0200"
    [3]="0x2fe3 0x0204"
)

declare -A CMSIS_SERIAL=(
    [1]="4DA6E205F220EC8E"
    [2]="E2E5D9AED9D33D64"
    [3]="E35E8B7E733FD598"
)

# --- GPIO / Power Control Functions ---
set_gpio() {
    local pin="$1"
    local state="$2" # "dh" (HIGH) or "dl" (LOW)
    if command -v pinctrl >/dev/null 2>&1; then
        pinctrl set "$pin" op "$state" >/dev/null 2>&1 || true
    elif command -v raspi-gpio >/dev/null 2>&1; then
        raspi-gpio set "$pin" op "$state" >/dev/null 2>&1 || true
    elif command -v gpioset >/dev/null 2>&1; then
        local val=0
        [ "$state" = "dh" ] && val=1
        gpioset 0 "$pin"="$val" >/dev/null 2>&1 || true
    else
        python3 -c "import subprocess; subprocess.run(['pinctrl', 'set', '$pin', 'op', '$state'], capture_output=True)" >/dev/null 2>&1 || true
    fi
}

power_board_on() {
    local b="$1"
    local pin="${SOCKET_PWR[$b]}"
    set_gpio "$pin" dh
    sleep 0.3
}

power_board_off() {
    local b="$1"
    local pin="${SOCKET_PWR[$b]}"
    set_gpio "$pin" dl
    sleep 0.1
}

power_all_off() {
    if command -v pinctrl >/dev/null 2>&1; then
        pinctrl set 13,26,27 op dl >/dev/null 2>&1 || {
            set_gpio 13 dl
            set_gpio 27 dl
            set_gpio 26 dl
        }
    else
        set_gpio 13 dl
        set_gpio 27 dl
        set_gpio 26 dl
    fi
}

# Ensure all boards are turned OFF on exit or unexpected interruption
cleanup() {
    echo ""
    echo "[POWER] Turning OFF all board power outputs..."
    power_all_off
}
trap cleanup EXIT INT TERM

# --- OpenOCD Runner ---
run_openocd() {
    local vid_pid="$1"
    local serial="$2"
    shift 2

    local cmd=("$OPENOCD_BIN")
    if [ -n "$OPENOCD_TCL" ]; then
        cmd+=("-s" "$OPENOCD_TCL")
    fi
    cmd+=(
        "-f" "interface/cmsis-dap.cfg"
        "-c" "cmsis-dap backend usb_bulk"
        "-c" "cmsis-dap vid_pid ${vid_pid}"
        "-c" "cmsis_dap_serial ${serial}"
        "-c" "transport select swd"
        "-c" "adapter speed 1000"
        "-c" "gdb_port disabled"
        "-c" "telnet_port disabled"
        "-c" "tcl_port disabled"
        "-f" "target/nordic/nrf52.cfg"
    )
    cmd+=("$@")

    "${cmd[@]}"
}

# --- Recover Function for a Single Socket ---
recover_socket() {
    local b="$1"
    local pin="${SOCKET_PWR[$b]}"
    local vid_pid="${CMSIS_VID_PID[$b]}"
    local serial="${CMSIS_SERIAL[$b]}"

    echo ""
    echo "============================================================"
    echo " [SOCKET $b] Starting nRF52 Recovery"
    echo "  Power Pin : GPIO $pin"
    echo "  VID/PID   : $vid_pid"
    echo "  Serial    : $serial"
    echo "============================================================"

    # Step 1: Turn ON the board power
    echo "[SOCKET $b] Turning ON board power (GPIO $pin)..."
    power_board_on "$b"

    # Step 2: Run nrf52_recover via OpenOCD
    echo "[SOCKET $b] Running OpenOCD nrf52_recover..."
    local output
    local exit_code=0
    output=$(run_openocd "$vid_pid" "$serial" -c "init" -c "nrf52_recover" -c "shutdown" 2>&1) || exit_code=$?

    # Step 3: Turn OFF the board power
    echo "[SOCKET $b] Turning OFF board power (GPIO $pin)..."
    power_board_off "$b"

    # Step 4: Evaluate outcome
    if [ $exit_code -eq 0 ]; then
        echo "[SOCKET $b] ✔ SUCCESS: Socket $b recovered successfully."
        return 0
    else
        echo "[SOCKET $b] ✘ FAILED: Recovery failed (exit code $exit_code)."
        echo "--- OpenOCD Log Output ---"
        echo "$output"
        echo "--------------------------"
        return 1
    fi
}

# --- Target Sockets Selection ---
TARGET_SOCKETS=(1 2 3)
if [ $# -ge 1 ]; then
    if [[ "$1" =~ ^[1-3]$ ]]; then
        TARGET_SOCKETS=("$1")
    elif [ "$1" == "all" ]; then
        TARGET_SOCKETS=(1 2 3)
    elif [ "$1" == "-h" ] || [ "$1" == "--help" ]; then
        echo "Usage: $0 [1|2|3|all]"
        echo ""
        echo "Examples:"
        echo "  $0        # Recover all 3 sockets (1, 2, 3)"
        echo "  $0 1      # Recover socket 1 only"
        echo "  $0 2      # Recover socket 2 only"
        echo "  $0 3      # Recover socket 3 only"
        trap - EXIT INT TERM
        exit 0
    else
        echo "Usage: $0 [1|2|3|all]"
        echo "  Default: recovers all 3 sockets (1, 2, 3)"
        trap - EXIT INT TERM
        exit 1
    fi
fi

echo "============================================================"
echo " NEO TEST JIG - RECOVER ALL SOCKETS"
echo " OpenOCD Binary : $OPENOCD_BIN"
echo " OpenOCD TCL    : ${OPENOCD_TCL:-'(default search path)'}"
echo " Sockets        : ${TARGET_SOCKETS[*]}"
echo "============================================================"

# Check if OpenOCD binary exists
if ! command -v "$OPENOCD_BIN" >/dev/null 2>&1 && [ ! -x "$OPENOCD_BIN" ]; then
    echo "ERROR: OpenOCD binary not found or not executable at '$OPENOCD_BIN'."
    exit 1
fi

declare -A RESULTS
ANY_FAILED=0

for b in "${TARGET_SOCKETS[@]}"; do
    if recover_socket "$b"; then
        RESULTS[$b]="SUCCESS"
    else
        RESULTS[$b]="FAILED"
        ANY_FAILED=1
    fi
done

# --- Summary Report ---
echo ""
echo "============================================================"
echo " RECOVERY SUMMARY"
echo "============================================================"
for b in "${TARGET_SOCKETS[@]}"; do
    if [ "${RESULTS[$b]}" == "SUCCESS" ]; then
        printf " Socket %d: [ ✔ RECOVERED ]\n" "$b"
    else
        printf " Socket %d: [ ✘ FAILED    ]\n" "$b"
    fi
done
echo "============================================================"

if [ $ANY_FAILED -eq 0 ]; then
    echo "All target sockets recovered successfully!"
    exit 0
else
    echo "One or more sockets failed recovery."
    exit 1
fi
