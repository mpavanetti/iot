# Message contract

Every component speaks the same message: one reading from one device, as a flat JSON object.
The field names never change along the way, from the firmware's `build_message()` through Kafka,
the Spark schema, the SQLite and PostgreSQL columns, the REST API, to the dashboard.
The single source of truth is the `Reading` model in [`src/iotcenter/protocol.py`](../src/iotcenter/protocol.py),
and [`tests/test_contract.py`](../tests/test_contract.py) fails if any reader drifts from it.

## On the wire: NDJSON

Devices send **newline-delimited JSON**: one JSON object per line, `\n` at the end.

```json
{"v": 2, "device_id": "pico-e6614103e7473b2a", "name": "living-room", "seq": 1234, "ts": "2026-10-08T04:19:53Z", "temperature_c": 21.92, "humidity_pct": 44.83, "pressure_hpa": 890.07, "cpu_temp_c": 24.2, "mem_free_bytes": 151232, "mem_alloc_bytes": 40768, "storage_free_kb": 632.0, "cpu_freq_mhz": 125, "uptime_s": 3600, "wifi_rssi_dbm": -61, "ip": "192.168.1.74", "firmware": "2.1.0", "cpu_busy_pct": 4.2, "loop_max_ms": 61, "sensor_errors": 0, "boot_reason": "power on"}
```

- **TCP** (port 1500 by default): the firmware keeps **one connection open** and writes a line per reading.
  The newline is what separates messages, so the server never depends on how TCP splits the bytes into packets.
- **USB serial**: the same lines, printed on the Pico's USB serial port (115200 baud). Lines that do not start
  with `{` (MicroPython's boot banner, `[iot] ...` log lines) are ignored.
- Lines longer than 16 KB are rejected and the connection is closed: a real reading is about 400 bytes.
- A connection that opens with an HTTP request (a browser, a port scanner, service discovery) is closed
  without a reply and is not counted as rejected messages.

### USB host lines

USB is the board's primary link, so the computer reading it says so. While IoT Center (Lite, or the platform
gateway) has a board's serial port open, it writes a **host line** to it every 5 seconds:

```
#iot {"now": 1791480000}
```

