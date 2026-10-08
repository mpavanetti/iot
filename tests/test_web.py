"""The platform web app's own logic, with Kafka and PostgreSQL replaced by fakes."""

import time

from iotcenter.config import Settings
from iotcenter.hub import LiveHub
from iotcenter.web.app import PlatformSource


class FakeKafka:
    topic, dlq_topic = "iot.readings", "iot.readings.dlq"

    def __init__(self, devices=None, connected=True):
        self.devices = devices or {}
        self.connected = connected
        self.last_error = None
        self.last_dead_letter = {"error": "humidity_pct: too high"}

    def totals(self):
        return {self.topic: 120, self.dlq_topic: 3}


class FakeDatabase:
    def __init__(self, devices=None, fail=False):
        self._devices = devices or []
        self.fail = fail

    async def devices(self):
        if self.fail:
            raise ConnectionError("PostgreSQL is down")
        return self._devices


def source(hub=None, kafka=None, db=None) -> PlatformSource:
    return PlatformSource(Settings(), hub or LiveHub(), kafka or FakeKafka(), db or FakeDatabase())


def record(device_id: str, event_time: float, seq: int = 1) -> dict:
    return {
        "device_id": device_id,
        "event_time": event_time,
        "received_at": event_time,
        "seq": seq,
        "name": "kitchen",
        "ip": "10.0.0.2",
        "firmware": "2.0.0",
        "source": "tcp",
    }


async def test_devices_merge_spark_history_with_fresher_kafka_data():
    now = time.time()
    stored = {
        "device_id": "pico-a",
        "name": "kitchen",
        "first_seen": now - 3600,
        "last_seen": now - 60,
        "messages": 500,
        "latest": record("pico-a", now - 60),
    }
    hub = LiveHub()
    hub.publish(record("pico-a", now - 1, seq=9))  # newer than what Spark has written
    kafka = FakeKafka({"pico-a": {"first_seen": now - 120, "last_seen": now - 1, "messages": 40}})

    [device] = await source(hub, kafka, FakeDatabase([stored])).devices()
    assert device["latest"]["seq"] == 9
    assert device["last_seen"] == now - 1
    assert device["messages"] == 500  # Spark's full count, not just this process's view
    assert device["first_seen"] == now - 3600


async def test_devices_still_work_when_postgres_is_down():
    now = time.time()
    hub = LiveHub()
    hub.publish(record("pico-b", now))
    kafka = FakeKafka({"pico-b": {"first_seen": now, "last_seen": now, "messages": 1}})

    [device] = await source(hub, kafka, FakeDatabase(fail=True)).devices()
    assert device["device_id"] == "pico-b" and device["name"] == "kitchen"


def test_spark_status_needs_a_running_job_and_fresh_batches():
    s = source()
    master = {
        "aliveworkers": 1,
        "activeapps": [{"name": "iot-stream-readings", "state": "RUNNING"}],
    }
    fresh = {"readings": {"age_s": 4.0, "input_rows": 12, "batch_id": 41}}
    stale = {"readings": {"age_s": 900.0, "input_rows": 0, "batch_id": 41}}

    assert s._spark(master, fresh)["status"] == "up"
    assert s._spark(master, fresh)["metrics"]["batches"] == 42
    assert s._spark(master, stale)["status"] == "degraded"
    assert s._spark({"aliveworkers": 1, "activeapps": []}, fresh)["status"] == "down"
    assert s._spark(None, fresh)["status"] == "down"


def test_gateway_and_kafka_status():
    s = source()
    stats = {
        "tcp_listening": True,
        "tcp_port": 1500,
        "tcp_connections_open": 2,
        "messages_ok": 10,
        "messages_invalid": 1,
        "failed": 0,
        "last_failure": None,
    }
    assert s._gateway(stats)["status"] == "up"
    assert s._gateway({**stats, "failed": 3, "last_failure": "timeout"})["status"] == "degraded"
    assert s._gateway(None)["status"] == "down"

    kafka = s._kafka()
    assert kafka["metrics"] == {"messages produced": 120, "dead letters": 3}
    assert "humidity_pct" in kafka["note"]
