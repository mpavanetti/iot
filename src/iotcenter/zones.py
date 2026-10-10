"""Zones: named parts of the camera's picture, each watched for one thing.

  * area   motion inside it. A motion event also names the zones it touched.
  * floor  water. Wet concrete is darker than dry, and standing water can mirror a lamp: a patch
           of the zone that turns clearly darker (or much brighter) than the dry floor it
           learned, and stays that way, is reported as possible water.
  * drain  a floor drain, where some water is normal (a furnace's condensate line ends there):
           only water pooling around it, over a good part of the zone, is reported (a backup).
  * light  an indicator: a status LED, or a burner's flame through its window. Lit means colour
           (a grey metal panel has none, in daylight or under the room's lights) or, for a white
           LED, a bright spot in a darker zone, so the room's own lights do not fool it. On, off
           or blinking (and how often it changes: a board's LED that toggles with each delivery
           blinks while it streams), and how often and how long it was on in the last 24 hours.

When a floor zone starts watching, it also looks for water that is already there: patches
darker than the floor around them, of the floor's own colour (a painted plate or a bluish tank is
not), with clear edges (a shadow's are soft). One picture cannot tell water from a stain, so
such a patch is reported as "water or stain?", and watched: if it fades, it was water drying; if
it spreads, it is reported as water; if it stays as it is for STAIN_S, it is a stain, and is
learned as part of the floor.

A floor is judged against the dry floor it learned *under the same lighting*. The tracker
recognises each lighting (the lamps on, daylight only...) by what the whole picture looks like,
and keeps a reference picture of each one to correct for the camera's exposure. So a leak that
starts at night with the lights off is seen the moment the lights come back on, against the dry
floor of that lighting, instead of being learned as the new normal.

A zone is a rectangle in fractions of the picture, and can be switched off without deleting it.
Zones are kept in a small JSON file next to the database (`data/camera-zones.json`): names and
positions only, never a picture. What a floor zone learns stays in memory, and is learned again
after a restart.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections import deque
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field, ValidationError, model_validator

log = logging.getLogger(__name__)

MAX_ZONES = 12
ZONE_MOTION_PCT = 1.0  # a motion event "touched" a zone when this share of the zone moved

LIGHT_ON = 100  # how lit (colour strength, or a bright spot's lead over the zone) is surely lit
LIGHT_OFF = 40  # ...and surely unlit; in between it is unknown until it has been seen both ways
LIGHT_SPAN = 30  # once a light was seen this far apart lit and unlit, it switches halfway
LIGHT_MEMORY_S = 600.0  # between the two levels it saw over about the last 10 minutes
LIGHT_HOLD_S = 1.0  # a state must last this long; quicker flips are blinks
BLINK_FLIPS = 4  # this many flips within BLINK_WINDOW_S is blinking (a reading every 2-3 s)
BLINK_WINDOW_S = 15.0
DAY_S = 86_400

FLOOR_EVERY_S = 1.0  # water is slow: the floors are judged once a second, on a sharper copy
# of the picture than motion (960 wide for 1080p: a small spill is then tens of pixels)
FLOOR_LEARN_S = 10.0  # a floor first learns the dry floor (for each lighting it sees)
FLOOR_DARKER = 0.88  # a pixel at 88% or less of its dry brightness, after exposure: wet
FLOOR_BRIGHTER = 1.35  # or at 135% or more: standing water mirroring a lamp
FLOOR_VISIBLE = 30  # grey levels: below this a pixel is too dark to see it darken
FLOOR_PATCH_PX = 30  # possible water: a patch of this many pixels of the floor copy (960 wide
# for 1080p: a spill a hand across, 4 m from the camera), whatever the zone's size
FLOOR_HOLD_S = 30.0  # it must stay this long
FLOOR_CLEAR_S = 60.0  # dry again after this long under half that size
FLOOR_BUSY_PCT = (
    3.0  # someone is in the zone: this share of it moving (a spreading puddle moves less)
)
QUIET_S = 20.0  # a floor waits this long after someone was in it, or anything moved anywhere
FLOOR_ADAPT_S = 1800.0  # the dry floor follows slow changes (daylight, dust) over ~30 minutes
DARK_PCT = 12.0  # a picture darker than this (lights off) is not judged: the camera cannot see
SCENES = 4  # lightings remembered (the oldest unused one is forgotten)
SCENE_SIZE = (48, 27)  # the whole picture, tiny, to recognise a lighting
SCENE_MATCH = 0.08  # two pictures this alike (mean difference, brightness-normalised) share one
WET_RATIO = (0.45, 0.88)  # water already there: this much darker than the floor around it...
WET_HUE = 4  # ...no bluer than the floor (blue minus red, 0-255): paint and metal are bluer
WET_EDGE = 42  # ...with edges this sharp (grey levels per pixel, smoothed): a shadow's are softer
STAIN_S = 1800.0  # a dark patch found at the start that has not changed for this long is a stain
FADE = 0.04  # ...one that got lighter by this much (of the floor's brightness) is water drying
SPREAD = 1.5  # ...and one that grew to this many times its size is water spreading
DRAIN_POOL_PCT = 15.0  # a drain zone reports water over this share of it: a pool, not the usual
WET_MIN_PX = 20  # ...at least this big; and not touching the zone's edge (a tank's or a furnace's
# base, a wall: objects that only reach into the zone)


class ZoneSpec(BaseModel):
    """A zone as the dashboard sends it."""

    name: str = Field(min_length=1, max_length=40)
    kind: Literal["area", "floor", "drain", "light"]
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0.01, le=1)
    h: float = Field(ge=0.01, le=1)
    enabled: bool = True  # off: kept and drawn, but not watched

    @model_validator(mode="after")
    def _inside(self) -> ZoneSpec:
        if self.x + self.w > 1.001 or self.y + self.h > 1.001:
            raise ValueError("the zone must be inside the picture")
        self.name = self.name.strip()
        return self


class ZoneChange(BaseModel):
    """What the dashboard may change on a zone without redrawing it."""

    name: str | None = Field(None, min_length=1, max_length=40)
    enabled: bool | None = None


class Zone(ZoneSpec):
    id: str

    @classmethod
    def new(cls, spec: ZoneSpec) -> Zone:
        return cls(id=secrets.token_hex(4), **spec.model_dump())

    def pixels(self, width: int, height: int) -> tuple[slice, slice]:
        """The zone's rows and columns in a picture of this size (at least one pixel)."""
        x0, y0 = int(self.x * width), int(self.y * height)
        x1 = max(x0 + 1, min(width, round((self.x + self.w) * width)))
        y1 = max(y0 + 1, min(height, round((self.y + self.h) * height)))
        return slice(y0, y1), slice(x0, x1)


