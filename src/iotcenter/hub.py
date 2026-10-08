"""In-memory fan-out of live readings to every open dashboard.

Whatever produces readings (the Lite ingest loop, or the platform's Kafka consumer) calls
`publish()`. Each browser tab holds a subscription queue that the Server-Sent Events endpoint
drains. The hub also keeps the latest readings per device, so a dashboard opened a moment
ago can draw its "live" chart immediately instead of starting empty.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

Item = dict[str, Any]

# Sentinel pushed into every queue on shutdown so open streams finish promptly.
CLOSED: Item = {"type": "closed"}


class LiveHub:
    def __init__(self, keep_per_device: int = 1800, queue_size: int = 256) -> None:
        self._keep = keep_per_device
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[Item]] = set()
        self._recent: dict[str, deque[Item]] = {}
        self.published = 0

    def publish(self, reading: Item) -> None:
        """Remember a reading and push it to every subscriber (never blocks)."""
        self.published += 1
        device = reading["device_id"]
        self._recent.setdefault(device, deque(maxlen=self._keep)).append(reading)
        for queue in self._subscribers:
            if queue.full():  # a slow client loses its oldest update rather than stalling us
                queue.get_nowait()
            queue.put_nowait(reading)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Item]]:
        queue: asyncio.Queue[Item] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def recent(self, device_id: str, since: float = 0.0) -> list[Item]:
        """Readings of one device with `event_time` (Unix seconds) at or after `since`."""
        return [r for r in self._recent.get(device_id, ()) if r["event_time"] >= since]

    def latest(self) -> dict[str, Item]:
        """The newest reading of every device seen so far."""
        return {device: items[-1] for device, items in self._recent.items() if items}

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def close(self) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(CLOSED)
