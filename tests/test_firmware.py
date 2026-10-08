"""The Pico W firmware, run on CPython.

telemetry.py is pure Python and is tested directly. The rest (main.py, link.py,
hardware.py) runs against small fakes of MicroPython's hardware modules (machine, network,
rp2, ntptime, bme280, ssd1306) and sends real TCP traffic to a real ingest server, so the
bytes the firmware produces are checked against the server's contract.
"""

import array
import asyncio
import gc
import importlib
import sys
import time
import types

import pytest

from iotcenter.ingest import IngestStats, LineProcessor, TcpIngestServer
from iotcenter.protocol import Reading, parse_line

from .conftest import ROOT

FIRMWARE = ROOT / "firmware"


@pytest.fixture
def telemetry(monkeypatch):
    monkeypatch.syspath_prepend(str(FIRMWARE))
    module = importlib.import_module("telemetry")
    yield module
    sys.modules.pop("telemetry", None)


def test_message_matches_the_server_contract(telemetry):
    message = telemetry.build_message(
        "pico-e6614103e7473b2a",
        "bench",
        7,
        telemetry.iso8601((2026, 10, 8, 4, 19, 53, 3, 281)),
        {"temperature_c": 21.917, "humidity_pct": 44.833, "pressure_hpa": 890.071},
        {
            "cpu_temp_c": 24.2,
            "mem_free_bytes": 150_000,
            "ip": "192.168.1.74",
            "wifi_rssi_dbm": None,
        },
    )
    reading = parse_line(telemetry.encode(message))
    assert reading.ts.isoformat() == "2026-10-08T04:19:53+00:00"
    assert (reading.temperature_c, reading.humidity_pct, reading.pressure_hpa) == (
        21.92,
        44.83,
        890.07,
    )
    assert reading.wifi_rssi_dbm is None  # missing values are simply left out
    assert reading.firmware == telemetry.FIRMWARE_VERSION


def test_clock_counts_as_set_only_after_ntp(telemetry):
    assert not telemetry.clock_is_set((2021, 1, 1, 0, 0, 0, 4, 1))  # RP2040 power-on time
    assert telemetry.clock_is_set((2026, 10, 8, 4, 19, 53, 3, 281))


def test_outbox_drops_the_oldest_when_full(telemetry):
    outbox = telemetry.Outbox(capacity=3)
    for item in range(5):
        outbox.push(item)
    assert outbox.items == [2, 3, 4]
    assert outbox.dropped == 2
    assert outbox.pop() == 2 and len(outbox) == 2


def test_backoff_doubles_up_to_a_maximum(telemetry):
    backoff = telemetry.Backoff(lambda a, b: a - b, initial_ms=1000, maximum_ms=4000)
    assert backoff.ready(0)
    delays = []
    for _ in range(4):
        backoff.failed(0)
        delays.append(backoff.delay_ms)
    assert delays == [1000, 2000, 4000, 4000]
    assert not backoff.ready(3999) and backoff.ready(4000)
    backoff.succeeded()
    assert backoff.ready(0)


# --- the whole firmware against fake hardware ------------------------------------------------


