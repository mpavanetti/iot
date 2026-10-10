"""IoT Center Platform web app: the same dashboard as Lite, fed by the platform.

    Kafka  iot.readings ──▶ KafkaLive (one consumer) ──▶ LiveHub ──▶ SSE ──▶ browser   live
    PostgreSQL (written by Spark) ─────────────────────▶ REST API ─────────▶ browser   history

The Pipeline page asks every service for its health: gateway, Kafka, Spark, PostgreSQL and
Streamlit, so the dashboard doubles as a live picture of the architecture.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from ..api import create_app
from ..config import Settings
from ..hub import LiveHub
from ..sysinfo import host_metrics
from .kafka_live import KafkaLive
from .postgres import Database

log = logging.getLogger(__name__)

SPARK_APP = "iot-stream-readings"
STALE_AFTER_S = 120  # no Spark progress for this long while data flows -> degraded


class PlatformSource:
    """The dashboard's data source for the platform edition (see `api.DataSource`)."""

    edition = "platform"

    def __init__(self, settings: Settings, hub: LiveHub, kafka: KafkaLive, db: Database):
        self.settings = settings
        self.hub = hub
        self.kafka = kafka
        self.db = db
        self.offline_after_s = settings.offline_after_s
        self.altitude_m = settings.altitude_m
        self.ingest_port = settings.tcp_port
        self.http = httpx.AsyncClient(timeout=2.0)

    async def devices(self) -> list[dict[str, Any]]:
        try:
            rows = await self.db.devices()
        except Exception as exc:  # PostgreSQL down: live data alone still works
            log.warning("Device registry unavailable: %s", exc)
            rows = []
        devices = {row["device_id"]: row for row in rows}
        latest = self.hub.latest()
        for device_id, live in self.kafka.devices.items():  # Kafka is fresher than Spark
            row = devices.setdefault(
                device_id,
                {
                    "device_id": device_id,
                    "first_seen": live["first_seen"],
                    "messages": 0,
                    "dropped": None,
                    "restarts": None,
                    "latest": None,
                },
            )
            reading = latest.get(device_id)
            if reading and (
                not row["latest"] or reading["event_time"] >= row["latest"]["event_time"]
            ):
                row["latest"] = reading
                row.update({k: reading.get(k) for k in ("name", "ip", "firmware", "source")})
            row["last_seen"] = max(row.get("last_seen") or 0.0, live["last_seen"])
            row["messages"] = max(row["messages"] or 0, live["messages"])
        return list(devices.values())

    async def recent(self, device_id: str, since: float) -> list[dict[str, Any]]:
        readings = self.hub.recent(device_id, since)  # arrival order
        if readings:
            return sorted(readings, key=lambda reading: reading["event_time"])
        return await self.db.readings(device_id, since, time.time() + 60, 5_000)

    async def history(self, device_id: str, start: float, end: float, bucket_s: int) -> dict:
        return await self.db.history(device_id, start, end, bucket_s)

    async def readings(self, device_id: str, start: float, end: float, limit: int) -> list[dict]:
        return await self.db.readings(device_id, start, end, limit)

    def links(self) -> list[dict[str, Any]]:
        s = self.settings
        return [
            {"label": "Analytics", "port": s.analytics_public_port, "title": "Streamlit analytics"},
            {"label": "Spark", "port": s.spark_public_port, "title": "Spark master UI"},
        ]

    # --- pipeline health ------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        s = self.settings
        gateway, spark, analytics, db_stats, progress = await asyncio.gather(
            self._json(f"{s.gateway_url}/stats"),
            self._json(f"{s.spark_master_url}/json/"),
            self._text(f"{s.analytics_url}/_stcore/health"),
            self._safe(self.db.stats()),
            self._safe(self.db.progress()),
        )
        components = [
            self._gateway(gateway),
            self._kafka(),
            self._spark(spark, progress or {}),
            self._postgres(db_stats),
            {
                "id": "analytics",
                "name": "Streamlit",
                "role": "Historical analytics on top of PostgreSQL",
                "status": "up" if analytics == "ok" else "down",
                "detail": None,
                "metrics": {},
                "links": [{"label": "Open analytics", "port": s.analytics_public_port}],
            },
            {
                "id": "dashboard",
                "name": "Dashboard",
                "role": "REST API + Server-Sent Events (this page)",
                "status": "up",
                "detail": None,
                "metrics": {"live viewers": self.hub.subscriber_count},
            },
        ]
        storage = {"title": "PostgreSQL", "subtitle": "Tables written by Spark", **(db_stats or {})}
        return {
            "edition": self.edition,
            "generated_at": time.time(),
            "components": components,
            "storage": storage if db_stats else None,
            "host": host_metrics(),
        }

    def _gateway(self, stats: dict | None) -> dict[str, Any]:
        component = {
            "id": "gateway",
            "name": "Gateway",
            "role": "Validates device lines, publishes to Kafka",
            "status": "down",
            "detail": "unreachable",
            "metrics": {},
        }
        if stats:
            component.update(
                status="up" if stats["tcp_listening"] else "down",
                detail=f"tcp :{stats['tcp_port']}",
                metrics={
                    "open connections": stats["tcp_connections_open"],
                    "accepted since start": stats["messages_ok"],
                    "rejected → DLQ": stats["messages_invalid"],
                    "Kafka delivery failures": stats["failed"],
                },
            )
            if stats["failed"]:
                component.update(status="degraded", last_error=stats["last_failure"])
        return component

    def _kafka(self) -> dict[str, Any]:
        totals = self.kafka.totals()
        component = {
            "id": "kafka",
            "name": "Kafka",
            "role": "Durable log of every reading (7 days)",
            "status": "up" if self.kafka.connected else "down",
            "detail": f"topic {self.kafka.topic}",
            "metrics": {
                "messages produced": totals[self.kafka.topic],
                "dead letters": totals[self.kafka.dlq_topic],
            },
        }
        if not self.kafka.connected:
            component["last_error"] = self.kafka.last_error
        elif self.kafka.last_dead_letter:
            component["note"] = f"Last dead letter: {self.kafka.last_dead_letter.get('error')}"
        return component

    def _spark(self, master: dict | None, progress: dict[str, dict]) -> dict[str, Any]:
        s = self.settings
        component = {
            "id": "spark",
            "name": "Spark Structured Streaming",
            "role": "Parses, validates, aggregates hourly; writes PostgreSQL",
            "status": "down",
            "detail": "Spark master unreachable",
            "metrics": {},
            "links": [
                {"label": "Spark master", "port": s.spark_public_port},
                {"label": "Streaming job", "port": s.spark_app_public_port},
            ],
        }
        if master is None:
            return component
        apps = [a for a in master.get("activeapps", []) if a.get("name") == SPARK_APP]
        workers = master.get("aliveworkers", 0)
        readings = progress.get("readings")
        component["detail"] = f"{workers} worker(s) · job {'running' if apps else 'not running'}"
        if readings:
            component["metrics"] = {
                "last batch age": readings["age_s"],
                "rows in last batch": readings["input_rows"],
                "batches": readings["batch_id"] + 1,
            }
        if apps and workers:
            fresh = readings is not None and readings["age_s"] < STALE_AFTER_S
            component["status"] = "up" if fresh else "degraded"
        return component

    def _postgres(self, stats: dict | None) -> dict[str, Any]:
        if stats is None:
            return {
                "id": "postgres",
                "name": "PostgreSQL",
                "role": "Serving layer: raw readings + hourly aggregates",
                "status": "down",
                "detail": "unreachable",
                "metrics": {},
            }
        return {
            "id": "postgres",
            "name": "PostgreSQL",
            "role": "Serving layer: raw readings + hourly aggregates",
            "status": "up",
            "detail": "readings · readings_hourly",
            "metrics": {
                "readings": stats["messages"],
                "hourly rows": stats["hourly_rows"],
                "size": stats["size_bytes"],
            },
        }

    async def _json(self, url: str) -> dict | None:
        try:
            response = await self.http.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    async def _text(self, url: str) -> str | None:
        try:
            response = await self.http.get(url)
            return response.text.strip() if response.is_success else None
        except httpx.HTTPError:
            return None

    @staticmethod
    async def _safe(awaitable: Any) -> Any:
        try:
            return await awaitable
        except Exception as exc:
            log.debug("Status query failed: %s", exc)
            return None


