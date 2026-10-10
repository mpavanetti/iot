"""Motion and light detection on synthetic frames: no camera needed."""

import cv2
import numpy as np
import pytest

from iotcenter import vision
from iotcenter.vision import SceneAnalyzer, grey_thumbnail

RATE = 5  # samples a second, like the monitor
W, H = 320, 180


def room(level: int = 110, seed: int = 0) -> np.ndarray:
    """A grey scene with a little sensor noise, as a webcam delivers it."""
    noise = np.random.default_rng(seed).normal(0, 2, (H, W))
    return np.clip(level + noise, 0, 255).astype(np.uint8)


def with_square(frame: np.ndarray, x: int, y: int, size: int = 30) -> np.ndarray:
    frame = frame.copy()
    frame[y : y + size, x : x + size] = 220
    return frame


def feed(analyzer: SceneAnalyzer, frames, start: float = 1000.0):
    return [analyzer.update(frame, start + i / RATE) for i, frame in enumerate(frames)]


def test_a_still_scene_has_no_motion():
    samples = feed(SceneAnalyzer(), [room(seed=i) for i in range(50)])  # 10 s of noise only
    assert max(s.motion_pct for s in samples) < vision.MOTION_START_PCT
    assert not any(s.moving for s in samples)
    assert 40 < samples[-1].brightness_pct < 46  # 110 of 255


def test_something_moving_becomes_one_event_with_boxes():
    analyzer = SceneAnalyzer()
    frames = [room(seed=i) for i in range(10)]  # 2 s still: learn the background
    frames += [with_square(room(seed=i), 20 + 8 * i, 60) for i in range(15)]  # 3 s moving
    frames += [room(seed=i) for i in range(25)]  # 5 s still again: the event ends
    samples = feed(analyzer, frames)

    moving = samples[10:25]
    assert all(s.motion_pct > vision.MOTION_START_PCT for s in moving[1:])
    assert moving[-1].moving
    # the square is inside one of the boxes (fractions of the picture)
    x, y = (20 + 8 * 14 + 15) / W, (60 + 15) / H
    assert any(bx <= x <= bx + bw and by <= y <= by + bh for bx, by, bw, bh in moving[-1].boxes)

    assert not samples[-1].moving
    [event] = analyzer.events
    assert event["kind"] == "motion"
    assert event["start"] == pytest.approx(1000 + 11 / RATE)  # needs two samples in a row
    assert event["end"] is not None and event["end"] > event["start"]
    assert event["peak_pct"] > 1


def test_a_single_flicker_is_not_an_event():
    analyzer = SceneAnalyzer()
    frames = [room(seed=i) for i in range(10)] + [with_square(room(), 100, 60)]
    frames += [room(seed=i) for i in range(10)]
    feed(analyzer, frames)
    assert not analyzer.events


def test_a_light_switch_is_an_event_not_motion():
    analyzer = SceneAnalyzer()
    frames = [room(110, seed=i) for i in range(10)]
    frames += [room(25, seed=i) for i in range(40)]  # off: the whole picture goes dark
    frames += [room(110, seed=i) for i in range(10)]  # on again 8 s later
    samples = feed(analyzer, frames)

    assert samples[10].light == "off"
    assert samples[50].light == "on"
    assert all(s.motion_pct == 0 for s in samples[10:20])  # ignored while it settles
    assert not any(s.moving for s in samples)
    assert [e["kind"] for e in analyzer.events] == ["lights_on", "lights_off"]
    assert analyzer.lights == "on"


def test_auto_exposure_swinging_back_is_not_a_second_switch():
    analyzer = SceneAnalyzer()
    levels = [110] * 10 + [20] + [40, 60, 80, 90] + [90] * 20  # the camera brightens itself
    feed(analyzer, [room(level, seed=i) for i, level in enumerate(levels)])
    assert [e["kind"] for e in analyzer.events] == ["lights_off"]


@pytest.mark.parametrize(
    ("size", "expected"),
    [((1920, 1080), (480, 270)), ((1280, 720), (320, 180)), ((640, 480), (320, 240))],
)
def test_thumbnails_are_decoded_small_but_not_too_small(size, expected):
    width, height = size
    ok, jpeg = cv2.imencode(".jpg", np.full((height, width, 3), 128, np.uint8))
    grey = grey_thumbnail(jpeg.tobytes(), width)
    assert grey.ndim == 2
    assert (grey.shape[1], grey.shape[0]) == expected


def test_a_damaged_jpeg_decodes_to_nothing():
    assert grey_thumbnail(b"\xff\xd8 not really a jpeg", 1920) is None


@pytest.mark.parametrize("kinds", [(), ("floor",), ("light",), ("floor", "light", "area")])
def test_every_frame_is_analysed_whatever_the_zones(kinds):
    """The monitor decodes differently with and without zones (a sharper copy for lights and,
    once a second, for floors): every path must give a sample."""
    from iotcenter.camera import Camera, DemoSource, demo_frame, demo_room
    from iotcenter.vision import ActivityMonitor
    from iotcenter.zones import Zone

    camera = Camera(DemoSource(width=1280, height=720))
    monitor = ActivityMonitor(camera)
    shapes = {
        "floor": (0.1, 0.7, 0.3, 0.25),
        "light": (0.72, 0.36, 0.18, 0.15),
        "area": (0.5, 0.6, 0.4, 0.3),
    }
    zones = [
        Zone(id=k, name=k, kind=k, x=x, y=y, w=w, h=h) for k in kinds for x, y, w, h in [shapes[k]]
    ]
    monitor.analyzer.zones.set_zones(zones)
    room = demo_room(1280, 720)
    for i in range(12):  # 2.4 s at 5 a second: floors due twice
        jpeg = cv2.imencode(".jpg", demo_frame(room, i / 5))[1].tobytes()
        sample = monitor._analyze(jpeg, 1000 + i / 5)
        assert sample is not None
        assert [z["id"] for z in sample.zones] == list(kinds)
