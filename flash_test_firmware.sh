#!/usr/bin/env bash
# ==============================================================================
#  flash_test_firmware.sh
#
#  Flashes test firmware onto nRF52 chips across all 3 test jig sockets using
#  CMSIS-DAP debuggers and OpenOCD.
#
#  Matches the CMSIS-DAP configuration and flashing logic from all_test_mux_remove.py:
#    - Socket 1: Power GPIO 13 | VID/PID 0x2fe3 0x01ff | Serial 4DA6E205F220EC8E
#    - Socket 2: Power GPIO 27 | VID/PID 0x2fe3 0x0200 | Serial E2E5D9AED9D33D64
#    - Socket 3: Power GPIO 26 | VID/PID 0x2fe3 0x0204 | Serial E35E8B7E733FD598
#
#  Flashing logic:
#    1. Turns board power ON before flashing.
#    2. Attempts direct programming (`program <hex> verify reset exit`).
#    3. If direct programming fails (e.g., APPROTECT-locked chip), executes
#       `nrf52_recover` and retries programming.
#    4. Retries up to 3 attempts with a 2-second pause.
#    5. Turns board power OFF after flashing.
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

# --- Default Firmware Path (from all_test_mux_remove.py TEST_HEX) ---
DEFAULT_HEX="/home/isc/neo_test_fw/neo_test_firmware.hex"
HEX_FILE="$DEFAULT_HEX"

# --- Parse Arguments (Support optional board number and/or custom hex file) ---
TARGET_SOCKETS=(1 2 3)

for arg in "$@"; do
    if [[ "$arg" =~ ^[1-3]$ ]]; then
        TARGET_SOCKETS=("$arg")
    elif [ "$arg" == "all" ]; then
        TARGET_SOCKETS=(1 2 3)
    elif [[ "$arg" == *.hex ]] || [ -f "$arg" ]; then
        HEX_FILE="$arg"
    elif [ "$arg" == "-h" ] || [ "$arg" == "--help" ]; then
        echo "Usage: $0 [1|2|3|all] [/path/to/test_firmware.hex]"
        echo ""
        echo "Examples:"
        echo "  $0                                # Flash default test firmware to all 3 boards"
        echo "  $0 2                              # Flash board 2 only"
        echo "  $0 /path/to/custom.hex            # Flash custom hex to all 3 boards"
        echo "  $0 1 /path/to/custom.hex          # Flash board 1 with custom hex"
        trap - EXIT INT TERM
        exit 0
    else
        echo "Unknown argument: $arg"
        echo "Run '$0 --help' for usage."
        trap - EXIT INT TERM
        exit 1
    fi
done

# Resolve absolute path for hex file if possible
if [ -f "$HEX_FILE" ]; then
    HEX_FILE="$(realpath "$HEX_FILE" 2>/dev/null || echo "$HEX_FILE")"
fi

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
    echo "[POWER] Ensuring all board power outputs are turned OFF..."
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

