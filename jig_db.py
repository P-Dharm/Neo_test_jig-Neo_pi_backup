"""
================================================================================
  JIG DATABASE MODULE (Offline SQLite + Online Supabase Sync)
================================================================================

Architecture:
  - Local Database: SQLite with WAL (Write-Ahead Logging) mode.
    Provides sub-millisecond local writes, zero latency for the test cycle,
    power-failure resilience on Raspberry Pi, and instant indexed MAC duplicate checks.
  - Remote Database: Supabase (PostgreSQL) via native REST API (PostgREST).
    Uses standard Python 'requests' library - zero additional dependencies.
  - Auto-Sync Worker: Background daemon thread uploading unsynced batches (synced=0)
    to Supabase. If internet is down, tests proceed without interruption; once
    connection is restored, all backlogged records are automatically uploaded in batches.
================================================================================
"""

import os
import sys
import time
import socket
import logging
import sqlite3
import threading
from typing import Optional, Dict, Any, List

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION (Set your Supabase credentials here)
# ══════════════════════════════════════════════════════════════════════════════

# Supabase Project Credentials
# Replace these with your actual Supabase Project URL and anon / service_role key
SUPABASE_URL = "https://biqnkyuwdgyuzlgfmtmp.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJpcW5reXV3ZGd5dXpsZ2ZtdG1wIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTM4NzcyNjEsImV4cCI6MjEwOTQ1MzI2MX0.6aY9E4raqTKfLlmPQyVxijwcfh_yWH-4942SkKcEW-A"
SUPABASE_TABLE = "jig_test_results"

# Master toggle for online Supabase push (False = offline SQLite only)
ENABLE_SUPABASE = False

# Station / Jig identifier (helps differentiate multiple jigs uploading to one Supabase DB)
DEFAULT_JIG_ID = "JIG_01"
JIG_ID = "JIG_01"

# Local SQLite Database Path
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(WORK_DIR, "jig_results.db")

# Sync Parameters
SYNC_BATCH_SIZE = 50          # Max records uploaded per batch
SYNC_RETRY_INTERVAL = 15.0    # Seconds between retry attempts during outage
SYNC_HTTP_TIMEOUT = 10.0      # Seconds before HTTP request times out

# ══════════════════════════════════════════════════════════════════════════════
#  INTERNAL STATE & LOCKS
# ══════════════════════════════════════════════════════════════════════════════

_db_lock = threading.Lock()
_active_db_path = DEFAULT_DB_PATH

_sync_thread: Optional[threading.Thread] = None
_sync_wake_event = threading.Event()
_sync_stop_event = threading.Event()
_is_syncing = False

