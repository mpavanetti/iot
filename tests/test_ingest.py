import asyncio
import json
import os
import time

import pytest

from iotcenter.ingest import IngestStats, LineProcessor, SerialIngest, TcpIngestServer
from iotcenter.protocol import Reading


class Collector:
    def __init__(self) -> None:
        self.readings: list[Reading] = []
        self.invalid: list[tuple[bytes, str, str]] = []
        self.stats = IngestStats()
        self.processor = LineProcessor(self.on_reading, self.on_invalid, self.stats)

    async def on_reading(self, reading: Reading) -> None:
        self.readings.append(reading)

    async def on_invalid(self, line: bytes, reason: str, source: str) -> None:
        self.invalid.append((line, reason, source))

    async def wait_for(self, count: int, timeout: float = 3.0) -> None:
        async def poll() -> None:
            while len(self.readings) + len(self.invalid) < count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(poll(), timeout)


@pytest.fixture
async def tcp_server():
    collector = Collector()
    server = TcpIngestServer("127.0.0.1", 0, collector.processor)
    await server.start()
    yield server, collector
    await server.stop()


async def test_tcp_streams_many_lines_on_one_connection(tcp_server, make_line):
    server, collector = tcp_server
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(make_line(seq=1) + make_line(seq=2) + make_line(seq=3))
    await writer.drain()
    await collector.wait_for(3)

    assert [r.seq for r in collector.readings] == [1, 2, 3]
    assert all(r.source == "tcp" for r in collector.readings)
    assert collector.stats.tcp_connections_open == 1
    writer.close()


async def test_tcp_reassembles_lines_split_across_packets(tcp_server, make_line):
    server, collector = tcp_server
    data = make_line(seq=7)
    _, writer = await asyncio.open_connection("127.0.0.1", server.port)
    for i in range(0, len(data), 10):  # dribble 10 bytes at a time
        writer.write(data[i : i + 10])
        await writer.drain()
        await asyncio.sleep(0.005)
    await collector.wait_for(1)
    assert collector.readings[0].seq == 7
    writer.close()


async def test_tcp_accepts_v1_style_one_message_per_connection(tcp_server, make_message):
    server, collector = tcp_server
    for seq in (1, 2):  # v1 firmware: connect, send JSON without newline, close
        _, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(json.dumps(make_message(seq=seq)).encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    await collector.wait_for(2)
    assert sorted(r.seq for r in collector.readings) == [1, 2]
    assert collector.stats.tcp_connections_total == 2


async def test_tcp_reports_invalid_lines_and_keeps_the_connection(tcp_server, make_line):
    server, collector = tcp_server
    _, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(b"not json\n" + make_line(humidity_pct=150) + make_line(seq=9))
    await writer.drain()
    await collector.wait_for(3)

    assert [r.seq for r in collector.readings] == [9]
    reasons = [reason for _, reason, _ in collector.invalid]
    assert "not JSON" in reasons[0] and "humidity_pct" in reasons[1]
    assert collector.stats.messages_invalid == 2
    assert collector.stats.messages_ok == 1
    writer.close()


async def test_tcp_turns_away_http_requests_without_counting_them(tcp_server, make_line):
    # Port scanners and service discovery probe the device port with HTTP now and then.
    server, collector = tcp_server
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(b"GET / HTTP/1.1\r\nHost: iotcenter.local:1500\r\nAccept: */*\r\n\r\n")
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 2) == b""  # closed, no reply
    writer.close()
    _, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(make_line(seq=1))
    await writer.drain()
    await collector.wait_for(1)
    assert collector.invalid == [] and collector.stats.messages_invalid == 0
    writer.close()


async def test_tcp_drops_connections_that_send_huge_lines(tcp_server):
    server, collector = tcp_server
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(b"{" + b"x" * 40_000 + b"}\n")
    await writer.drain()
    await collector.wait_for(1)
    assert "longer than" in collector.invalid[0][1]
    assert await asyncio.wait_for(reader.read(), 2) == b""  # server hung up
    writer.close()


async def test_serial_reads_json_lines_and_skips_device_logs(make_line):
    # A pseudo-terminal pair behaves like the Pico's USB CDC serial port.
    controller, device = os.openpty()
    collector = Collector()
    reader = SerialIngest(os.ttyname(device), 115200, collector.processor)
    await reader.start()
    try:
        await asyncio.sleep(0.2)
        os.write(controller, b"MicroPython v1.27 on 2026-01-01; Raspberry Pi Pico W\r\n")
        os.write(controller, b"Connecting to Wi-Fi...\r\n")
        os.write(controller, make_line(seq=42))
        await collector.wait_for(1)
        assert collector.readings[0].seq == 42
        assert collector.readings[0].source == "usb"
        assert collector.invalid == []
        assert collector.stats.serial_connected
    finally:
        await reader.stop()
        os.close(controller)
        os.close(device)


async def test_serial_tells_the_board_a_host_is_listening():
    # The board keeps USB as its link (Wi-Fi off) while these lines arrive, and sets its clock.
    controller, device = os.openpty()
    reader = SerialIngest(os.ttyname(device), 115200, Collector().processor)
    await reader.start()
    try:
        data = await asyncio.wait_for(asyncio.to_thread(os.read, controller, 1024), 3)
    finally:
        await reader.stop()
        os.close(controller)
        os.close(device)
    line = data.split(b"\n")[0]
    assert line.startswith(b"#iot ")
    now = json.loads(line[5:])["now"]
    assert isinstance(now, int) and now == pytest.approx(time.time(), abs=5)


async def test_serial_retries_until_the_port_appears():
    collector = Collector()
    reader = SerialIngest("/dev/does-not-exist", 115200, collector.processor)
    await reader.start()
    await asyncio.sleep(0.2)
    assert not collector.stats.serial_connected
    assert "does-not-exist" in collector.stats.last_error
    await reader.stop()
