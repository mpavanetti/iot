"""The camera: capture thread, frame fan-out and the HTTP API, against a fake webcam and the
demo scene (a real server, real sockets; no webcam needed)."""

import asyncio
import json
import threading
import time

import cv2
import httpx
import numpy as np
import pytest
import uvicorn

from iotcenter import camera as camera_module
from iotcenter.camera import Camera, CameraError, DemoSource
from iotcenter.config import Settings
from iotcenter.lite.app import create_lite_app
from iotcenter.recorder import ClipWriter

from .conftest import free_port


def jpeg(level: int = 128) -> bytes:
    ok, data = cv2.imencode(".jpg", np.full((48, 64, 3), level, np.uint8))
    return data.tobytes()


class FakeWebcam:
    """Unplugged for the first `missing` opens, then `frames` frames at `fps`, then silence."""

    width, height, format = 64, 48, "MJPEG"

    def __init__(self, missing: int = 0, frames: int | None = None, fps: float = 100) -> None:
        self.missing, self.frames, self.fps = missing, frames, fps
        self.opens = self.sent = 0
        self.open_now = False

    def open(self) -> None:
        self.opens += 1
        if self.opens <= self.missing:
            raise CameraError("/dev/video0 not found: is the camera plugged in?")
        self.open_now = True

    def read(self) -> bytes | None:
        time.sleep(1 / self.fps)
        if self.frames is not None and self.sent >= self.frames:
            return None  # unplugged mid-stream
        self.sent += 1
        return jpeg() if self.sent % 10 else b""  # every 10th frame arrives damaged

    def close(self) -> None:
        self.open_now = False


async def until(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(camera_module, "RETRY_S", 0.05)


async def test_waits_for_a_missing_camera_then_streams():
    webcam = FakeWebcam(missing=2)
    camera = Camera(webcam, name="Basement", device="/dev/video0")
    await camera.start()
    try:
        await until(lambda: camera.state == "unavailable")
        assert "plugged in" in camera.error
        await until(lambda: camera.state == "streaming" and camera.captured >= 20)
        status = camera.status()
        assert status["error"] is None
        assert (status["width"], status["height"], status["format"]) == (64, 48, "MJPEG")
        assert status["fps"] > 20
        assert status["reconnects"] == 0  # one successful open
        assert camera.latest.jpeg[:2] == b"\xff\xd8"
        assert camera.captured < webcam.sent  # damaged frames are skipped
    finally:
        await camera.stop()
    assert camera.state == "stopped"
    assert not webcam.open_now  # the device is released


async def test_a_camera_that_stops_sending_is_reopened():
    webcam = FakeWebcam(frames=5)
    camera = Camera(webcam)
    await camera.start()
    try:
        await until(lambda: webcam.opens >= 2)
        assert camera.status()["reconnects"] >= 1
    finally:
        await camera.stop()


async def test_viewers_get_new_frames_at_the_rate_they_ask_for():
    camera = Camera(FakeWebcam(fps=100))
    await camera.start()
    try:
        await until(lambda: camera.latest is not None)

        async def count(max_fps):
            seen = []
            started = time.monotonic()
            async for frame in camera.frames(max_fps):
                seen.append(frame.seq)
                if time.monotonic() - started > 0.6:
                    break
            return seen

        full, capped = await asyncio.gather(count(None), count(5))
        assert len(full) > 25
        assert 2 <= len(capped) <= 5
        assert capped == sorted(set(capped))  # never the same frame twice
    finally:
        await camera.stop()


async def test_stopping_ends_open_streams():
    camera = Camera(FakeWebcam())
    await camera.start()
    await until(lambda: camera.latest is not None)

    async def watch():
        with camera.viewing():
            async for _ in camera.frames():
                pass

    viewer = asyncio.create_task(watch())
    await until(lambda: camera.viewers == 1)
    await camera.stop()
    await asyncio.wait_for(viewer, 2)
    assert camera.viewers == 0


def test_the_demo_scene_moves_turns_its_lights_off_and_gets_wet():
    source = DemoSource(width=320, height=180, fps=1000)
    source.open()
    assert source.read()[:2] == b"\xff\xd8"
    room = camera_module.demo_room(320, 180)

    def frame(t: float) -> np.ndarray:
        return camera_module.demo_frame(room, t)

    assert not np.array_equal(frame(1.0), frame(5.0))  # the ball crosses
    assert np.array_equal(frame(25.0), frame(30.0))  # then it has left
    assert frame(36.0).mean() < frame(30.0).mean() / 3  # the lights go off
    floor = (slice(150, 165), slice(50, 90))  # the middle of the puddle
    assert frame(150.0)[floor].mean() < 0.8 * frame(30.0)[floor].mean()  # wet
    assert np.array_equal(frame(270.0), frame(30.0))  # and dry again


# --- end to end: Lite with the demo camera ---------------------------------------------------


@pytest.fixture
def lite_camera(tmp_path):
    settings = Settings(
        db_path=tmp_path / "lite.db",
        http_host="127.0.0.1",
        http_port=free_port(),
        tcp_enabled=False,
        camera_device="demo",
        camera_name="Basement",
        microphone_device="demo",
        camera_record=True,
    )
    _clip(tmp_path / "recordings", start=time.time() - 3600)  # one clip recorded earlier
    app = create_lite_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=settings.http_port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{settings.http_port}"
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.02)
    while httpx.get(url + "/api/camera").json()["state"] != "streaming":
        assert time.time() < deadline, "the demo camera did not start"
        time.sleep(0.05)
    url_with_data = type("Lite", (str,), {"zones_file": tmp_path / "camera-zones.json"})(url)
    yield url_with_data
    server.should_exit = True
    thread.join(timeout=10)