- It tells the firmware a host is reading USB: the board sends its readings there and keeps Wi-Fi off. After
  15 seconds without one, it falls back to Wi-Fi (see [the firmware](../firmware/README.md#how-the-board-picks-a-link)).
- `now` is the host's Unix time in **whole seconds**: the board sets its clock from it whenever it is more than
  2 s off, since it has no NTP without Wi-Fi. (MicroPython's floats are 32-bit on the Pico: a fractional Unix
  time would be rounded to the nearest 128 seconds.)
- `#` makes it a comment at the MicroPython REPL, so it is harmless when the firmware is not running. Boards
  with older firmware simply ignore it.

## Fields

Sent by the device:

| Field | Type | Unit / format | Valid range | Required | Notes |
|---|---|---|---|---|---|
| `v` | int | | | no (2) | protocol version; v1 payloads are upgraded to v2 on arrival |
| `device_id` | string | `[A-Za-z0-9._:-]`, up to 64 chars | | **yes** | from the board's flash ID: `pico-<16 hex>` |
| `name` | string | up to 64 chars | | no | friendly name from `config.py`, shown on dashboards |
| `seq` | int | | >= 0 | **yes** | counts readings since boot; gaps = lost messages, going back = reboot |
| `ts` | string | ISO-8601 UTC (`...Z`) | | no | device clock; `null` until NTP has set it |
| `temperature_c` | float | °C | -40 to 85 | **yes** | BME280 |
| `humidity_pct` | float | %RH | 0 to 100 | **yes** | BME280 |
| `pressure_hpa` | float | hPa | 300 to 1100 | **yes** | BME280 (station pressure, not sea-level) |
| `cpu_temp_c` | float | °C | -40 to 125 | no | RP2040 internal sensor (reads a few degrees above ambient) |
| `mem_free_bytes`, `mem_alloc_bytes` | int | bytes | >= 0 | no | MicroPython heap after a garbage collection |
| `storage_free_kb` | float | KB | >= 0 | no | free flash |
| `cpu_freq_mhz` | number | MHz | >= 0 | no | 125 on a Pico W, 150 on a Pico 2 W |
| `uptime_s` | int | s | >= 0 | no | |
| `wifi_rssi_dbm` | int | dBm | -127 to 0 | no | left out while on USB (the radio is off) |
| `ip` | string | | | no | |
| `firmware` | string | | | no | firmware version |
| `cpu_busy_pct` | float | % | 0 to 100 | no | share of time the firmware's loop worked (rather than idled) since the last reading. MicroPython runs it on one of the two cores, so this is the board's CPU usage |
| `loop_max_ms` | int | ms | >= 0 | no | the loop's longest single pass since the last reading: a stall (a slow network call) shows here before the watchdog has to act |
| `sensor_errors` | int | | >= 0 | no | BME280 reads that failed since boot (a failed read is skipped, not sent) |
| `boot_reason` | string | up to 32 chars | | no | why the board last started: `power on`, or `watchdog` (a hang, or a reset after a crash or an upload) |

Added by the server that receives the line (anything the device sends for these is replaced):

| Field | Type | Notes |
|---|---|---|
| `received_at` | ISO-8601 UTC | arrival time at the gateway (Platform) or at Lite |
| `source` | string | `tcp`, `usb`, or `simulator` (the simulator's direct-to-Kafka mode) |

Derived (computed by the model, included when it is serialized):

| Field | Notes |
|---|---|
| `event_time` | when the reading was taken: `ts` if the device clock was synced, else `received_at`. Charts, windows and retention all use it. |
| `dew_point_c` | Magnus formula with Sonntag constants (a = 17.62, b = 243.12 °C); Spark uses the identical expression |

A line is **rejected** when it is not UTF-8, not JSON, not a JSON object, misses a required field, or has a
value outside its range. A rejection never closes the connection (except for over-long lines): the next line
is processed as usual. In the Platform the rejected line goes to the dead-letter topic.

## Legacy v1 payloads

The 2023 firmware opened a connection per message, sent one JSON object without a newline and closed it,
with values as strings with units:

```json
{"id": 20923220, "picow": {"local_ip": "192.168.1.74", "temperature": 24.2, "free_storage_kb": 636.0, "mem_alloc_bytes": 53520, "mem_free_bytes": 89328, "cpu_freq_mhz": 125.0}, "bme280": {"temperature": "21.92C", "pressure": "890.07hPa", "humidity": "44.83%", "read_datetime": "2023-9-6 16:39:51"}}
```

Both servers still accept it: reading until end-of-stream returns the final unterminated line, and
`upgrade_v1()` maps it to v2 (`device_id` becomes `pico-192-168-1-74`, units are stripped, `id` becomes `seq`,
and `v` is 1). `python simulator/simulate_picow.py --legacy` speaks this format.

## In Kafka (Platform)

| Topic | Key | Value | Partitions | Retention |
|---|---|---|---|---|
| `iot.readings` | `device_id` | the validated reading as JSON, including `received_at`, `source`, `event_time` and `dew_point_c` | 3 | 7 days |
| `iot.readings.dlq` | none | `{"received_at", "source", "error", "raw"}`: the rejected line (first 4 KB) and why | 1 | 14 days |

Keying by `device_id` puts every reading of a board in the same partition, so consumers see them in order.

## In the API (both editions)

The dashboard API returns readings with the same field names, but **timestamps as Unix seconds**
(`ts`, `received_at`, `event_time`), which is what charting wants. See the interactive API docs at
`/api/docs` on either edition.

| Endpoint | Returns |
|---|---|
| `GET /api/info` | edition, version, ranges, links to the other UIs |
| `GET /api/devices` | every board: first/last seen, counters, `online`, and its `latest` reading |
| `GET /api/readings/recent?device_id=&minutes=15` | raw readings for the live chart |
| `GET /api/readings/history?device_id=&range=24h` | columnar buckets: `t`, `samples`, and `{avg, min, max}` per metric |
| `GET /api/readings/export.csv?device_id=&range=24h` | raw readings as CSV |
| `GET /api/insights?device_id=` | pressure tendency over 3 h (`trend`, `change_hpa`, `outlook`), sea-level pressure (with `IOT_ALTITUDE_M`) and indoor `comfort` |
| `GET /api/status` | pipeline components in data-flow order, with health and counters; host metrics |
| `GET /api/stream` | Server-Sent Events: a `hello`, then one `reading` event per new reading (and `camera` and `sound` events, with a camera and a microphone) |
| `GET /api/camera…`, `/api/sound…` | Lite with a webcam: status, the live MJPEG stream, a snapshot, zones, the activity history, recordings, the sound and Listen ([camera](camera.md#api)) |

History ranges and their bucket sizes: `1h` (15 s), `6h` (1 min), `24h` (5 min), `7d` (1 h), `30d` (4 h), `1y` (1 day).
Buckets of an hour or more are served from the hourly aggregates table, so long ranges stay fast.
