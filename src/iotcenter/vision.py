"""What the camera notices: how much of the picture moves, and how bright it is.

Five times a second the monitor takes the camera's newest frame and decodes it in grey at
a quarter of its size (JPEG decoders do that directly: about 3 ms for a 1080p frame), then
compares it with a background that slowly adapts to the scene:

  * motion: the share of the picture that differs from the background, with boxes around
    the parts that move. Motion that lasts becomes an event: its start, end and peak.
  * light: the picture's mean brightness. A sudden jump means a light was switched on or
    off (an event too). The whole picture changes then, and keeps changing while the
    camera's auto-exposure settles, so motion is ignored for a moment rather than reported.

Zones (`zones.py`) narrow it down to parts of the picture: motion in an area, water on a
floor, a status light or flame. Everything stays in memory: ten minutes at a point a second,
and the latest events. Each sample also goes out live on /api/stream as a `camera` event.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .camera import Camera
from .zones import MAX_ZONES, Zone, ZoneChange, ZoneSpec, ZoneStore, ZoneTracker

log = logging.getLogger(__name__)

RATE_HZ = 5.0
MIN_WIDTH = 320  # the analysed copy is at least this wide (480 px for a 1080p camera)
BLUR = (5, 5)  # smooths sensor noise and JPEG blocks before comparing
PIXEL_CHANGE = 25  # grey levels (of 255) a pixel must differ by to count as changed
BACKGROUND_RATE = 0.05  # share of each new frame blended into the background (~4 s memory)
MOTION_START_PCT = 0.4  # an event starts when this share of the picture moves, twice in a row
MOTION_STILL_PCT = 0.15  # ...and ends once it stays below this for MOTION_END_S
MOTION_END_S = 3.0
BOX_MIN_PCT = 0.08  # moving areas smaller than this share of the picture get no box
MAX_BOXES = 8
LIGHT_JUMP_PCT = 12.0  # brightness change within LIGHT_WINDOW_S that means a light switched
LIGHT_WINDOW_S = 1.0
LIGHT_QUIET_S = 5.0  # auto-exposure swings back after a switch: not a second switch
WHOLE_PICTURE_PCT = 40.0  # this much changing at once is the light, not something moving
SETTLE_S = 2.0  # after a light change, motion is ignored while the exposure settles
HISTORY_S = 600
MAX_EVENTS = 50


@dataclass(slots=True)
class Sample:
    t: float
    motion_pct: float
    brightness_pct: float
    moving: bool
    boxes: list[list[float]] = field(default_factory=list)  # [x, y, w, h], 0-1 of the picture
    light: str | None = None  # "on" or "off" when a light was just switched
    zones: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": round(self.t, 2),
            "motion_pct": round(self.motion_pct, 2),
            "brightness_pct": round(self.brightness_pct, 1),
            "moving": self.moving,
            "boxes": self.boxes,
            "light": self.light,
            "zones": [_live(zone) for zone in self.zones],
        }


def _live(zone: dict[str, Any]) -> dict[str, Any]:
    """What a live sample says about a zone: enough to label it on the picture."""
    keys = ("id", "name", "kind", "enabled", "state", "motion_pct", "level", "wet_pct", "box")
    return {key: zone[key] for key in keys if key in zone}


REDUCED = {  # factor: (grey, colour) decoder flags
    8: (cv2.IMREAD_REDUCED_GRAYSCALE_8, cv2.IMREAD_REDUCED_COLOR_8),
    4: (cv2.IMREAD_REDUCED_GRAYSCALE_4, cv2.IMREAD_REDUCED_COLOR_4),
    2: (cv2.IMREAD_REDUCED_GRAYSCALE_2, cv2.IMREAD_REDUCED_COLOR_2),
    1: (cv2.IMREAD_GRAYSCALE, cv2.IMREAD_COLOR),
}


def grey_thumbnail(jpeg: bytes, width: int, colour: bool = False) -> np.ndarray | None:
    """Decode a JPEG scaled down by 8, 4 or 2 while keeping at least MIN_WIDTH: in grey, or
    in colour (BGR) when `colour` is set."""
    factor = next((f for f in (8, 4, 2) if width // f >= MIN_WIDTH), 1)
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), REDUCED[factor][int(colour)])


class SceneAnalyzer:
    """Motion and light from a sequence of grey frames: plain computation, no I/O."""

    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)  # newest first
        self.last_motion_at: float | None = None
        self.lights: str | None = None  # "on" / "off" after the first switch seen
        self.lights_changed_at: float | None = None
        self._background: np.ndarray | None = None
        self._brightness: deque[tuple[float, float]] = deque()
        self._light_quiet_until = 0.0
        self._settle_until = 0.0
        self._event: dict[str, Any] | None = None  # the motion event in progress
        self._busy = 0  # samples in a row above MOTION_START_PCT
        self.zones = ZoneTracker(self.events)

    @property
    def moving(self) -> bool:
        return self._event is not None

    def update(
        self,
        grey: np.ndarray,
        t: float,
        colour: np.ndarray | None = None,
        detail: np.ndarray | None = None,
    ) -> Sample:
        """One frame: grey for motion and light, colour (when given) for the zones, and a
        sharper colour copy (when given) for the floors."""
        sample = self._scene(grey, t)
        sample.zones = self.zones.update(
            grey,
            colour,
            self._mask if t >= self._settle_until else None,
            t,
            settling=t < self._settle_until + LIGHT_QUIET_S,
            brightness=sample.brightness_pct,
            motion_event=self._event,
            detail=detail,
        )
        return sample

    def events_between(self, start: float, end: float) -> dict[str, Any]:
        """What moved between two times: the peak, and the zones it touched (for a clip)."""
        motion = [
            e
            for e in list(self.events)
            if e["kind"] == "motion" and e["start"] <= end and (e["end"] or end) >= start
        ]
        zones: list[str] = []
        for event in motion:
            zones += [zone for zone in event.get("zones", []) if zone not in zones]
        peak = max((event["peak_pct"] for event in motion), default=0.0)
        return {"peak_pct": round(peak, 2), "zones": zones}

    def _scene(self, grey: np.ndarray, t: float) -> Sample:
        self._mask = None
        brightness = float(grey.mean()) * 100 / 255
        light = self._light(brightness, t)
        smooth = cv2.GaussianBlur(grey, BLUR, 0)
        if self._background is None or self._background.shape != smooth.shape:
            self._background = smooth.astype(np.float32)
            return Sample(t, 0.0, brightness, self.moving, light=light)

        diff = cv2.absdiff(smooth, cv2.convertScaleAbs(self._background))
        _, mask = cv2.threshold(diff, PIXEL_CHANGE, 255, cv2.THRESH_BINARY)
        changed_pct = 100 * cv2.countNonZero(mask) / mask.size
        if light or changed_pct >= WHOLE_PICTURE_PCT:
            self._settle_until = t + SETTLE_S
        if t < self._settle_until:  # the light changed, not the scene: start over from here
            self._background = smooth.astype(np.float32)
            motion_pct, boxes = 0.0, []
        else:
            cv2.accumulateWeighted(smooth, self._background, BACKGROUND_RATE)
            motion_pct, boxes = changed_pct, _boxes(mask)
            self._mask = mask
        self._track(motion_pct, t)
        return Sample(t, motion_pct, brightness, self.moving, boxes, light)

    def _light(self, brightness: float, t: float) -> str | None:
        window = self._brightness
        window.append((t, brightness))
        while window[0][0] < t - LIGHT_WINDOW_S:
            window.popleft()
        if t < self._light_quiet_until:
            return None
        if brightness - min(b for _, b in window) >= LIGHT_JUMP_PCT:
            switched = "on"
        elif max(b for _, b in window) - brightness >= LIGHT_JUMP_PCT:
            switched = "off"
        else:
            return None
        self._light_quiet_until = t + LIGHT_QUIET_S
        self.lights, self.lights_changed_at = switched, t
        self.events.appendleft({"kind": f"lights_{switched}", "start": t, "end": t})
        return switched

    def _track(self, motion_pct: float, t: float) -> None:
        self._busy = self._busy + 1 if motion_pct >= MOTION_START_PCT else 0
        event = self._event
        if event is None:
            if self._busy >= 2:  # twice in a row: something moved, not a flicker
                self._event = {"kind": "motion", "start": t, "end": None, "peak_pct": motion_pct}
                self.events.appendleft(self._event)
                self.last_motion_at = t
            return
        event["peak_pct"] = max(event["peak_pct"], motion_pct)
        if motion_pct >= MOTION_STILL_PCT:
            self.last_motion_at = t
        elif t - self.last_motion_at >= MOTION_END_S:
            event["end"] = self.last_motion_at
            self._event = None


def _boxes(mask: np.ndarray) -> list[list[float]]:
    """Boxes around the larger moving areas, biggest first, as fractions of the picture."""
    height, width = mask.shape
    grown = cv2.dilate(mask, None, iterations=3)  # joins the pieces of one moving thing
    contours, _ = cv2.findContours(grown, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects = [cv2.boundingRect(c) for c in contours]
    rects = [r for r in rects if 100 * r[2] * r[3] / mask.size >= BOX_MIN_PCT]
    rects.sort(key=lambda r: r[2] * r[3], reverse=True)
    return [
        [round(x / width, 4), round(y / height, 4), round(w / width, 4), round(h / height, 4)]
        for x, y, w, h in rects[:MAX_BOXES]
    ]


class ActivityMonitor:
    """Runs a SceneAnalyzer on the camera's newest frame, RATE_HZ times a second."""

    def __init__(
        self,
        camera: Camera,
        publish: Callable[[dict[str, Any]], None] | None = None,
        rate_hz: float = RATE_HZ,
        zones: Path | None = None,
    ) -> None:
        self.camera = camera
        self.analyzer = SceneAnalyzer()
        self.store = ZoneStore(zones) if zones else None
        if self.store is not None:
            self.analyzer.zones.set_zones(self.store.load())
        self.latest: Sample | None = None
        self._publish = publish
        self._interval = 1 / rate_hz
        self._history: deque[tuple[float, float, float]] = deque(maxlen=HISTORY_S)
        self._peak = 0.0  # the most motion since the last history point
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        # The analysed copies are tiny: OpenCV's thread pool (a thread per core) costs more
        # than it saves; one thread keeps a 1080p camera at ~4% of one core instead of ~11%.
        cv2.setNumThreads(1)
        self._task = asyncio.create_task(self._run(), name="camera-activity")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        last_seq, due = 0, time.monotonic()
        while True:
            due = max(due + self._interval, time.monotonic())
            await asyncio.sleep(due - time.monotonic())
            frame = self.camera.latest
            if frame is None or frame.seq == last_seq:
                continue  # no new frame: the camera is slow, paused or gone
            last_seq = frame.seq
            try:
                sample = await asyncio.to_thread(self._analyze, frame.jpeg, frame.time)
            except Exception:  # never let one bad frame (or a bug) stop the watching
                log.exception("Camera analysis failed")
                continue
            if sample is not None:
                self._remember(sample)

    def _analyze(self, jpeg: bytes, t: float) -> Sample | None:
        width = self.camera.source.width
        zones = self.analyzer.zones
        if zones.needs_detail(t):  # light zones, or the floors' once a second: a sharper copy
            flag = cv2.IMREAD_REDUCED_COLOR_2 if width >= 1280 else cv2.IMREAD_COLOR
            detail = cv2.imdecode(np.frombuffer(jpeg, np.uint8), flag)
            if detail is None:
                return None
            size = (
                MIN_WIDTH * 3 // 2,
                round(detail.shape[0] * MIN_WIDTH * 3 / 2 / detail.shape[1]),
            )
            colour = cv2.resize(detail, size, interpolation=cv2.INTER_AREA)  # no second decode
            grey = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
            return self.analyzer.update(grey, t, colour, detail=detail)
        if not zones.needs_colour:
            grey = grey_thumbnail(jpeg, width)
            return None if grey is None else self.analyzer.update(grey, t)
        colour = grey_thumbnail(jpeg, width, colour=True)
        if colour is None:
            return None
        grey = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
        return self.analyzer.update(grey, t, colour)

    # --- zones (the dashboard draws them; they are kept in a JSON file) ----------------------

    @property
    def zones(self) -> list[Zone]:
        return self.analyzer.zones.zones

    def add_zone(self, spec: ZoneSpec) -> Zone:
        if len(self.zones) >= MAX_ZONES:
            raise ValueError(f"at most {MAX_ZONES} zones")
        zone = Zone.new(spec)
        self._save([*self.zones, zone])
        return zone

    def change_zone(self, zone_id: str, change: ZoneChange) -> Zone | None:
        """Rename a zone, or switch it on or off (it keeps what it learned)."""
        updates = change.model_dump(exclude_none=True)
        zones = [z.model_copy(update=updates) if z.id == zone_id else z for z in self.zones]
        if zones == self.zones and not any(z.id == zone_id for z in zones):
            return None
        self._save(zones)
        return next(z for z in zones if z.id == zone_id)

    def delete_zone(self, zone_id: str) -> bool:
        zones = [zone for zone in self.zones if zone.id != zone_id]
        if len(zones) == len(self.zones):
            return False
        self._save(zones)
        return True

    def reset_zone(self, zone_id: str) -> bool:
        return self.analyzer.zones.reset(zone_id)

    def _save(self, zones: list[Zone]) -> None:
        if self.store is not None:
            self.store.save(zones)
        self.analyzer.zones.set_zones(zones)

    def _remember(self, sample: Sample) -> None:
        self.latest = sample
        self._peak = max(self._peak, sample.motion_pct)
        if not self._history or sample.t - self._history[-1][0] >= 1.0 - self._interval / 2:
            self._history.append(
                (round(sample.t, 1), round(self._peak, 2), round(sample.brightness_pct, 1))
            )
            self._peak = 0.0
        if self._publish is not None:
            self._publish(sample.to_dict())

    def events(self, limit: int = 20) -> list[dict[str, Any]]:
        return [_rounded(event) for event in list(self.analyzer.events)[:limit]]

    def summary(self) -> dict[str, Any]:
        analyzer, latest = self.analyzer, self.latest
        return {
            "rate_hz": round(1 / self._interval, 1),
            "moving": analyzer.moving,
            "motion_pct": round(latest.motion_pct, 2) if latest else None,
            "brightness_pct": round(latest.brightness_pct, 1) if latest else None,
            "last_motion_at": analyzer.last_motion_at,
            "lights": analyzer.lights,
            "lights_changed_at": analyzer.lights_changed_at,
            "zones": analyzer.zones.summaries(time.time()),
            "events": self.events(),
        }

    def history(self) -> dict[str, Any]:
        points = list(self._history)
        return {
            "window_s": HISTORY_S,
            "t": [p[0] for p in points],
            "motion_pct": [p[1] for p in points],
            "brightness_pct": [p[2] for p in points],
            "events": self.events(MAX_EVENTS),
        }


def _rounded(event: dict[str, Any]) -> dict[str, Any]:
    event = dict(event)
    if "peak_pct" in event:
        event["peak_pct"] = round(event["peak_pct"], 2)
    return event
