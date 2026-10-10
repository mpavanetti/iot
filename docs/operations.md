# Operations

Commands assume the repository root; `make help` lists the shortcuts. For the platform, `docker compose`
commands run in `platform/` (or add `-f platform/compose.yaml`).

## Day to day

| Task | Command |
|---|---|
| Start / stop the platform | `make platform-up` / `make platform-down` (data is kept) |
| Status of every service | `docker compose ps` (each has a health check) |
| Follow the logs | `make platform-logs`, or `docker compose logs -f gateway web spark-streaming` |
| Delete all platform data | `make platform-reset` (removes the Kafka, PostgreSQL and checkpoint volumes) |
| Open Kafka UI | `make tools`, then http://localhost:8090 |
| Run Lite | `make lite` (local), `make lite-docker`, `make lite-docker-usb` (with a board on USB) or `make lite-docker-camera` (and a USB webcam) |
| Links, health and boards | `make lite-status` / `make platform-status`, or `iotcenter status http://host:port` |
| Upload the firmware | `make firmware` (pauses Lite in Docker while it uses the USB port) |
| Simulate boards | `make simulate` (three boards, live) |
| Load a week of history | `make backfill`, then `make rebuild-hourly` on the platform (see below) |

The dashboard's **Pipeline** page is the quickest health check: every stage, from the gateway to Streamlit,
reports its state and counters there.

## Backfills and the hourly aggregates

Spark's streaming aggregation only counts readings that arrive within its one-hour watermark. A backfill
(`simulate_picow.py --backfill 7d`, or a board that was offline for hours) is stored in `readings`, but older
hours are missing from `readings_hourly`. Recompute them with the batch job, which reuses the streaming
job's aggregation code:

```bash
make rebuild-hourly         # docker compose run --rm spark-rebuild
```

A backfill sent *before* any live data, on a fresh stack, needs no rebuild: it arrives in time order, so the
watermark advances with it.

## Kafka

```bash
K="docker compose exec kafka /opt/kafka/bin"

$K/kafka-topics.sh --bootstrap-server localhost:9092 --describe --exclude-internal

# Watch readings as they arrive (keys are device ids)
$K/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic iot.readings \
  --formatter-property print.key=true

# What the gateway rejected, and why
$K/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic iot.readings.dlq --from-beginning

# Messages per partition (end offsets)
$K/kafka-get-offsets.sh --bootstrap-server localhost:9092 --topic iot.readings
```

From the host, Kafka listens on `localhost:9094`, which is what `simulate_picow.py --target kafka://localhost:9094` uses.

## PostgreSQL

```bash
docker compose exec postgres psql -U iot -d iot
```

```sql
-- one row per board
SELECT * FROM devices;

-- the last hour of one board, five-minute averages
SELECT date_bin('5 minutes', event_time, TIMESTAMPTZ 'epoch') AS bucket,
       round(avg(temperature_c)::numeric, 2) AS temperature_c,
       round(avg(humidity_pct)::numeric, 1)  AS humidity_pct,
       count(*)
FROM readings
WHERE device_id = 'pico-sim01' AND event_time > now() - interval '1 hour'
GROUP BY 1 ORDER BY 1;

-- is Spark keeping up?
SELECT query_name, batch_id, input_rows, now() - updated_at AS age FROM stream_progress;

-- sequence gaps (lost messages) per board, last 24 hours
SELECT device_id, sum(seq - prev - 1) AS lost
FROM (SELECT device_id, seq, lag(seq) OVER (PARTITION BY device_id ORDER BY event_time) AS prev
      FROM readings WHERE event_time > now() - interval '1 day') t
WHERE seq > prev + 1
GROUP BY device_id;
```

PostgreSQL is published on `127.0.0.1:5432` only. From another machine, use an SSH tunnel:
`ssh -L 5432:localhost:5432 pi@raspberrypi.local`.

To keep the raw table bounded, delete old rows now and then (the hourly aggregates stay):

```sql
DELETE FROM readings WHERE event_time < now() - interval '180 days';
```

## Spark

- Master UI: http://localhost:8080 (workers, running applications)
- Streaming job UI: http://localhost:4040 (the **Structured Streaming** tab shows input and processing rates,
  batch durations and the watermark)
- The streaming job's offsets and state live in the `spark-checkpoints` volume. Removing it (with
  `make platform-reset`, or `docker volume rm iotcenter_spark-checkpoints`) makes the job reprocess everything
  Kafka still holds. That is safe: the PostgreSQL writes are idempotent.
- Test the transformations without the cluster: `make test-spark`.

## Lite

- The database is one file: `data/iot-lite.db` locally, or the `lite-data` volume in Docker. Back it up while
  Lite runs with `sqlite3 data/iot-lite.db ".backup backup.db"`.
- Explore it: `sqlite3 data/iot-lite.db "SELECT device_id, datetime(last_seen, 'unixepoch'), messages, dropped FROM devices"`.
- Hourly, raw readings older than `IOT_RETENTION_DAYS` (30) and hourly aggregates older than
  `IOT_HOURLY_RETENTION_DAYS` (730) are deleted, and the space goes back to the disk. `make lite-status` shows
  how fast the file grows and where it levels off.

## Troubleshooting

| Symptom | Check |
|---|---|
| The dashboard says "Waiting for the first reading" | Is a board or the simulator sending to the right host and port? The Pipeline page shows open TCP connections and rejected lines |
| A board is "Offline" | No reading for 30 s (`IOT_OFFLINE_AFTER_S`). Look at its display or USB log |
| "Lost in transit" grows | Weak Wi-Fi (see RSSI on the device card) or server restarts. Sequence gaps are counted, never hidden |
| The Spark stage is "degraded" | The job runs but has not finished a batch for 2 minutes: `docker compose logs spark-streaming` |
| `Initial job has not accepted any resources` in Spark logs | The worker is out of cores or memory. Raise `SPARK_WORKER_MEMORY` / `SPARK_WORKER_CORES` in `.env` |
| History charts are empty but live data flows | Spark or PostgreSQL is down or behind: check the Pipeline page and `stream_progress` |
| Kafka clients on another machine cannot connect | Set `KAFKA_EXTERNAL_HOST` to this host's name or IP and restart Kafka |
| `kafka-init` exits with an error | `docker compose logs kafka-init`; Kafka must be healthy first (`docker compose ps kafka`) |
| Port already in use | Change it in `platform/.env` (or `lite` variables), e.g. `WEB_PORT=8080` |
| The Camera tab says "Camera unavailable" (or the microphone is) | Unplugged, wrong `CAMERA_DEVICE` / `MICROPHONE_DEVICE`, or another program has the webcam: see [camera troubleshooting](camera.md#troubleshooting) |
| Serial port permission denied (Linux) | `sudo usermod -aG dialout $USER` and log in again; in Docker the image's user is already in `dialout` |
