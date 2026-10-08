# IoT Center Platform

The streaming edition: a gateway, Kafka, Spark Structured Streaming, PostgreSQL, the dashboard and a Streamlit
analytics app, all in Docker Compose. The [architecture guide](../docs/architecture.md#platform-a-streaming-pipeline)
explains the data flow and every design decision.

## Start

```bash
cp .env.example .env              # optional: ports, time zone, Spark size, password
docker compose up -d --build      # or `make platform-up` from the repository root
docker compose ps                 # wait until everything is healthy (kafka-init exits 0)
```

Then send data: point a Pico W at this host's port 1500, or run the simulator:

```bash
python ../simulator/simulate_picow.py --devices 3 --backfill 1d
```

| What | Where |
|---|---|
| Dashboard | http://localhost:8000 |
| Analytics (Streamlit) | http://localhost:8501 |
| Spark master / streaming job | http://localhost:8080 / http://localhost:4040 |
| Kafka UI (optional) | `docker compose --profile tools up -d kafka-ui`, then http://localhost:8090 |
| Devices connect to | TCP :1500 |
| Kafka from the host | localhost:9094 (`simulate_picow.py --target kafka://localhost:9094`) |
| PostgreSQL | 127.0.0.1:5432, user `iot`, database `iot` |

## Services

| Service | What it does |
|---|---|
| `kafka` | single-node KRaft broker, 7-day retention, about 450 MB of RAM |
| `kafka-init` | creates `iot.readings` (3 partitions, keyed by device) and `iot.readings.dlq`, then exits |
| `gateway` | TCP :1500 into Kafka; invalid lines go to the dead-letter topic |
| `spark-master`, `spark-worker` | a standalone Spark cluster (2 cores, 2 GB worker) |
| `spark-streaming` | the streaming job: Kafka into PostgreSQL every 10 seconds (raw readings and hourly aggregates) |
| `spark-rebuild` | on demand: recomputes the hourly aggregates (`docker compose run --rm spark-rebuild`) |
| `postgres` | the serving layer: `readings`, `readings_hourly`, `stream_progress`, view `devices` |
| `web` | the dashboard: live from Kafka, history from PostgreSQL, health of every stage |
| `analytics` | Streamlit: overview, patterns, data quality, explorer |
| `kafka-ui` | optional (`tools` profile) |

The whole stack uses about 3.5 GB of RAM; a Raspberry Pi 4 with 8 GB runs it comfortably.

## Files

| Path | |
|---|---|
| `compose.yaml` | the stack, with health checks and startup order |
| `.env.example` | every variable and its default |
| `kafka/create-topics.sh` | topic definitions |
| `postgres/init.sql` | the schema (runs once, on an empty volume) |
| `spark/Dockerfile` | official Spark 4.2 image plus the Kafka connector, the PostgreSQL driver and psycopg |
| `spark/jobs/iot_spark.py` | schema, parsing, validation, hourly aggregation, idempotent sinks |
| `spark/jobs/stream_readings.py` | the streaming job (two queries + a progress listener) |
| `spark/jobs/rebuild_hourly.py` | the batch rebuild |
| `spark/tests/` | Spark tests (`make test-spark`) |

Day-to-day commands, Kafka and SQL recipes and troubleshooting: [docs/operations.md](../docs/operations.md).
