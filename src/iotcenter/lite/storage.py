"""SQLite storage for IoT Center Lite.

Three tables (the same shape the platform keeps in PostgreSQL):

  readings         every message, kept for `retention_days` (30)
  readings_hourly  per-device hourly aggregates, updated on each insert, kept for
                   `hourly_retention_days` (2 years)
  devices          one row per board: first/last seen, message counters, sequence gaps

So the file has a ceiling: about 262 MB per board at one reading every 2 s with the defaults
(see `forecast`). Purged space goes back to the disk (incremental vacuum), so lowering a
retention also shrinks the file.

Timestamps are stored as Unix seconds (REAL) so time bucketing is plain arithmetic.
Methods are synchronous; the async app calls them through `asyncio.to_thread`.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..api import HISTORY_METRICS, empty_history
from ..protocol import Reading

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    device_id       TEXT    NOT NULL,
    event_time      REAL    NOT NULL,  -- device clock if synced, else received_at
    seq             INTEGER NOT NULL,
    name            TEXT,
    ts              REAL,              -- device clock (NULL when not NTP-synced)
    received_at     REAL    NOT NULL,
    source          TEXT,
    temperature_c   REAL,
    humidity_pct    REAL,
    pressure_hpa    REAL,
    dew_point_c     REAL,
    cpu_temp_c      REAL,
    mem_free_bytes  INTEGER,
    mem_alloc_bytes INTEGER,
    storage_free_kb REAL,
    cpu_freq_mhz    REAL,
    uptime_s        INTEGER,
    wifi_rssi_dbm   INTEGER,
    ip              TEXT,
    firmware        TEXT,
    cpu_busy_pct    REAL,
    loop_max_ms     INTEGER,
    sensor_errors   INTEGER,
    boot_reason     TEXT,
    PRIMARY KEY (device_id, event_time, seq)  -- a resent message is stored once
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS readings_by_time ON readings (event_time);

CREATE TABLE IF NOT EXISTS readings_hourly (
    device_id       TEXT    NOT NULL,
    hour            REAL    NOT NULL,  -- start of the hour, Unix seconds
    samples         INTEGER NOT NULL,
    temperature_sum REAL, temperature_min REAL, temperature_max REAL,
    humidity_sum    REAL, humidity_min    REAL, humidity_max    REAL,
    pressure_sum    REAL, pressure_min    REAL, pressure_max    REAL,
    dew_point_sum   REAL, dew_point_min   REAL, dew_point_max   REAL,
    PRIMARY KEY (device_id, hour)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS devices (
    device_id  TEXT PRIMARY KEY,
    name       TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    last_seq   INTEGER NOT NULL,
    messages   INTEGER NOT NULL DEFAULT 0,
    dropped    INTEGER NOT NULL DEFAULT 0,  -- gaps in `seq`: messages that never arrived
    restarts   INTEGER NOT NULL DEFAULT 0,  -- `seq` went backwards: the board rebooted
    ip         TEXT,
    firmware   TEXT,
    source     TEXT
);
"""

INCREMENTAL = 2  # PRAGMA auto_vacuum mode

# Measured on disk with 1.3 million real readings (30 days at one every 2 s): the row and its
# entry in readings_by_time. And a year of hourly aggregates for one board: 8,760 rows.
BYTES_PER_READING = 202
HOURLY_BYTES_PER_DEVICE_YEAR = 1_440_000

# API metric name -> column prefix in readings_hourly
HOURLY_PREFIX = {
    "temperature_c": "temperature",
    "humidity_pct": "humidity",
    "pressure_hpa": "pressure",
    "dew_point_c": "dew_point",
}

READING_COLUMNS = (
    "device_id",
    "event_time",
    "seq",
    "name",
    "ts",
    "received_at",
    "source",
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "dew_point_c",
    "cpu_temp_c",
    "mem_free_bytes",
    "mem_alloc_bytes",
    "storage_free_kb",
    "cpu_freq_mhz",
    "uptime_s",
    "wifi_rssi_dbm",
    "ip",
    "firmware",
    "cpu_busy_pct",
    "loop_max_ms",
    "sensor_errors",
    "boot_reason",
)

# Columns added after 2.0: a database created before gets them when it is opened.
ADDED_COLUMNS = (
    ("cpu_busy_pct", "REAL"),
    ("loop_max_ms", "INTEGER"),
    ("sensor_errors", "INTEGER"),
    ("boot_reason", "TEXT"),
)

INSERT_READING = (
    f"INSERT OR IGNORE INTO readings ({', '.join(READING_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in READING_COLUMNS)})"
)

