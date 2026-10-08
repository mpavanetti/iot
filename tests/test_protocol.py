import json
from datetime import UTC, datetime

import pytest

from iotcenter.protocol import InvalidMessage, Reading, dew_point, parse_line


def test_parses_a_v2_line_and_stamps_arrival(make_line, fixed_now):
    reading = parse_line(make_line(), source="usb", received_at=fixed_now)

    assert reading.device_id == "pico-test01"
    assert reading.temperature_c == 21.5
    assert reading.source == "usb"
    assert reading.received_at == fixed_now
    assert reading.event_time == datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)  # device clock wins


def test_event_time_falls_back_to_arrival_when_device_clock_is_unsynced(make_line, fixed_now):
    reading = parse_line(make_line(ts=None), received_at=fixed_now)
    assert reading.event_time == fixed_now


def test_server_owns_received_at_and_source(make_line, fixed_now):
    raw = make_line(received_at="1999-01-01T00:00:00Z", source="spoofed")
    reading = parse_line(raw, source="tcp", received_at=fixed_now)
    assert reading.received_at == fixed_now
    assert reading.source == "tcp"


def test_naive_timestamps_are_treated_as_utc(make_line, fixed_now):
    reading = parse_line(make_line(ts="2026-10-08T12:00:00"), received_at=fixed_now)
    assert reading.ts == datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def test_minimal_message_is_valid(fixed_now):
    raw = json.dumps(
        {"device_id": "d1", "seq": 0, "temperature_c": 20, "humidity_pct": 50, "pressure_hpa": 1000}
    )
    reading = parse_line(raw, received_at=fixed_now)
    assert reading.cpu_temp_c is None
    assert reading.v == 2


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"", "empty line"),
        (b"   \n", "empty line"),
        (b"hello", "not JSON"),
        (b"[1, 2]", "JSON must be an object"),
        (b"\xff\xfe", "UTF-8"),
        (b"x" * 20_000, "too long"),
    ],
)
def test_rejects_malformed_lines(raw, reason):
    with pytest.raises(InvalidMessage, match=reason):
        parse_line(raw)


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"humidity_pct": 120}, "humidity_pct"),
        ({"temperature_c": -80}, "temperature_c"),
        ({"pressure_hpa": float("nan")}, "pressure_hpa"),
        ({"device_id": "bad id with spaces"}, "device_id"),
        ({"seq": -1}, "seq"),
        ({"temperature_c": None}, "temperature_c"),
    ],
)
def test_rejects_values_outside_the_contract(make_line, override, field):
    with pytest.raises(InvalidMessage, match=field):
        parse_line(make_line(**override))


def test_upgrades_legacy_v1_payload_with_units(fixed_now):
    # Exactly what the 2023 firmware sent: one JSON object per connection, no newline.
    v1 = {
        "id": 20923220,
        "picow": {
            "local_ip": "192.168.1.74",
            "temperature": 24.24184,
            "free_storage_kb": 636.0,
            "mem_alloc_bytes": 53520,
            "mem_free_bytes": 89328,
            "cpu_freq_mhz": 125.0,
        },
        "bme280": {
            "pressure": "890.07hPa",
            "temperature": "21.92C",
            "humidity": "44.83%",
            "read_datetime": "2023-9-6 16:4:51",
        },
    }
    reading = parse_line(json.dumps(v1), received_at=fixed_now)

    assert reading.v == 1
    assert reading.device_id == "pico-192-168-1-74"
    assert reading.seq == 20923220
    assert (reading.temperature_c, reading.humidity_pct, reading.pressure_hpa) == (
        21.92,
        44.83,
        890.07,
    )
    assert reading.ts == datetime(2023, 9, 6, 16, 4, 51, tzinfo=UTC)
    assert reading.cpu_temp_c == 24.24184


def test_upgrades_legacy_v1_payload_with_numbers(fixed_now):
    v1 = {
        "bme280": {"temperature": 23.7, "pressure": 886.7, "humidity": 39.8, "read_datetime": "x"},
        "picow": {"local_ip": "10.0.0.5", "temperature": 31.3, "cpu_freq_mhz": 0},
    }
    reading = parse_line(json.dumps(v1), received_at=fixed_now)
    assert reading.ts is None  # unparseable device time -> arrival time is used
    assert reading.event_time == fixed_now
    assert reading.cpu_freq_mhz is None


@pytest.mark.parametrize(
    ("temperature", "humidity", "expected"),
    [(20.0, 50.0, 9.26), (25.0, 80.0, 21.31), (0.0, 100.0, 0.0), (30.0, 10.0, -4.96)],
)
def test_dew_point_matches_reference_values(temperature, humidity, expected):
    assert dew_point(temperature, humidity) == pytest.approx(expected, abs=0.02)


def test_dew_point_is_defined_at_zero_humidity():
    assert dew_point(20.0, 0.0) < -40


def test_record_uses_unix_seconds_and_kafka_json_round_trips(make_line, fixed_now):
    reading = parse_line(make_line(), received_at=fixed_now)

    record = reading.to_record()
    assert record["received_at"] == fixed_now.timestamp()
    assert record["event_time"] == reading.event_time.timestamp()
    assert record["dew_point_c"] == reading.dew_point_c

    wire = json.loads(reading.to_json())
    assert wire["ts"] == "2026-10-08T12:00:00Z"
    assert "dew_point_c" in wire and "event_time" in wire
    # Consumers (web app, Spark) read the enriched JSON back: computed fields are ignored.
    assert Reading.model_validate_json(reading.to_json()) == reading
