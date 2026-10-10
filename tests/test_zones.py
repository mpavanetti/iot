"""Zones on synthetic frames: water on a floor, a status light, motion in an area."""

import json

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from iotcenter import zones as zones_module
from iotcenter.vision import SceneAnalyzer
from iotcenter.zones import Zone, ZoneSpec, ZoneStore

RATE = 5
W, H = 320, 180
FLOOR = Zone(id="floor1", name="Heater floor", kind="floor", x=0.1, y=0.55, w=0.4, h=0.4)
LED = Zone(id="led1", name="Furnace light", kind="light", x=0.7, y=0.2, w=0.1, h=0.15)
AREA = Zone(id="area1", name="Stairs", kind="area", x=0.6, y=0.5, w=0.35, h=0.45)


def scene(rng, level: float = 150.0, puddle: bool = False, led: bool = False, gain: float = 1.0,
          lamp: bool = False, glare: bool = False):  # fmt: skip
    """A grey room (BGR) with sensor noise; optionally a puddle on the floor, a lit blue LED,
    a second lamp lighting the right of the room, standing water mirroring the ceiling light."""
    frame = np.full((H, W, 3), level, np.float32)
    if lamp:  # another lighting: the right half much brighter, the left a little darker
        frame[:, : W // 2] *= 0.85
        frame[:, W // 2 :] *= 1.4
    if puddle:  # wet concrete: 30% darker, a blob in the floor zone
        frame[120:160, 50:110] *= 0.7
    if glare:
        frame[125:140, 60:90] = 245
    frame *= gain
    frame += rng.normal(0, 2, frame.shape)
    if led:
        frame[45:55, 230:245] = (250, 70, 40)  # BGR: blue
    return np.clip(frame, 0, 255).astype(np.uint8)


def run(analyzer, frames, start=1000.0):
    samples = []
    for i, colour in enumerate(frames):
        grey = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
        samples.append(analyzer.update(grey, start + i / RATE, colour))
    return samples


def zone_state(sample, zone_id):
    return next(z for z in sample.zones if z["id"] == zone_id)


@pytest.fixture
def rng():
    return np.random.default_rng(7)


def test_water_spreading_on_the_floor_is_reported_and_clears(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    dry, wet = 15 * RATE, 80 * RATE  # 15 s to learn the dry floor, then 80 s with a puddle
    frames = [scene(rng) for _ in range(dry)] + [scene(rng, puddle=True) for _ in range(wet)]
    samples = run(analyzer, frames)

    assert zone_state(samples[dry - 1], "floor1")["state"] == "dry"
    floor = zone_state(samples[-1], "floor1")
    assert floor["state"] == "water"
    assert floor["wet_pct"] > 10
    x, y, w, h = floor["box"]  # around the puddle (columns 50-110, rows 120-160)
    assert x == pytest.approx(50 / W, abs=0.03) and y == pytest.approx(120 / H, abs=0.03)
    [event] = [e for e in analyzer.events if e["kind"] == "water"]
    assert event["zone"] == "Heater floor" and event["end"] is None

    # mopped up: once the zone is quiet again (20 s) and stays dry for a minute, it is dry
    samples = run(analyzer, [scene(rng) for _ in range(100 * RATE)], start=1000 + 95)
    assert zone_state(samples[-1], "floor1")["state"] == "dry"
    assert event["end"] is not None


def test_brighter_or_darker_lighting_is_not_water(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    frames = [scene(rng) for _ in range(15 * RATE)]
    frames += [scene(rng, gain=0.75) for _ in range(60 * RATE)]  # dimmer daylight, all over
    samples = run(analyzer, frames)
    assert zone_state(samples[-1], "floor1")["state"] in ("dry", "learning")
    assert not any(e["kind"] == "water" for e in analyzer.events)


def test_a_dark_room_is_not_judged(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    frames = [scene(rng) for _ in range(15 * RATE)] + [scene(rng, level=15) for _ in range(60)]
    samples = run(analyzer, frames)
    assert zone_state(samples[-1], "floor1")["state"] == "paused"


def test_a_status_light_turning_on_and_off(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([LED])
    pattern = [False] * 10 + [True] * 50 + [False] * 30  # off 2 s, on 10 s, off 6 s
    samples = run(analyzer, [scene(rng, led=on) for on in pattern])

    assert zone_state(samples[5], "led1")["state"] == "off"
    assert zone_state(samples[40], "led1")["state"] == "on"
    light = zone_state(samples[-1], "led1")
    assert light["state"] == "off"
    assert light["on_count_24h"] == 1
    assert light["on_s_24h"] == pytest.approx(10, abs=1.5)
    [event] = [e for e in analyzer.events if e["kind"] == "light_on"]
    assert event["zone"] == "Furnace light"
    assert event["end"] - event["start"] == pytest.approx(10, abs=1.5)


def test_the_room_lights_do_not_turn_a_status_light_on(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([LED])
    levels = [150] * 20 + [20] * 30 + [230] * 30  # lights off, then very bright
    samples = run(analyzer, [scene(rng, level=level) for level in levels])
    assert {zone_state(s, "led1")["state"] for s in samples} == {"off"}


def test_a_blinking_light(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([LED])
    blinks = ([True] * 2 + [False] * 2) * 15  # 0.4 s on, 0.4 s off for 12 s
    samples = run(analyzer, [scene(rng) for _ in range(10)] + [scene(rng, led=on) for on in blinks])
    assert zone_state(samples[-1], "led1")["state"] == "blinking"
    assert any(e["kind"] == "light_blinking" for e in analyzer.events)


def test_a_motion_event_names_the_zones_it_touched(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([AREA, FLOOR])
    frames = [scene(rng) for _ in range(10)]
    for i in range(10):  # something crosses the stairs area only
        frame = scene(rng)
        frame[100:150, 210 + 8 * i : 240 + 8 * i] = 30
        frames.append(frame)
    samples = run(analyzer, frames)
    assert zone_state(samples[-1], "area1")["state"] == "moving"
    [motion] = [e for e in analyzer.events if e["kind"] == "motion"]
    assert motion["zones"] == ["Stairs"]


def test_redrawing_other_zones_keeps_what_a_zone_learned(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    run(analyzer, [scene(rng) for _ in range(15 * RATE)])
    watch = analyzer.zones._watches["floor1"]
    renamed = FLOOR.model_copy(update={"name": "Water heater"})
    analyzer.zones.set_zones([renamed, LED])
    assert analyzer.zones._watches["floor1"] is watch
    assert watch.zone.name == "Water heater"
    moved = FLOOR.model_copy(update={"x": 0.2})
    analyzer.zones.set_zones([moved])
    assert analyzer.zones._watches["floor1"] is not watch  # a moved zone starts over


def test_zones_are_kept_in_a_json_file(tmp_path):
    store = ZoneStore(tmp_path / "data" / "camera-zones.json")
    assert store.load() == []
    store.save([FLOOR, LED])
    assert store.load() == [FLOOR, LED]
    saved = json.loads(store.path.read_text())
    assert set(saved["zones"][0]) == {"id", "name", "kind", "x", "y", "w", "h", "enabled"}
    store.path.write_text("{broken")
    assert store.load() == []


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "", "kind": "floor", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
        {"name": "a", "kind": "door", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
        {"name": "a", "kind": "floor", "x": 0.8, "y": 0, "w": 0.5, "h": 0.5},
        {"name": "a", "kind": "floor", "x": 0, "y": 0, "w": 0.001, "h": 0.5},
    ],
)
def test_zone_specs_are_validated(bad):
    with pytest.raises(ValidationError):
        ZoneSpec(**bad)


def test_pixels_cover_at_least_one_pixel():
    tiny = Zone(id="t", name="t", kind="area", x=0.99, y=0.99, w=0.01, h=0.01)
    rows, cols = tiny.pixels(W, H)
    assert rows.stop > rows.start and cols.stop > cols.start
    assert zones_module.MAX_ZONES >= 8


def test_a_light_found_on_has_no_event_until_it_changes(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([LED])
    samples = run(analyzer, [scene(rng, led=True) for _ in range(20)])
    light = zone_state(samples[-1], "led1")
    assert light["state"] == "on" and light["since_start"] is True
    assert not analyzer.events
    samples = run(analyzer, [scene(rng) for _ in range(20)], start=1004)
    light = zone_state(samples[-1], "led1")
    assert light["state"] == "off" and light["since_start"] is False
    assert light["on_count_24h"] == 1  # the on period seen from the start still counts


def test_a_leak_in_the_dark_is_seen_when_the_lights_come_back_on(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    frames = [scene(rng) for _ in range(15 * RATE)]  # lights on: learn the dry floor
    frames += [scene(rng, level=10) for _ in range(60 * RATE)]  # night: too dark to judge
    frames += [scene(rng, level=10, puddle=True) for _ in range(60 * RATE)]  # it leaks
    frames += [scene(rng, puddle=True) for _ in range(60 * RATE)]  # morning: lights on
    samples = run(analyzer, frames)
    assert zone_state(samples[15 * RATE + 100], "floor1")["state"] == "paused"
    floor = zone_state(samples[-1], "floor1")
    assert floor["state"] == "water", floor
    assert floor["lightings"] == 1  # the morning was recognised as the lighting it learned


def test_each_lighting_has_its_own_dry_floor(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    frames = [scene(rng) for _ in range(15 * RATE)]
    frames += [scene(rng, lamp=True) for _ in range(60 * RATE)]  # another lamp: not water
    frames += [scene(rng) for _ in range(30 * RATE)]  # back to the first lighting
    frames += [scene(rng, lamp=True, puddle=True) for _ in range(60 * RATE)]  # a leak, lamp on
    samples = run(analyzer, frames)
    assert not any(zone_state(s, "floor1")["state"] == "water" for s in samples[: 105 * RATE])
    floor = zone_state(samples[-1], "floor1")
    assert floor["lightings"] == 2
    assert floor["state"] == "water"
    assert [e["kind"] for e in analyzer.events if e["kind"] == "new_view"] == ["new_view"]


def test_standing_water_mirroring_a_lamp_counts_too(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    frames = [scene(rng) for _ in range(15 * RATE)] + [
        scene(rng, glare=True) for _ in range(80 * RATE)
    ]
    assert zone_state(run(analyzer, frames)[-1], "floor1")["state"] == "water"


def test_a_small_puddle_in_a_big_zone(rng):
    big = Zone(id="big", name="Whole floor", kind="floor", x=0.0, y=0.5, w=1.0, h=0.5)
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([big])
    frames = [scene(rng) for _ in range(15 * RATE)]
    for _ in range(80 * RATE):  # a puddle of 1% of the zone (0.5% of the picture)
        frame = scene(rng)
        frame[150:162, 100:124] = (frame[150:162, 100:124] * 0.7).astype(np.uint8)
        frames.append(frame)
    assert zone_state(run(analyzer, frames)[-1], "big")["state"] == "water"


def test_someone_moving_anywhere_makes_the_floors_wait(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    frames = [scene(rng) for _ in range(15 * RATE)]
    for i in range(10 * RATE):  # someone walks around at the back of the room
        frame = scene(rng)
        x = 150 + (i * 6) % 150
        frame[20:80, x : x + 25] = 40
        frames.append(frame)
    samples = run(analyzer, frames)
    assert zone_state(samples[-1], "floor1")["state"] == "paused"


def test_a_switched_off_zone_is_kept_but_not_watched(rng):
    analyzer = SceneAnalyzer()
    off = FLOOR.model_copy(update={"enabled": False})
    analyzer.zones.set_zones([off])
    frames = [scene(rng) for _ in range(15 * RATE)] + [
        scene(rng, puddle=True) for _ in range(80 * RATE)
    ]
    samples = run(analyzer, frames)
    floor = zone_state(samples[-1], "floor1")
    assert floor["state"] == "off" and floor["enabled"] is False
    assert not any(e["kind"] == "water" for e in analyzer.events)


def floor_with(rng, *, wet=False, plate=False, shadow=False):
    """A grey concrete floor (BGR) with sensor noise; a wet patch (the floor's colour, darker,
    clear edges), a bluish painted plate, or a soft shadow."""
    floor = np.full((H, W, 3), (128, 130, 132), np.float32)
    if wet:
        floor[120:150, 60:100] *= 0.72
    if plate:
        mask = np.zeros((H, W), np.uint8)
        cv2.circle(mask, (80, 135), 16, 1, -1)
        floor[mask > 0] = (118, 100, 88)  # slate blue: blue well above red
    if shadow:
        soft = np.zeros((H, W), np.float32)
        soft[110:160, 50:110] = 0.3
        soft = cv2.GaussianBlur(soft, (0, 0), 12)
        floor *= (1 - soft)[..., None]
    floor += rng.normal(0, 2, floor.shape)
    return np.clip(floor, 0, 255).astype(np.uint8)


def test_water_already_there_is_found_without_a_dry_floor(rng):
    from iotcenter.zones import wet_patches

    def found(frame):
        rows, cols = FLOOR.pixels(W, H)
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return int(wet_patches(grey[rows, cols], frame[rows, cols]).sum())

    assert found(floor_with(rng, wet=True)) > 200
    assert found(floor_with(rng, plate=True)) == 0  # painted: bluer than the floor
    assert found(floor_with(rng, shadow=True)) == 0  # soft edges
    assert found(floor_with(rng)) == 0


def test_a_zone_started_on_a_wet_floor_asks_and_clears_when_it_dries(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    samples = run(analyzer, [floor_with(rng, wet=True) for _ in range(50 * RATE)])
    floor = zone_state(samples[-1], "floor1")
    assert floor["state"] == "check"  # water or a stain? one picture cannot tell
    assert floor["stain_at"] is not None
    [event] = [e for e in analyzer.events if e["kind"] == "water"]
    assert event["unconfirmed"] is True
    samples = run(analyzer, [floor_with(rng) for _ in range(100 * RATE)], start=1050)  # dried
    assert zone_state(samples[-1], "floor1")["state"] == "dry"
    assert event["end"] is not None


def wet_floor(rng, darker: float = 0.72, rows=(120, 150), cols=(60, 100)):
    floor = np.full((H, W, 3), (128, 130, 132), np.float32)
    floor[rows[0] : rows[1], cols[0] : cols[1]] *= darker
    floor += rng.normal(0, 2, floor.shape)
    return np.clip(floor, 0, 255).astype(np.uint8)


def test_a_dark_patch_that_never_changes_is_a_stain(rng, monkeypatch):
    monkeypatch.setattr(zones_module, "STAIN_S", 60.0)
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    samples = run(analyzer, [wet_floor(rng) for _ in range(150 * RATE)])
    floor = zone_state(samples[-1], "floor1")
    assert floor["state"] == "dry"  # learned as part of the floor
    [event] = [e for e in analyzer.events if e["kind"] == "water"]
    assert event["stain"] is True and event["end"] is not None
    assert not any(zone_state(s, "floor1")["state"] == "water" for s in samples)  # never red


def test_a_dark_patch_that_fades_was_water_drying(rng, monkeypatch):
    monkeypatch.setattr(zones_module, "STAIN_S", 60.0)
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    run(analyzer, [wet_floor(rng, 0.70) for _ in range(40 * RATE)])
    samples = run(analyzer, [wet_floor(rng, 0.78) for _ in range(40 * RATE)], start=1040)  # lighter
    floor = zone_state(samples[-1], "floor1")
    assert floor["drying"] is True and floor["state"] == "check"  # not a stain: it changes
    [event] = [e for e in analyzer.events if e["kind"] == "water"]
    assert event["drying"] is True and not event.get("stain")


def test_a_dark_patch_that_spreads_is_water(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    run(analyzer, [wet_floor(rng) for _ in range(45 * RATE)])
    spread = [wet_floor(rng, cols=(60, 150)) for _ in range(40 * RATE)]  # more than twice as big
    floor = zone_state(run(analyzer, spread, start=1045)[-1], "floor1")
    assert floor["state"] == "water"


def test_a_drain_reports_a_pool_not_its_usual_wetness(rng):
    drain = Zone(id="drain", name="Floor drain", kind="drain", x=0.1, y=0.55, w=0.4, h=0.4)
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([drain])
    run(analyzer, [floor_with(rng) for _ in range(15 * RATE)])
    streak = [wet_floor(rng, rows=(130, 136), cols=(70, 110)) for _ in range(60 * RATE)]
    samples = run(analyzer, streak, start=1015)  # condensate running in: normal
    assert {zone_state(s, "drain")["state"] for s in samples[-50:]} <= {"dry", "paused"}
    pool = [wet_floor(rng, rows=(100, 170), cols=(35, 160)) for _ in range(60 * RATE)]
    samples = run(analyzer, pool, start=1075)  # backing up
    assert zone_state(samples[-1], "drain")["state"] == "water"


def test_floor_is_dry_trusts_the_floor_as_it_is(rng):
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([FLOOR])
    run(analyzer, [floor_with(rng, wet=True) for _ in range(50 * RATE)])
    assert analyzer.zones.reset("floor1")  # "it's a stain, not water"
    samples = run(analyzer, [floor_with(rng, wet=True) for _ in range(60 * RATE)], start=1050)
    assert zone_state(samples[-1], "floor1")["state"] == "dry"


def test_a_white_led_toggling_with_each_delivery_blinks(rng):
    pico = Zone(id="pico", name="Pico W data LED", kind="light", x=0.7, y=0.2, w=0.08, h=0.12)
    analyzer = SceneAnalyzer()
    analyzer.zones.set_zones([pico])

    def board(lit):
        frame = scene(rng, level=90)
        if lit:
            frame[47:52, 235:240] = 250  # a white LED: bright, no colour
        return frame

    toggles = [board((i // (2 * RATE)) % 2 == 0) for i in range(16 * RATE)]  # changes every 2 s
    light = zone_state(run(analyzer, toggles)[-1], "pico")
    assert light["state"] == "blinking"
    assert light["flip_s"] == pytest.approx(2.0, abs=0.3)
    steady = run(analyzer, [board(True) for _ in range(15 * RATE)], start=1016)
    light = zone_state(steady[-1], "pico")
    assert light["state"] == "on" and light["flip_s"] is None  # it stopped: no deliveries
