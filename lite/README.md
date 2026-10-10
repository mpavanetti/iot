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
| `--camera DEVICE` | | stream a USB webcam, e.g. `/dev/video0` (needs `pip install -e ".[lite,camera]"`); `demo` streams a test scene. See [camera](../docs/camera.md) |
| `--record` | | with `--camera`: record a short clip of each motion, kept 30 days in `data/recordings` |
| `--microphone DEVICE` | | listen to an ALSA device, e.g. `plughw:CARD=C960,DEV=0` (needs `arecord`): sound level, alarms, Listen; `demo` plays test sounds |
| `--retention-days N` | 30 | days of raw readings to keep (every message: the live to 24h charts, CSV export); 0 keeps everything |
| `--hourly-retention-days N` | 730 | days of hourly averages to keep (the 7d to 1y charts); 0 keeps everything |

With both retentions set, the database has a ceiling: about 265 MB per board at one reading every 2 s (about
55 MB at one every 10 s). Purges run hourly and give the space back to the disk.

The same settings exist as `IOT_*` environment variables: see [docs/configuration.md](../docs/configuration.md).

No board yet? `python simulator/simulate_picow.py --devices 3`, or `--target pty` for a fake USB port.

## Run it with Docker

```bash
make lite-docker-usb      # from the repository root: build, start, then print the links and status
make lite-status          # the links, each stage's health and the boards, any time
```

or by hand:

```bash
cd lite
docker compose up -d --build                                  # boards over Wi-Fi (TCP) only
docker compose -f compose.yaml -f compose.usb.yaml up -d      # plus a board on USB
```

One container does everything: TCP and USB ingest, SQLite, the dashboard and its live stream. The database
lives in the `lite-data` volume.

**USB.** The overlay mounts the host's `/dev` read-only and allows only USB serial ports (`ttyACM*`) to be
opened, so the board can be plugged in, unplugged or rebooted at any time, and the container starts without
it. While the container reads the board, the board sends over USB with its Wi-Fi off; stop the container and,
15 seconds later, the board switches to Wi-Fi. The port's number can change when the board reboots, so prefer
its stable name: `ls /dev/serial/by-id`.

Settings go in a `.env` file here (git-ignored), for example:

```bash
COMPOSE_FILE=compose.yaml:compose.usb.yaml   # always include the USB overlay
WEB_PORT=8410
SERIAL_DEVICE=serial/by-id/usb-MicroPython_Board_in_FS_mode_e6614c311b7a1234-if00
ALTITUDE_M=1045                              # adds sea-level pressure to the dashboard
```

**A USB webcam.** `make lite-docker-camera`, or add the camera overlay to `COMPOSE_FILE` with the camera's stable
name from `ls /dev/v4l/by-id` (it includes the camera's serial number, so it stays in this git-ignored file):

```bash
COMPOSE_FILE=compose.yaml:compose.usb.yaml:compose.camera.yaml
CAMERA_DEVICE=v4l/by-id/usb-<camera>-video-index0
CAMERA_NAME=Basement
MICROPHONE_DEVICE=plughw:CARD=<card>,DEV=0   # its microphone, from `arecord -l` (optional)
CAMERA_RECORD=true                           # a short clip of each motion, kept 30 days (optional)
```

All of them: `WEB_PORT`, `DEVICE_PORT`, `RETENTION_DAYS`, `HOURLY_RETENTION_DAYS`, `ALTITUDE_M`, `SERIAL_DEVICE`, `CAMERA_DEVICE`, `CAMERA_NAME`, `MICROPHONE_DEVICE`, `CAMERA_RECORD`, `CAMERA_RECORD_DAYS`, `CAMERA_RECORD_MAX_GB`, `COMPOSE_FILE`
([configuration](../docs/configuration.md#lite-in-docker-compose-variables)).

## What you get

- **Overview**: the latest values with trends, live charts (15 minutes) or history (1 hour to 1 year) with
  min–max bands, the board's health (memory, Wi-Fi signal, uptime, lost messages, restarts) and a CSV export.
  The tiles also read the data for you: indoor comfort (dry, comfortable, humid), the 3-hour pressure tendency
  with its weather outlook, and sea-level pressure when `ALTITUDE_M` is set.
- **Camera** (with a USB webcam): the live picture at the camera's own resolution and quality, a frame-rate
  choice for slow connections, snapshots and full screen, plus what the camera notices: how much of the picture
  moves (with boxes around it), the brightness, and events when something moves or a light is switched on or
  off. Zones you draw watch a floor for water, a status light or flame (on, off, blinking, run cycles), or an
  area for motion. With the webcam's microphone: the sound level, Listen, and smoke and CO alarms recognised by
  their beeping, with a red banner on every view. Optionally, a short clip of each motion, kept 30 days, to watch
  in the tab ([camera](../docs/camera.md)).
- **Devices**: every board, its last reading and whether it is online.
- **Pipeline**: the TCP listener, USB reader, validation and storage, each with its state and counters, plus
  the host's CPU, memory, disk and temperature.

The API behind it is documented at http://localhost:8000/api/docs.
