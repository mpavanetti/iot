"""The microphone's capture thread and live sharing, against a fake microphone."""

import asyncio
import contextlib
import io
import time
import wave

import numpy as np
import pytest

from iotcenter import microphone as microphone_module
from iotcenter.microphone import (
    CHUNK_BYTES,
    ArecordSource,
    DemoMicrophone,
    Microphone,
    MicrophoneError,
    wav_header,
)


class FakeMicrophone:
    """Unplugged for the first `missing` opens, then a quiet hum in real time."""

    def __init__(self, missing: int = 0) -> None:
        self.missing, self.opens, self.chunks = missing, 0, 0

    def open(self) -> None:
        self.opens += 1
        if self.opens <= self.missing:
            raise MicrophoneError("audio open error: No such file or directory")

    def read(self) -> bytes:
        time.sleep(0.01)  # ten times faster than real time
        self.chunks += 1
        t = np.arange(CHUNK_BYTES // 2) / 16_000
        return (3000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()

    def close(self) -> None:
        pass


async def until(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(microphone_module, "RETRY_S", 0.05)


async def test_waits_for_a_missing_microphone_then_listens_and_shares():
    samples = []
    source = FakeMicrophone(missing=1)
    microphone = Microphone(source, device="plughw:CARD=C960", publish=samples.append)
    await microphone.start()
    try:
        await until(lambda: microphone.state == "unavailable")
        assert "No such file" in microphone.error
        await until(lambda: microphone.state == "listening" and len(samples) >= 2)
        assert samples[0]["level_db"] == pytest.approx(-23.8, abs=0.5)  # RMS of a 3000/32768 sine

        heard = []

        async def listen():
            async with contextlib.aclosing(microphone.chunks()) as chunks:
                async for chunk in chunks:
                    heard.append(chunk)
                    if len(heard) == 3:
                        return

        await asyncio.wait_for(listen(), 2)
        assert all(len(chunk) == CHUNK_BYTES for chunk in heard)
        assert microphone.listeners == 0  # released when the listener left
        assert microphone.status()["reconnects"] == 0
    finally:
        await microphone.stop()
    assert microphone.state == "stopped"


async def test_stopping_ends_live_listening():
    microphone = Microphone(FakeMicrophone())
    await microphone.start()
    await until(lambda: microphone.state == "listening")

    async def listen():
        async for _ in microphone.chunks():
            pass

    listener = asyncio.create_task(listen())
    await until(lambda: microphone.listeners == 1)
    await microphone.stop()
    await asyncio.wait_for(listener, 2)


def test_the_wav_header_opens_as_16_khz_mono():
    with wave.open(io.BytesIO(wav_header() + b"\0" * 3200)) as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16_000, 1, 2)


def test_the_demo_microphone_has_an_alarm_to_find():
    demo = DemoMicrophone()
    demo.open()
    demo._started -= 1_000  # do not wait in real time
    demo._heard = 20 * 16_000  # 20 s in: the smoke alarm's first beep
    loud = np.frombuffer(demo.read(), np.int16)
    demo._heard = 10 * 16_000
    quiet = np.frombuffer(demo.read(), np.int16)
    assert np.abs(loud).max() > 5 * np.abs(quiet).max()


def test_a_missing_arecord_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(microphone_module.shutil, "which", lambda name: None)
    with pytest.raises(MicrophoneError, match="alsa-utils"):
        ArecordSource("default").open()
