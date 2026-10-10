"""Motion recordings: clips around motion, their files, and how long they are kept."""

import asyncio
import json
import time

import av
import cv2
import numpy as np
import pytest

from iotcenter import recorder as recorder_module
from iotcenter.recorder import ClipWriter, Recorder


class FakeCamera:
    async def frames(self, max_fps=None):
        await asyncio.Event().wait()  # the test feeds frames itself
        yield


class FakeAnalyzer:
    moving = False

    def events_between(self, start, end):
        return {"peak_pct": 4.5, "zones": ["Floor by the drain"]}


class FakeActivity:
    analyzer = FakeAnalyzer()


def jpeg(i: int) -> bytes:
    frame = np.full((180, 320, 3), 100, np.uint8)
    frame[70:110, (5 * i) % 280 : (5 * i) % 280 + 40] = 220
    return cv2.imencode(".jpg", frame)[1].tobytes()


async def until(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.02)


def feed(recorder, start, seconds, moving, first=0):
    for i in range(int(seconds * 10)):
        recorder.update(start + i / 10, jpeg(first + i), moving)
    return start + seconds


@pytest.fixture
async def recorder(tmp_path):
    rec = Recorder(FakeCamera(), FakeActivity(), tmp_path / "recordings", days=30, max_gb=1)
    await rec.start()
    yield rec
    await rec.stop()


async def test_a_motion_becomes_one_clip_with_what_led_up_to_it(recorder):
    t = feed(recorder, 1000.0, 5, moving=False)  # still: only the last 3 s are kept
    t = feed(recorder, t, 2, moving=True)
    assert recorder.recording is not None
    feed(recorder, t, 5, moving=False)  # it ends 3 s after the motion
    await until(lambda: recorder.clips())
    [clip] = recorder.clips()
    assert recorder.recording is None
    assert clip["start"] == pytest.approx(1002.0, abs=0.2)  # 3 s before the motion
    assert clip["duration_s"] == pytest.approx(8.0, abs=0.3)  # 3 before + 2 moving + 3 after
    assert clip["zones"] == ["Floor by the drain"] and clip["peak_pct"] == 4.5
    video = recorder.path(clip["id"], ".mp4")
    assert recorder.path(clip["id"], ".jpg") is not None
    assert json.loads(recorder.path(clip["id"], ".json").read_text())["id"] == clip["id"]
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert (stream.width, stream.height) == (320, 180)  # smaller cameras stay as they are
        assert sum(1 for _ in container.decode(stream)) == pytest.approx(80, abs=2)
    assert not list(video.parent.glob("*.part"))


async def test_long_motion_goes_on_in_the_next_clip(recorder, monkeypatch):
    monkeypatch.setattr(recorder_module, "MAX_S", 4.0)
    t = feed(recorder, 1000.0, 9, moving=True)
    feed(recorder, t, 4, moving=False)
    await until(lambda: len(recorder.clips()) == 3)
    durations = sorted(c["duration_s"] for c in recorder.clips())
    assert durations[-1] <= 4.0


async def test_stopping_finishes_the_clip_being_recorded(tmp_path):
    rec = Recorder(FakeCamera(), FakeActivity(), tmp_path / "recordings")
    await rec.start()
    feed(rec, 1000.0, 2, moving=True)
    await rec.stop()
    assert len(rec._scan()) == 1


async def test_old_clips_and_too_many_are_deleted(tmp_path):
    folder = tmp_path / "recordings"
    now = time.time()
    for days_ago in (40, 10, 5, 1):
        start = now - days_ago * 86_400
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(start))
        writer = ClipWriter(folder, {"id": f"{stamp}-{days_ago:04x}", "start": start, "end": start})
        for i in range(10):
            writer.add(start + i / 10, jpeg(i))
        writer.close({"id": writer.id, "start": start, "end": start + 1})
    rec = Recorder(FakeCamera(), FakeActivity(), folder, days=30, max_gb=1)
    rec._index = rec._scan()
    assert rec.purge(now) == 1  # the 40-day-old one
    size = rec.clips()[0]["size_bytes"]
    rec.max_bytes = int(size * 2.5)  # room for two
    assert rec.purge(now) == 1
    assert [round((now - c["start"]) / 86_400) for c in rec.clips()] == [1, 5]
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        {time.strftime("%Y-%m-%d", time.gmtime(c["start"])) for c in rec.clips()}
    )  # empty days are removed too


def test_clip_ids_cannot_reach_outside_the_folder(tmp_path):
    rec = Recorder(FakeCamera(), FakeActivity(), tmp_path / "recordings")
    assert rec.path("../../etc/passwd", ".mp4") is None
    assert rec.path("20261009-120000-abcd", ".mp4") is None  # well formed, but no such clip
