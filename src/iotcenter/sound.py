"""What the microphone hears: how loud it is, and the sounds that matter.

The analyser takes 16 kHz mono sound, 32 ms at a time every 16 ms, and works out:

  * level, in dBFS: 0 is the loudest sound the microphone can record. A background level
    follows the room's steady noise (a furnace, a fan) over about a minute.
  * loud noise: 20 dB over the background (a bang, something falling) is an event.
  * beeps: a pure tone between 2.5 and 4.5 kHz, where smoke and CO alarms sound, standing
    15 dB over that band's usual level. Their rhythm says what is beeping:
      - smoke alarm, the temporal-3 pattern: three beeps of about 0.5 s, 0.5 s apart, a pause
      - CO alarm, temporal-4: four beeps of about 0.1 s, 0.1 s apart, then a pause
      - other beeping: three or more beeps of one pitch within 6 s (a leak sensor, a pump alarm)
      - chirping: one short beep every 20 s to 2 min at a steady pace, a detector's low battery

Plain computation on arrays, no I/O; `microphone.py` feeds it. Nothing is recorded: the sound
is analysed and dropped, and only the numbers and events stay, in memory.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Any

import numpy as np

RATE = 16_000
WINDOW = 512  # 32 ms: short enough to hear the gaps of a CO alarm's 0.1 s beeps
HOP = 256  # 16 ms
HOP_S = HOP / RATE
SAMPLE_S = 0.25  # a live sample (a `sound` event) four times a second
SILENCE_DB = -90.0
BACKGROUND_S = 60.0
LOUD_DB = 20.0  # over the background
LOUD_MIN_DB = -50.0  # and at least this loud
LOUD_MERGE_S = 5.0
BEEP_BAND = (2500.0, 4500.0)
BEEP_TONE = 0.4  # share of the sound's energy in the beep's tone
BEEP_OVER_DB = 15.0  # over the band's usual level (its 20th percentile over 10 s)
BAND_HISTORY_S = 10.0
BEEP_MIN_S, BEEP_MAX_S = 0.05, 1.5
SAME_PITCH_HZ = 150.0
RUN_GAP_S = 0.9  # beeps closer than this belong to one group
ALARM_END_S = 10.0  # an alarm is over this long after its last beep
CHIRP_MAX_S = 0.25
CHIRP_GAPS = (20.0, 120.0)
HISTORY_S = 600
MAX_EVENTS = 50
PATTERNS = {"co": 3, "smoke": 2, "beeping": 1}  # the more specific pattern wins


@dataclass(frozen=True, slots=True)
class Beep:
    start: float
    duration: float
    freq: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def rhythm(beeps: list[Beep]) -> str | None:
    """The alarm pattern a run of same-pitch beeps (oldest first) follows, if any."""
    runs, run = [], beeps[:1]
    for previous, beep in zip(beeps, beeps[1:], strict=False):
        if beep.start - previous.end <= RUN_GAP_S:
            run.append(beep)
        else:
            runs.append(run)
            run = [beep]
    runs.append(run)
    for group in runs:
        if len(group) < 3:
            continue
        length = median(b.duration for b in group)
        gap = median(b.start - a.end for a, b in zip(group, group[1:], strict=False))
        if len(group) >= 4 and length <= 0.25 and gap <= 0.3:
            return "co"
        if 0.3 <= length <= 0.9 and 0.2 <= gap <= 0.9:
            return "smoke"
    last = beeps[-1].start
    return "beeping" if sum(b.start >= last - 6 for b in beeps) >= 3 else None


class SoundAnalyzer:
    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)  # newest first
        self.history: deque[tuple[float, float]] = deque(maxlen=HISTORY_S)  # (t, peak dBFS)
        self.level_db = SILENCE_DB
        self.background_db: float | None = None
        self.alarm: dict[str, Any] | None = None
        self.chirping: dict[str, Any] | None = None
        self.beeps: deque[Beep] = deque(maxlen=64)
        self._window = np.hanning(WINDOW).astype(np.float32)
        freqs = np.fft.rfftfreq(WINDOW, 1 / RATE)
        self._freqs = freqs
        self._audible = (freqs >= 100) & (freqs <= 7500)
        self._band = np.flatnonzero((freqs >= BEEP_BAND[0]) & (freqs <= BEEP_BAND[1]))
        self._band_levels: deque[float] = deque(maxlen=int(BAND_HISTORY_S / HOP_S))
        self._pending = np.zeros(0, np.float32)
        self._started: float | None = None
        self._beep: list[Any] | None = None  # the beep being heard: [start, hops, pitches]
        self._chirps: deque[Beep] = deque(maxlen=8)
        self._alarm_last = self._chirp_last = 0.0
        self._loud: dict[str, Any] | None = None
        self._peak = self._second_peak = SILENCE_DB
        self._sample_at: float | None = None

    @property
    def beeping(self) -> bool:
        return self._beep is not None

    def update(self, pcm: np.ndarray, t_end: float) -> list[dict[str, Any]]:
        """Analyse new sound (int16, mono, its last sample heard at `t_end`) and return the
        live samples that are due."""
        buffer = np.concatenate([self._pending, pcm.astype(np.float32) / 32768])
        samples = []
        start = 0
        while start + WINDOW <= len(buffer):
            t = t_end - (len(buffer) - start - WINDOW) / RATE  # when its last sample was heard
            sample = self._hop(buffer[start : start + WINDOW], t)
            if sample is not None:
                samples.append(sample)
            start += HOP
        self._pending = buffer[start:]
        return samples

    def _hop(self, frame: np.ndarray, t: float) -> dict[str, Any] | None:
        newest = frame[-HOP:]
        level = max(SILENCE_DB, 20 * math.log10(float(np.sqrt(np.mean(newest**2))) + 1e-12))
        tonal = self._listen_for_beeps(np.abs(np.fft.rfft(frame * self._window)) ** 2, t)
        self._level(level, t, tonal)
        if self.alarm is not None and t - self._alarm_last > ALARM_END_S:
            self.alarm["end"] = self._alarm_last
            self.alarm = None
        if self.chirping is not None and t - self._chirp_last > 3 * self.chirping["interval_s"]:
            self.chirping["end"] = self._chirp_last
            self.chirping = None
        return self._sample(level, t)

    def _level(self, level: float, t: float, tonal: bool) -> None:
        self.level_db = level
        if self._started is None:
            self._started, self.background_db = t, level
        elif level < self.background_db + 10:  # loud moments do not raise the background
            settle = min(BACKGROUND_S, max(1.0, t - self._started))  # quick at first
            self.background_db += (level - self.background_db) * HOP_S / settle
        quiet = level < max(self.background_db + LOUD_DB, LOUD_MIN_DB)
        if quiet or tonal or self.beeping or self.alarm:  # a loud beep is not a bang
            return
        loud = self._loud
        if loud is None or t - loud["end"] > LOUD_MERGE_S:
            self._loud = {"kind": "loud", "start": t, "end": t, "peak_db": round(level, 1)}
            self._loud["over_db"] = round(level - self.background_db, 1)
            self.events.appendleft(self._loud)
        else:
            loud["end"], loud["peak_db"] = t, round(max(loud["peak_db"], level), 1)

    def _listen_for_beeps(self, spectrum: np.ndarray, t: float) -> bool:
        """Follow beeps; True when this moment's sound is mostly one tone in the band."""
        band = spectrum[self._band]
        peak = int(np.argmax(band))
        tone = float(band[max(0, peak - 2) : peak + 3].sum())
        tone_db = 10 * math.log10(tone + 1e-12)
        enough = len(self._band_levels) >= 60  # a second of history first
        floor = float(np.percentile(self._band_levels, 20)) if enough else None
        self._band_levels.append(tone_db)
        total = float(spectrum[self._audible].sum()) + 1e-12
        tonal = tone / total >= BEEP_TONE
        on = tonal and floor is not None and tone_db >= floor + BEEP_OVER_DB
        if on:
            if self._beep is None:
                self._beep = [t - WINDOW / RATE / 2, 0, []]
            self._beep[1] += 1
            self._beep[2].append(float(self._freqs[self._band[peak]]))
        elif self._beep is not None:
            start, hops, pitches = self._beep
            self._beep = None
            duration = hops * HOP_S
            if BEEP_MIN_S <= duration <= BEEP_MAX_S:
                self._heard(Beep(start, duration, median(pitches)))
        return tonal

    def _heard(self, beep: Beep) -> None:
        self.beeps.append(beep)
        same = [
            b
            for b in self.beeps
            if b.start >= beep.start - 12 and abs(b.freq - beep.freq) <= SAME_PITCH_HZ
        ]
        pattern = rhythm(same)
        if pattern is not None:
            self._alarm_last = beep.end
            if self.alarm is None:
                self.alarm = {
                    "kind": "alarm",
                    "pattern": pattern,
                    "freq_hz": round(beep.freq),
                    "start": same[0].start,
                    "end": None,
                }
                self.events.appendleft(self.alarm)
                first = same[0].start  # its first beep was no low-battery chirp
                self._chirps = deque((c for c in self._chirps if c.start < first), maxlen=8)
            elif PATTERNS[pattern] > PATTERNS[self.alarm["pattern"]]:
                self.alarm["pattern"] = pattern
        elif len(same) == 1 and beep.duration <= CHIRP_MAX_S:
            self._chirp(beep)

    def _chirp(self, beep: Beep) -> None:
        self._chirps.append(beep)
        starts = [c.start for c in self._chirps if abs(c.freq - beep.freq) <= SAME_PITCH_HZ][-4:]
        gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
        steady = len(gaps) >= 2 and max(gaps) <= 1.3 * min(gaps)
        if not steady or not all(CHIRP_GAPS[0] <= g <= CHIRP_GAPS[1] for g in gaps):
            return
        self._chirp_last = beep.start
        if self.chirping is None:
            self.chirping = {
                "kind": "chirping",
                "start": starts[0],
                "end": None,
                "interval_s": round(median(gaps)),
                "freq_hz": round(beep.freq),
                "count": len(starts),
            }
            self.events.appendleft(self.chirping)
        else:
            self.chirping["interval_s"] = round(median(gaps))
            self.chirping["count"] += 1

    def _sample(self, level: float, t: float) -> dict[str, Any] | None:
        self._peak = max(self._peak, level)
        self._second_peak = max(self._second_peak, level)
        if not self.history or t - self.history[-1][0] >= 1.0:
            self.history.append((round(t, 1), round(self._second_peak, 1)))
            self._second_peak = SILENCE_DB
        if self._sample_at is None:
            self._sample_at = t
        if t - self._sample_at < SAMPLE_S:
            return None
        sample = {
            "t": round(t, 2),
            "level_db": round(self._peak, 1),
            "background_db": round(self.background_db, 1),
            "beeping": self.beeping,
            "alarm": self.alarm["pattern"] if self.alarm else None,
        }
        self._peak, self._sample_at = SILENCE_DB, t
        return sample

    def summary(self, limit: int = 20) -> dict[str, Any]:
        return {
            "level_db": round(self.level_db, 1),
            "background_db": None if self.background_db is None else round(self.background_db, 1),
            "alarm": dict(self.alarm) if self.alarm else None,
            "chirping": dict(self.chirping) if self.chirping else None,
            "events": [dict(event) for event in list(self.events)[:limit]],
        }
