#!/usr/bin/env python3
"""Simulate Raspberry Pi Pico W + BME280 boards streaming sensor readings.

Each simulated board behaves like the real firmware: it keeps one TCP connection open,
sends one JSON object per line every `--interval` seconds, buffers readings while the
server is unreachable and delivers them once it is back. The values are physically
plausible: a daily temperature cycle, slowly drifting weather fronts, relative humidity
derived from a drifting dew point, and barometric pressure with its twice-daily tide.

Only the Python standard library is needed, except for kafka:// (pip install aiokafka).
Status messages go to stderr, so `--target stdout` prints nothing but NDJSON.

Examples:
  python simulator/simulate_picow.py                              # 1 board -> tcp://127.0.0.1:1500
  python simulator/simulate_picow.py --devices 3 --interval 1     # 3 boards, 1 reading/s each
  python simulator/simulate_picow.py --target tcp://raspberrypi.local:1500
  python simulator/simulate_picow.py --target kafka://localhost:9094   # skip the gateway
  python simulator/simulate_picow.py --target pty                 # fake USB serial port
  python simulator/simulate_picow.py --backfill 7d --backfill-only     # a week of history
  python simulator/simulate_picow.py --invalid-rate 0.05          # 5% broken messages
  python simulator/simulate_picow.py --target stdout --count 3    # just print NDJSON
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import math
import os
import random
import sys
import time
from collections import deque
from datetime import UTC, datetime
from urllib.parse import urlparse

FIRMWARE = "2.0.0-sim"

log = functools.partial(print, file=sys.stderr, flush=True)

# name, base °C, daily swing °C, dew point °C, Wi-Fi RSSI dBm
PROFILES = [
    ("living-room", 21.5, 1.2, 9.5, -52),
    ("office", 22.8, 1.8, 8.0, -61),
    ("garage", 12.0, 4.5, 4.0, -74),
    ("bedroom", 19.5, 1.0, 8.5, -66),
    ("kitchen", 22.0, 2.2, 11.0, -57),
    ("basement", 17.0, 0.6, 7.0, -78),
    ("attic", 18.0, 6.0, 3.0, -81),
    ("greenhouse", 20.0, 7.0, 13.0, -70),
]


# --- The physics ------------------------------------------------------------------------


class Drift:
    """A mean-reverting random walk (Ornstein-Uhlenbeck): wanders like weather, never escapes.

    `std` is how far it typically strays from zero, `timescale_s` how slowly it changes.
    """

    def __init__(self, rng: random.Random, std: float, timescale_s: float) -> None:
        self.rng, self.theta, self.value = rng, 1.0 / timescale_s, 0.0
        self.sigma = std * math.sqrt(2.0 * self.theta)

    def step(self, dt: float) -> float:
        noise = self.sigma * math.sqrt(max(dt, 0.0)) * self.rng.gauss(0, 1)
        self.value += -self.theta * self.value * max(dt, 0.0) + noise
        return self.value


class SimulatedPico:
    def __init__(self, index: int, rng: random.Random, base_pressure: float) -> None:
        name, base, swing, dew, rssi = PROFILES[index % len(PROFILES)]
        self.device_id = f"pico-sim{index + 1:02d}"
        self.name = name if index < len(PROFILES) else f"{name}-{index + 1}"
        self.index, self.rng = index, rng
        self.base, self.swing, self.dew, self.rssi = base, swing, dew, rssi
        self.base_pressure = base_pressure
        self.temperature_front = Drift(rng, std=1.0, timescale_s=6 * 3600)
        self.moisture_front = Drift(rng, std=1.5, timescale_s=12 * 3600)
        self.pressure_front = Drift(rng, std=6.0, timescale_s=2 * 86400)
        self.seq = 0
        self.boot_time: float | None = None
        self.last_t: float | None = None

    def reading(self, t: float) -> dict:
        """The message this board would send at Unix time `t` (call with increasing `t`)."""
        dt = 0.0 if self.last_t is None else t - self.last_t
        self.last_t = t
        if self.boot_time is None:
            self.boot_time = t
        rng = self.rng

        local = time.localtime(t)
        hour = local.tm_hour + local.tm_min / 60 + local.tm_sec / 3600
        daily = math.sin(2 * math.pi * (hour - 9) / 24)  # coolest ~03:00, warmest ~15:00

        temperature = self.base + self.swing * daily + self.temperature_front.step(dt)
        temperature += rng.gauss(0, 0.03)
        dew_point = min(self.dew + 0.3 * daily + self.moisture_front.step(dt), temperature - 0.5)
        humidity = 100 * math.exp(magnus(dew_point) - magnus(temperature)) + rng.gauss(0, 0.2)
        tide = 0.6 * math.sin(4 * math.pi * t / 86400)  # semidiurnal atmospheric tide
        pressure = self.base_pressure + self.pressure_front.step(dt) + tide + rng.gauss(0, 0.03)
        mem_free = int(min(max(rng.gauss(152_000, 6_000), 90_000), 185_000))

        message = {
            "v": 2,
            "device_id": self.device_id,
            "name": self.name,
            "seq": self.seq,
            "ts": iso(t),
            "temperature_c": round(temperature, 2),
            "humidity_pct": round(min(max(humidity, 1.0), 100.0), 2),
            "pressure_hpa": round(pressure, 2),
            "cpu_temp_c": round(temperature + 4.5 + rng.gauss(0, 0.6), 2),
            "mem_free_bytes": mem_free,
            "mem_alloc_bytes": 192_000 - mem_free,
            "storage_free_kb": 632.0,
            "cpu_freq_mhz": 125,
            "uptime_s": int(t - self.boot_time),
            "wifi_rssi_dbm": int(min(self.rssi + rng.gauss(0, 2), -30)),
            "ip": f"192.168.1.{100 + self.index}",
            "firmware": FIRMWARE,
        }
        self.seq += 1
        return message


def magnus(temperature_c: float) -> float:
    return 17.62 * temperature_c / (243.12 + temperature_c)


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def encode(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode()


def to_v1(message: dict) -> dict:
    """The 2023 (v1) firmware payload, to exercise the servers' backward compatibility."""
    when = datetime.fromtimestamp(time.time(), UTC)
    return {
        "id": random.randrange(60_000_000),
        "picow": {
            "local_ip": message["ip"],
            "temperature": message["cpu_temp_c"],
            "free_storage_kb": message["storage_free_kb"],
            "mem_alloc_bytes": message["mem_alloc_bytes"],
            "mem_free_bytes": message["mem_free_bytes"],
            "cpu_freq_mhz": message["cpu_freq_mhz"],
        },
        "bme280": {
            "temperature": f"{message['temperature_c']}C",
            "pressure": f"{message['pressure_hpa']}hPa",
            "humidity": f"{message['humidity_pct']}%",
            "read_datetime": f"{when.year}-{when.month}-{when.day} "
            f"{when.hour}:{when.minute}:{when.second}",
        },
    }


