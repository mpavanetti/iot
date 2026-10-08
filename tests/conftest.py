from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def message(**overrides: Any) -> dict[str, Any]:
    """A valid v2 device message, as the firmware sends it."""
    data: dict[str, Any] = {
        "v": 2,
        "device_id": "pico-test01",
        "name": "bench",
        "seq": 1,
        "ts": "2026-10-08T12:00:00Z",
        "temperature_c": 21.5,
        "humidity_pct": 45.0,
        "pressure_hpa": 1012.3,
        "cpu_temp_c": 27.1,
        "mem_free_bytes": 120_000,
        "mem_alloc_bytes": 60_000,
        "storage_free_kb": 640.0,
        "cpu_freq_mhz": 125,
        "uptime_s": 60,
        "wifi_rssi_dbm": -58,
        "ip": "192.168.1.74",
        "firmware": "2.0.0",
    }
    data.update(overrides)
    return data


def line(**overrides: Any) -> bytes:
    return (json.dumps(message(**overrides)) + "\n").encode()


@pytest.fixture
def make_message():
    return message


@pytest.fixture
def make_line():
    return line


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 10, 8, 12, 0, 5, tzinfo=UTC)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
