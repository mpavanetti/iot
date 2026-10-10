"""The HTTP API behind the dashboard, shared by both editions.

The browser only talks to these endpoints. Each edition plugs in a `DataSource`:

  * Lite      SQLite for history, its own LiveHub for live readings
  * Platform  PostgreSQL (written by Spark) for history, a Kafka consumer for live readings

so the very same dashboard runs unchanged on top of either pipeline. A camera, when there is
one (`camera.py`), adds its own routes under /api/camera, and a microphone (`microphone.py`)
under /api/sound.
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import AsyncIterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles

from . import __version__
from .hub import CLOSED, Event, LiveHub
from .insights import BUCKET_S, TENDENCY_HOURS, summarize

if TYPE_CHECKING:  # the camera needs OpenCV, an optional extra
    from .camera import Camera
    from .microphone import Microphone
    from .recorder import Recorder
    from .vision import ActivityMonitor

DASHBOARD_DIR = Path(__file__).parent / "dashboard"

# Range -> (window, bucket) in seconds. Buckets keep each chart at ~170-360 points.
# Buckets of an hour or more are answered from the hourly aggregates table.
RANGES: dict[str, tuple[int, int]] = {
    "1h": (3_600, 15),
    "6h": (21_600, 60),
    "24h": (86_400, 300),
    "7d": (604_800, 3_600),
    "30d": (2_592_000, 14_400),
    "1y": (31_536_000, 86_400),  # daily points, from the hourly aggregates
}
LIVE_WINDOW_S = 15 * 60
HISTORY_METRICS = ("temperature_c", "humidity_pct", "pressure_hpa", "dew_point_c")
EXPORT_COLUMNS = (
    "event_time",
    "device_id",
    "name",
    "seq",
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "dew_point_c",
    "cpu_temp_c",
    "wifi_rssi_dbm",
    "mem_free_bytes",
    "uptime_s",
    "source",
)
EXPORT_LIMIT = 500_000


class DataSource(Protocol):
    """What an edition must provide for the dashboard. Timestamps are Unix seconds."""

    edition: str
    hub: LiveHub
    offline_after_s: float
    ingest_port: int | None  # where devices connect (shown in the "waiting for data" hint)
    altitude_m: float | None  # of the sensors, for sea-level pressure (None: not shown)

    async def devices(self) -> list[dict[str, Any]]:
        """One row per device: ids, first/last seen, counters and its `latest` reading."""

    async def recent(self, device_id: str, since: float) -> list[dict[str, Any]]:
        """Raw readings (as `Reading.to_record()` dicts) newer than `since`, oldest first."""

    async def history(
        self, device_id: str, start: float, end: float, bucket_s: int
    ) -> dict[str, Any]:
        """Columnar aggregates: {"t": [...], "samples": [...], "<metric>": {avg, min, max}}."""

    async def readings(
        self, device_id: str, start: float, end: float, limit: int
    ) -> list[dict[str, Any]]:
        """Raw readings for the CSV export, oldest first."""

    async def status(self) -> dict[str, Any]:
        """Pipeline components (in data-flow order) with their health, plus host metrics."""

    def links(self) -> list[dict[str, Any]]:
        """Other UIs to link from the header (the browser resolves the host)."""


def create_app(
    source: DataSource,
    *,
    lifespan: Any = None,
    camera: Camera | None = None,
    activity: ActivityMonitor | None = None,
    microphone: Microphone | None = None,
    recorder: Recorder | None = None,
) -> FastAPI:
    app = FastAPI(
        title=f"IoT Center {source.edition.title()}",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/info")
    async def info() -> dict[str, Any]:
        return {
            "edition": source.edition,
            "version": __version__,
            "links": source.links(),
            "ingest_port": source.ingest_port,
            "offline_after_s": source.offline_after_s,
            "altitude_m": source.altitude_m,
            "live_window_s": LIVE_WINDOW_S,
            "ranges": list(RANGES),
            "camera": {"name": camera.name, "recording": recorder is not None} if camera else None,
            "microphone": microphone is not None,
        }

    @app.get("/api/devices")
    async def devices() -> list[dict[str, Any]]:
        now = time.time()
        rows = await source.devices()
        for row in rows:
            last_seen = row.get("last_seen")
            row["online"] = last_seen is not None and now - last_seen <= source.offline_after_s
        # Oldest first and stable over time, so the dashboard's device colors never shuffle.
        rows.sort(key=lambda row: (row.get("first_seen") or 0, row["device_id"]))
        return rows

    @app.get("/api/readings/recent")
    async def recent(device_id: str, minutes: int = Query(15, ge=1, le=60)) -> dict[str, Any]:
        readings = await source.recent(device_id, time.time() - minutes * 60)
        return {"device_id": device_id, "readings": readings}

    @app.get("/api/readings/history")
    async def history(device_id: str, range_: str = Query("24h", alias="range")) -> dict:
        start, end, bucket = _window(range_)
        data = await source.history(device_id, start, end, bucket)
        return {
            "device_id": device_id,
            "range": range_,
            "start": start,
            "end": end,
            "bucket_s": bucket,
            **data,
        }

    @app.get("/api/insights")
    async def insights(device_id: str) -> dict[str, Any]:
        """Pressure tendency over 3 hours, sea-level pressure and indoor comfort."""
        end = time.time()
        start = (end - TENDENCY_HOURS * 3600 - 2 * BUCKET_S) // BUCKET_S * BUCKET_S
        history = await source.history(device_id, start, end, BUCKET_S)
        return {"device_id": device_id, **summarize(history, source.altitude_m)}

    @app.get("/api/readings/export.csv")
    async def export(device_id: str, range_: str = Query("24h", alias="range")) -> Response:
        start, end, _ = _window(range_)
        rows = await source.readings(device_id, start, end, EXPORT_LIMIT)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        filename = f"{device_id}-{range_}.csv"
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return await source.status()

    @app.get("/api/stream", response_class=EventSourceResponse)
    async def stream() -> AsyncIterable[ServerSentEvent]:
        """Live readings as Server-Sent Events (the browser's EventSource reconnects itself),
        plus `camera` and `sound` events with what a camera and a microphone notice."""
        async with source.hub.subscribe() as queue:
            yield ServerSentEvent(event="hello", data={"edition": source.edition}, retry=3000)
            while (item := await queue.get()) is not CLOSED:
                if isinstance(item, Event):
                    yield ServerSentEvent(event=item.name, data=item.data)
                else:
                    yield ServerSentEvent(event="reading", data=item)

    if camera is not None:
        from .camera import camera_routes

        app.include_router(camera_routes(camera, activity, recorder))
    if microphone is not None:
        from .microphone import microphone_routes

        app.include_router(microphone_routes(microphone))

    app.mount("/", DashboardFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
    return app


class DashboardFiles(StaticFiles):
    """Static files that browsers revalidate (cheap 304s), so upgrades show up immediately."""

    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _window(range_: str) -> tuple[float, float, int]:
    if range_ not in RANGES:
        raise HTTPException(400, f"range must be one of {', '.join(RANGES)}")
    window, bucket = RANGES[range_]
    end = time.time()
    start = (end - window) // bucket * bucket  # align so buckets start on round times
    return start, end, bucket


def empty_history() -> dict[str, Any]:
    return {
        "t": [],
        "samples": [],
        **{m: {"avg": [], "min": [], "max": []} for m in HISTORY_METRICS},
    }