def fake_micropython(monkeypatch, oled_lines):
    """Install minimal stand-ins for the MicroPython modules the firmware imports."""

    class Pin:
        OUT, IN, PULL_UP = 1, 0, 1

        def __init__(self, *args, **kwargs):
            self._value = 1  # buttons read 1 (not pressed) thanks to the pull-up

        def value(self, *args):
            if args:
                self._value = args[0]
            return self._value

        def toggle(self):
            self._value ^= 1

    class ADC:
        def __init__(self, channel):
            pass

        def read_u16(self):
            return 14_021  # 0.706 V: exactly 27 °C

    machine = types.SimpleNamespace(
        Pin=Pin,
        I2C=lambda *args, **kwargs: object(),
        ADC=ADC,
        WDT=lambda timeout: types.SimpleNamespace(feed=lambda: None),
        unique_id=lambda: bytes.fromhex("e6614103e7473b2a"),
        freq=lambda: 125_000_000,
        reset=lambda: None,
    )

    class WLAN:
        PM_NONE = 0

        def __init__(self, interface):
            pass

        def active(self, on):
            pass

        def config(self, **kwargs):
            pass

        def connect(self, ssid, password):
            pass

        def disconnect(self):
            pass

        def isconnected(self):
            return True

        def ifconfig(self):
            return ("192.168.1.74", "255.255.255.0", "192.168.1.1", "192.168.1.1")

        def status(self, what=None):
            return -61

    class BME280:
        def __init__(self, i2c, address):
            pass

        def read_compensated_data(self):  # 21.50 °C, 1013.25 hPa, 45 %RH in fixed point
            return array.array("i", (2150, 1013_25 * 256, 45 * 1024))

    class Oled:
        def __init__(self, width, height, i2c):
            pass

        def fill(self, color):
            oled_lines.clear()

        def text(self, text, x, y):
            oled_lines.append(text)

        def show(self):
            pass

    modules = {
        "machine": machine,
        "network": types.SimpleNamespace(WLAN=WLAN, STA_IF=0),
        "rp2": types.SimpleNamespace(country=lambda code: None),
        "ntptime": types.SimpleNamespace(settime=lambda: None, host=None, timeout=1),
        "bme280": types.SimpleNamespace(BME280=BME280),
        "ssd1306": types.SimpleNamespace(SSD1306_I2C=Oled),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    # MicroPython's extra time and gc functions
    monkeypatch.setattr(time, "ticks_ms", lambda: int(time.monotonic() * 1000), raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda a, b: a - b, raising=False)
    monkeypatch.setattr(time, "ticks_add", lambda a, b: a + b, raising=False)
    monkeypatch.setattr(time, "sleep_ms", lambda ms: time.sleep(ms / 1000), raising=False)
    monkeypatch.setattr(gc, "mem_free", lambda: 150_000, raising=False)
    monkeypatch.setattr(gc, "mem_alloc", lambda: 42_000, raising=False)


def firmware_config(port: int, **overrides):
    namespace: dict = {}
    exec((FIRMWARE / "config.example.py").read_text(), namespace)
    config = types.SimpleNamespace(**{k: v for k, v in namespace.items() if k.isupper()})
    config.SERVER_HOST, config.SERVER_PORT, config.INTERVAL_S = "127.0.0.1", port, 0.05
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


@pytest.fixture
def firmware(monkeypatch):
    oled_lines: list[str] = []
    fake_micropython(monkeypatch, oled_lines)
    monkeypatch.syspath_prepend(str(FIRMWARE))
    for name in ("telemetry", "hardware", "link", "main"):
        sys.modules.pop(name, None)
    main = importlib.import_module("main")
    yield main, oled_lines
    for name in ("telemetry", "hardware", "link", "main"):
        sys.modules.pop(name, None)


async def test_firmware_streams_valid_readings_over_tcp(firmware):
    main, oled_lines = firmware
    received: list[Reading] = []

    async def on_reading(reading: Reading) -> None:
        received.append(reading)

    stats = IngestStats()
    server = TcpIngestServer("127.0.0.1", 0, LineProcessor(on_reading, None, stats))
    await server.start()
    try:
        await asyncio.to_thread(main.run, firmware_config(server.port), 5)
        for _ in range(100):
            if len(received) == 5:
                break
            await asyncio.sleep(0.02)
    finally:
        await server.stop()

    assert stats.messages_invalid == 0
    assert [r.seq for r in received] == [0, 1, 2, 3, 4]
    first = received[0]
    assert first.device_id == "pico-e6614103e7473b2a"
    assert (first.temperature_c, first.humidity_pct, first.pressure_hpa) == (21.5, 45.0, 1013.25)
    assert first.cpu_temp_c == pytest.approx(27.0, abs=0.05)  # 16-bit ADC quantization
    assert first.wifi_rssi_dbm == -61 and first.ip == "192.168.1.74"
    assert stats.tcp_connections_total == 1  # one persistent connection, not one per message
    assert oled_lines[0] == "21.5C  45%RH"
    assert oled_lines[-1] == "STREAMING"


async def test_firmware_buffers_until_the_server_comes_back(firmware):
    main, _ = firmware
    received: list[Reading] = []

    async def on_reading(reading: Reading) -> None:
        received.append(reading)

    from .conftest import free_port

    port = free_port()
    config = firmware_config(port, INTERVAL_S=0.1)
    run = asyncio.create_task(asyncio.to_thread(main.run, config, 12))
    await asyncio.sleep(0.5)  # a few readings are taken while nothing listens
    server = TcpIngestServer("127.0.0.1", port, LineProcessor(on_reading, None, IngestStats()))
    await server.start()
    try:
        await asyncio.wait_for(run, 15)
        for _ in range(100):
            if len(received) >= 11:
                break
            await asyncio.sleep(0.02)
    finally:
        await server.stop()
    seqs = [r.seq for r in received]
    assert seqs == sorted(seqs) and seqs[0] == 0  # the outage cost nothing, order kept


async def test_usb_only_mode_prints_json_lines(firmware, capsys):
    main, _ = firmware
    config = firmware_config(1, WIFI_ENABLED=False)
    await asyncio.to_thread(main.run, config, 2)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert len(lines) == 2
    reading = parse_line(lines[1], source="usb")
    assert reading.seq == 1 and reading.ip is None
