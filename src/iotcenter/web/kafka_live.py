"""Live readings from Kafka for the platform dashboard.

One consumer per web-app process (v1 opened one per browser tab and slept between
messages). It follows the readings topic and the dead-letter topic and fans every reading
out through the LiveHub. On start it rewinds `replay` messages per partition, so the live
charts are full right away, even after a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from aiokafka import AIOKafkaConsumer, TopicPartition
from pydantic import ValidationError

from ..hub import LiveHub
from ..protocol import Reading

log = logging.getLogger(__name__)


class KafkaLive:
    def __init__(self, bootstrap: str, topic: str, dlq_topic: str, hub: LiveHub, replay: int):
        self.bootstrap, self.topic, self.dlq_topic = bootstrap, topic, dlq_topic
        self.hub, self.replay = hub, replay
        self.connected = False
        self.last_error: str | None = None
        self.consumed = 0
        self.invalid = 0  # unparseable messages (only possible from direct producers)
        self.last_dead_letter: dict[str, Any] | None = None
        self.devices: dict[str, dict[str, Any]] = {}  # device_id -> first/last seen, count
        self._consumer: AIOKafkaConsumer | None = None
        self._partitions: list[TopicPartition] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="kafka-live")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def totals(self) -> dict[str, int | None]:
        """Messages ever written to each topic (sum of partition high watermarks)."""
        totals: dict[str, int | None] = {self.topic: None, self.dlq_topic: None}
        if self._consumer is None or not self.connected:
            return totals
        for tp in self._partitions:
            highwater = self._consumer.highwater(tp)
            if highwater is not None:
                totals[tp.topic] = (totals[tp.topic] or 0) + highwater
        return totals

    async def _run(self) -> None:
        delay = 1.0
        while True:
            # No consumer group: this process simply reads every partition of both topics.
            consumer = AIOKafkaConsumer(
                self.topic,
                self.dlq_topic,
                bootstrap_servers=self.bootstrap,
                group_id=None,
                enable_auto_commit=False,
                auto_offset_reset="latest",
            )
            try:
                await consumer.start()
                await self._rewind(consumer)
                self._consumer, self.connected, self.last_error, delay = consumer, True, None, 1.0
                log.info("Following Kafka topic %s at %s", self.topic, self.bootstrap)
                async for message in consumer:
                    self._handle(message.topic, message.value)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # broker down, topic missing, network trouble...
                self.last_error = str(exc) or type(exc).__name__
                log.warning(
                    "Kafka consumer problem (%s); retrying in %.0fs", self.last_error, delay
                )
            finally:
                self.connected = False
                self._consumer = None
                await consumer.stop()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)

    async def _rewind(self, consumer: AIOKafkaConsumer) -> None:
        """Wait for the partitions to be assigned, then step back `replay` messages in each."""
        for _ in range(50):
            partitions = sorted(consumer.assignment(), key=lambda tp: (tp.topic, tp.partition))
            if any(tp.topic == self.topic for tp in partitions):
                break
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError(f"topic {self.topic} not found")
        beginning = await consumer.beginning_offsets(partitions)
        end = await consumer.end_offsets(partitions)
        for tp in partitions:
            consumer.seek(tp, max(beginning[tp], end[tp] - self.replay))
        self._partitions = partitions

    def _handle(self, topic: str, value: bytes) -> None:
        if topic == self.dlq_topic:
            with contextlib.suppress(ValueError):
                self.last_dead_letter = json.loads(value)
            return
        try:
            reading = Reading.model_validate_json(value)
        except ValidationError:
            self.invalid += 1
            return
        record = reading.to_record()
        self.consumed += 1
        self.hub.publish(record)
        device = self.devices.setdefault(
            reading.device_id, {"first_seen": record["received_at"], "messages": 0}
        )
        device["last_seen"] = max(device.get("last_seen", 0.0), record["received_at"])
        device["messages"] += 1
