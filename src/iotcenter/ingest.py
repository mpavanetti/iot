"""Device ingestion: newline-delimited JSON from TCP clients or a USB serial port.

Both readers turn raw lines into `Reading`s with `protocol.parse_line()` and hand them to an
`on_reading` callback. That single seam is what lets the same code feed the Lite edition
(callback stores to SQLite) and the platform gateway (callback produces to Kafka).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

from .protocol import MAX_LINE_BYTES, InvalidMessage, Reading, host_line, parse_line

log = logging.getLogger(__name__)

HOST_LINE_EVERY_S = 5.0  # boards fall back to Wi-Fi after 15 s without a host line

# "GET / HTTP/1.1": a browser, a port scanner or a dashboard's service discovery, not a board.
HTTP_REQUEST = re.compile(rb"^[A-Z]{3,7} \S+ HTTP/\d")

OnReading = Callable[[Reading], Awaitable[None]]
OnInvalid = Callable[[bytes, str, str], Awaitable[None]]  # (raw line, reason, source)


@dataclass
class IngestStats:
    """Counters shown on the dashboard's Pipeline page."""

    tcp_listening: bool = False
    tcp_port: int | None = None
    tcp_connections_open: int = 0
    tcp_connections_total: int = 0
    serial_port: str | None = None
    serial_connected: bool = False
    messages_ok: int = 0
    messages_invalid: int = 0
    last_message_at: float | None = None
    last_error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class LineProcessor:
    """Parse one line and route it to the right callback, counting as it goes."""

    def __init__(self, on_reading: OnReading, on_invalid: OnInvalid | None, stats: IngestStats):
        self.on_reading = on_reading
        self.on_invalid = on_invalid
        self.stats = stats

    async def process(self, line: bytes, source: str) -> None:
        try:
            reading = parse_line(line, source=source)
        except InvalidMessage as exc:
            await self.reject(line, str(exc), source)
            return
        self.stats.messages_ok += 1
        self.stats.last_message_at = time.time()
        try:
            await self.on_reading(reading)
        except Exception:  # a storage hiccup must not kill the device connection
            log.exception("Failed to handle reading from %s", reading.device_id)

    async def reject(self, line: bytes, reason: str, source: str) -> None:
        self.stats.messages_invalid += 1
        self.stats.last_error = reason
        log.warning("Rejected message from %s: %s", source, reason)
        if self.on_invalid:
            await self.on_invalid(line, reason, source)


class TcpIngestServer:
    """Accepts any number of device connections; each connection streams NDJSON lines.

    A connection may stay open for hours (v2 firmware) or carry a single message and close
    (v1 firmware): `readline()` returns the final unterminated line at EOF, so both work.
    """

    def __init__(self, host: str, port: int, processor: LineProcessor) -> None:
        self.host = host
        self.port = port
        self.processor = processor
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, self.host, self.port, limit=MAX_LINE_BYTES
        )
        sockets = self._server.sockets or []
        if sockets:  # port 0 means "pick a free port" (used by the tests)
            self.port = sockets[0].getsockname()[1]
        self.processor.stats.tcp_listening = True
        self.processor.stats.tcp_port = self.port
        log.info("Listening for devices on tcp://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            for writer in list(self._clients):  # wait_closed() waits for open connections
                writer.close()
            await self._server.wait_closed()
        self.processor.stats.tcp_listening = False

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stats = self.processor.stats
        peer = writer.get_extra_info("peername")
        peer_name = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        self._clients.add(writer)
        stats.tcp_connections_open += 1
        stats.tcp_connections_total += 1
        log.info("Device connected from %s", peer_name)
        first = True
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:  # longer than MAX_LINE_BYTES: not one of our devices
                    await self.processor.reject(
                        b"", f"line longer than {MAX_LINE_BYTES} bytes", "tcp"
                    )
                    break
                if not line:  # EOF: the device closed the connection
                    break
                if first and HTTP_REQUEST.match(line):
                    log.info(
                        "HTTP request from %s on the device port: not a board, closing", peer_name
                    )
                    break
                first = False
                if line.strip():
                    await self.processor.process(line, "tcp")
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            stats.tcp_connections_open -= 1
            writer.close()
            log.info("Device disconnected from %s", peer_name)


class SerialIngest:
    """Reads NDJSON lines from a Pico W plugged in over USB.

    pyserial is blocking, so each read runs in a worker thread. Lines that are not JSON
    objects (MicroPython boot banners, print() logs) are skipped. When the board is
    unplugged or reboots, the port disappears; we simply retry until it comes back.

    While the port is open, a host line goes to the board every few seconds: the firmware
    then keeps USB as its link (Wi-Fi off) and sets its clock from it.
    """

    def __init__(self, port: str, baudrate: int, processor: LineProcessor) -> None:
        self.port = port
        self.baudrate = baudrate
        self.processor = processor
        self._task: asyncio.Task | None = None
        self._stopping = False
        processor.stats.serial_port = port

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"serial:{self.port}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        import serial  # imported lazily: only needed when a serial port is configured

        stats = self.processor.stats
        while not self._stopping:
            try:
                port = await asyncio.to_thread(
                    serial.Serial, self.port, self.baudrate, timeout=1, write_timeout=2
                )
            except (serial.SerialException, OSError) as exc:
                stats.serial_connected = False
                stats.last_error = f"{self.port}: {exc}"
                await asyncio.sleep(2)
                continue
            log.info("Reading device data from serial port %s", self.port)
            stats.serial_connected = True
            announce = asyncio.create_task(self._announce(port))
            try:
                while not self._stopping:
                    line = await asyncio.to_thread(port.readline)  # b"" after a 1 s timeout
                    if line.lstrip().startswith(b"{"):
                        await self.processor.process(line, "usb")
            except (serial.SerialException, OSError) as exc:
                log.warning("Serial port %s lost: %s", self.port, exc)
            finally:
                announce.cancel()
                stats.serial_connected = False
                port.close()

    async def _announce(self, port) -> None:
        """Write a host line now and every few seconds while the port is open."""
        import serial

        while True:
            try:
                await asyncio.to_thread(port.write, host_line(time.time()))
            except serial.SerialTimeoutException:
                pass  # the board is not reading its USB input (older firmware): harmless
            except (serial.SerialException, OSError):
                return  # the port is gone; the reader notices and reopens it
            await asyncio.sleep(HOST_LINE_EVERY_S)
