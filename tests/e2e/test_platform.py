"""End-to-end checks against a running platform stack: `make e2e` (or `pytest -m e2e`).

A reading sent to the gateway over TCP must come out the other side: in Kafka, in
PostgreSQL (written by Spark), in the dashboard API and live stream, and in Streamlit.
URLs default to the stack's published ports; override with IOT_E2E_* variables.
"""

import json
import os
import socket
import threading
import time
import uuid
from datetime import UTC, datetime

import httpx
import psycopg
import pytest

from ..conftest import ROOT, message

pytestmark = pytest.mark.e2e

WEB = os.getenv("IOT_E2E_WEB", "http://localhost:8000")
GATEWAY = os.getenv("IOT_E2E_GATEWAY", "localhost:1500")
DATABASE = os.getenv("IOT_E2E_DATABASE_URL", "postgresql://iot:iot@localhost:5432/iot")
SPARK_TIMEOUT_S = 90  # micro-batches run every 10 s; allow for a cold JVM


def send(*lines: bytes) -> None:
    host, port = GATEWAY.rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(b"".join(lines))


def eventually(check, timeout: float, what: str):
    deadline = time.time() + timeout
    while True:
        result = check()
        if result:
            return result
        assert time.time() < deadline, f"timed out waiting for {what}"
        time.sleep(1)


def reading_line(device_id: str, seq: int) -> bytes:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    temperature = 20 + seq % 10  # stays inside the BME280 range, or the gateway rejects it
    data = message(device_id=device_id, name="e2e", seq=seq, ts=now, temperature_c=temperature)
    return (json.dumps(data) + "\n").encode()


@pytest.fixture(scope="module")
def device_id() -> str:
    return f"pico-e2e-{uuid.uuid4().hex[:8]}"


def test_readings_flow_from_the_gateway_to_postgres_and_the_dashboard(device_id):
    send(*(reading_line(device_id, seq) for seq in range(5)))

    def stored() -> int:
        with psycopg.connect(DATABASE) as conn:
            rows = conn.execute(
                "SELECT count(*) FROM readings WHERE device_id = %s", (device_id,)
            ).fetchone()
        return rows[0] == 5

    eventually(stored, SPARK_TIMEOUT_S, "Spark to write the readings to PostgreSQL")

    def aggregated() -> bool:
        with psycopg.connect(DATABASE) as conn:
            row = conn.execute(
                "SELECT sum(samples), max(temperature_max) FROM readings_hourly"
                " WHERE device_id = %s",
                (device_id,),
            ).fetchone()
        return row[0] == 5 and row[1] == 24.0

    eventually(aggregated, SPARK_TIMEOUT_S, "Spark to update the hourly aggregates")

    devices = httpx.get(f"{WEB}/api/devices").json()
    device = next(d for d in devices if d["device_id"] == device_id)
    assert device["online"] and device["latest"]["seq"] == 4

    recent = httpx.get(f"{WEB}/api/readings/recent", params={"device_id": device_id}).json()
    assert [r["seq"] for r in recent["readings"]] == [0, 1, 2, 3, 4]

    history = httpx.get(
        f"{WEB}/api/readings/history", params={"device_id": device_id, "range": "1h"}
    ).json()
    assert sum(history["samples"]) == 5


def test_invalid_lines_go_to_the_dead_letter_queue():
    def dead_letters() -> int | None:
        kafka = next(
            c for c in httpx.get(f"{WEB}/api/status").json()["components"] if c["id"] == "kafka"
        )
        return kafka["metrics"]["dead letters"]

    before = eventually(lambda: dead_letters() is not None, 30, "Kafka metrics") and dead_letters()
    send(b'{"device_id": "pico-e2e", "this is": "not a reading"}\n')
    eventually(lambda: dead_letters() == before + 1, 30, "the dead letter")


def test_live_stream_pushes_readings_as_they_arrive(device_id):
    subscribed = threading.Event()
    received: list[dict] = []

    def listen() -> None:
        with httpx.stream("GET", f"{WEB}/api/stream", timeout=30) as response:
            for line in response.iter_lines():
                if line == "event: hello":  # the server has registered this viewer
                    subscribed.set()
                elif line.startswith("data:"):
                    data = json.loads(line[5:])
                    if data.get("seq") == 99 and data.get("device_id") == device_id:
                        received.append(data)
                        return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    assert subscribed.wait(10), "no hello event from /api/stream"
    sent = time.time()
    send(reading_line(device_id, 99))
    listener.join(timeout=20)
    assert received, "the reading never reached the live stream"
    assert time.time() - sent < 5  # gateway -> Kafka -> web app -> browser, in real time


def test_pipeline_reports_every_component_up():
    def all_up():
        status = httpx.get(f"{WEB}/api/status").json()
        states = {c["id"]: c["status"] for c in status["components"]}
        return states if set(states.values()) == {"up"} else None

    states = eventually(all_up, 60, "every pipeline component to be up")
    assert list(states) == ["gateway", "kafka", "spark", "postgres", "analytics", "dashboard"]


@pytest.mark.parametrize(
    ("page", "title"),
    [
        ("overview", "Overview"),
        ("patterns", "Patterns"),
        ("quality", "Data quality"),
        ("explorer", "Explorer"),
    ],
)
def test_streamlit_pages_render_without_errors(page, title, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("IOT_DATABASE_URL", DATABASE)
    app = AppTest.from_file(str(ROOT / "src/iotcenter/analytics/app.py"), default_timeout=60)
    app.run()
    if page != "overview":
        app.switch_page(f"views/{page}.py").run()
    assert not app.exception, app.exception
    assert app.title[0].value == title
