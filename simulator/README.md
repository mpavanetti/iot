# Pico W simulator

[`simulate_picow.py`](simulate_picow.py) pretends to be any number of Raspberry Pi Pico W + BME280 boards. It
speaks exactly the [message contract](../docs/protocol.md) the firmware does (a test keeps it that way), so
both editions treat it like real hardware. It needs only the Python standard library, plus `aiokafka` for the
Kafka target.

The data looks like a real home: each board has a daily temperature cycle, slow weather fronts, humidity that
follows from a drifting dew point, and barometric pressure with its twice-daily atmospheric tide. The garage
swings far more than the living room.

## Examples

```bash
python simulator/simulate_picow.py                                   # 1 board -> tcp://127.0.0.1:1500
python simulator/simulate_picow.py --devices 3 --interval 1          # 3 boards, a reading per second each
python simulator/simulate_picow.py --target tcp://raspberrypi.local:1500
python simulator/simulate_picow.py --backfill 7d --backfill-only     # a week of history, then exit
python simulator/simulate_picow.py --backfill 1d --devices 3         # a day of history, then live
python simulator/simulate_picow.py --target kafka://localhost:9094   # straight into Kafka, skipping the gateway
python simulator/simulate_picow.py --target pty                      # a fake USB serial port for Lite
python simulator/simulate_picow.py --invalid-rate 0.05               # 5% broken messages: see validation work
python simulator/simulate_picow.py --legacy                          # the 2023 v1 protocol
python simulator/simulate_picow.py --target stdout --count 3         # just print NDJSON
```

## Options

| Option | Default | |
|---|---|---|
| `--target` | `tcp://127.0.0.1:1500` | `tcp://host:port`, `kafka://host:port[/topic]`, `pty`, `serial:///dev/X` or `stdout` |
| `--devices` | 1 | number of boards (named living-room, office, garage, bedroom, ...) |
| `--interval` | 2 | seconds between readings, per board |
| `--count` | 0 | live readings per board before exiting (0 = run until Ctrl-C) |
| `--backfill` | | history to send first, e.g. `24h`, `7d` |
| `--backfill-step` | 60s | spacing of the backfilled readings |
| `--backfill-only` | | exit after the backfill |
| `--pressure` | 1013.25 | mean pressure in hPa: 1013 at sea level, about 888 at 1,045 m |
| `--invalid-rate` | 0 | share of deliberately broken messages |
| `--legacy` | | send v1 payloads, one connection per message |
| `--seed` | | make the data reproducible |

## Behaves like the firmware

Each board keeps one TCP connection open. While the server is unreachable it buffers readings (up to 1,000)
and delivers them in order once the server is back, so you can restart Lite or the gateway and watch nothing
get lost. If the server dies with a reading in flight, that one reading is lost, exactly as with a real board,
and shows up as a sequence gap.

The `pty` target creates a pseudo-terminal and prints its path. Point Lite at it to test the USB path without
hardware:

```bash
python simulator/simulate_picow.py --target pty --devices 2
# Fake USB serial port ready: /dev/pts/7
iotcenter lite --serial /dev/pts/7 --no-tcp
```

With `kafka://`, the simulator plays the gateway too: it stamps `received_at` itself and labels readings
`source: simulator`. Broken messages go straight into the readings topic, where Spark's validation drops them.
