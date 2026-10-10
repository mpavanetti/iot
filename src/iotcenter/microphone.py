"""The microphone: the webcam's (or any ALSA capture device), heard live and analysed.

    microphone ──arecord, 16 kHz mono──▶ capture thread ─┬─▶ sound.SoundAnalyzer ─▶ `sound` events
                                                         └─▶ /api/sound/live.wav (Listen)

Capture runs `arecord` (alsa-utils), which converts whatever the device offers to 16 kHz mono.
The analyser hears every 0.1 s chunk; listeners get the same chunks, and a slow one loses the
oldest rather than falling behind. Like the camera, the microphone retries every few seconds
when it is unplugged, and nothing is ever recorded: the sound is analysed and dropped.

`--microphone demo` plays a synthetic room instead: a hum, a bang now and then, a detector
chirping every 40 s and a smoke alarm (temporal-3) for 9 s every 2 minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import struct
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

import numpy as np
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from .sound import RATE, SoundAnalyzer

log = logging.getLogger(__name__)

CHUNK_S = 0.1
CHUNK_BYTES = int(RATE * CHUNK_S) * 2  # 16-bit mono
RETRY_S = 3.0
DEMO = "demo"
LISTEN_QUEUE = 30  # chunks (3 s) a listener may fall behind before losing the oldest


class MicrophoneError(Exception):
    """The microphone cannot be opened, or stopped sending sound."""


class SoundSource(Protocol):
    def open(self) -> None: ...

    def read(self) -> bytes:
        """The next CHUNK_S of 16 kHz, 16-bit mono sound; raises MicrophoneError when it ends."""

    def close(self) -> None: ...


class ArecordSource:
    """An ALSA capture device through `arecord`, e.g. plughw:CARD=C960,DEV=0."""

    def __init__(self, device: str) -> None:
        self.device = device
        self._process: subprocess.Popen[bytes] | None = None

    def open(self) -> None:
        if shutil.which("arecord") is None:
            raise MicrophoneError("arecord not found: install alsa-utils")
        command = ["arecord", "-q", "-D", self.device, "-f", "S16_LE", "-r", str(RATE)]
        command += ["-c", "1", "-t", "raw"]
        self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def read(self) -> bytes:
        data = self._process.stdout.read(CHUNK_BYTES)
        if len(data) < CHUNK_BYTES:
            self._process.wait(timeout=2)
            error = self._process.stderr.read().decode(errors="replace").strip().splitlines()
            raise MicrophoneError(error[-1] if error else "the microphone stopped sending sound")
        return data

    def close(self) -> None:
        if self._process is not None:
            self._process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(timeout=2)
            self._process = None


class DemoMicrophone:
    """A synthetic room to hear: no microphone needed."""

    CYCLE_S = 120.0

    def open(self) -> None:
        self._rng = np.random.default_rng()
        self._heard = 0  # samples so far
        self._started = time.monotonic()

    def read(self) -> bytes:
        size = CHUNK_BYTES // 2
        delay = self._started + (self._heard + size) / RATE - time.monotonic()
        if delay > 0:
            time.sleep(delay)  # in real time, like a microphone
        t = (self._heard + np.arange(size)) / RATE
        self._heard += size
        sound = self._rng.normal(0, 0.004, size) + 0.006 * np.sin(2 * np.pi * 60 * t)
        phase = t % self.CYCLE_S
        alarm = (phase >= 20) & (phase < 29) & ((phase - 20) % 3 < 2.5) & ((phase - 20) % 1 < 0.5)
        sound += np.where(alarm, 0.1 * np.sin(2 * np.pi * 3200 * t), 0)  # temporal-3
        chirp = (t % 40 >= 5) & (t % 40 < 5.06)
        sound += np.where(chirp, 0.05 * np.sin(2 * np.pi * 3700 * t), 0)
        bang = (phase % 50 >= 48) & (phase % 50 < 48.05)
        sound += np.where(bang, self._rng.normal(0, 0.3, size), 0)
        return (np.clip(sound, -1, 1) * 32767).astype(np.int16).tobytes()

    def close(self) -> None:
        pass


def open_source(device: str) -> SoundSource:
    return DemoMicrophone() if device == DEMO else ArecordSource(device)


class Microphone:
    """Hears on a thread of its own (reads block); analyses and shares every chunk."""

    def __init__(
        self,
        source: SoundSource,
        device: str | None = None,
        publish: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.source = source
        self.device = device
        self.analyzer = SoundAnalyzer()
        self.state = "starting"  # starting | listening | unavailable | stopped
        self.error: str | None = None
        self.listening_since: float | None = None
        self.opened = 0
        self._publish = publish
        self._listeners: set[asyncio.Queue[bytes | None]] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._run, name="microphone", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        for queue in self._listeners:
            _offer(queue, None)  # ends the live streams
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5)
        self.state = "stopped"

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.source.open()
                self.opened += 1
                while not self._stop.is_set():
                    self._heard(self.source.read())
            except Exception as exc:  # MicrophoneError, or a broken pipe
                if str(exc) != self.error:
                    log.warning("Microphone %s: %s", self.device, exc)
                self.state, self.error, self.listening_since = "unavailable", str(exc), None
            finally:
                self.source.close()
            self._stop.wait(RETRY_S)

    def _heard(self, chunk: bytes) -> None:
        if self.state != "listening":
            self.state, self.error, self.listening_since = "listening", None, time.time()
            log.info("Microphone %s: listening at %d Hz", self.device, RATE)
        samples = self.analyzer.update(np.frombuffer(chunk, np.int16), time.time())
        with contextlib.suppress(RuntimeError):  # the event loop has closed: shutting down
            if self._listeners:
                self._loop.call_soon_threadsafe(self._share, chunk)
            if self._publish is not None:
                for sample in samples:
                    self._loop.call_soon_threadsafe(self._publish, sample)

    def _share(self, chunk: bytes) -> None:
        for queue in self._listeners:
            _offer(queue, chunk)

    async def chunks(self) -> AsyncIterator[bytes]:
        """The live sound, chunk by chunk, until the microphone stops."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=LISTEN_QUEUE)
        self._listeners.add(queue)
        try:
            while (chunk := await queue.get()) is not None:
                yield chunk
        finally:
            self._listeners.discard(queue)

    @property
    def listeners(self) -> int:
        return len(self._listeners)

    def status(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "state": self.state,
            "error": self.error,
            "rate_hz": RATE,
            "listening_since": self.listening_since,
            "reconnects": max(0, self.opened - 1),
            "listeners": self.listeners,
            **self.analyzer.summary(),
        }


