# Configuration

## Python services: `IOT_*` environment variables

Every Python service (Lite, gateway, web app, Streamlit) reads its settings from environment variables with the
`IOT_` prefix, all defined in one class: [`src/iotcenter/config.py`](../src/iotcenter/config.py). Each service
uses only the ones it needs. For Lite, command-line flags override them (`iotcenter lite --help`).

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `IOT_TCP_ENABLED` | `true` | Lite | listen for devices over TCP (`--no-tcp` turns it off) |
| `IOT_TCP_HOST` | `0.0.0.0` | Lite, gateway | interface for the device listener |
| `IOT_TCP_PORT` | `1500` | Lite, gateway, web | device port (`--tcp-port`); the web app shows it in its "waiting for data" hint |
| `IOT_SERIAL_PORT` | none | Lite, gateway | read a Pico W over USB, e.g. `/dev/ttyACM0` (`--serial`); IoT Center also writes [host lines](protocol.md#usb-host-lines) to it, so the board keeps USB as its link |
| `IOT_SERIAL_BAUD` | `115200` | Lite, gateway | (`--baud`); USB CDC ignores it, real UARTs do not |
| `IOT_HTTP_HOST` | `0.0.0.0` | Lite, web, gateway | HTTP interface |
| `IOT_HTTP_PORT` | `8000` | Lite, web, gateway | dashboard port (`--http-port`); the gateway serves `/health` here (8001 in Compose) |
| `IOT_DB_PATH` | `data/iot-lite.db` | Lite | the SQLite file (`--db`); `/data/iot-lite.db` in Docker |
| `IOT_RETENTION_DAYS` | `30` | Lite, web | days of raw readings to keep (`--retention-days`); `0` keeps everything |
| `IOT_HOURLY_RETENTION_DAYS` | `730` | Lite, web | days of hourly aggregates to keep (`--hourly-retention-days`); `0` keeps everything |
| `IOT_OFFLINE_AFTER_S` | `30` | Lite, web | a device shows as offline after this many seconds without data |
| `IOT_ALTITUDE_M` | none | Lite, web | the sensors' altitude in metres (e.g. `1045`): the dashboard then also shows sea-level pressure |
| `IOT_CAMERA_DEVICE` | none | Lite | a USB webcam to stream (`--camera`), e.g. `/dev/video0`; `demo` streams a test scene ([camera](camera.md)) |
| `IOT_CAMERA_NAME` | `Camera` | Lite | the camera's name on the dashboard, e.g. `Basement` |
| `IOT_CAMERA_WIDTH`, `IOT_CAMERA_HEIGHT` | `1920`, `1080` | Lite | the picture size to ask for; the camera picks its closest |
| `IOT_CAMERA_FPS` | `30` | Lite | the frame rate to ask for, at most (webcams slow down in dim light) |
| `IOT_CAMERA_RECORD` | `false` | Lite | record a short clip of each motion (`--record`), in `recordings/` next to the database ([camera](camera.md#recordings)) |
| `IOT_CAMERA_RECORD_DAYS` | `30` | Lite | days to keep the clips; `0` keeps them until the size limit |
| `IOT_CAMERA_RECORD_MAX_GB` | `20` | Lite | the oldest clips are deleted sooner when they take more than this |
| `IOT_MICROPHONE_DEVICE` | none | Lite | an ALSA capture device to listen to (`--microphone`), e.g. `plughw:CARD=C960,DEV=0` (`arecord -l`); `demo` plays test sounds |
| `IOT_TIMEZONE` | `UTC` | Streamlit | time zone for displayed times, e.g. `America/New_York` (the dashboard uses the browser's) |
| `IOT_KAFKA_BOOTSTRAP` | `localhost:9094` | gateway, web | Kafka brokers (`kafka:9092` inside Compose) |
| `IOT_KAFKA_TOPIC` | `iot.readings` | gateway, web | readings topic |
| `IOT_KAFKA_DLQ_TOPIC` | `iot.readings.dlq` | gateway, web | dead-letter topic |
| `IOT_DATABASE_URL` | `postgresql://iot:iot@localhost:5432/iot` | web, Streamlit | PostgreSQL written by Spark |
| `IOT_REPLAY_MESSAGES` | `900` | web | messages per partition replayed from Kafka at startup to fill the live charts |
| `IOT_GATEWAY_URL` | `http://localhost:8001` | web | where to ask the gateway for `/stats` |
| `IOT_SPARK_MASTER_URL` | `http://localhost:8080` | web | Spark master JSON, for the Pipeline page |
| `IOT_ANALYTICS_URL` | `http://localhost:8501` | web | Streamlit health check |
| `IOT_ANALYTICS_PUBLIC_PORT` | `8501` | web | port the *browser* uses for the Analytics link |
| `IOT_SPARK_PUBLIC_PORT` | `8080` | web | port the browser uses for the Spark master link |
| `IOT_SPARK_APP_PUBLIC_PORT` | `4040` | web | port the browser uses for the streaming job UI link |

## Platform: Compose variables (`platform/.env`)

[`platform/.env.example`](../platform/.env.example) lists every variable with its default. Copy it to `.env` next
to `compose.yaml`; Compose reads it automatically.

| Variable | Default | Meaning |
|---|---|---|
| `WEB_PORT` | `8000` | dashboard (set `80` for a plain `http://raspberrypi.local/`) |
| `GATEWAY_PORT` | `1500` | where Pico W boards connect |
| `ANALYTICS_PORT` | `8501` | Streamlit |
| `SPARK_UI_PORT`, `SPARK_APP_PORT` | `8080`, `4040` | Spark master UI, streaming job UI |
| `KAFKA_PORT` | `9094` | Kafka's external listener (tools on the host) |
| `KAFKA_EXTERNAL_HOST` | `localhost` | the host name other machines use for Kafka, e.g. `raspberrypi.local` |
| `KAFKA_UI_PORT` | `8090` | Kafka UI (`--profile tools`) |
| `POSTGRES_PORT` | `5432` | PostgreSQL, bound to 127.0.0.1 only |
| `POSTGRES_PASSWORD` | `iot` | used by Spark, the web app and Streamlit; change it before exposing anything |
| `SPARK_WORKER_CORES`, `SPARK_WORKER_MEMORY` | `2`, `2g` | the worker's size |
| `SPARK_STREAMING_CORES` | `1` | cores for the streaming job; the rest is free for batch jobs like the rebuild |
| `TIMEZONE` | `UTC` | Streamlit's display time zone |
| `ALTITUDE_M` | empty | `IOT_ALTITUDE_M` for the dashboard (sea-level pressure) |
| `RETENTION_DAYS`, `HOURLY_RETENTION_DAYS` | `30`, `730` | how long PostgreSQL keeps readings and hourly aggregates (the web app purges hourly; Kafka keeps 7 days regardless) |

Spark job settings are environment variables of the Spark containers (set in `compose.yaml`):
`KAFKA_BOOTSTRAP`, `KAFKA_TOPIC`, `POSTGRES_DSN`, `CHECKPOINT_DIR`, `TRIGGER_INTERVAL` (default `10 seconds`)
and `WATERMARK_DELAY` (default `1 hour`).

## Lite in Docker: Compose variables

| Variable | Default | Meaning |
|---|---|---|
| `WEB_PORT` | `8000` | dashboard |
| `DEVICE_PORT` | `1500` | device TCP port |
| `RETENTION_DAYS` | `30` | raw-reading retention |
| `HOURLY_RETENTION_DAYS` | `730` | hourly-aggregate retention |
| `ALTITUDE_M` | empty | `IOT_ALTITUDE_M`: shows sea-level pressure on the dashboard |
| `SERIAL_DEVICE` | `ttyACM0` | with `compose.usb.yaml`: the board's path under `/dev`; `serial/by-id/usb-MicroPython_…-if00` survives reboots, `ttyACM0` may become `ttyACM1` |
| `CAMERA_DEVICE` | `video0` | with `compose.camera.yaml`: the webcam's path under `/dev`; `v4l/by-id/usb-…-video-index0` survives replugging |
| `CAMERA_NAME` | `Camera` | with `compose.camera.yaml`: `IOT_CAMERA_NAME` |
| `CAMERA_RECORD`, `CAMERA_RECORD_DAYS`, `CAMERA_RECORD_MAX_GB` | `false`, `30`, `20` | with `compose.camera.yaml`: the `IOT_CAMERA_RECORD*` settings; the clips live in the `lite-data` volume |
| `MICROPHONE_DEVICE` | empty (off) | with `compose.camera.yaml`: `IOT_MICROPHONE_DEVICE`, e.g. `plughw:CARD=C960,DEV=0` |
| `COMPOSE_FILE` | `compose.yaml` | set `compose.yaml:compose.usb.yaml` in `lite/.env` to always include the USB overlay (add `:compose.camera.yaml` for a webcam) |

## Firmware: `config.py`

See [`firmware/config.example.py`](../firmware/config.example.py); every setting is commented there.
