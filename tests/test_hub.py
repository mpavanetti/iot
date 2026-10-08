import asyncio

from iotcenter.hub import CLOSED, LiveHub


def reading(device: str, t: float) -> dict:
    return {"device_id": device, "event_time": t, "temperature_c": 20.0}


async def test_subscribers_receive_published_readings():
    hub = LiveHub()
    async with hub.subscribe() as first, hub.subscribe() as second:
        hub.publish(reading("a", 1))
        assert (await first.get())["device_id"] == "a"
        assert (await second.get())["device_id"] == "a"
    assert hub.subscriber_count == 0


async def test_keeps_recent_readings_per_device_for_new_viewers():
    hub = LiveHub(keep_per_device=3)
    for t in range(5):
        hub.publish(reading("a", t))
    hub.publish(reading("b", 10))

    assert [r["event_time"] for r in hub.recent("a")] == [2, 3, 4]  # bounded
    assert [r["event_time"] for r in hub.recent("a", since=3)] == [3, 4]
    assert hub.recent("missing") == []
    assert set(hub.latest()) == {"a", "b"}


async def test_slow_subscriber_drops_oldest_instead_of_blocking():
    hub = LiveHub(queue_size=2)
    async with hub.subscribe() as queue:
        for t in range(4):
            hub.publish(reading("a", t))
        assert [queue.get_nowait()["event_time"] for _ in range(2)] == [2, 3]


async def test_close_ends_open_streams():
    hub = LiveHub()
    async with hub.subscribe() as queue:
        hub.close()
        assert await asyncio.wait_for(queue.get(), 1) is CLOSED