def _offer(queue: asyncio.Queue, item: Any) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


def wav_header(rate: int = RATE) -> bytes:
    """A WAV header for a stream of unknown length (16-bit mono PCM)."""
    unknown = 0xFFFFFFFF
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return (
        b"RIFF"
        + struct.pack("<I", unknown)
        + b"WAVEfmt "
        + fmt
        + b"data"
        + struct.pack("<I", unknown - 36)
    )


def microphone_routes(microphone: Microphone) -> APIRouter:
    router = APIRouter(prefix="/api/sound", tags=["sound"])

    @router.get("")
    async def status() -> dict[str, Any]:
        """The microphone, the level, any alarm sounding, and the latest sound events."""
        return microphone.status()

    @router.get("/activity")
    async def history() -> dict[str, Any]:
        """The loudest moment of each second over the last 10 minutes, and the events."""
        points = list(microphone.analyzer.history)
        return {
            "t": [p[0] for p in points],
            "level_db": [p[1] for p in points],
            "events": microphone.analyzer.summary(limit=50)["events"],
        }

    @router.get("/live.wav")
    async def live() -> StreamingResponse:
        """The live sound as an endless WAV stream (an <audio> element plays it)."""

        async def body() -> AsyncIterator[bytes]:
            yield wav_header()
            async with contextlib.aclosing(microphone.chunks()) as chunks:
                async for chunk in chunks:  # the listener is released as soon as it leaves
                    yield chunk

        return StreamingResponse(
            body(),
            media_type="audio/wav",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
