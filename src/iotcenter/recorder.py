"""Recordings: a short video of each motion event, kept for a while on this machine's disk.

    Camera ──10 frames a second──▶ the last PRE_S seconds, in memory
                                        │ motion starts (vision.SceneAnalyzer)
                                        ▼
                    writer thread: JPEG ─▶ 1280×720 ─▶ H.264 ─▶ data/recordings/<day>/<id>.mp4

A clip starts PRE_S before the motion and ends POST_S after it, so it shows what led up to it
and how it ended. A clip stops at MAX_S; motion that goes on continues in the next clip. Each
clip is an MP4 (H.264, plays in any browser), a poster JPEG and a small JSON (when, how long,
the motion's peak and the zones it touched). The live view keeps the camera's full quality;
clips are a review copy, 1280×720 at 10 fps, a few MB a minute.

Clips are deleted after IOT_CAMERA_RECORD_DAYS (30), or sooner, oldest first, when they take
more than IOT_CAMERA_RECORD_MAX_GB. They live next to the database (data/ locally, the
lite-data volume in Docker), so they are never in git.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import queue
import re
import secrets
import shutil
import threading
import time
from collections import deque
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

import av
import cv2
import numpy as np

if TYPE_CHECKING:
    from .camera import Camera
    from .vision import ActivityMonitor

log = logging.getLogger(__name__)

FPS = 10
PRE_S = 3.0  # before the motion started
POST_S = 3.0  # after it ended
MAX_S = 60.0  # longer motion continues in a new clip
SIZE = (1280, 720)  # at most; smaller cameras are kept as they are
CRF = "26"  # H.264 quality: lower is better and bigger (18 is close to the original)
POSTER_WIDTH = 480
RETENTION_EVERY_S = 3600
QUEUE_FRAMES = 200  # 20 s of frames waiting for the encoder before the newest are dropped
CLIP_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


class Recorder:
    """Watches the camera's frames and the motion state; writes clips on a thread of its own."""

    def __init__(
        self,
        camera: Camera,
        activity: ActivityMonitor,
        folder: Path,
        days: int = 30,
        max_gb: float = 20.0,
    ) -> None:
        self.camera = camera
        self.activity = activity
        self.folder = folder
        self.days = days
        self.max_bytes = int(max_gb * 1e9)
        self.recording: dict[str, Any] | None = None  # the clip being recorded
        self.dropped = 0
        self._index: dict[str, dict[str, Any]] = {}  # finished clips, by id
        self._recent: deque[tuple[float, bytes]] = deque(maxlen=int(PRE_S * FPS))
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=QUEUE_FRAMES)
        self._thread: threading.Thread | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._until = 0.0

    async def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        for leftover in self.folder.rglob("*.part"):  # a clip cut short by a restart
            leftover.unlink(missing_ok=True)
        self._index = await asyncio.to_thread(self._scan)
        self._thread = threading.Thread(target=self._write, name="recorder", daemon=True)
        self._thread.start()
        self._tasks = [
            asyncio.create_task(self._watch(), name="recorder"),
            asyncio.create_task(self._retention(), name="recorder-retention"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._finish()
        self._queue.put(("quit", None))
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 10)

    # --- deciding what to record (the event loop) ------------------------------------------

    async def _watch(self) -> None:
        async for frame in self.camera.frames(FPS):
            self.update(frame.time, frame.jpeg, self.activity.analyzer.moving)

    def update(self, t: float, jpeg: bytes, moving: bool) -> None:
        """One frame, and whether something is moving: start, continue or end a clip."""
        if moving:
            self._until = t + POST_S
        clip = self.recording
        if clip is not None and (t > self._until or t - clip["start"] >= MAX_S):
            self._finish()
            clip = None
        if clip is None and moving:
            clip = self._begin(t)
        if clip is None:
            self._recent.append((t, jpeg))
            return
        self._send("frame", (t, jpeg))
        clip["end"] = t

    def _begin(self, t: float) -> dict[str, Any]:
        start = self._recent[0][0] if self._recent else t
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(start))
        clip = {"id": f"{stamp}-{secrets.token_hex(2)}", "start": start, "end": t}
        self.recording = clip
        self._send("begin", dict(clip))
        while self._recent:
            self._send("frame", self._recent.popleft())
        return clip

    def _finish(self) -> None:
        clip, self.recording = self.recording, None
        if clip is None:
            return
        event = self.activity.analyzer.events_between(clip["start"], clip["end"])
        self._send("end", {**clip, **event})

    def _send(self, kind: str, item: Any) -> None:
        try:
            self._queue.put_nowait((kind, item))
        except queue.Full:  # the encoder fell behind: lose frames, never a clip's start or end
            if kind == "frame":
                self.dropped += 1
            else:
                self._queue.put((kind, item), timeout=5)

    # --- writing clips (the recorder thread) ------------------------------------------------

    def _write(self) -> None:
        writer: ClipWriter | None = None
        while True:
            kind, item = self._queue.get()
            try:
                if kind == "quit":
                    return
                if kind == "begin":
                    writer = ClipWriter(self.folder, item)
                elif kind == "frame" and writer is not None:
                    writer.add(*item)
                elif kind == "end" and writer is not None:
                    meta = writer.close(item)
                    if meta is not None:
                        self._index[meta["id"]] = meta
                    writer = None
            except Exception:
                log.exception("Recording failed")
                if writer is not None:
                    writer.discard()
                writer = None

    # --- keeping it bounded -----------------------------------------------------------------

    async def _retention(self) -> None:
        while True:
            removed = await asyncio.to_thread(self.purge, time.time())
            if removed:
                log.info("Recordings: removed %d old clips", removed)
            await asyncio.sleep(RETENTION_EVERY_S)

    def purge(self, now: float) -> int:
        """Delete clips older than `days`, then the oldest while they take more than max."""
        clips = sorted(self.clips(), key=lambda c: c["start"])
        removed = 0
        total = sum(c["size_bytes"] for c in clips)
        for clip in clips:
            old = self.days > 0 and clip["start"] < now - self.days * 86_400
            if not old and total <= self.max_bytes:
                break
            self.delete(clip["id"])
            total -= clip["size_bytes"]
            removed += 1
        for day in self.folder.iterdir() if self.folder.exists() else ():
            if day.is_dir() and not any(day.iterdir()):
                day.rmdir()
        return removed

    # --- reading -------------------------------------------------------------------------

    def _scan(self) -> dict[str, dict[str, Any]]:
        index = {}
        for meta in self.folder.glob("*/*.json"):
            try:
                clip = json.loads(meta.read_text())
            except (OSError, ValueError):
                continue
            if CLIP_ID.match(str(clip.get("id", ""))) and (meta.with_suffix(".mp4")).exists():
                index[clip["id"]] = clip
        return index

    def clips(self, since: float = 0.0, until: float = float("inf")) -> list[dict[str, Any]]:
        """Every finished clip that started between `since` and `until`, newest first."""
        found = [c for c in list(self._index.values()) if since <= c["start"] <= until]
        return sorted(found, key=lambda c: c["start"], reverse=True)

    def path(self, clip_id: str, suffix: str) -> Path | None:
        if not CLIP_ID.match(clip_id):
            return None
        day = f"{clip_id[:4]}-{clip_id[4:6]}-{clip_id[6:8]}"
        path = self.folder / day / f"{clip_id}{suffix}"
        return path if path.exists() else None

    def delete(self, clip_id: str) -> bool:
        paths = [self.path(clip_id, suffix) for suffix in (".mp4", ".jpg", ".json")]
        for path in paths:
            if path is not None:
                path.unlink(missing_ok=True)
        self._index.pop(clip_id, None)
        return any(paths)

    def status(self) -> dict[str, Any]:
        clips = self.clips()
        usage = shutil.disk_usage(self.folder) if self.folder.exists() else None
        return {
            "enabled": True,
            "days": self.days,
            "max_bytes": self.max_bytes,
            "count": len(clips),
            "size_bytes": sum(c["size_bytes"] for c in clips),
            "oldest": clips[-1]["start"] if clips else None,
            "recording": self.recording is not None,
            "dropped_frames": self.dropped,
            "disk_free_bytes": usage.free if usage else None,
            "fps": FPS,
            "size": list(SIZE),
        }


