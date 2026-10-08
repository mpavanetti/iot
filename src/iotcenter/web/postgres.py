"""History for the platform dashboard, read from the tables Spark Structured Streaming writes.

Timestamps leave PostgreSQL as Unix seconds, the API's format. Short ranges are bucketed
from raw `readings` with date_bin(); hour-sized buckets come from `readings_hourly`.
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool

from ..api import HISTORY_METRICS, empty_history

EPOCH = "TIMESTAMPTZ 'epoch'"

READING = """
SELECT device_id, name, seq, source, temperature_c, humidity_pct, pressure_hpa, dew_point_c,
       cpu_temp_c, mem_free_bytes, mem_alloc_bytes, storage_free_kb, cpu_freq_mhz, uptime_s,
       wifi_rssi_dbm, ip, firmware,
       extract(epoch FROM event_time)::float8  AS event_time,
       extract(epoch FROM ts)::float8          AS ts,
       extract(epoch FROM received_at)::float8 AS received_at
FROM readings
"""

DEVICES = f"""
SELECT h.device_id, h.messages, extract(epoch FROM h.first_hour)::float8 AS first_seen, latest.*
FROM (
    SELECT device_id, sum(samples)::bigint AS messages, min(hour) AS first_hour
    FROM readings_hourly GROUP BY device_id
) h
CROSS JOIN LATERAL (
    {READING} WHERE readings.device_id = h.device_id ORDER BY event_time DESC LIMIT 1
) latest
"""

READINGS = f"""
{READING}
WHERE device_id = %(device)s AND event_time >= to_timestamp(%(start)s)
  AND event_time < to_timestamp(%(end)s)
ORDER BY event_time LIMIT %(limit)s
"""

# HISTORY_METRICS column -> readings_hourly prefix
HOURLY_PREFIX = {
    "temperature_c": "temperature",
    "humidity_pct": "humidity",
    "pressure_hpa": "pressure",
    "dew_point_c": "dew_point",
}

HISTORY_RAW = f"""
SELECT extract(epoch FROM date_bin(make_interval(secs => %(bucket)s), event_time, {EPOCH}))::float8,
       count(*)::int,
       {", ".join(f"avg({m}), min({m}), max({m})" for m in HISTORY_METRICS)}
FROM readings
WHERE device_id = %(device)s AND event_time >= to_timestamp(%(start)s)
  AND event_time < to_timestamp(%(end)s)
GROUP BY 1 ORDER BY 1
"""

HISTORY_HOURLY = f"""
SELECT extract(epoch FROM date_bin(make_interval(secs => %(bucket)s), hour, {EPOCH}))::float8,
       sum(samples)::int,
       {
    ", ".join(
        f"sum({p}_avg * samples) / sum(samples), min({p}_min), max({p}_max)"
        for p in HOURLY_PREFIX.values()
    )
}
FROM readings_hourly
WHERE device_id = %(device)s AND hour >= to_timestamp(%(start)s) AND hour < to_timestamp(%(end)s)
GROUP BY 1 ORDER BY 1
"""

STATS = """
SELECT (SELECT CASE WHEN c.reltuples < 50000 THEN (SELECT count(*) FROM readings)
                    ELSE c.reltuples::bigint END
        FROM pg_class c WHERE c.oid = 'readings'::regclass)               AS messages,
       (SELECT count(DISTINCT device_id) FROM readings_hourly)             AS devices,
       (SELECT count(*) FROM readings_hourly)                              AS hourly_rows,
       (SELECT extract(epoch FROM min(event_time))::float8 FROM readings)  AS oldest,
       (SELECT extract(epoch FROM max(event_time))::float8 FROM readings)  AS newest,
       pg_database_size(current_database())                                AS size_bytes
"""

PROGRESS = """
SELECT query_name, batch_id, input_rows, rows_per_second,
       extract(epoch FROM now() - updated_at)::float8 AS age_s,
       extract(epoch FROM watermark)::float8          AS watermark
FROM stream_progress
"""


class Database:
    def __init__(self, url: str) -> None:
        self.pool = AsyncConnectionPool(
            url,
            min_size=1,
            max_size=4,
            open=False,
            timeout=5,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open(wait=False)  # PostgreSQL may still be starting: don't block

    async def close(self) -> None:
        await self.pool.close()

    async def fetch(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall()

    async def devices(self) -> list[dict[str, Any]]:
        devices = []
        for row in await self.fetch(DEVICES):
            latest = {k: v for k, v in row.items() if k not in ("messages", "first_seen")}
            devices.append(
                {
                    "device_id": row["device_id"],
                    "name": row["name"],
                    "first_seen": row["first_seen"],
                    "last_seen": row["received_at"],
                    "messages": row["messages"],
                    "dropped": None,  # sequence gaps are analysed in Streamlit (Data quality)
                    "restarts": None,
                    "ip": row["ip"],
                    "firmware": row["firmware"],
                    "source": row["source"],
                    "latest": latest,
                }
            )
        return devices

    async def readings(self, device_id: str, start: float, end: float, limit: int) -> list[dict]:
        params = {"device": device_id, "start": start, "end": end, "limit": limit}
        return await self.fetch(READINGS, params)

    async def history(self, device_id: str, start: float, end: float, bucket_s: int) -> dict:
        sql = HISTORY_HOURLY if bucket_s % 3600 == 0 else HISTORY_RAW
        params = {"device": device_id, "start": start, "end": end, "bucket": bucket_s}
        result = empty_history()
        async with self.pool.connection() as conn:
            cursor = conn.cursor(row_factory=tuple_row)  # positional: columns follow the SQL
            rows = await (await cursor.execute(sql, params)).fetchall()
        for row in rows:
            result["t"].append(row[0])
            result["samples"].append(row[1])
            for i, metric in enumerate(HISTORY_METRICS):
                for j, stat in enumerate(("avg", "min", "max")):
                    value = row[2 + i * 3 + j]
                    result[metric][stat].append(None if value is None else round(value, 2))
        return result

    async def stats(self) -> dict[str, Any]:
        return (await self.fetch(STATS))[0]

    async def progress(self) -> dict[str, dict[str, Any]]:
        return {row["query_name"]: row for row in await self.fetch(PROGRESS)}
