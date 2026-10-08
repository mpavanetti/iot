from datetime import UTC, datetime, timedelta

import pytest

from iotcenter.lite.storage import Storage
from iotcenter.protocol import dew_point, parse_line

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "iot.db")


def store(storage, make_line, offset_s=0.0, **fields):
    when = T0 + timedelta(seconds=offset_s)
    fields.setdefault("ts", when.isoformat())
    return storage.insert(parse_line(make_line(**fields), received_at=when))


def test_insert_is_idempotent(storage, make_line):
    assert store(storage, make_line, seq=1)
    assert not store(storage, make_line, seq=1)  # the same message delivered twice
    assert storage.stats()["messages"] == 1


def test_device_registry_counts_gaps_and_restarts(storage, make_line):
    for offset, seq in [(0, 1), (2, 2), (4, 5), (6, 0), (8, 1)]:
        store(storage, make_line, offset, seq=seq)

    [device] = storage.devices()
    assert device["messages"] == 5
    assert device["dropped"] == 2  # seq 3 and 4 never arrived
    assert device["restarts"] == 1  # seq went back to 0
    assert device["first_seen"] == T0.timestamp()
    assert device["last_seen"] == (T0 + timedelta(seconds=8)).timestamp()
    assert device["latest"]["seq"] == 1
    assert device["latest"]["dew_point_c"] == dew_point(21.5, 45.0)


def test_readings_are_returned_oldest_first_in_api_format(storage, make_line):
    for seq in range(5):
        store(storage, make_line, seq * 2, seq=seq, temperature_c=20 + seq)
    rows = storage.readings("pico-test01", T0.timestamp() + 1, T0.timestamp() + 100)

    assert [r["seq"] for r in rows] == [1, 2, 3, 4]
    assert rows[0]["event_time"] == T0.timestamp() + 2
    assert rows[0]["temperature_c"] == 21


def test_history_buckets_raw_readings(storage, make_line):
    for i in range(6):  # one reading every 10 s, temperatures 20..25
        store(storage, make_line, i * 10, seq=i, temperature_c=20 + i)
    start = T0.timestamp()
    history = storage.history("pico-test01", start, start + 60, bucket_s=30)

    assert history["t"] == [start, start + 30]
    assert history["samples"] == [3, 3]
    assert history["temperature_c"] == {"avg": [21.0, 24.0], "min": [20, 23], "max": [22, 25]}


def test_history_uses_hourly_aggregates_for_long_buckets(storage, make_line):
    for i, temperature in enumerate([10, 20, 30]):  # hour 0: 10 and 20, hour 1: 30
        store(storage, make_line, i * 1800, seq=i, temperature_c=temperature)
    storage.purge(older_than=T0.timestamp() + 10_000)  # raw rows gone: aggregates remain
    start = T0.timestamp()

    hourly = storage.history("pico-test01", start, start + 7200, bucket_s=3600)
    assert hourly["samples"] == [2, 1]
    assert hourly["temperature_c"]["avg"] == [15.0, 30.0]

    two_hourly = storage.history("pico-test01", start, start + 7200, bucket_s=7200)
    assert two_hourly["samples"] == [3]
    assert two_hourly["temperature_c"] == {"avg": [20.0], "min": [10], "max": [30]}


def test_purge_only_removes_old_raw_readings(storage, make_line):
    store(storage, make_line, 0, seq=1)
    store(storage, make_line, 100, seq=2)
    assert storage.purge(T0.timestamp() + 50) == 1
    assert [r["seq"] for r in storage.readings("pico-test01", 0, 2e9)] == [2]
    assert storage.devices()[0]["messages"] == 2  # counters are history, not current rows