logger = logging.getLogger("jig_db")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA TYPE CONVERSION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _to_float(val: Any) -> Optional[float]:
    """Converts a value to float, converting 'NA', empty strings, or None to None."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip().upper()
    if val_str in ("NA", "N/A", "NONE", "NULL", ""):
        return None
    try:
        return float(val_str)
    except ValueError:
        return None


def _to_str(val: Any) -> str:
    """Safely converts value to string."""
    if val is None:
        return ""
    return str(val).strip()


# ══════════════════════════════════════════════════════════════════════════════
#  LOCAL DATABASE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a thread-safe connection to the SQLite database with WAL mode."""
    target_path = db_path or _active_db_path
    conn = sqlite3.connect(target_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db(db_path: Optional[str] = None):
    """Initializes the SQLite schema and creates performance indexes."""
    global _active_db_path
    if db_path:
        _active_db_path = db_path

    with _db_lock:
        conn = get_db_connection(_active_db_path)
        try:
            with conn:
                conn.execute("""
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
                        comments TEXT,
                        synced INTEGER NOT NULL DEFAULT 0,
                        synced_at TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                # Fast duplicate check index
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_mac_result
                    ON test_results(mac, result);
                """)
                # Fast unsynced queue index
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_synced
                    ON test_results(synced, sr);
                """)
                # Migrate existing table if missing V_charging / amber_led_delta_ma columns
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(test_results);")
                cols = [row[1] for row in cursor.fetchall()]
                if cols and "V_charging" not in cols:
                    conn.execute("ALTER TABLE test_results ADD COLUMN V_charging REAL;")
                if cols and "amber_led_delta_ma" not in cols:
                    conn.execute("ALTER TABLE test_results ADD COLUMN amber_led_delta_ma REAL;")
            logger.info(f"Local SQLite database initialized at {_active_db_path}")
        finally:
            conn.close()


def check_mac_passed(mac: str) -> Optional[str]:
    """
    Checks the local SQLite database if the given MAC has already PASSED.
    Returns 'PASS' if found, None otherwise.
    Fast indexed lookup (< 1 ms).
    """
    if not mac:
        return None
    mac_clean = mac.strip().upper()
    with _db_lock:
        conn = get_db_connection(_active_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT result FROM test_results
                WHERE UPPER(mac) = ? AND UPPER(result) = 'PASS'
                LIMIT 1;
            """, (mac_clean,))
            row = cursor.fetchone()
            return "PASS" if row else None
        except Exception as err:
            logger.error(f"Error checking MAC in database: {err}")
            return None
        finally:
            conn.close()