def test_info_announces_the_camera(lite_camera):
    info = httpx.get(lite_camera + "/api/info").json()
    assert info["camera"] == {"name": "Basement", "recording": True}
    assert info["microphone"] is True
    status = httpx.get(lite_camera + "/api/camera").json()
    assert status["name"] == "Basement"
    assert (status["width"], status["height"]) == (1280, 720)
    assert status["activity"]["rate_hz"] == 5.0


def test_snapshot_is_the_newest_frame_and_never_cached(lite_camera):
    response = httpx.get(lite_camera + "/api/camera/snapshot.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    picture = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    assert picture.shape == (720, 1280, 3)


def test_mjpeg_stream_sends_whole_jpeg_parts_and_counts_viewers(lite_camera):
    parts = []
    with httpx.stream("GET", lite_camera + "/api/camera/stream.mjpg", timeout=10) as response:
        assert response.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
        assert response.headers["x-accel-buffering"] == "no"
        assert httpx.get(lite_camera + "/api/camera").json()["viewers"] == 1
        buffer = b""
        for chunk in response.iter_raw():
            buffer += chunk
            while b"\r\n\r\n" in buffer:
                head, rest = buffer.split(b"\r\n\r\n", 1)
                length = int(head.split(b"Content-Length: ")[1])
                if len(rest) < length + 2:
                    break
                assert head.startswith(b"--frame\r\nContent-Type: image/jpeg")
                parts.append(rest[:length])
                buffer = rest[length + 2 :]
            if len(parts) >= 3:
                break
    assert all(part[:2] == b"\xff\xd8" and part[-2:] == b"\xff\xd9" for part in parts)
    deadline = time.time() + 5
    while httpx.get(lite_camera + "/api/camera").json()["viewers"]:
        assert time.time() < deadline, "the viewer was not released"
        time.sleep(0.05)


def test_rate_is_validated(lite_camera):
    assert httpx.get(lite_camera + "/api/camera/stream.mjpg?fps=0").status_code == 422


def test_activity_rides_the_live_stream(lite_camera):
    samples = []
    with httpx.stream("GET", lite_camera + "/api/stream", timeout=10) as response:
        event = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "camera":
                samples.append(json.loads(line.split(":", 1)[1]))
                if len(samples) == 3:
                    break
    keys = {"t", "motion_pct", "brightness_pct", "moving", "boxes", "light", "zones"}
    assert set(samples[0]) == keys
    assert samples[1]["t"] > samples[0]["t"]

    history = httpx.get(lite_camera + "/api/camera/activity").json()
    assert history["window_s"] == 600
    assert len(history["t"]) == len(history["motion_pct"]) == len(history["brightness_pct"]) >= 1


def test_zones_are_drawn_kept_and_deleted(lite_camera):
    url = lite_camera + "/api/camera/zones"
    floor = {"name": " Heater floor ", "kind": "floor", "x": 0.1, "y": 0.6, "w": 0.3, "h": 0.3}
    created = httpx.post(url, json=floor)
    assert created.status_code == 201
    zone = created.json()
    assert zone["name"] == "Heater floor" and len(zone["id"]) == 8
    area = httpx.post(url, json={**floor, "name": "Stairs", "kind": "area"}).json()

    listed = httpx.get(url).json()["zones"]
    assert [z["name"] for z in listed] == ["Heater floor", "Stairs"]
    assert listed[0]["state"] == "learning"  # a new floor learns the dry floor first
    saved = json.loads(lite_camera.zones_file.read_text())  # names and rectangles only
    assert [z["id"] for z in saved["zones"]] == [zone["id"], area["id"]]

    assert httpx.post(f"{url}/{zone['id']}/dry").json()["state"] == "learning"
    assert httpx.post(f"{url}/{area['id']}/dry").status_code == 404  # not a floor
    bad = {**floor, "x": 0.9}
    assert httpx.post(url, json=bad).status_code == 422  # outside the picture
    off = httpx.patch(f"{url}/{area['id']}", json={"enabled": False, "name": "Stairs (off)"})
    assert off.json()["enabled"] is False and off.json()["name"] == "Stairs (off)"
    listed = {z["id"]: z for z in httpx.get(url).json()["zones"]}
    assert listed[area["id"]]["state"] == "off"
    assert httpx.patch(f"{url}/nope", json={"enabled": True}).status_code == 404
    assert httpx.patch(f"{url}/{area['id']}", json={"name": ""}).status_code == 422
    assert httpx.delete(f"{url}/{area['id']}").status_code == 204
    assert httpx.delete(f"{url}/{area['id']}").status_code == 404
    assert [z["name"] for z in httpx.get(url).json()["zones"]] == ["Heater floor"]
    status = httpx.get(lite_camera + "/api/camera").json()
    assert [z["id"] for z in status["activity"]["zones"]] == [zone["id"]]


def test_sound_status_and_live_listening(lite_camera):
    deadline = time.time() + 5
    while (status := httpx.get(lite_camera + "/api/sound").json())["state"] != "listening":
        assert time.time() < deadline, "the demo microphone did not start"
        time.sleep(0.05)
    assert status["rate_hz"] == 16_000
    assert status["background_db"] is not None
    with httpx.stream("GET", lite_camera + "/api/sound/live.wav", timeout=10) as response:
        assert response.headers["content-type"] == "audio/wav"
        data = b""
        for chunk in response.iter_raw():
            data += chunk
            if len(data) > 44 + 6400:
                break
    assert data[:4] == b"RIFF" and data[8:16] == b"WAVEfmt "
    assert int.from_bytes(data[24:28], "little") == 16_000  # sample rate
    history = httpx.get(lite_camera + "/api/sound/activity").json()
    assert len(history["t"]) == len(history["level_db"])


def _clip(folder, start: float, seconds: float = 2.0) -> dict:
    """A real little clip on disk, as the recorder leaves it."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(start))
    clip = {"id": f"{stamp}-abcd", "start": start, "end": start + seconds}
    writer = ClipWriter(folder, clip)
    for i in range(int(seconds * 10)):
        frame = np.full((180, 320, 3), 90, np.uint8)
        frame[60:100, 10 + 10 * i : 50 + 10 * i] = 230
        writer.add(start + i / 10, cv2.imencode(".jpg", frame)[1].tobytes())
    return writer.close({**clip, "peak_pct": 3.2, "zones": ["Stairs"]})


def test_recordings_are_listed_played_and_deleted(lite_camera):
    url = lite_camera + "/api/camera/recordings"
    listing = httpx.get(url).json()
    assert listing["enabled"] is True and listing["days"] == 30
    [clip] = listing["clips"]
    assert clip["zones"] == ["Stairs"] and clip["duration_s"] == pytest.approx(1.9, abs=0.15)
    video = httpx.get(f"{url}/{clip['id']}.mp4")
    assert video.headers["content-type"] == "video/mp4" and video.content[4:8] == b"ftyp"
    part = httpx.get(f"{url}/{clip['id']}.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100  # so a <video> can seek
    assert httpx.get(f"{url}/{clip['id']}.jpg").headers["content-type"] == "image/jpeg"
    assert httpx.get(f"{url}/..%2F..%2Flite.db.mp4").status_code == 404
    assert httpx.get(url, params={"since": time.time()}).json()["clips"] == []
    assert httpx.delete(f"{url}/{clip['id']}").status_code == 204
    assert httpx.get(url).json()["clips"] == []
    assert httpx.get(f"{url}/{clip['id']}.mp4").status_code == 404