# --- Flash Function for a Single Socket ---
flash_socket() {
    local b="$1"
    local pin="${SOCKET_PWR[$b]}"
    local vid_pid="${CMSIS_VID_PID[$b]}"
    local serial="${CMSIS_SERIAL[$b]}"
    local hex_path="$2"

    echo ""
    echo "============================================================"
    echo " [SOCKET $b] Flashing Test Firmware"
    echo "  Firmware  : $hex_path"
    echo "  Power Pin : GPIO $pin"
    echo "  VID/PID   : $vid_pid"
    echo "  Serial    : $serial"
    echo "============================================================"

    # Step 1: Turn ON the board power
    echo "[SOCKET $b] Turning ON board power (GPIO $pin)..."
    power_board_on "$b"

    local max_attempts=3
    local success=false
    local output=""

    for ((attempt=1; attempt<=max_attempts; attempt++)); do
        echo "[SOCKET $b] Direct flash attempt $attempt of $max_attempts..."
        local exit_code=0
        output=$(run_openocd "$vid_pid" "$serial" -c "program ${hex_path} verify reset exit" 2>&1) || exit_code=$?

        if echo "$output" | grep -qi "verified"; then
            echo "[SOCKET $b] ✔ SUCCESS: Test firmware flashed and verified on attempt $attempt."
            success=true
            break
        fi

        # Direct program failed - attempt chip recovery (unlock + mass erase)
        echo "[SOCKET $b] Direct program attempt $attempt failed, recovering chip (nrf52_recover)..."
        local rec_output
        local rec_code=0
        rec_output=$(run_openocd "$vid_pid" "$serial" -c "init" -c "nrf52_recover" -c "shutdown" 2>&1) || rec_code=$?

        # Retry programming after recover
        echo "[SOCKET $b] Retrying program post-recover..."
        output=$(run_openocd "$vid_pid" "$serial" -c "program ${hex_path} verify reset exit" 2>&1) || exit_code=$?

        if echo "$output" | grep -qi "verified"; then
            echo "[SOCKET $b] ✔ SUCCESS: Test firmware flashed and verified post-recover on attempt $attempt."
            success=true
            break
        fi

        if [ $attempt -lt $max_attempts ]; then
            echo "[SOCKET $b] Flash attempt $attempt failed. Retrying in 2.0s..."
            sleep 2.0
        fi
    done

    # Step 2: Turn OFF the board power
    echo "[SOCKET $b] Turning OFF board power (GPIO $pin)..."
    power_board_off "$b"

    # Step 3: Evaluate outcome
    if [ "$success" = true ]; then
        return 0
    else
        echo "[SOCKET $b] ✘ FAILED: Test firmware flash failed after $max_attempts attempts."
        echo "--- OpenOCD Log Tail ---"
        echo "$output" | tail -n 8
        echo "------------------------"
        return 1
    fi
}

echo "============================================================"
echo " NEO TEST JIG - FLASH TEST FIRMWARE"
echo " Firmware File  : $HEX_FILE"
echo " OpenOCD Binary : $OPENOCD_BIN"
echo " OpenOCD TCL    : ${OPENOCD_TCL:-'(default search path)'}"
echo " Target Sockets : ${TARGET_SOCKETS[*]}"
echo "============================================================"

# Check if OpenOCD binary exists
if ! command -v "$OPENOCD_BIN" >/dev/null 2>&1 && [ ! -x "$OPENOCD_BIN" ]; then
    echo "ERROR: OpenOCD binary not found or not executable at '$OPENOCD_BIN'."
    exit 1
fi

# Verify firmware file exists
if [ ! -f "$HEX_FILE" ]; then
    echo "ERROR: Firmware hex file not found: '$HEX_FILE'."
    echo "Please ensure the test firmware exists or specify the correct path:"
    echo "  $0 [socket_num] /path/to/neo_test_firmware.hex"
    exit 1
fi

declare -A RESULTS
ANY_FAILED=0

for b in "${TARGET_SOCKETS[@]}"; do
    if flash_socket "$b" "$HEX_FILE"; then
        RESULTS[$b]="SUCCESS"
    else
        RESULTS[$b]="FAILED"
        ANY_FAILED=1
    fi
done

# --- Summary Report ---
echo ""
echo "============================================================"
echo " FLASHING SUMMARY"
echo "============================================================"
for b in "${TARGET_SOCKETS[@]}"; do
    if [ "${RESULTS[$b]}" == "SUCCESS" ]; then
        printf " Socket %d: [ ✔ FLASHED  ]\n" "$b"
    else
        printf " Socket %d: [ ✘ FAILED   ]\n" "$b"
    fi
done
echo "============================================================"

if [ $ANY_FAILED -eq 0 ]; then
    echo "All target sockets flashed successfully!"
    exit 0
else
    echo "One or more sockets failed to flash."
    exit 1
fi