def save_test_result(record: Dict[str, Any]) -> int:
    """
    Saves a test cycle record into the local SQLite database.
    Marks row with synced = 0.
    Triggers the background sync worker.
    Returns the assigned serial number (sr).
    """
    jig = record.get("jig_id") or JIG_ID
    ts = _to_str(record.get("timestamp"))
    mac = _to_str(record.get("mac"))
    ficr_mac = _to_str(record.get("ficr_mac"))
    rssi_dbm = _to_float(record.get("rssi_dbm"))
    ble = _to_str(record.get("ble"))
    imu_acctest = _to_str(record.get("imu_acctest"))
    mic_result = _to_str(record.get("mic_result"))
    led_delta_ma = _to_float(record.get("led_delta_ma"))
    amber_led_delta_ma = _to_float(record.get("amber_led_delta_ma"))
    active_stream_ma = _to_float(record.get("active_stream_ma"))
    v_charging = _to_float(record.get("V_charging"))
    sleep_current_ma = _to_float(record.get("sleep_current_ma"))
    client_firmware = _to_str(record.get("client_firmware"))
    result = _to_str(record.get("result")) or "FAIL"
    comments = _to_str(record.get("comments"))

    with _db_lock:
        conn = get_db_connection(_active_db_path)
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO test_results (
                        jig_id, timestamp, mac, ficr_mac, rssi_dbm, ble,
                        imu_acctest, mic_result,
                        led_delta_ma, amber_led_delta_ma, active_stream_ma,
                        V_charging, sleep_current_ma,
                        client_firmware, result, comments, synced
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, 0
                    );
                """, (
                    jig, ts, mac, ficr_mac, rssi_dbm, ble,
                    imu_acctest, mic_result,
                    led_delta_ma, amber_led_delta_ma, active_stream_ma,
                    v_charging, sleep_current_ma,
                    client_firmware, result, comments
                ))
                serial = cursor.lastrowid
        finally:
            conn.close()

    # Notify background sync worker to immediately push
    trigger_sync()
    return serial


def get_unsynced_records(batch_size: int = SYNC_BATCH_SIZE) -> List[Dict[str, Any]]:
    """Retrieves up to batch_size unsynced rows from local database."""
    with _db_lock:
        conn = get_db_connection(_active_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM test_results
                WHERE synced = 0
                ORDER BY sr ASC
                LIMIT ?;
            """, (batch_size,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


def mark_records_synced(sr_list: List[int]):
    """Marks the given SR numbers as synced=1 with current timestamp."""
    if not sr_list:
        return
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ",".join("?" for _ in sr_list)
    with _db_lock:
        conn = get_db_connection(_active_db_path)
        try:
            with conn:
                conn.execute(f"""
                    UPDATE test_results
                    SET synced = 1, synced_at = ?
                    WHERE sr IN ({placeholders});
                """, [now_str] + sr_list)
        finally:
            conn.close()


def get_sync_stats() -> Dict[str, int]:
    """Returns total, synced, and pending record counts."""
    with _db_lock:
        conn = get_db_connection(_active_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM test_results;")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM test_results WHERE synced = 1;")
            synced = cursor.fetchone()[0]
            return {"total": total, "synced": synced, "pending": total - synced}
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE SYNC ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def is_supabase_configured() -> bool:
    """Checks if Supabase sync is enabled and valid credentials are configured."""
    if not ENABLE_SUPABASE:
        return False
    return bool(
        SUPABASE_URL
        and "your-project-ref" not in SUPABASE_URL
        and SUPABASE_KEY
        and "your-anon-or-service-role-key" not in SUPABASE_KEY
    )


def push_batch_to_supabase(records: List[Dict[str, Any]]) -> bool:
    """
    Pushes a batch of records to the Supabase REST API endpoint.
    Uses 'Prefer: resolution=merge-duplicates' to make upload idempotent.
    Returns True if successfully saved in Supabase, False otherwise.
    """
    if not records:
        return True

    if not is_supabase_configured():
        logger.debug("Supabase credentials not configured yet. Records will remain safely queued in local DB.")
        return False

    if not REQUESTS_OK:
        logger.warning("'requests' library is not available. Cannot upload to Supabase.")
        return False

    endpoint = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=merge-duplicates"
    }

    # Prepare payload clean of local SQLite metadata
    payload = []
    for r in records:
        item = {
            "sr": r["sr"],
            "jig_id": r["jig_id"],
            "timestamp": r["timestamp"],
            "mac": r["mac"],
            "ficr_mac": r["ficr_mac"],
            "rssi_dbm": r["rssi_dbm"],
            "ble": r["ble"],
            "imu_acctest": r["imu_acctest"],
            "mic_result": r["mic_result"],
            "led_delta_ma": r["led_delta_ma"],
            "amber_led_delta_ma": r["amber_led_delta_ma"] if "amber_led_delta_ma" in r.keys() else None,
            "active_stream_ma": r["active_stream_ma"],
            "V_charging": _to_float(r["V_charging"]) if "V_charging" in r.keys() else None,
            "sleep_current_ma": r["sleep_current_ma"],
            "client_firmware": r["client_firmware"],
            "result": r["result"],
            "comments": r["comments"]
        }
        payload.append(item)

    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=SYNC_HTTP_TIMEOUT
        )
        # PostgREST returns 201 Created or 200/204 on success
        if response.status_code in (200, 201, 204):
            return True
        else:
            logger.warning(f"Supabase sync HTTP {response.status_code}: {response.text}")
            return False
    except requests.exceptions.RequestException as exc:
        # Expected during internet outage
        logger.debug(f"Internet connection unavailable or timed out: {exc}")
        return False
    except Exception as exc:
        logger.warning(f"Unexpected error uploading to Supabase: {exc}")
        return False


def sync_all_pending() -> int:
    """
    Syncs all pending records in batches until queue is empty or network fails.
    Returns count of successfully synced records.
    """
    if not is_supabase_configured():
        return 0

    total_synced = 0
    while not _sync_stop_event.is_set():
        batch = get_unsynced_records(SYNC_BATCH_SIZE)
        if not batch:
            break

        sr_ids = [r["sr"] for r in batch]
        success = push_batch_to_supabase(batch)
        if success:
            mark_records_synced(sr_ids)
            total_synced += len(sr_ids)
            logger.info(f"[sync] Uploaded batch of {len(sr_ids)} test records to Supabase (SRs: {sr_ids[0]}..{sr_ids[-1]})")
        else:
            # Network issue or Supabase error; stop current cycle and retry later
            break

    return total_synced


