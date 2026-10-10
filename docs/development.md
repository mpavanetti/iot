# Development

## Setup

```bash
make install          # python3 -m venv .venv && pip install -e ".[dev]"
. .venv/bin/activate
```

Python 3.11 or newer (the Docker images use 3.14). The extras in `pyproject.toml` keep each install small:
`lite`, `camera` (OpenCV, for a webcam on Lite), `platform` (gateway and web app), `analytics` (Streamlit), and
`dev` (everything plus pytest and ruff).

## Where to start reading

Follow a reading through the code:

1. [`firmware/main.py`](../firmware/main.py): the loop on the board. Messages are built in [`telemetry.py`](../firmware/telemetry.py).
2. [`src/iotcenter/protocol.py`](../src/iotcenter/protocol.py): the contract. `parse_line()` is the gate every reading passes.
3. [`src/iotcenter/ingest.py`](../src/iotcenter/ingest.py): the TCP server and serial reader; both call `on_reading`.
4. Lite: [`lite/app.py`](../src/iotcenter/lite/app.py) (wiring) and [`lite/storage.py`](../src/iotcenter/lite/storage.py) (SQLite).
5. Platform: [`gateway.py`](../src/iotcenter/gateway.py), then [`platform/spark/jobs/iot_spark.py`](../platform/spark/jobs/iot_spark.py),
   then [`web/app.py`](../src/iotcenter/web/app.py) with [`kafka_live.py`](../src/iotcenter/web/kafka_live.py) and [`postgres.py`](../src/iotcenter/web/postgres.py).
6. The API both editions serve: [`api.py`](../src/iotcenter/api.py) (the `DataSource` protocol is the seam).
7. The browser: [`dashboard/assets/js/main.js`](../src/iotcenter/dashboard/assets/js/main.js), then `overview.js` and `charts.js`.
8. The camera (a separate flow): [`camera.py`](../src/iotcenter/camera.py) (capture, MJPEG, API), then
   [`vision.py`](../src/iotcenter/vision.py) (motion and light), [`zones.py`](../src/iotcenter/zones.py),
   [`microphone.py`](../src/iotcenter/microphone.py), [`sound.py`](../src/iotcenter/sound.py) and
   [`recorder.py`](../src/iotcenter/recorder.py), and in the browser `camera.js`, `zones.js` and `recordings.js`.

## Tests

| Command | What | Needs |
|---|---|---|
| `make test` | unit and integration tests: the contract, ingestion over real sockets and a pseudo-terminal, SQLite, the Lite server end to end (REST and SSE), the simulator, the firmware on CPython, the gateway and web app with fakes, the camera and microphone with fakes and the demo scene, motion, zones and light on synthetic frames, alarm patterns on synthetic sound, motion clips and their retention | nothing |
| `make test-spark` | the Spark transformations in local mode, inside the Spark image | Docker |
| `make e2e` | the running platform: TCP into the gateway, through Kafka and Spark into PostgreSQL, out through the dashboard API, the live stream, the dead-letter topic and every Streamlit page | `make platform-up` |
| `make lint` | ruff (lint and formatting) | nothing |

Highlights:

- [`tests/test_contract.py`](../tests/test_contract.py) fails if the pydantic model, the Spark schema, the SQL tables
  or the firmware's field names drift apart.
- [`tests/test_firmware.py`](../tests/test_firmware.py) runs the real `main.py`, `link.py` and `hardware.py` on CPython,
  with small fakes of `machine`, `network` and the drivers, against a real TCP server, with a pipe standing in
  for the board's USB input. It covers store-and-forward across an outage, USB-only mode, USB taking over from
  Wi-Fi (radio off, clock set by the host) and the fall back to Wi-Fi when the host goes quiet.
- [`tests/test_ingest.py`](../tests/test_ingest.py) uses `os.openpty()` as a stand-in for a Pico W on USB.
- The end-to-end tests read URLs from `IOT_E2E_WEB`, `IOT_E2E_GATEWAY` and `IOT_E2E_DATABASE_URL` when the stack
  runs on other ports.

CI ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs lint, the test suite, the Spark tests and
validates both Compose files on every push.

## Conventions

- One message shape everywhere: add a field to `Reading`, then to the Spark schema, the two SQL schemas and the
  firmware. The contract test tells you what you missed.
- Timestamps are UTC. JSON on the wire and in Kafka uses ISO-8601; the API uses Unix seconds.
- The dashboard has no build step: ES modules, one CSS file, uPlot vendored in `assets/vendor/`.
  Device-supplied text is inserted with `textContent` (the `el()` helper), never `innerHTML`.
- Colors are design tokens in `app.css`, with separate light and dark steps of a colorblind-validated palette.
  The Streamlit app uses the same palette (`analytics/charts.py`, `.streamlit/config.toml`).
- Code style: `ruff format` and `ruff check`, line length 100. Firmware files stay within what MicroPython
  supports: no type hints, no `dataclasses`, no `typing`.
- Settings are `IOT_*` environment variables in one class (`config.py`); document new ones in
  [configuration.md](configuration.md).

## Release images

The Python image builds from the repository root (`docker build --build-arg EXTRAS=lite .`) and works with
both the classic builder and BuildKit. The Spark image builds from `platform/spark/` and verifies every
downloaded jar against a pinned SHA-256.
