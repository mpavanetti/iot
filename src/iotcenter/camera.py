"""The camera: a USB webcam streamed live to the dashboard, at full quality.

    webcam ──V4L2, MJPEG──▶ capture thread ──▶ newest frame ─┬─▶ /api/camera/stream.mjpg
                                                             ├─▶ /api/camera/snapshot.jpg
                                                             └─▶ vision.ActivityMonitor

Most webcams compress every frame to JPEG themselves (MJPEG). The capture thread asks for
that format and passes the camera's own JPEG bytes through untouched: nothing is decoded or
re-encoded, so the dashboard shows exactly what the sensor produced, at full resolution,
for almost no CPU. Every viewer gets the newest frame; a slow one skips frames rather than
falling behind. Frames only live in memory: IoT Center never writes one to disk.

Unplug the camera and the thread retries every few seconds, so it comes back by itself.
`--camera demo` streams a synthetic scene instead, to try the Camera tab without a webcam.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

try:
    import cv2
    import numpy as np
except ImportError as exc:  # pragma: no cover - depends on the installed extras
    raise ImportError("the camera needs OpenCV: pip install 'iotcenter[camera]'") from exc

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

from .zones import ZoneChange, ZoneSpec

if TYPE_CHECKING:
    from .recorder import Recorder
    from .vision import ActivityMonitor

log = logging.getLogger(__name__)

RETRY_S = 3.0  # between attempts to open a missing or failed camera
FAILED_READS = 3  # reads in a row without a frame before the camera counts as gone
DEMO = "demo"
BOUNDARY = "frame"
NO_STORE = {"Cache-Control": "no-store"}


class CameraError(Exception):
    """The camera cannot be opened, or stopped sending frames."""


@dataclass(frozen=True, slots=True)
class Frame:
    seq: int
    time: float  # Unix seconds, when it was captured
    jpeg: bytes


class FrameSource(Protocol):
    """Where frames come from: a V4L2 webcam, or the demo scene. Used by one thread."""

    width: int
    height: int
    format: str

    def open(self) -> None:
        """Start capturing; raises CameraError when the camera cannot be used."""

    def read(self) -> bytes | None:
        """The next frame as JPEG; b"" to skip a damaged one; None when none came."""

    def close(self) -> None: ...


class V4L2Source:
    """A UVC webcam on Linux, through OpenCV's V4L2 backend."""

    def __init__(self, device: str, width: int, height: int, fps: int) -> None:
        self.device = device
        self.width, self.height, self.fps = width, height, fps
        self.format = "MJPEG"
        self._capture: Any = None
        self._passthrough = True

    def open(self) -> None:
        if not os.path.exists(self.device):
            raise CameraError(f"{self.device} not found: is the camera plugged in?")
        capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not capture.isOpened():
            raise CameraError(f"cannot open {self.device}: in use, or no permission")
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, self.fps)
        fourcc = int(capture.get(cv2.CAP_PROP_FOURCC)).to_bytes(4, "little").decode("latin-1")
        # MJPEG: hand over the camera's JPEG bytes as they are. Anything else (raw YUYV from
        # an older webcam) is decoded by OpenCV and encoded to JPEG here instead.
        self._passthrough = fourcc == "MJPG" and capture.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        self.format = "MJPEG" if self._passthrough else f"{fourcc.strip()} as JPEG"
        self.width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._capture = capture

    def read(self) -> bytes | None:
        ok, frame = self._capture.read()
        if not ok or frame is None:
            return None
        if self._passthrough:
            data = frame.tobytes()
            return data if data[:2] == b"\xff\xd8" else b""  # every JPEG starts with SOI
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return jpeg.tobytes() if ok else b""

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class DemoSource:
    """A synthetic room, to try everything without a webcam: a ball crosses the right of the
    floor for 20 s of every minute, the lights go off for 6 s of every minute, and every 5
    minutes a puddle spreads on the left of the floor, stays 3 minutes and dries. Draw a
    floor zone at the lower left and a light zone on the red lid to see the zones work."""

    format = "JPEG (demo)"
    CYCLE_S = 60.0  # 0-20 s the ball crosses; 35-41 s the lights are off
    PUDDLE_S = 300.0  # 60-80 s it spreads, until 240 s it stays, by 260 s it has dried

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 15) -> None:
        self.width, self.height, self.fps = width, height, fps
        self._room: np.ndarray | None = None

    def open(self) -> None:
        self._room = demo_room(self.width, self.height)
        self._started = self._due = time.monotonic()

    def read(self) -> bytes:
        self._due += 1 / self.fps
        delay = self._due - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            self._due = time.monotonic()
        frame = demo_frame(self._room, time.monotonic() - self._started)
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return jpeg.tobytes() if ok else b""

    def close(self) -> None:
        pass