def corrupt(message: dict, rng: random.Random) -> bytes:
    """A deliberately invalid line, to watch validation and the dead-letter queue work."""
    choice = rng.randrange(3)
    if choice == 0:
        return b'{"device_id": "' + message["device_id"].encode() + b'", "oops"\n'
    if choice == 1:
        return encode({**message, "humidity_pct": 142.0})
    return encode({k: v for k, v in message.items() if k != "temperature_c"})


# --- Where the readings go ------------------------------------------------------------------


class TcpLink:
    """One persistent connection per board, like the firmware."""

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self.writer: asyncio.StreamWriter | None = None

    async def send(self, payload: bytes) -> None:
        if self.writer is None:
            _, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=5
            )
        self.writer.write(payload)
        await self.writer.drain()

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer = None


class OneShotTcpLink(TcpLink):
    """v1 behaviour: connect, send one JSON object without a newline, disconnect."""

    async def send(self, payload: bytes) -> None:
        _, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 5)
        writer.write(payload.rstrip(b"\n"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()


class FileLink:
    """Writes lines to a file descriptor: stdout, a pseudo-terminal or a serial device."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    async def send(self, payload: bytes) -> None:
        os.write(self.fd, payload)  # BlockingIOError (an OSError) while nobody is reading

    async def close(self) -> None:
        pass


class KafkaLink:
    """Produces straight to Kafka, bypassing the gateway, so it stamps `received_at` itself."""

    def __init__(self, bootstrap: str, topic: str) -> None:
        self.bootstrap, self.topic = bootstrap, topic
        self.producer = None

    async def start(self) -> None:
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError:
            sys.exit("kafka:// needs aiokafka: pip install aiokafka")
        self.producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap, linger_ms=20)
        await self.producer.start()

    async def send(self, payload: bytes) -> None:
        from aiokafka.errors import KafkaError

        try:
            message = json.loads(payload)
            message.update(received_at=iso(time.time()), source="simulator")
            key, value = str(message.get("device_id", "")).encode(), encode(message).rstrip()
        except (ValueError, AttributeError):  # a deliberately corrupt line: send it as-is
            key, value = None, payload.rstrip()
        try:
            await self.producer.send(self.topic, value, key=key)  # batched, flushed on stop()
        except KafkaError as exc:
            raise OSError(str(exc)) from exc

    async def close(self) -> None:
        pass

    async def stop(self) -> None:
        if self.producer:
            await self.producer.stop()


# --- The simulation loop ----------------------------------------------------------------


class Board:
    """One simulated Pico W: generates readings and delivers them, buffering when offline."""

    def __init__(self, pico: SimulatedPico, link, args: argparse.Namespace, stats: dict) -> None:
        self.pico, self.link, self.args, self.stats = pico, link, args, stats
        self.pending: deque[bytes] = deque(maxlen=1000)
        self.online: bool | None = None
        self.rng = random.Random(pico.rng.random())

    def queue(self, message: dict) -> None:
        if self.args.legacy:
            self.pending.append(encode(to_v1(message)))
        elif self.args.invalid_rate and self.rng.random() < self.args.invalid_rate:
            self.pending.append(corrupt(message, self.rng))
        else:
            self.pending.append(encode(message))

    async def flush(self) -> bool:
        """Try to deliver everything pending; False if the server is unreachable."""
        try:
            while self.pending:
                await self.link.send(self.pending[0])
                self.pending.popleft()
                self.stats["sent"] += 1
        except (TimeoutError, OSError) as exc:
            if self.online is not False:
                reason = exc or type(exc).__name__
                log(f"[{self.pico.name}] server unreachable ({reason}); buffering")
            self.online = False
            await self.link.close()
            return False
        if self.online is False:
            log(f"[{self.pico.name}] reconnected, buffer delivered")
        self.online = True
        return True

    async def run(self) -> None:
        args = self.args
        if args.backfill:  # history first, as fast as the server accepts it
            start = time.time() - args.backfill
            steps = int(args.backfill // args.backfill_step)
            backoff = 1.0
            for i in range(steps):
                self.queue(self.pico.reading(start + i * args.backfill_step))
                while len(self.pending) >= 200 and not await self.flush():
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
            while not await self.flush():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            log(f"[{self.pico.name}] backfilled {steps} readings")
            if args.backfill_only:
                return

        sent, next_at = 0, time.monotonic()
        while args.count == 0 or sent < args.count:
            self.queue(self.pico.reading(time.time()))
            sent += 1
            await self.flush()  # on failure, simply retry at the next tick
            next_at += args.interval
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))
        for _ in range(5):  # a few last attempts to deliver anything still buffered
            if await self.flush():
                return
            await asyncio.sleep(1)
        log(f"[{self.pico.name}] gave up: {len(self.pending)} readings were never delivered")


def open_link(target: str, boards: int, legacy: bool):
    """Build the transport(s) for a --target; returns (links, description, shared_link)."""
    url = urlparse(target if "://" in target else f"{target}://")
    if url.scheme == "tcp":
        host, port = url.hostname or "127.0.0.1", url.port or 1500
        link_class = OneShotTcpLink if legacy else TcpLink
        return [link_class(host, port) for _ in range(boards)], f"tcp://{host}:{port}", None
    if url.scheme == "kafka":
        topic = url.path.strip("/") or "iot.readings"
        shared = KafkaLink(f"{url.hostname or 'localhost'}:{url.port or 9094}", topic)
        return [shared] * boards, f"Kafka topic {topic} at {shared.bootstrap}", shared
    if url.scheme == "pty":
        import tty

        controller, device = os.openpty()
        tty.setraw(device)  # no echo or line editing: a clean byte pipe, like USB CDC
        os.set_blocking(controller, False)
        path = os.ttyname(device)
        log(f"Fake USB serial port ready: {path}\n  run: iotcenter lite --serial {path}")
        return [FileLink(controller)] * boards, f"pseudo-terminal {path}", None
    if url.scheme == "serial":
        fd = os.open(url.path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        return [FileLink(fd)] * boards, f"serial device {url.path}", None
    if url.scheme == "stdout":
        return [FileLink(sys.stdout.fileno())] * boards, "stdout", None
    sys.exit(
        f"Unknown target {target!r}: use tcp://host:port, kafka://host:port, pty, "
        "serial:///dev/ttyX or stdout"
    )


async def simulate(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    picos = [
        SimulatedPico(i, random.Random(rng.random()), args.pressure) for i in range(args.devices)
    ]
    links, where, shared = open_link(args.target, len(picos), args.legacy)
    if shared:
        await shared.start()
    stats = {"sent": 0}
    log(f"Simulating {len(picos)} Pico W ({', '.join(p.name for p in picos)}) -> {where}")

    async def report() -> None:
        while True:
            await asyncio.sleep(10)
            log(f"  ... {stats['sent']} messages sent")

    reporter = asyncio.create_task(report())
    try:
        await asyncio.gather(
            *(Board(p, link, args, stats).run() for p, link in zip(picos, links, strict=True))
        )
    finally:
        reporter.cancel()
        if shared:
            await shared.stop()
    log(f"Done: {stats['sent']} messages sent.")
    return stats["sent"]


def duration(text: str) -> float:
    """'90' -> 90 s, '15m', '24h', '7d'."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate Raspberry Pi Pico W + BME280 boards.",
        epilog="Examples:" + __doc__.split("Examples:", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add = parser.add_argument
    add(
        "--target",
        default="tcp://127.0.0.1:1500",
        help="tcp://host:port (default), kafka://host:port[/topic], pty, serial:///dev/X, stdout",
    )
    add("--devices", type=int, default=1, help="number of boards (default 1)")
    add("--interval", type=float, default=2.0, help="seconds between readings (default 2)")
    add("--count", type=int, default=0, help="live readings per board, then exit (0 = forever)")
    add("--backfill", type=duration, default=0, help="first send this much history: 24h, 7d")
    add("--backfill-step", type=duration, default=60, help="history spacing (default 60s)")
    add("--backfill-only", action="store_true", help="exit once the history is sent")
    add(
        "--pressure",
        type=float,
        default=1013.25,
        help="mean pressure in hPa (sea level 1013; at Calgary's altitude ~888)",
    )
    add("--invalid-rate", type=float, default=0.0, help="share of broken messages (default 0)")
    add("--legacy", action="store_true", help="speak the 2023 v1 protocol")
    add("--seed", type=int, help="random seed, for reproducible data")
    args = parser.parse_args(argv)
    if not 1 <= args.devices <= 64:
        parser.error("--devices must be between 1 and 64")
    if args.interval <= 0 or args.backfill_step <= 0:
        parser.error("--interval and --backfill-step must be positive")
    return args


def main(argv: list[str] | None = None) -> None:
    try:
        asyncio.run(simulate(parse_args(argv)))
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
