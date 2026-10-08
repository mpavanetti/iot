"""The platform gateway with a fake Kafka producer: what gets published where."""

import asyncio
import json

import httpx
import pytest
from aiokafka.errors import KafkaConnectionError

from iotcenter import gateway
from iotcenter.ingest import IngestStats, LineProcessor


class FakeProducer:
    instances: list["FakeProducer"] = []
    failures_before_start = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.sent: list[tuple[str, bytes, bytes | None]] = []
        FakeProducer.instances.append(self)

    async def start(self):
        if FakeProducer.failures_before_start:
            FakeProducer.failures_before_start -= 1
            raise KafkaConnectionError("broker not ready")

    async def stop(self):
        pass

    async def send(self, topic, value, key=None):
        self.sent.append((topic, value, key))
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future


@pytest.fixture
async def sink(monkeypatch):
    FakeProducer.instances.clear()
    monkeypatch.setattr(gateway, "AIOKafkaProducer", FakeProducer)
    sink = gateway.KafkaSink("kafka:9092", "iot.readings", "iot.readings.dlq")
    await sink.start()
    return sink


async def test_valid_readings_are_published_keyed_by_device(sink, make_line):
    processor = LineProcessor(sink.publish, sink.dead_letter, IngestStats())
    await processor.process(make_line(seq=3), "tcp")
    await asyncio.sleep(0)  # delivery callbacks

    [(topic, value, key)] = sink.producer.sent
    assert topic == "iot.readings"
    assert key == b"pico-test01"  # same key -> same partition -> per-device order
    message = json.loads(value)
    assert message["seq"] == 3
    assert message["source"] == "tcp" and "received_at" in message
    assert message["dew_point_c"] == pytest.approx(9.06, abs=0.01)
    assert sink.published == 1
    assert sink.producer.kwargs["enable_idempotence"] is True


async def test_invalid_lines_go_to_the_dead_letter_topic(sink):
    processor = LineProcessor(sink.publish, sink.dead_letter, IngestStats())
    await processor.process(b'{"device_id": "x", "seq": 1}\n', "usb")
    await asyncio.sleep(0)

    [(topic, value, key)] = sink.producer.sent
    assert topic == "iot.readings.dlq" and key is None
    letter = json.loads(value)
    assert letter["source"] == "usb"
    assert "temperature_c" in letter["error"]
    assert letter["raw"].startswith('{"device_id": "x"')
    assert sink.dead_lettered == 1


async def test_waits_for_kafka_to_come_up(monkeypatch):
    FakeProducer.instances.clear()
    FakeProducer.failures_before_start = 2
    monkeypatch.setattr(gateway, "AIOKafkaProducer", FakeProducer)
    monkeypatch.setattr(gateway.asyncio, "sleep", _no_sleep)
    sink = gateway.KafkaSink("kafka:9092", "t", "d")
    await sink.start()
    assert len(FakeProducer.instances) == 3  # two failed attempts, then connected
    assert sink.producer is FakeProducer.instances[-1]


async def _no_sleep(seconds):
    return None


async def test_health_endpoint_reflects_readiness(sink):
    stats = IngestStats()
    app = gateway.create_health_app(stats, sink)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw"
    ) as client:
        assert (await client.get("/health")).status_code == 503  # not listening yet
        stats.tcp_listening = True
        assert (await client.get("/health")).status_code == 200
        body = (await client.get("/stats")).json()
    assert body["topic"] == "iot.readings" and body["published"] == 0
