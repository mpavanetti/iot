"""IoT Center Lite: device ingestion, SQLite history and the dashboard in one process.

    Pico W ──TCP :1500──┐
                        ├─▶ parse_line() ─▶ SQLite ──────────▶ REST API ─┐
    Pico W ──USB serial─┘                 └─▶ LiveHub ─▶ SSE stream ─────┴─▶ dashboard

Everything runs on one asyncio event loop: the TCP server, the optional serial reader, a
retention task and the web server (uvicorn).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from ..api import create_app
from ..config import Settings
from ..hub import LiveHub
from ..ingest import IngestStats, LineProcessor, SerialIngest, TcpIngestServer
from ..protocol import Reading
from ..sysinfo import host_metrics
from .storage import Storage

log = logging.getLogger(__name__)


class LiteSource:
    """The dashboard's data source for the Lite edition (see `api.DataSource`)."""

    edition = "lite"

    def __init__(self, settings: Settings, storage: Storage, hub: LiveHub, stats: IngestStats):
        self.settings = settings
        self.storage = storage
        self.hub = hub
        self.stats = stats
        self.offline_after_s = settings.offline_after_s

    @property
    def ingest_port(self) -> int | None:
        return self.stats.tcp_port if self.stats.tcp_listening else None

    async def devices(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.storage.devices)

    async def recent(self, device_id: str, since: float) -> list[dict[str, Any]]:
        return await self.readings(device_id, since, time.time() + 60, 5_000)

    async def history(self, device_id: str, start: float, end: float, bucket_s: int) -> dict:
        return await asyncio.to_thread(self.storage.history, device_id, start, end, bucket_s)

    async def readings(self, device_id: str, start: float, end: float, limit: int) -> list[dict]:
        return await asyncio.to_thread(self.storage.readings, device_id, start, end, limit)

    async def status(self) -> dict[str, Any]:
        stats, settings = self.stats, self.settings
        db = await asyncio.to_thread(self.storage.stats)
        components = [
            {
                "id": "tcp",
                "name": "TCP listener",
                "role": "Receives NDJSON from Pico W boards over Wi-Fi",
                "status": _status(settings.tcp_enabled, stats.tcp_listening),
                "detail": f":{stats.tcp_port}" if stats.tcp_listening else "disabled",
                "metrics": {"open connections": stats.tcp_connections_open},
            },
            {
                "id": "usb",
                "name": "USB serial",
                "role": "Reads NDJSON from a board plugged in over USB",
                "status": _status(bool(settings.serial_port), stats.serial_connected),
                "detail": settings.serial_port or "enable with --serial /dev/ttyACM0",
                "metrics": {},
            },
            {
                "id": "ingest",
                "name": "Validation",
                "role": "Checks every line against the message contract",
                "status": "up",
                "detail": None,
                "note": f"Last rejected: {stats.last_error}" if stats.messages_invalid else None,
                "metrics": {
                    "accepted since start": stats.messages_ok,
                    "rejected since start": stats.messages_invalid,
                },
            },
            {
                "id": "sqlite",
                "name": "SQLite",
                "role": "Raw readings + hourly aggregates + device registry",
                "status": "up",
                "detail": settings.db_path.name,
                "metrics": {"messages stored": db["messages"], "size": db["size_bytes"]},
            },
            {
                "id": "dashboard",
                "name": "Dashboard",
                "role": "REST API + Server-Sent Events for live updates",
                "status": "up",
                "detail": None,
                "metrics": {"live viewers": self.hub.subscriber_count},
            },
        ]
        return {
            "edition": self.edition,
            "generated_at": time.time(),
            "components": components,
            "storage": {"title": "SQLite", "subtitle": str(settings.db_path), **db},
            "host": host_metrics(str(settings.db_path.resolve().parent)),
        }

    def links(self) -> list[dict[str, Any]]:
        return []


def _status(enabled: bool, healthy: bool) -> str:
    if not enabled:
        return "disabled"
    return "up" if healthy else "down"


def create_lite_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    storage = Storage(settings.db_path)
    hub = LiveHub()
    stats = IngestStats()
    write_lock = asyncio.Lock()  # one writer at a time keeps the device counters exact

    async def on_reading(reading: Reading) -> None:
        async with write_lock:
            stored = await asyncio.to_thread(storage.insert, reading)
        if stored:  # duplicates were already shown once
            hub.publish(reading.to_record())

    processor = LineProcessor(on_reading, None, stats)
    source = LiteSource(settings, storage, hub, stats)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        readers: list[TcpIngestServer | SerialIngest] = []
        if settings.tcp_enabled:
            readers.append(TcpIngestServer(settings.tcp_host, settings.tcp_port, processor))
        if settings.serial_port:
            readers.append(SerialIngest(settings.serial_port, settings.serial_baud, processor))
        for reader in readers:
            await reader.start()
        app.state.readers = readers
        retention = asyncio.create_task(_retention_loop(storage, settings.retention_days))
        log.info("Dashboard on http://%s:%s", settings.http_host, settings.http_port)
        try:
            yield
        finally:
            hub.close()  # ends open SSE streams so shutdown is quick
            retention.cancel()
            for reader in readers:
                await reader.stop()

    app = create_app(source, lifespan=lifespan)
    app.state.source = source
    return app


async def _retention_loop(storage: Storage, retention_days: int) -> None:
    """Hourly: drop raw readings past the retention window (aggregates are kept)."""
    if retention_days <= 0:
        return
    while True:
        cutoff = time.time() - retention_days * 86_400
        deleted = await asyncio.to_thread(storage.purge, cutoff)
        if deleted:
            log.info("Retention: removed %s readings older than %s days", deleted, retention_days)
        await asyncio.sleep(3600)
