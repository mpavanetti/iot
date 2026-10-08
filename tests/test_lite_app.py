"""The Lite edition end to end: a real server, real sockets, a real (pseudo) serial port."""

import json
import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from iotcenter.config import Settings
from iotcenter.lite.app import create_lite_app

from .conftest import free_port


@pytest.fixture
def lite(tmp_path):
    controller, device = os.openpty()  # stands in for a Pico W on /dev/ttyACM0
    settings = Settings(
        db_path=tmp_path / "lite.db",
        http_host="127.0.0.1",
        http_port=free_port(),
        tcp_host="127.0.0.1",
        tcp_port=0,
        serial_port=os.ttyname(device),
    )
    app = create_lite_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=settings.http_port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.02)

    class Lite:
        url = f"http://127.0.0.1:{settings.http_port}"
        tcp_port = app.state.readers[0].port
        serial_writer = controller

        def send_tcp(self, *lines: bytes) -> None:
            with socket.create_connection(("127.0.0.1", self.tcp_port)) as sock:
                sock.sendall(b"".join(lines))

        def get(self, path: str, **params):
            response = httpx.get(self.url + path, params=params, timeout=5)
            response.raise_for_status()
            return response

        def wait_for_messages(self, count: int) -> None:
            deadline = time.time() + 5
            while self.get("/api/status").json()["storage"]["messages"] < count:
                assert time.time() < deadline, f"expected {count} stored messages"
                time.sleep(0.05)

    yield Lite()
    server.should_exit = True
    thread.join(timeout=10)
    os.close(controller)
    os.close(device)


def test_tcp_readings_reach_the_api(lite, make_line):
    now = time.time()
    lines = [
        make_line(seq=i, ts=None, temperature_c=20 + i / 10) for i in range(5)
    ]  # unsynced clock -> server time
    lite.send_tcp(*lines)
    lite.wait_for_messages(5)

    [device] = lite.get("/api/devices").json()
    assert device["device_id"] == "pico-test01"
    assert device["online"] is True
    assert device["messages"] == 5
    assert device["latest"]["seq"] == 4

    recent = lite.get("/api/readings/recent", device_id="pico-test01").json()["readings"]
    assert [r["seq"] for r in recent] == [0, 1, 2, 3, 4]
    assert recent[0]["event_time"] >= now - 1

    history = lite.get("/api/readings/history", device_id="pico-test01", range="1h").json()
    assert sum(history["samples"]) == 5
    assert history["bucket_s"] == 15

    csv_text = lite.get("/api/readings/export.csv", device_id="pico-test01", range="1h").text
    assert csv_text.splitlines()[0].startswith("event_time,device_id,name,seq")
    assert len(csv_text.splitlines()) == 6


def test_usb_serial_readings_reach_the_api(lite, make_line):
    os.write(lite.serial_writer, b"boot: Wi-Fi disabled, USB mode\r\n")
    os.write(lite.serial_writer, make_line(device_id="pico-usb", seq=1, ts=None))
    lite.wait_for_messages(1)

    [device] = lite.get("/api/devices").json()
    assert device["device_id"] == "pico-usb"
    assert device["source"] == "usb"
    usb = next(c for c in lite.get("/api/status").json()["components"] if c["id"] == "usb")
    assert usb["status"] == "up"


def test_live_stream_pushes_new_readings(lite, make_line):
    events: list[tuple[str, dict]] = []

    def listen() -> None:
        with httpx.stream("GET", lite.url + "/api/stream", timeout=10) as response:
            event = None
            for raw in response.iter_lines():
                if raw.startswith("event:"):
                    event = raw.split(":", 1)[1].strip()
                elif raw.startswith("data:"):
                    events.append((event, json.loads(raw.split(":", 1)[1])))
                    if event == "reading":
                        return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    deadline = time.time() + 5
    while not events:  # wait for "hello" so we know the subscription exists
        assert time.time() < deadline
        time.sleep(0.02)
    lite.send_tcp(make_line(seq=99, ts=None))
    listener.join(timeout=5)

    assert events[0][0] == "hello"
    assert events[0][1] == {"edition": "lite"}
    kind, reading = events[-1]
    assert kind == "reading" and reading["seq"] == 99
    assert isinstance(reading["event_time"], float)


def test_info_status_and_dashboard(lite):
    info = lite.get("/api/info").json()
    assert info["edition"] == "lite"
    assert info["ranges"] == ["1h", "6h", "24h", "7d", "30d"]

    status = lite.get("/api/status").json()
    assert [c["id"] for c in status["components"]] == [
        "tcp",
        "usb",
        "ingest",
        "sqlite",
        "dashboard",
    ]
    assert status["host"]["mem_total_bytes"] > 0

    assert "<title>" in lite.get("/").text
    assert httpx.get(lite.url + "/api/readings/history?device_id=x&range=2y").status_code == 400