class ZoneStore:
    """The zones, in a JSON file (written atomically, so a crash never leaves half of it)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Zone]:
        try:
            data = json.loads(self.path.read_text())
            return [Zone(**zone) for zone in data["zones"]][:MAX_ZONES]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            log.warning("Ignoring the camera zones in %s: %s", self.path, exc)
            return []

    def save(self, zones: list[Zone]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = {"zones": [zone.model_dump() for zone in zones]}
        temporary.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(temporary, self.path)


class AreaWatch:
    kind = "area"

    def __init__(self, zone: Zone) -> None:
        self.zone = zone
        self.motion_pct = 0.0
        self.last_motion_at: float | None = None

    def summary(self, now: float) -> dict[str, Any]:
        return {"state": "moving" if self.motion_pct >= ZONE_MOTION_PCT else "still"}


class LightWatch(AreaWatch):
    kind = "light"

    def __init__(self, zone: Zone) -> None:
        super().__init__(zone)
        self.level = 0.0
        self.state: str | None = None  # "on", "off", "blinking"; None until known
        self.since: float | None = None
        self.since_start = False
        self._low: float | None = None  # the levels it was seen at, unlit and lit
        self._high = 0.0
        self._lit: bool | None = None  # the latest reading, before LIGHT_HOLD_S
        self._lit_since = 0.0
        self._flips: deque[float] = deque()
        self._periods: deque[tuple[float, float]] = deque()  # on periods of the last 24 h
        self._event: dict[str, Any] | None = None

    def update(self, colour: np.ndarray, t: float, events: deque) -> None:
        brightest = colour.max(axis=2)
        strength = brightest.astype(np.int16) - colour.min(axis=2)
        coloured = float(np.percentile(strength, 99))  # the lit part may be small
        spot = float(np.percentile(brightest, 99) - np.median(brightest))  # a white LED
        self.level = level = max(coloured, spot)
        lit = self._lit_now(level, t)
        if lit is None:
            return  # not known yet: neither surely lit nor surely unlit, never seen both ways
        if lit != self._lit:
            if self._lit is not None:
                self._flips.append(t)
            self._lit, self._lit_since = lit, t
        while self._flips and self._flips[0] < t - BLINK_WINDOW_S:
            self._flips.popleft()
        if len(self._flips) >= BLINK_FLIPS:
            state = "blinking"
        elif t - self._lit_since >= LIGHT_HOLD_S or self.state is None:
            state = "on" if lit else "off"
        else:
            state = self.state
        if state != self.state:
            self._switch(state, t, events)

    def _lit_now(self, level: float, t: float) -> bool | None:
        """Lit above halfway between the levels this light was seen at, lit and unlit (with a
        little hysteresis). Until it has been seen both ways: lit when surely lit, unlit when
        surely unlit, and otherwise as it was (unknown at first)."""
        if self._low is None:
            self._low = self._high = level
            self._seen_at = t
        forget = min(1.0, (t - self._seen_at) / LIGHT_MEMORY_S)
        self._seen_at = t
        self._low = min(level, self._low + (self._high - self._low) * forget)
        self._high = max(level, self._high - (self._high - self._low) * forget)
        span = self._high - self._low
        if span >= LIGHT_SPAN:
            middle = (self._low + self._high) / 2
            return level > (middle - 0.1 * span if self._lit else middle + 0.1 * span)
        if level >= LIGHT_ON:
            return True
        if level <= LIGHT_OFF:
            return False
        return self._lit

    def _switch(self, state: str, t: float, events: deque) -> None:
        if self.state == "on":
            self._periods.append((self.since, t))
        if self._event is not None:
            self._event["end"] = t
            self._event = None
        found = self.state is None  # the state it was in when IoT Center started: no event
        if state in ("on", "blinking") and not found:
            self._event = {"kind": f"light_{state}", "zone": self.zone.name, "start": t}
            self._event["end"] = None
            events.appendleft(self._event)
        self.since_start = found  # `since` is then when watching began, not when it changed
        self.state, self.since = state, t

    def summary(self, now: float) -> dict[str, Any]:
        while self._periods and self._periods[0][1] < now - DAY_S:
            self._periods.popleft()
        periods = list(self._periods)
        if self.state == "on" and self.since is not None:
            periods.append((self.since, now))
        on_s = sum(min(end, now) - max(start, now - DAY_S) for start, end in periods)
        flips = list(self._flips)
        gaps = [b - a for a, b in zip(flips, flips[1:], strict=False)]
        return {
            "flip_s": round(float(np.median(gaps)), 1)
            if self.state == "blinking" and gaps
            else None,
            "state": self.state or "unknown",
            "since": self.since,
            "since_start": self.since_start,
            "level": round(self.level),
            "unlit_level": round(self._low)
            if self._high - (self._low or 0) >= LIGHT_SPAN
            else None,
            "lit_level": round(self._high) if self._high - (self._low or 0) >= LIGHT_SPAN else None,
            "on_count_24h": len(periods),
            "on_s_24h": round(on_s),
        }


class FloorWatch(AreaWatch):
    kind = "floor"
    pool_pct: float | None = None  # report only a patch this share of the zone (drains)
    survey = True  # look for water already there when it starts

    def __init__(self, zone: Zone) -> None:
        super().__init__(zone)
        self.state = "learning"  # learning, dry, water, check (water or a stain?), paused
        self.suspect: dict[str, Any] | None = None  # a dark patch found at the start
        self.since: float | None = None
        self.wet_pct = 0.0
        self.box: list[float] | None = None  # the patch, as fractions of the picture
        self._dry: dict[int, np.ndarray] = {}  # the dry floor, for each lighting (scene id)
        self._learning: dict[int, float] = {}  # scene id: learning until
        self._scene: int | None = None  # the lighting it was last judged in
        self._last = 0.0
        self._patch_since: float | None = None
        self._clear_since: float | None = None
        self._event: dict[str, Any] | None = None
        self.busy_at = float("-inf")  # when someone was last in the zone
        self._dismissed = False
        self._trusted: set[int] = set()  # lightings learned after "the floor is dry now"

    def reset(self) -> None:
        """The floor is dry now: learn it again, for the lighting it is in now, as it is (no
        looking for water already there). The other lightings keep theirs, so a puddle that
        is there in them is still found."""
        self._dry.pop(self._scene, None)
        if self._scene is not None:
            self._trusted.add(self._scene)
        self._dismissed = True  # and a patch reported now was not water

    def update(self, crop: np.ndarray, scene: int, gain: float, t: float, can_judge: bool,
               events: deque, frame: tuple, colour: np.ndarray | None = None) -> None:  # fmt: skip
        raw = crop
        crop = crop.astype(np.float32) / gain  # as it looks at this lighting's usual exposure
        if self._dismissed:
            self._dismissed = False
            if self._event is not None:
                self._event["end"], self._event = t, None
            self._patch_since = self._clear_since = self.suspect = None
        self._scene = scene
        dry = self._dry.get(scene)
        if dry is None or dry.shape != crop.shape:
            self._dry[scene], self._learning[scene] = crop, t + FLOOR_LEARN_S
            if self._event is None:
                self.state, self.since, self.wet_pct, self.box = "learning", t, 0.0, None
            self._last = t
            return
        if scene in self._learning:
            if t < self._learning[scene]:
                cv2.accumulateWeighted(crop, dry, 0.3)
                self._last = t
                return
            del self._learning[scene]  # learned: is there water already?
            if self.survey and scene not in self._trusted and colour is not None:
                wet = wet_patches(raw, colour)
                if wet.any():
                    floor = cv2.medianBlur(raw, _kernel(raw.shape)).astype(np.float32) / gain
                    dry[wet] = floor[wet]  # the dry floor there is the floor around it
                    self.suspect = {"mask": wet, "scene": scene, "since": t, "size": int(wet.sum())}
                    self.suspect["darkness"] = _darkness(raw, wet)
                    log.info("Zone %s: %d pixels look wet already", self.zone.name, wet.sum())
        if not can_judge:  # someone in the zone, or something moved: wait
            self.update_paused(t)
            return
        visible = dry > FLOOR_VISIBLE
        brighter = crop > dry * FLOOR_BRIGHTER
        if brighter.any():  # glare is brighter than the floor around it; a patch drying is not
            around = cv2.medianBlur(raw, _kernel(raw.shape)).astype(np.float32) / gain
            drying = brighter & (crop <= around * 1.1)
            dry[drying] = crop[drying]  # it was learned wet, and is dry now
            brighter &= ~drying
        changed = ((crop < dry * FLOOR_DARKER) & visible) | brighter
        kernel = np.ones((2, 2), np.uint8)  # drops lone pixels, keeps a thin streak of water
        changed = cv2.morphologyEx(changed.astype(np.uint8), cv2.MORPH_OPEN, kernel)
        count = cv2.countNonZero(changed)
        rate = min(1.0, (t - self._last) / FLOOR_ADAPT_S)
        self._last = t
        steady = changed == 0  # the dry floor follows slow changes, but never towards a patch
        dry[steady] += (crop[steady] - dry[steady]) * rate
        size = FLOOR_PATCH_PX
        if self.pool_pct is not None:
            size = max(size, crop.size * self.pool_pct / 100)
        self.wet_pct = 100 * count / crop.size
        self.box = _patch_box(changed, frame) if count >= size / 2 else None
        unconfirmed = self._watch_suspect(raw, changed, dry, crop, scene, t, size)
        self._judge(count >= size, count < size / 2, t, events, unconfirmed)

    def _watch_suspect(self, raw, changed, dry, crop, scene, t, size) -> bool:  # noqa: ANN001
        """A dark patch found at the start: water drying, water spreading, or a stain? True
        while what the zone sees is only that patch, and it has not spread."""
        suspect = self.suspect
        if suspect is None or suspect["scene"] != scene or suspect["mask"].shape != changed.shape:
            return False
        near = cv2.dilate(suspect["mask"].astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        beyond = cv2.countNonZero(changed & ~near)
        within = cv2.countNonZero(changed & near)
        if beyond >= size or within >= SPREAD * max(suspect["size"], size):
            self.suspect = None  # new water, or it spread: no longer a question
            return False
        if _darkness(raw, suspect["mask"]) >= suspect["darkness"] + FADE:
            suspect["drying"] = True  # lighter than it was: water, drying
        elif not suspect.get("drying") and t - suspect["since"] >= STAIN_S:
            mask = suspect["mask"]
            dry[mask] = crop[mask]  # unchanged all this time: a stain, part of the floor now
            self.suspect = None
            if self._event is not None:
                self._event["stain"] = True
            self._patch_since, self._clear_since = None, t - FLOOR_CLEAR_S
            log.info("Zone %s: the dark patch did not change: a stain", self.zone.name)
            return False
        return True

    def update_paused(self, t: float) -> None:
        """Not judged now (dark, a light switching, someone there): a patch stays reported."""
        if self.state not in ("water", "check"):
            self.state = "paused"  # keeps `since`: when it was last judged dry or wet
        self._last = t

    def _judge(self, patch: bool, clear: bool, t: float, events: deque, unconfirmed=False) -> None:  # noqa: ANN001
        if patch:
            self._patch_since = self._patch_since or t
            self._clear_since = None
        elif clear:
            self._clear_since = self._clear_since or t
            self._patch_since = None
        if self._event is None and self._patch_since and t - self._patch_since >= FLOOR_HOLD_S:
            self._event = {
                "kind": "water",
                "zone": self.zone.name,
                "start": self._patch_since,
                "end": None,
                "peak_pct": self.wet_pct,
                "unconfirmed": unconfirmed,  # found at the start: water, or a stain?
            }
            events.appendleft(self._event)
            self.state, self.since = "check" if unconfirmed else "water", self._patch_since
        elif self._event is not None:
            self._event["peak_pct"] = max(self._event["peak_pct"], self.wet_pct)
            if self._event["unconfirmed"] and not unconfirmed and not self._event.get("stain"):
                self._event["unconfirmed"] = False  # it spread, or new water: water
                self.state = "water"
            if self.suspect is not None and self.suspect.get("drying"):
                self._event["drying"] = True
            if self._clear_since and t - self._clear_since >= FLOOR_CLEAR_S:
                self._event["end"] = t
                self._event = self.suspect = None
                self.state, self.since = "dry", t
        if self._event is None and self.state != "dry":
            if self.state in ("learning", "water") or self.since is None:
                self.since = t
            self.state = "dry"

    def summary(self, now: float) -> dict[str, Any]:
        suspect = self.suspect
        return {
            "state": self.state,
            "since": self.since,
            "wet_pct": round(self.wet_pct, 1),
            "box": self.box,
            "lightings": len(self._dry),
            "drying": bool(suspect and suspect.get("drying")),
            "stain_at": suspect["since"] + STAIN_S
            if suspect and not suspect.get("drying")
            else None,
        }


class DrainWatch(FloorWatch):
    """A floor drain: some water is normal there, so only a pool around it is reported."""

    kind = "drain"
    pool_pct = DRAIN_POOL_PCT
    survey = False


def _darkness(grey: np.ndarray, mask: np.ndarray) -> float:
    """How bright a patch is, as a share of the floor around it (lower: darker)."""
    around = cv2.medianBlur(grey, _kernel(grey.shape)).astype(np.float32)
    return float((grey[mask] / np.maximum(around[mask], 1)).mean())


def _kernel(shape: tuple[int, ...]) -> int:
    """An odd median size as large as the zone's smaller side (at most 99 pixels): the floor
    around a point, even beside a patch half that size."""
    size = max(5, min(99, min(shape[:2])))
    return size if size % 2 else size - 1


def wet_patches(grey: np.ndarray, colour: np.ndarray) -> np.ndarray:
    """Pixels that look like water already on the floor, with no dry floor to compare to:
    patches darker than the floor around them, of the floor's own colour, with clear edges.
    Each patch is judged as a whole: its colour on average, the edge around it (holes filled),
    and it must not be the outline of something black (a drain cover, a pipe)."""
    floor = cv2.medianBlur(grey, _kernel(grey.shape)).astype(np.float32)
    ratio = grey.astype(np.float32) / np.maximum(floor, 1)
    hue = colour[..., 0].astype(np.float32) - colour[..., 2]  # blue minus red
    plain = ratio > 0.92
    floor_hue = float(np.median(hue[plain])) if plain.any() else 0.0
    darker = (ratio > WET_RATIO[0]) & (ratio < WET_RATIO[1]) & (floor > FLOOR_VISIBLE)
    darker = cv2.morphologyEx(darker.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(darker)
    smooth = cv2.GaussianBlur(grey, (3, 3), 0)  # sensor noise is no edge
    dx, dy = (cv2.Sobel(smooth, cv2.CV_32F, *d, ksize=5) for d in ((1, 0), (0, 1)))
    edges = cv2.magnitude(dx, dy) / 16  # grey levels per pixel
    height, width = grey.shape
    wet = np.zeros(grey.shape, bool)
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < WET_MIN_PX or x == 0 or y == 0 or x + w == width or y + h == height:
            continue
        blob = (labels == label).astype(np.uint8)
        if float(hue[blob > 0].mean()) - floor_hue > WET_HUE:
            continue  # bluer than the floor: paint, metal
        outline, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(blob)
        cv2.drawContours(filled, outline, -1, 1, -1)
        inside = filled > 0
        if (ratio[inside] <= WET_RATIO[0]).mean() > 0.3:
            continue  # the edge of something black, not a patch of water
        ring = cv2.dilate(filled, None) - cv2.erode(filled, None)
        if edges[ring > 0].mean() >= WET_EDGE:
            wet |= inside
    return wet


def _patch_box(mask: np.ndarray, frame: tuple) -> list[float] | None:
    """The patch's bounding box, as fractions of the whole picture."""
    (rows, cols), (height, width) = frame
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    x0, y0 = (cols.start + xs.min()) / width, (rows.start + ys.min()) / height
    x1, y1 = (cols.start + xs.max() + 1) / width, (rows.start + ys.max() + 1) / height
    return [round(float(v), 4) for v in (x0, y0, x1 - x0, y1 - y0)]