async def retention_loop(db: Database, retention_days: int, hourly_days: int) -> None:
    """Hourly: drop readings and hourly aggregates past their retention windows, so the
    PostgreSQL tables level off instead of growing forever (the same policy as Lite)."""
    if retention_days <= 0 and hourly_days <= 0:
        return
    while True:
        try:
            raw, hourly = await db.purge(retention_days, hourly_days)
        except Exception as exc:  # PostgreSQL still starting, or down: try again soon
            log.warning("Retention: purge failed, retrying in a minute: %s", exc)
            await asyncio.sleep(60)
            continue
        if raw or hourly:
            log.info(
                "Retention: removed %s readings (> %s days) and %s hourly rows (> %s days)",
                raw,
                retention_days,
                hourly,
                hourly_days,
            )
        await asyncio.sleep(3600)


def create_web_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    hub = LiveHub()
    kafka = KafkaLive(
        settings.kafka_bootstrap,
        settings.kafka_topic,
        settings.kafka_dlq_topic,
        hub,
        settings.replay_messages,
    )
    db = Database(settings.database_url)
    source = PlatformSource(settings, hub, kafka, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await db.open()
        await kafka.start()
        retention = asyncio.create_task(
            retention_loop(db, settings.retention_days, settings.hourly_retention_days)
        )
        log.info("Dashboard on http://%s:%s", settings.http_host, settings.http_port)
        try:
            yield
        finally:
            retention.cancel()
            hub.close()
            await kafka.stop()
            await db.close()
            await source.http.aclose()

    app = create_app(source, lifespan=lifespan)
    app.state.source = source
    return app
