# Pure logic of the firmware: building messages, buffering, backoff.
# No hardware imports here, so the same file runs (and is unit-tested) on CPython.

import json

PROTOCOL_VERSION = 2
FIRMWARE_VERSION = "2.0.0"


def iso8601(tm):
    """time.gmtime() tuple -> '2026-10-08T04:19:53Z'."""
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % tm[:6]


def clock_is_set(tm):
    # The Pico's real-time clock starts in 2021 at power-on; NTP moves it to the present.
    return tm[0] >= 2024


def build_message(device_id, name, seq, ts, env, board):
    """One reading in the v2 wire format (docs/protocol.md).

    env:   dict with temperature_c, humidity_pct, pressure_hpa (from the BME280)
    board: dict with the Pico's health figures (any subset)
    ts:    ISO-8601 UTC string, or None when the clock is not NTP-synced
    """
    message = {
        "v": PROTOCOL_VERSION,
        "device_id": device_id,
        "seq": seq,
        "ts": ts,
        "temperature_c": round(env["temperature_c"], 2),
        "humidity_pct": round(env["humidity_pct"], 2),
        "pressure_hpa": round(env["pressure_hpa"], 2),
        "firmware": FIRMWARE_VERSION,
    }
    if name:
        message["name"] = name
    for key in (
        "cpu_temp_c",
        "mem_free_bytes",
        "mem_alloc_bytes",
        "storage_free_kb",
        "cpu_freq_mhz",
        "uptime_s",
        "wifi_rssi_dbm",
        "ip",
    ):
        if board.get(key) is not None:
            message[key] = board[key]
    return message


def encode(message):
    """JSON plus the newline that frames it (NDJSON)."""
    return (json.dumps(message) + "\n").encode()


class Outbox:
    """Readings waiting to be delivered. Bounded: when full, the oldest is dropped, so a
    long outage costs history but never memory."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.items = []
        self.dropped = 0

    def push(self, item):
        self.items.append(item)
        if len(self.items) > self.capacity:
            self.items.pop(0)
            self.dropped += 1

    def peek(self):
        return self.items[0]

    def pop(self):
        return self.items.pop(0)

    def __len__(self):
        return len(self.items)


class Backoff:
    """Exponential backoff for reconnecting: 1 s, 2 s, 4 s ... up to `maximum_ms`.
    Times are milliseconds from time.ticks_ms(); `diff` is time.ticks_diff."""

    def __init__(self, diff, initial_ms=1000, maximum_ms=30000):
        self.diff = diff
        self.initial_ms = initial_ms
        self.maximum_ms = maximum_ms
        self.delay_ms = 0
        self.last_failure = None

    def ready(self, now_ms):
        return self.last_failure is None or self.diff(now_ms, self.last_failure) >= self.delay_ms

    def failed(self, now_ms):
        self.delay_ms = min(max(self.delay_ms * 2, self.initial_ms), self.maximum_ms)
        self.last_failure = now_ms

    def succeeded(self):
        self.delay_ms = 0
        self.last_failure = None

    def wait_ms(self, now_ms):
        if self.ready(now_ms):
            return 0
        return self.delay_ms - self.diff(now_ms, self.last_failure)