UPSERT_HOURLY = """
INSERT INTO readings_hourly VALUES (
    :device_id, :hour, 1,
    :temperature_c, :temperature_c, :temperature_c,
    :humidity_pct,  :humidity_pct,  :humidity_pct,
    :pressure_hpa,  :pressure_hpa,  :pressure_hpa,
    :dew_point_c,   :dew_point_c,   :dew_point_c)
ON CONFLICT (device_id, hour) DO UPDATE SET
    samples         = samples + 1,
    temperature_sum = temperature_sum + excluded.temperature_sum,
    temperature_min = MIN(temperature_min, excluded.temperature_min),
    temperature_max = MAX(temperature_max, excluded.temperature_max),
    humidity_sum    = humidity_sum + excluded.humidity_sum,
    humidity_min    = MIN(humidity_min, excluded.humidity_min),
    humidity_max    = MAX(humidity_max, excluded.humidity_max),
    pressure_sum    = pressure_sum + excluded.pressure_sum,
    pressure_min    = MIN(pressure_min, excluded.pressure_min),
    pressure_max    = MAX(pressure_max, excluded.pressure_max),
    dew_point_sum   = dew_point_sum + excluded.dew_point_sum,
    dew_point_min   = MIN(dew_point_min, excluded.dew_point_min),
    dew_point_max   = MAX(dew_point_max, excluded.dew_point_max)
"""

UPSERT_DEVICE = """
INSERT INTO devices (device_id, name, first_seen, last_seen, last_seq, messages, dropped,
                     restarts, ip, firmware, source)
VALUES (:device_id, :name, :received_at, :received_at, :seq, 1, :dropped, :restarts,
        :ip, :firmware, :source)
ON CONFLICT (device_id) DO UPDATE SET
    name      = COALESCE(excluded.name, name),
    last_seen = MAX(last_seen, excluded.last_seen),
    last_seq  = excluded.last_seq,
    messages  = messages + 1,
    dropped   = dropped + excluded.dropped,
    restarts  = restarts + excluded.restarts,
    ip        = COALESCE(excluded.ip, ip),
    firmware  = COALESCE(excluded.firmware, firmware),
    source    = excluded.source
"""