def demo_room(width: int, height: int) -> np.ndarray:
    """The still part of the demo scene: a wall, a floor and some boxes."""
    room = np.empty((height, width, 3), np.uint8)
    horizon = int(height * 0.62)
    wall = np.linspace(150, 120, horizon, dtype=np.float32)[:, None, None]
    floor = np.linspace(110, 165, height - horizon, dtype=np.float32)[:, None, None]
    room[:horizon] = (wall * np.array([1.0, 0.98, 0.93])).astype(np.uint8)
    room[horizon:] = (floor * np.array([0.92, 0.93, 0.95])).astype(np.uint8)
    boxes = [  # (left, top, right, bottom) as fractions of the picture, and a BGR color
        ((0.08, 0.37, 0.24, 0.67), (70, 72, 78)),
        ((0.30, 0.25, 0.40, 0.67), (150, 155, 160)),
        ((0.72, 0.36, 0.90, 0.51), (40, 40, 160)),
        ((0.72, 0.52, 0.90, 0.67), (35, 38, 42)),
    ]
    for (left, top, right, bottom), color in boxes:
        corner = (int(left * width), int(top * height))
        cv2.rectangle(room, corner, (int(right * width), int(bottom * height)), color, -1)
    at, scale, thickness = (int(width * 0.03), int(height * 0.08)), height / 900, height // 360
    font, grey = cv2.FONT_HERSHEY_SIMPLEX, (90, 90, 90)
    cv2.putText(room, "IoT Center demo camera", at, font, scale, grey, thickness, cv2.LINE_AA)
    return room


PUDDLE = (0.08, 0.76, 0.38, 0.95)  # where the demo puddle spreads: left, top, right, bottom