class ClipWriter:
    """One clip: decodes the camera's JPEG frames and encodes them to H.264, in an .mp4.part
    that becomes the .mp4 when the clip is complete."""

    def __init__(self, folder: Path, clip: dict[str, Any]) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(clip["start"]))
        self.folder = folder / day
        self.folder.mkdir(parents=True, exist_ok=True)
        self.id = clip["id"]
        self.part = self.folder / f"{self.id}.mp4.part"
        self.container: Any = None
        self.stream: Any = None
        self.first: float | None = None
        self.last_pts = -1
        self.frames = 0
        self.poster: np.ndarray | None = None

    def add(self, t: float, jpeg: bytes) -> None:
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return
        if self.container is None:
            self._open(image)
        height, width = image.shape[:2]
        if (width, height) != (self.stream.width, self.stream.height):
            image = cv2.resize(image, (self.stream.width, self.stream.height), cv2.INTER_AREA)
        if self.first is None:
            self.first = t
        pts = round((t - self.first) * 1000)  # milliseconds: the camera's own pace
        if pts <= self.last_pts:
            return
        self.last_pts = pts
        frame = av.VideoFrame.from_ndarray(image, format="bgr24")
        frame.pts, frame.time_base = pts, Fraction(1, 1000)
        for packet in self.stream.encode(frame):
            self.container.mux(packet)
        self.frames += 1
        if self.poster is None or self.frames <= int(PRE_S * FPS) + 1:
            self.poster = image  # ends up as the moment the motion started

    def _open(self, image: np.ndarray) -> None:
        height, width = image.shape[:2]
        scale = min(1.0, SIZE[0] / width, SIZE[1] / height)
        size = (int(width * scale) // 2 * 2, int(height * scale) // 2 * 2)  # even, for H.264
        self.container = av.open(
            str(self.part), "w", format="mp4", options={"movflags": "faststart"}
        )
        options = {"crf": CRF, "preset": "veryfast", "threads": "2"}
        self.stream = self.container.add_stream("libx264", rate=FPS, options=options)
        self.stream.width, self.stream.height = size
        self.stream.pix_fmt = "yuv420p"
        self.stream.codec_context.time_base = Fraction(1, 1000)
        self.first = None

    def close(self, clip: dict[str, Any]) -> dict[str, Any] | None:
        if self.container is None or self.frames == 0:
            self.discard()
            return None
        for packet in self.stream.encode():
            self.container.mux(packet)
        self.container.close()
        video = self.folder / f"{self.id}.mp4"
        os.replace(self.part, video)
        poster = self.poster
        if poster is not None:
            scale = POSTER_WIDTH / poster.shape[1]
            small = cv2.resize(poster, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(self.folder / f"{self.id}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        meta = {
            **clip,
            "duration_s": round(self.last_pts / 1000, 1),
            "frames": self.frames,
            "width": self.stream.width,
            "height": self.stream.height,
            "size_bytes": video.stat().st_size,
            "poster": poster is not None,
        }
        (self.folder / f"{self.id}.json").write_text(json.dumps(meta) + "\n")
        log.info(
            "Recorded %s: %.0f s, %d KB", self.id, meta["duration_s"], meta["size_bytes"] // 1024
        )
        return meta

    def discard(self) -> None:
        with contextlib.suppress(Exception):
            if self.container is not None:
                self.container.close()
        self.part.unlink(missing_ok=True)
