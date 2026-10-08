"""The message contract shared by every component.

A Pico W (or the simulator) sends one JSON object per line ("NDJSON") over TCP or USB serial:

    {"v": 2, "device_id": "pico-e6614103e7", "name": "living-room", "seq": 42,
     "ts": "2026-10-08T04:19:53Z", "temperature_c": 21.92, "humidity_pct": 44.83,
     "pressure_hpa": 890.07, "cpu_temp_c": 24.2, "wifi_rssi_dbm": -61, ...}

`parse_line()` turns those raw bytes into a validated `Reading`. The receiving server stamps
`received_at` and `source`, and the model derives `event_time` and `dew_point_c`.

The same flat field names are used end to end: Kafka messages, SQLite/PostgreSQL columns,
the REST API and the dashboard. See docs/protocol.md for the full reference.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field, field_validator

PROTOCOL_VERSION = 2

# Lines longer than this are rejected; a real reading is ~400 bytes.
MAX_LINE_BYTES = 16 * 1024


class InvalidMessage(ValueError):
    """A line that cannot be turned into a valid `Reading`."""


class Reading(BaseModel):
    """One measurement from one device, plus the metadata added on arrival."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    # Identity
    v: int = PROTOCOL_VERSION
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    name: str | None = Field(default=None, max_length=64)
    seq: int = Field(ge=0, description="Message counter, restarts at 0 when the board reboots")
    ts: datetime | None = Field(default=None, description="Device clock (UTC); null if not synced")

    # BME280 environment sensor (limits are the sensor's operating range)
    temperature_c: float = Field(ge=-40, le=85)
    humidity_pct: float = Field(ge=0, le=100)
    pressure_hpa: float = Field(ge=300, le=1100)

    # Pico W board health (all optional, so minimal senders stay valid)
    cpu_temp_c: float | None = Field(default=None, ge=-40, le=125)
    mem_free_bytes: int | None = Field(default=None, ge=0)
    mem_alloc_bytes: int | None = Field(default=None, ge=0)
    storage_free_kb: float | None = Field(default=None, ge=0)
    cpu_freq_mhz: float | None = Field(default=None, ge=0)
    uptime_s: int | None = Field(default=None, ge=0)
    wifi_rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    ip: str | None = Field(default=None, max_length=45)
    firmware: str | None = Field(default=None, max_length=32)

    # Stamped by the server that received the line
    received_at: datetime
    source: str = Field(default="tcp", max_length=32)

    @field_validator("ts", "received_at")
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:  # naive timestamps are UTC by convention
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @computed_field
    @property
    def event_time(self) -> datetime:
        """When the measurement happened: the device clock if synced, else arrival time."""
        return self.ts or self.received_at

    @computed_field
    @property
    def dew_point_c(self) -> float:
        return dew_point(self.temperature_c, self.humidity_pct)

    def to_json(self) -> str:
        """The wire/Kafka representation (timestamps as ISO-8601 strings)."""
        return self.model_dump_json()

    def to_record(self) -> dict[str, Any]:
        """The API/dashboard representation: same fields, timestamps as Unix seconds."""
        record = self.model_dump(mode="json", exclude={"ts", "received_at", "event_time"})
        record["ts"] = self.ts.timestamp() if self.ts else None
        record["received_at"] = self.received_at.timestamp()
        record["event_time"] = self.event_time.timestamp()
        return record


def dew_point(temperature_c: float, humidity_pct: float) -> float:
    """Dew point via the Magnus formula (Sonntag 1990 constants, accurate to ~0.1 °C)."""
    a, b = 17.62, 243.12
    rh = min(max(humidity_pct, 0.1), 100.0)  # ln(0) is undefined
    gamma = math.log(rh / 100.0) + a * temperature_c / (b + temperature_c)
    return round(b * gamma / (a - gamma), 2)


def parse_line(
    line: bytes | str,
    *,
    source: str = "tcp",
    received_at: datetime | None = None,
) -> Reading:
    """Validate one line from a device and stamp it with arrival metadata.

    Accepts the v2 format above and the legacy v1 format (2023 firmware), which is upgraded
    on the fly. Raises `InvalidMessage` with a readable reason for anything else.
    """
    if isinstance(line, bytes):
        if len(line) > MAX_LINE_BYTES:
            raise InvalidMessage(f"line too long ({len(line)} bytes)")
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidMessage("line is not valid UTF-8") from exc

    text = line.strip()
    if not text:
        raise InvalidMessage("empty line")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidMessage(f"not JSON: {exc.msg} at column {exc.colno}") from exc
    if not isinstance(data, dict):
        raise InvalidMessage("JSON must be an object")

    if "bme280" in data:
        data = upgrade_v1(data)

    # The receiving server is the authority for these two fields.
    data["received_at"] = received_at or datetime.now(UTC)
    data["source"] = source
    try:
        return Reading.model_validate(data)
    except ValidationError as exc:
        raise InvalidMessage(_summarize(exc)) from exc


def upgrade_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Translate a v1 payload: nested `bme280`/`picow` objects, values like "21.92C"."""
    bme = data.get("bme280") or {}
    pico = data.get("picow") or {}
    ip = pico.get("local_ip")
    return {
        "v": 1,
        "device_id": f"pico-{ip.replace('.', '-')}" if ip else "pico-v1",
        "seq": abs(int(data.get("id") or 0)),
        "ts": _parse_v1_datetime(bme.get("read_datetime")),
        "temperature_c": _strip_unit(bme.get("temperature"), "C"),
        "humidity_pct": _strip_unit(bme.get("humidity"), "%"),
        "pressure_hpa": _strip_unit(bme.get("pressure"), "hPa"),
        "cpu_temp_c": pico.get("temperature"),
        "mem_free_bytes": pico.get("mem_free_bytes"),
        "mem_alloc_bytes": pico.get("mem_alloc_bytes"),
        "storage_free_kb": pico.get("free_storage_kb"),
        "cpu_freq_mhz": pico.get("cpu_freq_mhz") or None,
        "ip": ip,
        "firmware": "1.x",
    }


def _strip_unit(value: Any, unit: str) -> Any:
    if isinstance(value, str):
        return value.strip().removesuffix(unit)
    return value


def _parse_v1_datetime(value: Any) -> datetime | None:
    # v1 formatted time.gmtime() without zero padding, e.g. "2023-9-6 16:4:51" (UTC).
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _summarize(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:3]:
        field = ".".join(str(p) for p in error["loc"]) or "message"
        parts.append(f"{field}: {error['msg']}")
    return "; ".join(parts)
