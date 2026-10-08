"""Platform gateway: device lines in, Kafka messages out.

    Pico W ──TCP :1500 / USB──▶ parse_line() ──valid───▶ Kafka  iot.readings      (key = device_id)
                                             └─invalid─▶ Kafka  iot.readings.dlq  (with the reason)

Keying by device id sends all readings of a board to the same partition, so they stay in
order. A tiny HTTP server (/health, /stats) lets Docker and the dashboard check on it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import uvicorn
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from fastapi import FastAPI, Response

from . import __version__
from .config import Settings
from .ingest import IngestStats, LineProcessor, SerialIngest, TcpIngestServer
from .protocol import Reading

log = logging.getLogger(__name__)
STARTED = time.time()


class KafkaSink:
    """Publishes readings and dead letters, counting what happens to each."""

    def __init__(self, bootstrap: str, topic: str, dlq_topic: str) -> None:
        self.bootstrap, self.topic, self.dlq_topic = bootstrap, topic, dlq_topic
        self.producer: AIOKafkaProducer | None = None
        self.published = 0
        self.dead_lettered = 0
        self.failed = 0
        self.last_failure: str | None = None

    async def start(self) -> None:
        """Connect, retrying until Kafka is reachable (it may still be starting up)."""
        delay = 1.0
        while True:
            producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap,
                acks="all",  # wait until the broker has persisted the message
                enable_idempotence=True,  # retries never create duplicates
                linger_ms=5,  # tiny batching window: higher throughput, negligible latency
            )
            try:
                await producer.start()
            except KafkaError as exc:
                await producer.stop()
                log.warning(
                    "Kafka at %s not ready (%s); retrying in %.0fs", self.bootstrap, exc, delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            self.producer = producer
            log.info("Connected to Kafka at %s; publishing to %s", self.bootstrap, self.topic)
            return

    async def stop(self) -> None:
        if self.producer:
            await self.producer.stop()  # flushes anything still batched

    async def publish(self, reading: Reading) -> None:
        await self._send(self.topic, reading.to_json().encode(), reading.device_id.encode())

    async def dead_letter(self, line: bytes, reason: str, source: str) -> None:
        envelope = {
            "received_at": datetime.now(UTC).isoformat(),
            "source": source,
            "error": reason,
            "raw": line.decode("utf-8", "replace")[:4096],
        }
        await self._send(self.dlq_topic, json.dumps(envelope).encode(), None)

    async def _send(self, topic: str, value: bytes, key: bytes | None) -> None:
        assert self.producer is not None
        # send() only enqueues (back-pressure if the buffer is full); delivery is confirmed
        # in the background, so one slow broker round-trip never blocks device reads.
        future = await self.producer.send(topic, value, key=key)
        future.add_done_callback(lambda done: self._delivered(topic, done))

    def _delivered(self, topic: str, future: asyncio.Future) -> None:
        if future.cancelled() or future.exception():
            self.failed += 1
            self.last_failure = str(future.exception() or "cancelled")
            log.error("Kafka delivery to %s failed: %s", topic, self.last_failure)
        elif topic == self.dlq_topic:
            self.dead_lettered += 1
        else:
            self.published += 1


def create_health_app(stats: IngestStats, sink: KafkaSink) -> FastAPI:
    app = FastAPI(title="IoT Center gateway", version=__version__, docs_url=None)

    @app.get("/health")
    async def health(response: Response) -> dict[str, Any]:
        healthy = stats.tcp_listening and sink.producer is not None
        response.status_code = 200 if healthy else 503
        return {"status": "ok" if healthy else "starting"}

    @app.get("/stats")
    async def gateway_stats() -> dict[str, Any]:
        return {
            **stats.as_dict(),
            "published": sink.published,
            "dead_lettered": sink.dead_lettered,
            "failed": sink.failed,
            "last_failure": sink.last_failure,
            "topic": sink.topic,
            "dlq_topic": sink.dlq_topic,
            "uptime_s": round(time.time() - STARTED),
        }

    return app


async def serve(settings: Settings) -> None:
    sink = KafkaSink(settings.kafka_bootstrap, settings.kafka_topic, settings.kafka_dlq_topic)
    await sink.start()
    stats = IngestStats()
    processor = LineProcessor(sink.publish, sink.dead_letter, stats)

    readers: list[TcpIngestServer | SerialIngest] = [
        TcpIngestServer(settings.tcp_host, settings.tcp_port, processor)
    ]
    if settings.serial_port:
        readers.append(SerialIngest(settings.serial_port, settings.serial_baud, processor))
    for reader in readers:
        await reader.start()

    config = uvicorn.Config(
        create_health_app(stats, sink),
        host=settings.http_host,
        port=settings.http_port,
        log_level="warning",
    )
    try:
        await uvicorn.Server(config).serve()  # runs until SIGTERM / Ctrl-C
    finally:
        for reader in readers:
            await reader.stop()
        await sink.stop()
        log.info("Gateway stopped: %s readings published", sink.published)


def run_gateway(settings: Settings) -> None:
    asyncio.run(serve(settings))