class Storage:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            if db.execute("PRAGMA auto_vacuum").fetchone()[0] != INCREMENTAL:
                # Lets purges give space back. Instant on a new file; on an older one the
                # VACUUM rewrites it once (seconds, even at full size).
                db.execute(f"PRAGMA auto_vacuum={INCREMENTAL}")
                db.execute("VACUUM")
            db.execute("PRAGMA journal_mode=WAL")  # readers never block the writer
            db.executescript(SCHEMA)
            existing = {row["name"] for row in db.execute("PRAGMA table_info(readings)")}
            for column, kind in ADDED_COLUMNS:
                if column not in existing:
                    db.execute(f"ALTER TABLE readings ADD COLUMN {column} {kind}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection per operation: simple and safe across threads."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=NORMAL")
        try:
            with db:  # one transaction: commit on success, rollback on error
                yield db
        finally:
            db.close()

    # --- writes ---------------------------------------------------------------------------

    def insert(self, reading: Reading) -> bool:
        """Store a reading; returns False when it was a duplicate (already stored)."""
        row = reading_row(reading)
        with self._connect() as db:
            if db.execute(INSERT_READING, row).rowcount == 0:
                return False
            db.execute(UPSERT_HOURLY, {**row, "hour": row["event_time"] // 3600 * 3600})
            previous = db.execute(
                "SELECT last_seq FROM devices WHERE device_id = ?", (reading.device_id,)
            ).fetchone()
            dropped = restarts = 0
            if previous is not None:
                if reading.seq > previous["last_seq"] + 1:
                    dropped = reading.seq - previous["last_seq"] - 1
                elif reading.seq <= previous["last_seq"]:
                    restarts = 1
            db.execute(UPSERT_DEVICE, {**row, "dropped": dropped, "restarts": restarts})
        return True

    def purge(self, older_than: float) -> int:
        """Delete raw readings older than `older_than` (hourly aggregates are kept)."""
        with self._connect() as db:
            return db.execute("DELETE FROM readings WHERE event_time < ?", (older_than,)).rowcount

    def purge_hourly(self, older_than: float) -> int:
        """Delete hourly aggregates of hours that started before `older_than`."""
        with self._connect() as db:
            return db.execute("DELETE FROM readings_hourly WHERE hour < ?", (older_than,)).rowcount

    def reclaim(self) -> int:
        """Give the space freed by purges back to the disk. Returns the bytes released."""
        with self._connect() as db:
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            free = db.execute("PRAGMA freelist_count").fetchone()[0]
            db.execute("PRAGMA incremental_vacuum").fetchall()  # frees one page per step
        return free * page_size

    # --- reads ----------------------------------------------------------------------------

    def devices(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            devices = [dict(row) for row in db.execute("SELECT * FROM devices")]
            for device in devices:
                latest = db.execute(
                    "SELECT * FROM readings WHERE device_id = ? ORDER BY event_time DESC LIMIT 1",
                    (device["device_id"],),
                ).fetchone()
                device["latest"] = record(latest) if latest else None
        return devices

    def readings(
        self, device_id: str, start: float, end: float, limit: int = 10_000
    ) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM readings WHERE device_id = ? AND event_time >= ? AND event_time < ?"
                " ORDER BY event_time LIMIT ?",
                (device_id, start, end, limit),
            ).fetchall()
        return [record(row) for row in rows]

    def history(self, device_id: str, start: float, end: float, bucket_s: int) -> dict[str, Any]:
        """Average/min/max per time bucket, from raw rows or (for >= 1 h buckets) hourly rows."""
        if bucket_s % 3600 == 0:
            columns = ", ".join(
                f"SUM({p}_sum) / SUM(samples), MIN({p}_min), MAX({p}_max)"
                for p in HOURLY_PREFIX.values()
            )
            sql = (
                f"SELECT CAST(hour / :bucket AS INTEGER) * :bucket AS t, SUM(samples), {columns}"
                " FROM readings_hourly WHERE device_id = :device AND hour >= :start"
                " AND hour < :end GROUP BY t ORDER BY t"
            )
        else:
            columns = ", ".join(f"AVG({m}), MIN({m}), MAX({m})" for m in HISTORY_METRICS)
            sql = (
                f"SELECT CAST(event_time / :bucket AS INTEGER) * :bucket AS t, COUNT(*), {columns}"
                " FROM readings WHERE device_id = :device AND event_time >= :start"
                " AND event_time < :end GROUP BY t ORDER BY t"
            )
        params = {"device": device_id, "start": start, "end": end, "bucket": bucket_s}
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()

        result = empty_history()
        for row in rows:
            result["t"].append(row[0])
            result["samples"].append(row[1])
            for i, metric in enumerate(HISTORY_METRICS):
                for j, stat in enumerate(("avg", "min", "max")):
                    value = row[2 + i * 3 + j]
                    result[metric][stat].append(None if value is None else round(value, 2))
        return result

    def growth(
        self, retention_days: int, hourly_retention_days: int, now: float | None = None
    ) -> dict[str, Any]:
        """The storage forecast at the pace of the last hour (index lookups only: cheap)."""
        now = now or time.time()
        with self._connect() as db:
            last_hour = db.execute(
                "SELECT COUNT(*) FROM readings WHERE event_time >= ?", (now - 3600,)
            ).fetchone()[0]
            oldest = db.execute("SELECT MIN(event_time) FROM readings").fetchone()[0]
            devices = db.execute(
                "SELECT COUNT(*) FROM devices WHERE last_seen >= ?", (now - 86_400,)
            ).fetchone()[0]
        return forecast(last_hour * 24, retention_days, hourly_retention_days, oldest, devices)

    def stats(self) -> dict[str, Any]:
        with self._connect() as db:
            messages, device_count = db.execute(
                "SELECT COALESCE(SUM(messages), 0), COUNT(*) FROM devices"
            ).fetchone()
            oldest, newest = db.execute(
                "SELECT MIN(event_time), MAX(event_time) FROM readings"
            ).fetchone()
            hours = db.execute("SELECT COUNT(*) FROM readings_hourly").fetchone()[0]
        size = sum(
            p.stat().st_size
            for p in (self.path, self.path.with_name(self.path.name + "-wal"))
            if p.exists()
        )
        return {
            "messages": messages,
            "devices": device_count,
            "hourly_rows": hours,
            "oldest": oldest,
            "newest": newest,
            "size_bytes": size,
        }


def forecast(
    per_day: float,
    retention_days: int,
    hourly_retention_days: int,
    oldest: float | None,
    devices: int,
) -> dict[str, Any]:
    """How big the database gets at `per_day` readings a day from `devices` boards. Each tier
    levels off once its retention window is full; a retention of 0 (forever) never does."""
    raw_per_day = per_day * BYTES_PER_READING
    hourly_per_day = devices * HOURLY_BYTES_PER_DEVICE_YEAR / 365
    result: dict[str, Any] = {
        "readings_per_day": round(per_day),
        "growth_bytes_per_day": round(raw_per_day + hourly_per_day),
        "retention_days": retention_days,
        "hourly_retention_days": hourly_retention_days,
        "raw_bytes": None,  # the ceiling of each tier
        "hourly_bytes": None,
        "levels_off_bytes": None,  # the ceiling of the whole file
        "raw_full_at": None,  # when the raw window is full (most of the ceiling)
    }
    if retention_days > 0:
        result["raw_bytes"] = round(raw_per_day * retention_days)
        result["raw_full_at"] = (oldest or time.time()) + retention_days * 86_400
    if hourly_retention_days > 0:
        result["hourly_bytes"] = round(hourly_per_day * hourly_retention_days)
    if retention_days > 0 and hourly_retention_days > 0:
        result["levels_off_bytes"] = result["raw_bytes"] + result["hourly_bytes"]
    return result


def reading_row(reading: Reading) -> dict[str, Any]:
    """Flatten a Reading into column values (timestamps as Unix seconds)."""
    row = reading.model_dump(include=set(READING_COLUMNS))
    row["event_time"] = reading.event_time.timestamp()
    row["received_at"] = reading.received_at.timestamp()
    row["ts"] = reading.ts.timestamp() if reading.ts else None
    row["dew_point_c"] = reading.dew_point_c
    return row


def record(row: sqlite3.Row) -> dict[str, Any]:
    """A stored row in the API's reading format (the fields of `Reading.to_record()`)."""
    return dict(row)
