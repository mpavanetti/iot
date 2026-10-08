"""Spark transformations, tested in local mode inside the Spark image (`make test-spark`)."""

import json
from datetime import datetime

import iot_spark as job
import pytest
from pyspark.sql import SparkSession


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[2]")
        .appName("iot-spark-tests")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


def reading(**overrides):
    data = {
        "v": 2,
        "device_id": "pico-a",
        "seq": 1,
        "ts": "2026-10-08T12:00:00Z",
        "received_at": "2026-10-08T12:00:01Z",
        "source": "tcp",
        "temperature_c": 20.0,
        "humidity_pct": 50.0,
        "pressure_hpa": 1000.0,
    }
    data.update(overrides)
    return data


def kafka_frame(spark, payloads):
    rows = [
        (p if isinstance(p, bytes) else json.dumps(p).encode(), 0, i, datetime(2026, 10, 8, 13, 0))
        for i, p in enumerate(payloads)
    ]
    return spark.createDataFrame(
        rows, "value binary, partition int, offset long, timestamp timestamp"
    )


def test_parse_keeps_valid_readings_and_drops_the_rest(spark):
    frame = kafka_frame(
        spark,
        [
            reading(seq=1),
            reading(seq=2, humidity_pct=150.0),  # out of range
            reading(seq=3, device_id=None),  # missing identity
            b"{not json",
        ],
    )
    rows = job.parse_readings(frame).collect()
    assert [r.seq for r in rows] == [1]
    assert rows[0].kafka_offset == 0


def test_event_time_prefers_the_device_clock(spark):
    frame = kafka_frame(
        spark, [reading(seq=1), reading(seq=2, ts=None), reading(seq=3, ts=None, received_at=None)]
    )
    times = {r.seq: r.event_time for r in job.parse_readings(frame).collect()}
    assert times[1] == datetime(2026, 10, 8, 12, 0, 0)  # device clock
    assert times[2] == datetime(2026, 10, 8, 12, 0, 1)  # gateway arrival time
    assert times[3] == datetime(2026, 10, 8, 13, 0, 0)  # Kafka record time


def test_dew_point_matches_the_python_services(spark):
    frame = kafka_frame(spark, [reading(temperature_c=20.0, humidity_pct=50.0)])
    [row] = job.parse_readings(frame).collect()
    assert row.dew_point_c == pytest.approx(9.26, abs=0.01)  # same as protocol.dew_point()


def test_hourly_aggregates(spark):
    frame = kafka_frame(
        spark,
        [
            reading(seq=1, ts="2026-10-08T12:10:00Z", temperature_c=10.0),
            reading(seq=2, ts="2026-10-08T12:50:00Z", temperature_c=20.0),
            reading(seq=3, ts="2026-10-08T13:05:00Z", temperature_c=30.0),
        ],
    )
    hourly = job.hourly_aggregates(job.parse_readings(frame)).orderBy("hour").collect()
    assert [(r.hour.hour, r.samples) for r in hourly] == [(12, 2), (13, 1)]
    first = hourly[0]
    assert (first.temperature_avg, first.temperature_min, first.temperature_max) == (
        15.0,
        10.0,
        20.0,
    )
    assert set(job.HOURLY_COLUMNS) <= set(hourly[0].asDict())
