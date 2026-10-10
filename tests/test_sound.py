"""Sound analysis on synthetic sound: alarm patterns, chirps, loud noises (no microphone)."""

import numpy as np
import pytest

from iotcenter.sound import RATE, Beep, SoundAnalyzer, rhythm


def room(seconds: float, rng, noise: float = 0.01) -> np.ndarray:
    """A room's steady noise (about -40 dBFS) with a low hum, as float samples."""
    t = np.arange(int(seconds * RATE)) / RATE
    return rng.normal(0, noise, t.size) + 0.01 * np.sin(2 * np.pi * 60 * t)


def tone(sound: np.ndarray, start: float, length: float, freq: float, amplitude: float = 0.2):
    first, last = int(start * RATE), int((start + length) * RATE)
    t = np.arange(last - first) / RATE
    sound[first:last] += amplitude * np.sin(2 * np.pi * freq * t)


def temporal(sound, start: float, beeps: int, on: float, off: float, pause: float, cycles: int,
             freq: float) -> None:  # fmt: skip
    """A temporal alarm pattern: `beeps` beeps of `on` s, `off` s apart, then a pause."""
    t = start
    for _ in range(cycles):
        for _ in range(beeps):
            tone(sound, t, on, freq)
            t += on + off
        t += pause - off


def hear(sound: np.ndarray, start: float = 1000.0, chunk_s: float = 0.1):
    analyzer = SoundAnalyzer()
    pcm = (np.clip(sound, -1, 1) * 32767).astype(np.int16)
    step = int(chunk_s * RATE)
    samples = []
    for i in range(0, pcm.size, step):
        chunk = pcm[i : i + step]
        samples += analyzer.update(chunk, start + (i + chunk.size) / RATE)
    return analyzer, samples


@pytest.fixture
def rng():
    return np.random.default_rng(3)


def test_a_quiet_room_has_a_level_and_no_events(rng):
    analyzer, samples = hear(room(20, rng), chunk_s=0.1)
    assert -45 < analyzer.background_db < -30
    assert not analyzer.events
    assert len(samples) == pytest.approx(20 / 0.25, abs=2)  # four live samples a second
    assert {"t", "level_db", "background_db", "beeping", "alarm"} == set(samples[-1])
    assert len(analyzer.history) == pytest.approx(20, abs=1)


def test_a_smoke_alarm_temporal_3(rng):
    sound = room(30, rng)
    temporal(sound, 5, beeps=3, on=0.5, off=0.5, pause=1.5, cycles=4, freq=3200)
    analyzer, samples = hear(sound)
    [alarm] = [e for e in analyzer.events if e["kind"] == "alarm"]
    assert alarm["pattern"] == "smoke"
    assert alarm["freq_hz"] == pytest.approx(3200, abs=50)
    assert alarm["start"] == pytest.approx(1005, abs=0.1)
    assert any(s["alarm"] == "smoke" for s in samples)
    assert alarm["end"] is not None  # 10 s after the last beep
    assert analyzer.alarm is None
    assert not any(e["kind"] == "loud" for e in analyzer.events)  # it was an alarm, not a bang


def test_a_co_alarm_temporal_4(rng):
    sound = room(25, rng)
    temporal(sound, 4, beeps=4, on=0.1, off=0.1, pause=5.0, cycles=3, freq=3400)
    analyzer, _ = hear(sound)
    [alarm] = [e for e in analyzer.events if e["kind"] == "alarm"]
    assert alarm["pattern"] == "co"


def test_other_beeping(rng):
    sound = room(20, rng)
    for start in (3, 4.5, 6, 7.5):  # a leak sensor: 0.2 s beeps every 1.5 s
        tone(sound, start, 0.2, 2900)
    analyzer, _ = hear(sound)
    [alarm] = [e for e in analyzer.events if e["kind"] == "alarm"]
    assert alarm["pattern"] == "beeping"


def test_a_low_battery_chirp(rng):
    sound = room(100, rng)
    for start in (5, 35, 65, 95):  # one short chirp every 30 s
        tone(sound, start, 0.08, 3800)
    analyzer, _ = hear(sound)
    [chirping] = [e for e in analyzer.events if e["kind"] == "chirping"]
    assert chirping["interval_s"] == 30
    assert chirping["count"] == 4
    assert not any(e["kind"] == "alarm" for e in analyzer.events)


def test_a_loud_bang(rng):
    sound = room(15, rng)
    sound[int(10 * RATE) : int(10.05 * RATE)] += rng.normal(0, 0.5, int(0.05 * RATE))
    analyzer, _ = hear(sound)
    [loud] = [e for e in analyzer.events if e["kind"] == "loud"]
    assert loud["start"] == pytest.approx(1010, abs=0.1)
    assert loud["over_db"] >= 20


def test_speech_like_noise_is_not_a_beep(rng):
    sound = room(20, rng)
    for start in np.arange(3, 15, 0.7):  # bursts of broadband noise, like voices or a fan
        first = int(start * RATE)
        sound[first : first + 4000] += rng.normal(0, 0.05, 4000)
    analyzer, _ = hear(sound)
    assert not any(e["kind"] in ("alarm", "chirping") for e in analyzer.events)


def test_rhythm_patterns():
    def run(n, on, off, freq=3000.0, start=0.0):
        return [Beep(start + i * (on + off), on, freq) for i in range(n)]

    assert rhythm(run(3, 0.5, 0.5)) == "smoke"
    assert rhythm(run(4, 0.1, 0.1)) == "co"
    assert rhythm(run(3, 0.2, 1.2)) == "beeping"
    assert rhythm(run(2, 0.5, 0.5)) is None
