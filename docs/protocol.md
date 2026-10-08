# Message contract

Every component speaks the same message: one reading from one device, as a flat JSON object.
The field names never change along the way, from the firmware's `build_message()` through Kafka,
the Spark schema, the SQLite and PostgreSQL columns, the REST API, to the dashboard.
The single source of truth is the `Reading` model in [`src/iotcenter/protocol.py`](../src/iotcenter/protocol.py),
and [`tests/test_contract.py`](../tests/test_contract.py) fails if any reader drifts from it.

## On the wire: NDJSON

Devices send **newline-delimited JSON**: one JSON object per line, `\n` at the end.

```json
{"v": 2, "device_id": "pico-e6614103e7473b2a", "name": "living-room", "seq": 1234, "ts": "2026-10-08T04:19:53Z", "temperature_c": 21.92, "humidity_pct": 44.83, "pressure_hpa": 890.07, "cpu_temp_c": 24.2, "mem_free_bytes": 151232, "mem_alloc_bytes": 40768, "storage_free_kb": 632.0, "cpu_freq_mhz": 125, "uptime_s": 3600, "wifi_rssi_dbm": -61, "ip": "192.168.1.74", "firmware": "2.0.0"}
```

- **TCP** (port 1500 by default): the firmware keeps **one connection open** and writes a line per reading.
  The newline is what separates messages, so the server never depends on how TCP splits the bytes into packets.
- **USB serial**: the same lines, printed on the Pico's USB serial port (115200 baud). Lines that do not start
  with `{` (MicroPython's boot banner, `[iot] ...` log lines) are ignored.
- Lines longer than 16 KB are rejected and the connection is closed: a real reading is about 400 bytes.

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
| `wifi_rssi_dbm` | int | dBm | -127 to 0 | no | `null` in USB-only mode |
| `ip` | string | | | no | |
| `firmware` | string | | | no | firmware version |

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
| `GET /api/status` | pipeline components in data-flow order, with health and counters; host metrics |
| `GET /api/stream` | Server-Sent Events: a `hello`, then one `reading` event per new reading |

History ranges and their bucket sizes: `1h` (15 s), `6h` (1 min), `24h` (5 min), `7d` (1 h), `30d` (4 h).
Buckets of an hour or more are served from the hourly aggregates table, so long ranges stay fast.
