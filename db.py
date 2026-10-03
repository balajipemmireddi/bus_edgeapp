"""
Local SQLite storage for the bus edge device.

Holds three things, per the spec (§5, §7.5, §7.6):
  1. roster        - cached student encodings, synced nightly from the cloud
  2. daily_state   - each child's current state machine position, TODAY only
  3. event_queue    - PICKED_UP / DROPPED / etc. events waiting to sync to cloud

State is written synchronously on every change (no in-memory-only state) so a
Pi reboot mid-route never loses or duplicates an event (§7.5).
"""

import sqlite3
import json
import datetime
import uuid
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "data" / "edge_local.db"


def _today() -> str:
    return datetime.date.today().isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS roster (
                child_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                encodings TEXT NOT NULL,      -- JSON list of 128-d vectors
                assigned_bus_id TEXT,
                pickup_stop_id TEXT,
                drop_stop_id TEXT,
                twin_group TEXT               -- shared id for known lookalikes/twins, else NULL
            );

            CREATE TABLE IF NOT EXISTS daily_state (
                child_id TEXT NOT NULL,
                date TEXT NOT NULL,
                status TEXT NOT NULL,          -- NOT_PICKED_UP | ON_BUS_TO_SCHOOL | AT_SCHOOL
                                                -- | ON_BUS_TO_HOME | DROPPED
                last_event_time TEXT,
                expected_today INTEGER DEFAULT 1,  -- daily attendance flag (0 = marked absent)
                PRIMARY KEY (child_id, date)
            );

            CREATE TABLE IF NOT EXISTS event_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uuid TEXT NOT NULL UNIQUE,
                child_id TEXT,
                event_type TEXT NOT NULL,      -- PICKED_UP | DROPPED | EXIT_UNEXPECTED_LOCATION
                                                -- | UNMATCHED_REVIEW | AMBIGUOUS_REVIEW
                confidence REAL,
                photo_path TEXT,
                gps_lat REAL,
                gps_lng REAL,
                bus_id TEXT,
                timestamp TEXT NOT NULL,
                synced INTEGER DEFAULT 0
            );
            """
        )
        # Upgrade databases created before event UUIDs were introduced.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(event_queue)")}
        if "event_uuid" not in columns:
            conn.execute("ALTER TABLE event_queue ADD COLUMN event_uuid TEXT")
            rows = conn.execute("SELECT id FROM event_queue WHERE event_uuid IS NULL").fetchall()
            conn.executemany(
                "UPDATE event_queue SET event_uuid=? WHERE id=?",
                [(str(uuid.uuid4()), row["id"]) for row in rows],
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_event_queue_uuid ON event_queue(event_uuid)")


def load_roster() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM roster").fetchall()
        return [
            {
                "child_id": r["child_id"],
                "name": r["name"],
                "encodings": json.loads(r["encodings"]),
                "assigned_bus_id": r["assigned_bus_id"],
                "pickup_stop_id": r["pickup_stop_id"],
                "drop_stop_id": r["drop_stop_id"],
                "twin_group": r["twin_group"],
            }
            for r in rows
        ]


def replace_roster(students: list[dict]):
    """Nightly sync overwrites the whole roster (§6 step 5)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM roster")
        conn.executemany(
            """INSERT INTO roster
               (child_id, name, encodings, assigned_bus_id, pickup_stop_id, drop_stop_id, twin_group)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    s["child_id"],
                    s["name"],
                    json.dumps(s["encodings"]),
                    s.get("assigned_bus_id"),
                    s.get("pickup_stop_id"),
                    s.get("drop_stop_id"),
                    s.get("twin_group"),
                )
                for s in students
            ],
        )


def get_status(child_id: str, date: str | None = None) -> str:
    date = date or _today()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM daily_state WHERE child_id=? AND date=?",
            (child_id, date),
        ).fetchone()
        return row["status"] if row else "NOT_PICKED_UP"


def is_expected_today(child_id: str, date: str | None = None) -> bool:
    date = date or _today()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT expected_today FROM daily_state WHERE child_id=? AND date=?",
            (child_id, date),
        ).fetchone()
        return True if row is None else bool(row["expected_today"])


def mark_absent_today(child_id: str, date: str | None = None):
    date = date or _today()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO daily_state (child_id, date, status, expected_today)
               VALUES (?, ?, 'NOT_PICKED_UP', 0)
               ON CONFLICT(child_id, date) DO UPDATE SET expected_today=0""",
            (child_id, date),
        )


def set_status(child_id: str, status: str, date: str | None = None):
    """Synchronous write on every transition - survives a reboot (§7.5)."""
    date = date or _today()
    now = datetime.datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO daily_state (child_id, date, status, last_event_time)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(child_id, date) DO UPDATE SET status=excluded.status,
                                                          last_event_time=excluded.last_event_time""",
            (child_id, date, status, now),
        )


def _insert_event(conn, child_id, event_type, confidence, photo_path, gps, bus_id, timestamp):
    conn.execute(
        """INSERT INTO event_queue
           (event_uuid, child_id, event_type, confidence, photo_path, gps_lat, gps_lng, bus_id, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), child_id, event_type, confidence, photo_path,
         gps[0] if gps else None, gps[1] if gps else None, bus_id, timestamp),
    )


def record_transition_and_event(
    child_id: str, status: str, event_type: str, confidence: float,
    photo_path: str, gps: tuple[float, float] | None, bus_id: str,
    date: str | None = None,
):
    """Persist a state transition and its outbox event in one SQLite transaction."""
    date = date or _today()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO daily_state (child_id, date, status, last_event_time)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(child_id, date) DO UPDATE SET status=excluded.status,
                                                          last_event_time=excluded.last_event_time""",
            (child_id, date, status, now),
        )
        _insert_event(conn, child_id, event_type, confidence, photo_path, gps, bus_id, now)


def queue_event(
    child_id: str,
    event_type: str,
    confidence: float,
    photo_path: str,
    gps: tuple[float, float] | None,
    bus_id: str,
):
    with get_conn() as conn:
        _insert_event(conn, child_id, event_type, confidence, photo_path, gps, bus_id,
                      datetime.datetime.now(datetime.timezone.utc).isoformat())


def get_unsynced_events() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM event_queue WHERE synced=0").fetchall()
        return [dict(r) for r in rows]


def mark_synced(event_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE event_queue SET synced=1 WHERE id=?", (event_id,))


def reset_new_school_day(date: str | None = None):
    """Calendar-driven reset (§7.6) - call once at the start of each school day.
    Does not touch history, just ensures today starts clean if not already present."""
    date = date or _today()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) as c FROM daily_state WHERE date=?", (date,)
        ).fetchone()
        if existing["c"] == 0:
            pass  # rows are created lazily by set_status/mark_absent_today as events occur
