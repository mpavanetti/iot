# Pure logic of the firmware: building messages, buffering, backoff.
# No hardware imports here, so the same file runs (and is unit-tested) on CPython.

import json
import math

PROTOCOL_VERSION = 2
FIRMWARE_VERSION = "2.1.0"

# A computer running IoT Center that reads the board's USB port writes a "host line" every few
# seconds: "#iot " and a JSON object with the Unix time in `now`. It means "USB is being read"
# (so the board keeps Wi-Fi off) and sets the clock (no NTP without Wi-Fi). At the REPL it is a
# comment, so it is harmless there.
HOST_LINE_PREFIX = "#iot "


def iso8601(tm):
    """time.gmtime() tuple -> '2026-10-08T04:19:53Z'."""
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % tm[:6]


def clock_is_set(tm):
    # The Pico's real-time clock starts in 2021 at power-on; NTP moves it to the present.
    return tm[0] >= 2024


def parse_host_line(line):
    """A line received over USB -> the host's message (a dict), or None if it is not one."""
    line = line.strip()
    if not line.startswith(HOST_LINE_PREFIX):
        return None
    try:
        message = json.loads(line[len(HOST_LINE_PREFIX) :])
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


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
        "cpu_busy_pct",
        "loop_max_ms",
        "sensor_errors",
        "boot_reason",
    ):
        if board.get(key) is not None:
            message[key] = board[key]
    return message


def dew_point(temperature_c, humidity_pct):
    """Dew point via the Magnus formula, the same as the server's (protocol.dew_point)."""
    a, b = 17.62, 243.12
    rh = min(max(humidity_pct, 0.1), 100.0)  # ln(0) is undefined
    gamma = math.log(rh / 100.0) + a * temperature_c / (b + temperature_c)
    return b * gamma / (a - gamma)


def comfort(humidity_pct):
    """Indoor humidity in a word: under 30 % is dry (a cold winter), over 60 % humid."""
    if humidity_pct < 30:
        return "dry"
    if humidity_pct > 60:
        return "humid"
    return "ok"


def duration(seconds):
    """A short uptime for the display: '45s', '12m', '3h05m', '2d03h'."""
    if seconds < 60:
        return "%ds" % seconds
    minutes = seconds // 60
    if minutes < 60:
        return "%dm" % minutes
    hours = minutes // 60
    if hours < 24:
        return "%dh%02dm" % (hours, minutes % 60)
    return "%dd%02dh" % (hours // 24, hours % 24)


def bar(left, right, width=16):
    """`left` and `right` on one display line, `right` flush right."""
    if not right:
        return left
    return left + " " * max(1, width - len(left) - len(right)) + right


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
