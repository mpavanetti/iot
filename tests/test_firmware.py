"""The Pico W firmware, run on CPython.

telemetry.py is pure Python and is tested directly. The rest (main.py, link.py,
hardware.py) runs against small fakes of MicroPython's hardware modules (machine, network,
rp2, ntptime, bme280, ssd1306) and sends real TCP traffic to a real ingest server, so the
bytes the firmware produces are checked against the server's contract. A pipe stands in for
the USB port's input, carrying the server's own host lines.
"""

import array
import asyncio
import gc
import importlib
import os
import sys
import time
import types

import pytest

from iotcenter.ingest import IngestStats, LineProcessor, TcpIngestServer
from iotcenter.protocol import Reading, host_line, parse_line

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


def test_host_lines_from_the_server_are_understood(telemetry):
    # Whole seconds: a 32-bit float (MicroPython on the Pico) cannot hold a Unix time exactly
    assert telemetry.parse_host_line(host_line(1791480000.4).decode()) == {"now": 1791480000}
    assert telemetry.parse_host_line("#iot {broken") is None
    assert telemetry.parse_host_line("#iot [1, 2]") is None
    assert telemetry.parse_host_line('{"now": 1}') is None  # not a host line
    assert telemetry.parse_host_line(">>> print('hi')") is None


def test_dew_point_matches_the_server(telemetry):
    from iotcenter.protocol import dew_point

    for temperature, humidity in [(21.5, 45), (-10, 80), (30, 95), (5, 0)]:
        assert telemetry.dew_point(temperature, humidity) == pytest.approx(
            dew_point(temperature, humidity), abs=0.01
        )


def test_display_helpers(telemetry):
    assert [telemetry.comfort(h) for h in (18, 45, 72)] == ["dry", "ok", "humid"]
    assert [telemetry.duration(s) for s in (45, 750, 11_100, 183_600)] == [
        "45s",
        "12m",
        "3h05m",
        "2d03h",
    ]
    assert telemetry.bar("USB", "connected") == "USB    connected"
    assert len(telemetry.bar("Wi-Fi", "-61 dBm")) == 16


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


class Radio:
    """What the tests observe of the fake Wi-Fi chip and clock."""

    def __init__(self):
        self.active = False
        self.ever_on = False
        self.clock = None  # the last tuple written to machine.RTC().datetime()


class HostPipe:
    """Stands in for the board's USB input (sys.stdin): what the host writes to the port."""

    def __init__(self):
        self.r, self.w = os.pipe()

    def fileno(self):
        return self.r

    def read(self, n):
        return os.read(self.r, n).decode()

    def send(self, now):
        os.write(self.w, host_line(now))

    def close(self):
        os.close(self.r)
        os.close(self.w)


