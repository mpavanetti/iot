# IoT Center Lite

One Python process: devices in over TCP or USB, history in SQLite, a live dashboard out. No broker, no
database server, no Spark. It runs on a laptop or a small Raspberry Pi and needs about 100 MB of RAM.

```
Pico W ──TCP :1500 / USB──▶ validate ──▶ SQLite (raw + hourly + devices) ──▶ dashboard :8000
                                    └──▶ live hub ──── Server-Sent Events ──┘
```

How it works in detail: [docs/architecture.md](../docs/architecture.md#lite-one-process).

## Run it with Python

```bash
pip install -e ".[lite]"            # from the repository root, ideally in a virtualenv
iotcenter lite                      # dashboard on http://localhost:8000, devices on TCP 1500
```

| Flag | Default | |
|---|---|---|
| `--http-port` | 8000 | dashboard |
| `--tcp-port` | 1500 | where boards connect over Wi-Fi |
| `--no-tcp` | | USB only |
| `--serial PORT` | | also read a board over USB, e.g. `/dev/ttyACM0`; `iotcenter ports` lists candidates |
| `--db PATH` | `data/iot-lite.db` | the SQLite file |
| `--retention-days N` | 30 | raw readings to keep (hourly aggregates are kept forever); 0 keeps everything |

The same settings exist as `IOT_*` environment variables: see [docs/configuration.md](../docs/configuration.md).

No board yet? `python simulator/simulate_picow.py --devices 3`, or `--target pty` for a fake USB port.

## Run it with Docker

```bash
cd lite
docker compose up -d --build                                  # TCP only
docker compose -f compose.yaml -f compose.usb.yaml up -d      # plus a board on /dev/ttyACM0
```

The database lives in the `lite-data` volume. Ports and retention: `WEB_PORT`, `DEVICE_PORT`, `RETENTION_DAYS`,
`SERIAL_DEVICE` (in a `.env` file here, or the environment).

## What you get

- **Overview**: the latest values with trends, live charts (15 minutes) or history (1 hour to 30 days) with
  min–max bands, the board's health (memory, Wi-Fi signal, uptime, lost messages, restarts) and a CSV export.
- **Devices**: every board, its last reading and whether it is online.
- **Pipeline**: the TCP listener, USB reader, validation and storage, each with its state and counters, plus
  the host's CPU, memory, disk and temperature.

The API behind it is documented at http://localhost:8000/api/docs.
