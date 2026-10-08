"""The simulator must speak exactly the contract the servers enforce."""

import asyncio
import importlib.util
import time

import pytest

from iotcenter.ingest import IngestStats, LineProcessor, TcpIngestServer
from iotcenter.protocol import Reading, parse_line

from .conftest import ROOT, free_port

spec = importlib.util.spec_from_file_location(
    "simulate_picow", ROOT / "simulator/simulate_picow.py"
)
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)


def test_every_simulated_message_satisfies_the_contract():
    pico = sim.SimulatedPico(0, sim.random.Random(7), base_pressure=1013.25)
    start = time.time() - 3 * 86400
    readings = [
        parse_line(sim.encode(pico.reading(start + i * 60))) for i in range(3 * 1440)
    ]  # three simulated days, one reading per minute

    assert [r.seq for r in readings[:3]] == [0, 1, 2]
    temperatures = [r.temperature_c for r in readings]
    assert 15 < min(temperatures) < max(temperatures) < 28  # a living room
    assert max(temperatures) - min(temperatures) > 1.5  # with a visible daily cycle
    assert all(r.dew_point_c < r.temperature_c for r in readings)
    assert all(990 < r.pressure_hpa < 1040 for r in readings)


def test_boards_get_distinct_identities():
    picos = [sim.SimulatedPico(i, sim.random.Random(i), 1013.0) for i in range(10)]
    assert len({p.device_id for p in picos}) == 10
    assert picos[0].name == "living-room" and picos[9].name == "office-10"


def test_legacy_mode_produces_v1_payloads_the_servers_upgrade():
    message = sim.SimulatedPico(1, sim.random.Random(1), 1013.0).reading(time.time())
    reading = parse_line(sim.encode(sim.to_v1(message)))
    assert reading.v == 1
    assert reading.temperature_c == message["temperature_c"]


@pytest.mark.parametrize("seed", range(5))
def test_corrupt_messages_are_rejected(seed):
    rng = sim.random.Random(seed)
    message = sim.SimulatedPico(0, rng, 1013.0).reading(time.time())
    with pytest.raises(ValueError):
        parse_line(sim.corrupt(message, rng))


def test_duration_parsing():
    assert [sim.duration(x) for x in ["90", "15m", "24h", "7d"]] == [90, 900, 86400, 604800]


async def collect(port: int, args: list[str], expect: int) -> list[Reading]:
    received: list[Reading] = []

    async def on_reading(reading: Reading) -> None:
        received.append(reading)

    server = TcpIngestServer("127.0.0.1", port, LineProcessor(on_reading, None, IngestStats()))
    await server.start()
    try:
        await sim.simulate(sim.parse_args(["--target", f"tcp://127.0.0.1:{port}", *args]))
        for _ in range(100):
            if len(received) >= expect:
                break
            await asyncio.sleep(0.02)
    finally:
        await server.stop()
    return received


async def test_streams_live_readings_over_tcp():
    readings = await collect(
        free_port(), ["--devices", "2", "--count", "3", "--interval", "0.05"], expect=6
    )
    assert len(readings) == 6
    assert {r.device_id for r in readings} == {"pico-sim01", "pico-sim02"}


async def test_backfill_sends_history_with_past_timestamps():
    readings = await collect(
        free_port(), ["--backfill", "2h", "--backfill-step", "60", "--backfill-only"], expect=120
    )
    assert len(readings) == 120
    oldest = min(r.event_time for r in readings).timestamp()
    assert time.time() - oldest == pytest.approx(7200, abs=60)


async def test_buffers_while_the_server_is_down_and_delivers_later():
    port = free_port()
    received: list[Reading] = []

    async def on_reading(reading: Reading) -> None:
        received.append(reading)

    args = sim.parse_args(
        ["--target", f"tcp://127.0.0.1:{port}", "--count", "8", "--interval", "0.1"]
    )
    simulation = asyncio.create_task(sim.simulate(args))
    await asyncio.sleep(0.45)  # ~4 readings are generated with nobody listening
    server = TcpIngestServer("127.0.0.1", port, LineProcessor(on_reading, None, IngestStats()))
    await server.start()
    try:
        await asyncio.wait_for(simulation, 10)
        await asyncio.sleep(0.1)
    finally:
        await server.stop()
    assert [r.seq for r in received] == list(range(8))  # nothing lost, in order