def demo_frame(room: np.ndarray, t: float) -> np.ndarray:
    height, width = room.shape[:2]
    phase = t % DemoSource.CYCLE_S
    frame = room.copy()
    size, darker = _puddle(t % DemoSource.PUDDLE_S)
    if size > 0:  # wet concrete: darker, with soft edges, spreading from the middle
        left, top, right, bottom = PUDDLE
        rows = slice(int(top * height), int(bottom * height))
        cols = slice(int(left * width), int(right * width))
        y, x = np.mgrid[
            -1 : 1 : (rows.stop - rows.start) * 1j, -1 : 1 : (cols.stop - cols.start) * 1j
        ]
        reach = np.hypot(x, y * 1.3) + 0.15 * np.sin(5 * np.arctan2(y, x))  # not quite round
        wet = np.clip((size - reach) * 4, 0, 1)[..., None]
        frame[rows, cols] = (frame[rows, cols] * (1 - darker * wet)).astype(np.uint8)
    if phase < 20:  # the ball crosses the right of the floor, then leaves
        x = _bounce(0.1 + 0.09 * phase + 0.37 * (t // DemoSource.CYCLE_S)) * (width * 0.4)
        y = _bounce(0.25 + 0.06 * phase) * (height * 0.26) + height * 0.66
        # dark, so it also stands out in grey (an orange ball is as bright as the floor)
        center = (int(x + width * 0.5), int(y))
        cv2.circle(frame, center, height // 12, (120, 60, 20), -1, cv2.LINE_AA)
    if 35.0 <= phase < 41.0:
        frame = cv2.convertScaleAbs(frame, alpha=0.12)  # lights off
    return frame


def _puddle(phase: float) -> tuple[float, float]:
    """The demo puddle's size (0-1.15) and how much darker it makes the floor."""
    if 60 <= phase < 80:
        return 1.15 * (phase - 60) / 20, 0.3
    if 80 <= phase < 240:
        return 1.15, 0.3
    if 240 <= phase < 260:
        return 1.15, 0.3 * (260 - phase) / 20
    return 0.0, 0.0


def _bounce(value: float) -> float:
    """A triangle wave between 0 and 1: back and forth, like a ball between two walls."""
    value %= 2.0
    return value if value <= 1.0 else 2.0 - value


def open_source(device: str, width: int, height: int, fps: int) -> FrameSource:
    return DemoSource() if device == DEMO else V4L2Source(device, width, height, fps)


class Camera:
    """Captures on a thread of its own (reads block); the event loop serves the frames."""

    def __init__(self, source: FrameSource, name: str = "Camera", device: str | None = None):
        self.source = source
        self.name = name
        self.device = device
        self.state = "starting"  # starting | streaming | unavailable | stopped
        self.error: str | None = None
        self.captured = 0
        self.opened = 0
        self.streaming_since: float | None = None
        self.viewers = 0
        self._latest: Frame | None = None
        self._times: deque[float] = deque(maxlen=120)  # capture times: the measured frame rate
        self._sizes: deque[int] = deque(maxlen=120)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._changed = asyncio.Event()
        self._closed = False

    @property
    def latest(self) -> Frame | None:
        return self._latest

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._closed = True
        self._stop.set()
        self._notify()  # ends the open streams
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5)
        self.state = "stopped"

    # --- the capture thread -------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.source.open()
            except Exception as exc:  # CameraError, or cv2.error from a driver
                self._down(str(exc))
                self._stop.wait(RETRY_S)
                continue
            self.opened += 1
            log.info(
                "Camera %s: %dx%d %s",
                self.device or self.name,
                self.source.width,
                self.source.height,
                self.source.format,
            )
            try:
                self._capture()
            except Exception as exc:
                self._down(str(exc))
            finally:
                self.source.close()
            self._stop.wait(RETRY_S)

    def _capture(self) -> None:
        failed = 0
        while not self._stop.is_set():
            jpeg = self.source.read()
            if jpeg is None:
                failed += 1
                if failed >= FAILED_READS:
                    raise CameraError("the camera stopped sending frames")
                continue
            failed = 0
            if jpeg:
                self._publish(jpeg)

    def _publish(self, jpeg: bytes) -> None:
        now = time.time()
        self.captured += 1
        self._latest = Frame(self.captured, now, jpeg)
        self._times.append(now)
        self._sizes.append(len(jpeg))
        if self.state != "streaming":
            self.state, self.error, self.streaming_since = "streaming", None, now
        with contextlib.suppress(RuntimeError):  # the event loop has closed: shutting down
            self._loop.call_soon_threadsafe(self._notify)

    def _down(self, error: str) -> None:
        if error != self.error:
            log.warning("Camera %s: %s", self.device or self.name, error)
        self.state, self.error, self.streaming_since = "unavailable", error, None

    # --- the event loop -----------------------------------------------------------------

    def _notify(self) -> None:
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def frames(self, max_fps: float | None = None) -> AsyncIterator[Frame]:
        """Each new frame, at most `max_fps` a second (the newest one when it is due)."""
        interval = 1 / max_fps if max_fps else 0.0
        last_seq, due = 0, 0.0
        while not self._closed:
            frame = self._latest
            if frame is None or frame.seq == last_seq or self.state != "streaming":
                await self._changed.wait()
                continue
            wait = due - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            last_seq, due = frame.seq, time.monotonic() + interval
            yield frame

    @contextmanager
    def viewing(self) -> Iterator[None]:
        self.viewers += 1
        try:
            yield
        finally:
            self.viewers -= 1

    def fps(self) -> float | None:
        """Frames a second over the last 3 seconds (webcams slow down in dim light)."""
        times = list(self._times)
        if len(times) < 2 or time.time() - times[-1] > 2:
            return None
        recent = [t for t in times if t >= times[-1] - 3]
        span = recent[-1] - recent[0]
        return (len(recent) - 1) / span if len(recent) > 1 and span > 0 else None

    def status(self) -> dict[str, Any]:
        frame, fps, sizes = self._latest, self.fps(), list(self._sizes)
        return {
            "name": self.name,
            "device": self.device,
            "state": self.state,
            "error": self.error,
            "width": self.source.width,
            "height": self.source.height,
            "format": self.source.format,
            "fps": round(fps, 1) if fps else None,
            "frame_bytes": round(sum(sizes) / len(sizes)) if sizes else None,
            "frames": self.captured,
            "reconnects": max(0, self.opened - 1),
            "viewers": self.viewers,
            "streaming_since": self.streaming_since,
            "last_frame_at": frame.time if frame else None,
        }


def camera_routes(
    camera: Camera, activity: ActivityMonitor | None, recorder: Recorder | None = None
) -> APIRouter:
    """The camera's part of the dashboard API (its live activity rides on /api/stream)."""
    router = APIRouter(prefix="/api/camera", tags=["camera"])
    part = f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n".encode()

    @router.get("")
    async def status() -> dict[str, Any]:
        """The camera's state and stream, and a summary of what it has noticed."""
        return {
            **camera.status(),
            "activity": activity.summary() if activity else None,
            "recording": recorder.status() if recorder else {"enabled": False},
        }

    @router.get("/activity")
    async def history() -> dict[str, Any]:
        """Motion and brightness over the last minutes (a point a second), and the events."""
        if activity is None:
            raise HTTPException(404, "activity detection is off")
        return activity.history()

    def monitor() -> ActivityMonitor:
        if activity is None:
            raise HTTPException(404, "activity detection is off")
        return activity

    @router.get("/zones")
    async def zones() -> dict[str, Any]:
        """The zones drawn on the picture, with what each one sees now."""
        return {"zones": monitor().analyzer.zones.summaries(time.time())}

    @router.post("/zones", status_code=201)
    async def add_zone(spec: ZoneSpec) -> dict[str, Any]:
        """A new zone: `area` (motion), `floor` (water) or `light` (a status light, a flame)."""
        try:
            zone = await asyncio.to_thread(monitor().add_zone, spec)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return zone.model_dump()

    @router.patch("/zones/{zone_id}")
    async def change_zone(zone_id: str, change: ZoneChange) -> dict[str, Any]:
        """Rename a zone, or switch it off (kept and drawn, not watched) and on again."""
        zone = await asyncio.to_thread(monitor().change_zone, zone_id, change)
        if zone is None:
            raise HTTPException(404, "no such zone")
        return zone.model_dump()

    @router.delete("/zones/{zone_id}", status_code=204)
    async def delete_zone(zone_id: str) -> Response:
        if not await asyncio.to_thread(monitor().delete_zone, zone_id):
            raise HTTPException(404, "no such zone")
        return Response(status_code=204)

    @router.post("/zones/{zone_id}/dry")
    async def mark_dry(zone_id: str) -> dict[str, Any]:
        """The floor in this zone is dry now: it learns the floor again from here."""
        if not monitor().reset_zone(zone_id):
            raise HTTPException(404, "no such floor zone")
        return {"id": zone_id, "state": "learning"}

    def recordings() -> Recorder:
        if recorder is None:
            raise HTTPException(404, "recording is off (IOT_CAMERA_RECORD)")
        return recorder

    @router.get("/recordings")
    async def list_recordings(
        since: float = 0.0,
        until: float | None = None,
        limit: int = Query(500, ge=1, le=5000),
    ) -> dict[str, Any]:
        """The motion clips (newest first) that started between `since` and `until`."""
        clips = recordings().clips(since, until if until is not None else float("inf"))
        return {**recordings().status(), "clips": clips[:limit]}

    @router.get("/recordings/{clip_id}.mp4")
    async def clip_video(clip_id: str) -> FileResponse:
        """A clip as H.264 MP4 (ranges supported, so a <video> can seek)."""
        return _file(recordings().path(clip_id, ".mp4"), "video/mp4")

    @router.get("/recordings/{clip_id}.jpg")
    async def clip_poster(clip_id: str) -> FileResponse:
        return _file(recordings().path(clip_id, ".jpg"), "image/jpeg")

    @router.delete("/recordings/{clip_id}", status_code=204)
    async def delete_clip(clip_id: str) -> Response:
        if not await asyncio.to_thread(recordings().delete, clip_id):
            raise HTTPException(404, "no such clip")
        return Response(status_code=204)

    @router.get("/stream.mjpg")
    async def stream(
        fps: float | None = Query(None, gt=0, le=60, description="at most; default: every frame"),
    ) -> StreamingResponse:
        """The live picture as MJPEG (multipart JPEG): an <img> plays it as it is."""

        async def parts() -> AsyncIterator[bytes]:
            with camera.viewing():
                async for frame in camera.frames(fps):
                    yield part % len(frame.jpeg) + frame.jpeg + b"\r\n"

        return StreamingResponse(
            parts(),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={**NO_STORE, "X-Accel-Buffering": "no"},  # proxies: do not buffer
        )

    @router.get("/snapshot.jpg")
    async def snapshot() -> Response:
        """The newest frame, at full resolution. Nothing is kept on the server."""
        frame = camera.latest
        if frame is None or camera.state != "streaming":
            raise HTTPException(503, camera.error or "no picture yet")
        return Response(frame.jpeg, media_type="image/jpeg", headers=NO_STORE)

    return router


def _file(path: Path | None, media_type: str) -> FileResponse:
    if path is None:
        raise HTTPException(404, "no such clip")
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"}
    )