def _sync_worker_loop():
    """Background thread that sleeps and syncs whenever triggered or on retry interval."""
    logger.info("Database background sync worker started.")
    while not _sync_stop_event.is_set():
        # Check and sync any pending records
        try:
            sync_all_pending()
        except Exception as err:
            logger.error(f"Error in sync worker: {err}")

        # Wait until awakened by new record or timeout (outage retry interval)
        _sync_wake_event.wait(timeout=SYNC_RETRY_INTERVAL)
        _sync_wake_event.clear()

    logger.info("Database background sync worker stopped.")


def trigger_sync():
    """Wakes the background sync thread immediately if Supabase is enabled."""
    if ENABLE_SUPABASE:
        _sync_wake_event.set()


def start_sync_worker(db_path: Optional[str] = None):
    """Starts the background auto-sync worker thread if ENABLE_SUPABASE is True."""
    global _sync_thread
    init_db(db_path)
    if not ENABLE_SUPABASE:
        logger.info("Supabase push disabled (ENABLE_SUPABASE=False). Local SQLite only.")
        return
    if _sync_thread is not None and _sync_thread.is_alive():
        return

    _sync_stop_event.clear()
    _sync_wake_event.set()
    _sync_thread = threading.Thread(target=_sync_worker_loop, daemon=True, name="JigDBSyncWorker")
    _sync_thread.start()


def stop_sync_worker(wait: bool = True):
    """Gracefully signals the background sync thread to stop."""
    global _sync_thread
    _sync_stop_event.set()
    _sync_wake_event.set()
    if wait and _sync_thread and _sync_thread.is_alive():
        _sync_thread.join(timeout=2.0)
    _sync_thread = None


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND-LINE INTERFACE (CLI Utilities)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JIG Database Management Utility")
    parser.add_argument("--status", action="store_true", help="Print total, synced, and pending record statistics")
    parser.add_argument("--sync", action="store_true", help="Force immediate sync of pending records to Supabase")
    parser.add_argument("--tail", type=int, default=0, metavar="N", help="Show last N records from local database")
    args = parser.parse_args()

    init_db()

    if args.status:
        stats = get_sync_stats()
        print("\n" + "=" * 48)
        print("  JIG DATABASE STATUS")
        print("=" * 48)
        print(f"  Station ID      : {JIG_ID}")
        print(f"  Local DB File   : {_active_db_path}")
        print(f"  Total Records   : {stats['total']}")
        print(f"  Synced (Cloud)  : {stats['synced']}")
        print(f"  Pending Sync    : {stats['pending']}")
        print(f"  Supabase Setup  : {'Configured' if is_supabase_configured() else 'Not configured (using placeholder)'}")
        print("=" * 48 + "\n")

    elif args.sync:
        if not is_supabase_configured():
            print("\n[!] Supabase credentials are not configured in jig_db.py.")
            print("    Please set SUPABASE_URL and SUPABASE_KEY before syncing.\n")
            sys.exit(1)
        print("\n[sync] Pushing all pending records to Supabase...")
        count = sync_all_pending()
        print(f"[sync] Finished. Successfully synced {count} records.\n")

    elif args.tail > 0:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sr, timestamp, mac, result, synced FROM test_results ORDER BY sr DESC LIMIT ?;", (args.tail,))
        rows = cursor.fetchall()
        conn.close()
        print("\n" + "=" * 70)
        print(f"{'SR':<6} {'TIMESTAMP':<20} {'MAC':<18} {'RESULT':<8} {'SYNCED':<6}")
        print("=" * 70)
        for r in reversed(rows):
            print(f"{r['sr']:<6} {r['timestamp']:<20} {r['mac']:<18} {r['result']:<8} {'YES' if r['synced'] else 'NO':<6}")
        print("=" * 70 + "\n")

    else:
        parser.print_help()