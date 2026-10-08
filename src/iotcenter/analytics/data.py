"""Queries for the analytics app. Each result is cached for a minute (`ttl`), so moving a
filter back and forth never hits PostgreSQL twice, and the data stays fresh.

Tables (written by Spark Structured Streaming, see platform/postgres/init.sql):
  readings         one row per reading
  readings_hourly  per device and hour: samples, avg/min/max per metric
  stream_progress  last micro-batch of each streaming query
  devices          view: one row per board
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import cache

import pandas as pd
import streamlit as st

from ..config import Settings

TTL = 60

RANGES = {
    "Last 24 hours": timedelta(hours=24),
    "Last 7 days": timedelta(days=7),
    "Last 30 days": timedelta(days=30),
    "Last 90 days": timedelta(days=90),
    "All time": None,
}

METRICS = {  # readings column -> (hourly prefix, label, unit)
    "temperature_c": ("temperature", "Temperature", "°C"),
    "humidity_pct": ("humidity", "Relative humidity", "%"),
    "pressure_hpa": ("pressure", "Barometric pressure", "hPa"),
    "dew_point_c": ("dew_point", "Dew point", "°C"),
}


@cache
def settings() -> Settings:
    return Settings()


def connection():
    # SQLAlchemy URL for psycopg 3; st.connection pools and caches it for us.
    url = settings().database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return st.connection("iot", type="sql", url=url)


def query(sql: str, **params) -> pd.DataFrame:
    return connection().query(sql, params=params, ttl=TTL)


def since(range_label: str) -> datetime:
    window = RANGES[range_label]
    return datetime(2000, 1, 1, tzinfo=UTC) if window is None else datetime.now(UTC) - window


def local(frame: pd.DataFrame, *columns: str) -> pd.DataFrame:
    """Convert UTC timestamp columns to the display time zone (IOT_TIMEZONE)."""
    for column in columns:
        frame[column] = pd.to_datetime(frame[column], utc=True).dt.tz_convert(settings().timezone)
    return frame


def devices() -> pd.DataFrame:
    frame = query(
        "SELECT device_id, COALESCE(name, device_id) AS name, first_seen, last_seen, messages,"
        " ip, firmware, source FROM devices ORDER BY first_seen, device_id"
    )
    return local(frame, "first_seen", "last_seen")


def hourly(device_ids: list[str], start: datetime) -> pd.DataFrame:
    frame = query(
        "SELECT * FROM readings_hourly WHERE device_id = ANY(:devices) AND hour >= :start"
        " ORDER BY hour",
        devices=device_ids,
        start=start,
    )
    return local(frame, "hour")


def readings(device_ids: list[str], start: datetime, limit: int) -> pd.DataFrame:
    frame = query(
        "SELECT event_time, device_id, name, temperature_c, humidity_pct, pressure_hpa,"
        " dew_point_c, cpu_temp_c, wifi_rssi_dbm, mem_free_bytes, uptime_s, seq, source,"
        " received_at FROM readings WHERE device_id = ANY(:devices) AND event_time >= :start"
        " ORDER BY event_time DESC LIMIT :limit",
        devices=device_ids,
        start=start,
        limit=limit,
    )
    return local(frame, "event_time", "received_at")


def quality(device_ids: list[str], start: datetime) -> pd.DataFrame:
    """Per device: readings, sequence gaps (lost messages), restarts and delivery delay."""
    return query(
        """
        SELECT device_id,
               count(*)                                                    AS readings,
               sum(CASE WHEN seq > prev_seq + 1 THEN seq - prev_seq - 1 ELSE 0 END) AS missing,
               sum(CASE WHEN seq <= prev_seq THEN 1 ELSE 0 END)            AS restarts,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY delay_s)
                   FILTER (WHERE delay_s <= 60)                            AS delay_p50_s,
               sum(CASE WHEN delay_s > 60 THEN 1 ELSE 0 END)               AS late_readings,
               min(event_time)                                             AS first_reading,
               max(event_time)                                             AS last_reading
        FROM (
            SELECT device_id, seq, event_time,
                   lag(seq) OVER (PARTITION BY device_id ORDER BY event_time, seq) AS prev_seq,
                   extract(epoch FROM received_at - COALESCE(ts, received_at))      AS delay_s
            FROM readings
            WHERE device_id = ANY(:devices) AND event_time >= :start
        ) ordered
        GROUP BY device_id
        ORDER BY device_id
        """,
        devices=device_ids,
        start=start,
    )


def progress() -> pd.DataFrame:
    return query(
        "SELECT query_name, batch_id, input_rows, rows_per_second, watermark, updated_at,"
        " extract(epoch FROM now() - updated_at) AS age_s FROM stream_progress"
        " ORDER BY query_name"
    )