WATCHES = {"area": AreaWatch, "light": LightWatch, "floor": FloorWatch, "drain": DrainWatch}
FLOORS = ("floor", "drain")


class Scene:
    """One lighting of the room, recognised by a tiny copy of the whole picture."""

    def __init__(self, scene_id: int, signature: np.ndarray, grey: np.ndarray, t: float) -> None:
        self.id, self.signature, self.used_at = scene_id, signature, t
        self.grey = grey  # the whole picture as usual in this lighting: the exposure reference
        self.adapted_at = t


def signature(grey: np.ndarray) -> np.ndarray:
    small = cv2.resize(grey, SCENE_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
    return small / (small.mean() + 1e-6)


class ZoneTracker:
    """Watches every zone on each analysed frame (see vision.SceneAnalyzer)."""

    def __init__(self, events: deque) -> None:
        self.events = events
        self._watches: dict[str, AreaWatch] = {}
        self._scenes: list[Scene] = []
        self._next_scene_id = 1
        self._next_floor = 0.0
        self._quiet_since = float("-inf")  # when something last moved anywhere

    @property
    def zones(self) -> list[Zone]:
        return [watch.zone for watch in self._watches.values()]

    def needs_detail(self, t: float) -> bool:
        """Whether this frame needs the sharper copy: always for light zones (a status LED is
        a pixel or two wide), once a second for the floors."""
        lights = any(w.kind == "light" and w.zone.enabled for w in self._watches.values())
        return lights or self.floors_due(t)

    def floors_due(self, t: float) -> bool:
        """Whether this frame is the one a second the floors are judged on (the monitor then
        decodes a sharper copy for them)."""
        return t >= self._next_floor and any(
            w.kind in FLOORS and w.zone.enabled for w in self._watches.values()
        )

    @property
    def needs_colour(self) -> bool:  # light zones; floors, to tell water from paint or metal
        return any(w.kind != "area" and w.zone.enabled for w in self._watches.values())

    def set_zones(self, zones: list[Zone]) -> None:
        """New zones get a new watch; a zone that did not move keeps what it learned (also
        while switched off)."""
        watches = {}
        place = {"name", "enabled"}
        for zone in zones:
            old = self._watches.get(zone.id)
            if old is not None and old.zone.model_dump(exclude=place) == zone.model_dump(
                exclude=place
            ):
                old.zone = zone
                watches[zone.id] = old
            else:
                watches[zone.id] = WATCHES[zone.kind](zone)
        self._watches = watches  # one assignment: the analysing thread sees old or new

    def reset(self, zone_id: str) -> bool:
        watch = self._watches.get(zone_id)
        if isinstance(watch, FloorWatch):
            watch.reset()
            return True
        return False

    def update(
        self,
        grey: np.ndarray,
        colour: np.ndarray | None,
        mask: np.ndarray | None,
        t: float,
        *,
        settling: bool,
        brightness: float,
        motion_event: dict[str, Any] | None,
        detail: np.ndarray | None = None,
    ) -> list[dict[str, Any]]:
        """`detail`: a sharper colour copy of the frame for the floors (else they use `grey`
        and `colour`)."""
        watches = [watch for watch in self._watches.values() if watch.zone.enabled]
        height, width = grey.shape
        if motion_event is not None:
            self._quiet_since = t
        floors = t >= self._next_floor and any(w.kind in FLOORS for w in watches)
        lit = brightness >= DARK_PCT and not settling
        scene, gain = None, 1.0
        floor_grey, floor_colour = grey, colour
        if floors and detail is not None:
            floor_grey, floor_colour = cv2.cvtColor(detail, cv2.COLOR_BGR2GRAY), detail
        if floors:
            self._next_floor = t + FLOOR_EVERY_S
            if lit:
                scene, gain = self._lighting(floor_grey, t)
        for watch in watches:
            rows, cols = watch.zone.pixels(width, height)
            if mask is not None:
                watch.motion_pct = 100 * cv2.countNonZero(mask[rows, cols]) / mask[rows, cols].size
            if watch.motion_pct >= ZONE_MOTION_PCT:
                watch.last_motion_at = t
                if motion_event is not None:
                    touched = motion_event.setdefault("zones", [])
                    if watch.zone.name not in touched:
                        touched.append(watch.zone.name)
            if isinstance(watch, LightWatch) and (colour is not None or detail is not None):
                if detail is not None:
                    drows, dcols = watch.zone.pixels(detail.shape[1], detail.shape[0])
                    watch.update(detail[drows, dcols], t, self.events)
                else:
                    watch.update(colour[rows, cols], t, self.events)
            elif isinstance(watch, FloorWatch) and floors:
                if watch.motion_pct >= FLOOR_BUSY_PCT:
                    watch.busy_at = t
                quiet = t - self._quiet_since >= QUIET_S and t - watch.busy_at >= QUIET_S
                if scene is None:  # dark, or the light just switched: the camera cannot judge
                    watch.update_paused(t)
                    continue
                size = floor_grey.shape
                frows, fcols = watch.zone.pixels(size[1], size[0])
                crop = floor_grey[frows, fcols]
                tint = None if floor_colour is None else floor_colour[frows, fcols]
                frame = ((frows, fcols), size)
                watch.update(crop, scene.id, gain, t, quiet, self.events, frame, tint)
        return self.summaries(t, live=True)

    def describe(self, watch: AreaWatch, now: float) -> dict[str, Any]:
        state = watch.summary(now) if watch.zone.enabled else {"state": "off"}
        return {
            "id": watch.zone.id,
            "name": watch.zone.name,
            "kind": watch.kind,
            "enabled": watch.zone.enabled,
            "motion_pct": round(watch.motion_pct, 2) if watch.zone.enabled else 0.0,
            "last_motion_at": watch.last_motion_at,
            **state,
        }

    def summaries(self, now: float, live: bool = False) -> list[dict[str, Any]]:
        watches = self._watches.values()
        if live:
            return [self.describe(watch, now) for watch in watches]
        return [{**watch.zone.model_dump(), **self.describe(watch, now)} for watch in watches]

    def _lighting(self, grey: np.ndarray, t: float) -> tuple[Scene, float]:
        """Which lighting the room is in (a new one is remembered), and how much brighter or
        darker the camera's exposure makes the picture than usual in it."""
        frame = grey.astype(np.float32)
        mark = signature(grey)
        scene = min(
            self._scenes, key=lambda s: float(np.abs(s.signature - mark).mean()), default=None
        )
        if scene is None or float(np.abs(scene.signature - mark).mean()) > SCENE_MATCH:
            if len(self._scenes) >= SCENES:
                self._scenes.remove(min(self._scenes, key=lambda s: s.used_at))
            if self._scenes:  # not the first one: the lighting changed, or the camera moved
                self.events.appendleft({"kind": "new_view", "start": t, "end": t})
            scene = Scene(self._next_scene_id, mark, frame, t)
            self._next_scene_id += 1
            self._scenes.append(scene)
            log.info("Camera zones: a new lighting or view (%d known)", len(self._scenes))
            return scene, 1.0
        if scene.grey.shape != frame.shape:  # the analysed size changed: start this one over
            scene.grey, scene.adapted_at = frame, t
            return scene, 1.0
        visible = scene.grey > FLOOR_VISIBLE
        gain = float(np.median(frame[visible] / scene.grey[visible])) if visible.any() else 1.0
        gain = gain if gain > 0.05 else 1.0
        rate = min(1.0, (t - scene.adapted_at) / FLOOR_ADAPT_S)  # as fast as the dry floors
        scene.grey += (frame / gain - scene.grey) * rate
        scene.adapted_at = scene.used_at = t
        scene.signature += (mark - scene.signature) * rate
        return scene, gain