def fake_micropython(monkeypatch, oled_lines, radio):
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

    class RTC:
        def datetime(self, value):
            radio.clock = value

    machine = types.SimpleNamespace(
        Pin=Pin,
        I2C=lambda *args, **kwargs: object(),
        ADC=ADC,
        RTC=RTC,
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
            radio.active = on
            radio.ever_on = radio.ever_on or on

        def config(self, **kwargs):
            pass

        def connect(self, ssid, password):
            pass

        def disconnect(self):
            pass

        def isconnected(self):
            return radio.active

        def ifconfig(self):
            return ("192.168.1.74", "255.255.255.0", "192.168.1.1", "192.168.1.1")

        def status(self, what=None):
            return -61

    class BME280:
        def __init__(self, i2c, address):
            pass

        def read_compensated_data(self):  # 21.50 °C, 1013.25 hPa, 45 %RH in fixed point
            return array.array("i", (2150, 1013_25 * 256, 45 * 1024))

    class Oled:  # records the text of each screen, top to bottom
        def __init__(self, width, height, i2c):
            pass

        def fill(self, color):
            oled_lines.clear()

        def fill_rect(self, x, y, width, height, color):
            pass

        def text(self, text, x, y, color=1):
            oled_lines.append(text)

        def show(self):
            pass

    class FrameBuffer:  # where Board.big_text draws before scaling up
        def __init__(self, buffer, width, height, format):
            pass

        def text(self, text, x, y, color=1):
            oled_lines.append(text)

        def pixel(self, x, y):
            return 0

    modules = {
        "machine": machine,
        "network": types.SimpleNamespace(WLAN=WLAN, STA_IF=0),
        "rp2": types.SimpleNamespace(country=lambda code: None),
        "ntptime": types.SimpleNamespace(settime=lambda: None, host=None, timeout=1),
        "bme280": types.SimpleNamespace(BME280=BME280),
        "ssd1306": types.SimpleNamespace(SSD1306_I2C=Oled),
        "framebuf": types.SimpleNamespace(FrameBuffer=FrameBuffer, MONO_VLSB=0),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    # MicroPython's extra time and gc functions
    monkeypatch.setattr(time, "ticks_ms", lambda: int(time.monotonic() * 1000), raising=False)
    monkeypatch.setattr(time, "ticks_us", lambda: int(time.monotonic() * 1e6), raising=False)
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
    """The firmware's modules on fake hardware, plus the fake radio and the USB input pipe."""
    oled_lines: list[str] = []
    radio = Radio()
    usb = HostPipe()
    fake_micropython(monkeypatch, oled_lines, radio)
    monkeypatch.syspath_prepend(str(FIRMWARE))
    for name in ("telemetry", "hardware", "link", "main"):
        sys.modules.pop(name, None)
    main = importlib.import_module("main")
    yield types.SimpleNamespace(main=main, oled=oled_lines, radio=radio, usb=usb)
    usb.close()
    for name in ("telemetry", "hardware", "link", "main"):
        sys.modules.pop(name, None)


@pytest.fixture
def no_usb_host(firmware, monkeypatch):
    """Nobody reads USB: skip the boot-time wait for a host and go straight to Wi-Fi."""
    monkeypatch.setattr(firmware.main, "USB_FIRST_MS", 0)
    return firmware


async def test_firmware_streams_valid_readings_over_tcp(no_usb_host):
    main, oled_lines, usb = no_usb_host.main, no_usb_host.oled, no_usb_host.usb
    received: list[Reading] = []

    async def on_reading(reading: Reading) -> None:
        received.append(reading)

    stats = IngestStats()
    server = TcpIngestServer("127.0.0.1", 0, LineProcessor(on_reading, None, stats))
    await server.start()
    try:
        await asyncio.to_thread(main.run, firmware_config(server.port), 5, usb)
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
    last = received[-1]  # the board's own health: how busy its loop is, errors, why it started
    assert 0 < last.cpu_busy_pct <= 100 and last.loop_max_ms >= 0
    assert last.sensor_errors == 0 and last.boot_reason == "unknown"  # no reset_cause() here
    assert stats.tcp_connections_total == 1  # one persistent connection, not one per message
    assert oled_lines == [
        "Wi-Fi    -61 dBm",
        "21.5C",
        "45%RH  1013.2hPa",
        "dew 9.1C      ok",
        "sent 5",
    ]


async def test_firmware_buffers_until_the_server_comes_back(no_usb_host):
    main, usb = no_usb_host.main, no_usb_host.usb
    received: list[Reading] = []

    async def on_reading(reading: Reading) -> None:
        received.append(reading)

    from .conftest import free_port

    port = free_port()
    config = firmware_config(port, INTERVAL_S=0.1)
    run = asyncio.create_task(asyncio.to_thread(main.run, config, 12, usb))
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


def json_lines(output: str) -> list[Reading]:
    return [parse_line(line, source="usb") for line in output.splitlines() if line[:1] == "{"]


async def test_usb_only_mode_prints_json_lines(firmware, capsys):
    config = firmware_config(1, WIFI_ENABLED=False)
    await asyncio.to_thread(firmware.main.run, config, 2, firmware.usb)
    readings = json_lines(capsys.readouterr().out)
    assert [r.seq for r in readings] == [0, 1]
    assert readings[1].ip is None
    assert not firmware.radio.ever_on


async def test_usb_host_comes_first_and_keeps_wifi_off(firmware, capsys):
    firmware.usb.send(1791480000)  # the server is already reading the port at boot
    stats = IngestStats()
    server = TcpIngestServer("127.0.0.1", 0, LineProcessor(lambda r: asyncio.sleep(0), None, stats))
    await server.start()
    try:
        await asyncio.to_thread(firmware.main.run, firmware_config(server.port), 3, firmware.usb)
    finally:
        await server.stop()

    readings = json_lines(capsys.readouterr().out)
    assert [r.seq for r in readings] == [0, 1, 2]
    assert readings[0].wifi_rssi_dbm is None and readings[0].ip is None
    assert not firmware.radio.ever_on  # the radio never came on
    assert stats.tcp_connections_total == 0
    # The host's time went into the real-time clock: (year, month, day, weekday, h, m, s, 0)
    expected = time.gmtime(1791480000)
    assert firmware.radio.clock == (*expected[:3], expected[6] + 1, *expected[3:6], 0)
    assert firmware.oled[0] == "USB    connected"


async def test_falls_back_to_wifi_when_the_usb_host_goes_quiet(no_usb_host, monkeypatch, capsys):
    firmware = no_usb_host
    monkeypatch.setattr(sys.modules["link"], "HOST_TIMEOUT_MS", 300)
    received: list[Reading] = []

    async def on_reading(reading: Reading) -> None:
        received.append(reading)

    server = TcpIngestServer("127.0.0.1", 0, LineProcessor(on_reading, None, IngestStats()))
    await server.start()
    firmware.usb.send(time.time())  # one host line, then silence: the server went away
    config = firmware_config(server.port, INTERVAL_S=0.1, USB_OUTPUT=False)
    try:
        await asyncio.to_thread(firmware.main.run, config, 10, firmware.usb)
        over_usb = [r.seq for r in json_lines(capsys.readouterr().out)]
        for _ in range(100):
            if len(over_usb) + len(received) >= 10:
                break
            await asyncio.sleep(0.02)
    finally:
        await server.stop()

    over_wifi = [r.seq for r in received]
    assert firmware.radio.ever_on
    assert firmware.radio.clock is None  # already in step with the host: left alone
    assert over_usb and over_wifi
    assert over_usb[0] == 0 and over_wifi == sorted(over_wifi)
    assert max(over_usb) < min(over_wifi)  # USB first, then Wi-Fi
    assert sorted(over_usb + over_wifi) == list(range(10))  # each reading exactly once


def test_a_button_click_counts_once_per_press(firmware, monkeypatch):
    clock = [0]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    button = sys.modules["hardware"].Button(7)

    def at(ms, pressed):
        clock[0] = ms
        button.pin.value(0 if pressed else 1)
        return button.clicked()

    assert at(100, True)  # pressed
    assert not at(120, True)  # still held
    assert not at(130, False) and not at(140, True)  # contact bounce, ignored
    assert not at(400, False)  # released
    assert at(500, True)  # pressed again


def test_the_display_shows_pauses_and_problems(firmware):
    main, outbox = firmware.main, sys.modules["telemetry"].Outbox(10)
    environment = {"temperature_c": 21.5, "humidity_pct": 22.0, "pressure_hpa": 888.1}
    big, lines = main.readings_page(environment, outbox, 12, streaming=False)
    assert big == "21.5C"
    assert lines == ["22%RH   888.1hPa", "dew -1.1C    dry", "PAUSED: press 1"]
    big, lines = main.readings_page(None, outbox, 12, streaming=True)
    assert (big, lines[0]) == ("--.-C", "sensor error")


async def test_sensor_failures_are_counted_and_skipped(firmware, capsys):
    reads = [0]
    healthy = sys.modules["bme280"].BME280

    class Flaky(healthy):
        def read_compensated_data(self):
            reads[0] += 1
            if reads[0] == 3:  # the 2nd reading (the 1st read at start-up is thrown away)
                raise OSError(5)  # EIO: what a loose wire looks like
            return super().read_compensated_data()

    sys.modules["bme280"].BME280 = Flaky
    config = firmware_config(1, WIFI_ENABLED=False)
    await asyncio.to_thread(firmware.main.run, config, 2, firmware.usb)
    readings = json_lines(capsys.readouterr().out)
    assert [r.seq for r in readings] == [0, 1]  # the failed read cost no sequence number
    assert [r.sensor_errors for r in readings] == [0, 1]
