"""Spark logic shared by the streaming job and the batch rebuild.

    Kafka record (JSON bytes)
        -> parse_readings()      typed columns, event_time, dew point, validation
        -> hourly_aggregates()   per device and hour: samples, avg/min/max
        -> write_*()             idempotent upserts into PostgreSQL

The same `hourly_aggregates()` runs on a stream (stream_readings.py) and on a static table
(rebuild_hourly.py): Spark's unified batch/streaming API in one function.
"""

import os
from functools import partial

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
)

# --- configuration (environment variables, set in platform/compose.yaml) ---------------

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "iot.readings")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://iot:iot@postgres:5432/iot")
CHECKPOINTS = os.getenv("CHECKPOINT_DIR", "/opt/spark/checkpoints")
TRIGGER = os.getenv("TRIGGER_INTERVAL", "10 seconds")
WATERMARK = os.getenv("WATERMARK_DELAY", "1 hour")

# The JSON contract (docs/protocol.md). Timestamps arrive as ISO-8601 strings.
READING_SCHEMA = StructType(
    [
        StructField("v", IntegerType()),
        StructField("device_id", StringType()),
        StructField("name", StringType()),
        StructField("seq", LongType()),
        StructField("ts", StringType()),
        StructField("received_at", StringType()),
        StructField("source", StringType()),
        StructField("temperature_c", DoubleType()),
        StructField("humidity_pct", DoubleType()),
        StructField("pressure_hpa", DoubleType()),
        StructField("dew_point_c", DoubleType()),
        StructField("cpu_temp_c", DoubleType()),
        StructField("mem_free_bytes", LongType()),
        StructField("mem_alloc_bytes", LongType()),
        StructField("storage_free_kb", DoubleType()),
        StructField("cpu_freq_mhz", DoubleType()),
        StructField("uptime_s", LongType()),
        StructField("wifi_rssi_dbm", IntegerType()),
        StructField("ip", StringType()),
        StructField("firmware", StringType()),
    ]
)

READING_COLUMNS = [
    "device_id",
    "event_time",
    "seq",
    "name",
    "ts",
    "received_at",
    "source",
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "dew_point_c",
    "cpu_temp_c",
    "mem_free_bytes",
    "mem_alloc_bytes",
    "storage_free_kb",
    "cpu_freq_mhz",
    "uptime_s",
    "wifi_rssi_dbm",
    "ip",
    "firmware",
    "kafka_partition",
    "kafka_offset",
]
TIMESTAMP_COLUMNS = ("event_time", "ts", "received_at")
METRICS = {  # column -> prefix in readings_hourly
    "temperature_c": "temperature",
    "humidity_pct": "humidity",
    "pressure_hpa": "pressure",
    "dew_point_c": "dew_point",
    "cpu_temp_c": "cpu_temp",
}


def spark_session(app_name: str) -> SparkSession:
    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "4")  # tiny data: 200 partitions is overkill
        .config("spark.sql.streaming.stateStore.stateSchemaCheck", "true")
        .getOrCreate()
    )


# --- transformations --------------------------------------------------------------------


def parse_readings(kafka: DataFrame) -> DataFrame:
    """Kafka records -> typed, validated readings. Rows that break the contract are dropped
    (the gateway already rejects bad input; this guards against direct producers)."""
    parsed = kafka.select(
        F.from_json(F.col("value").cast("string"), READING_SCHEMA).alias("r"),
        F.col("partition").alias("kafka_partition"),
        F.col("offset").alias("kafka_offset"),
        F.col("timestamp").alias("kafka_time"),
    ).select("r.*", "kafka_partition", "kafka_offset", "kafka_time")

    readings = (
        parsed.withColumn("ts", F.to_timestamp("ts"))
        .withColumn("received_at", F.coalesce(F.to_timestamp("received_at"), F.col("kafka_time")))
        # When was it measured? The device clock if it was synced, else arrival time.
        .withColumn("event_time", F.coalesce("ts", "received_at"))
        .withColumn(
            "dew_point_c", F.coalesce("dew_point_c", dew_point("temperature_c", "humidity_pct"))
        )
        .withColumn("source", F.coalesce("source", F.lit("kafka")))
    )
    valid = (
        F.col("device_id").isNotNull()
        & F.col("seq").isNotNull()
        & F.col("temperature_c").between(-40, 85)
        & F.col("humidity_pct").between(0, 100)
        & F.col("pressure_hpa").between(300, 1100)
    )
    return readings.where(valid).select(*READING_COLUMNS)


def dew_point(temperature: str, humidity: str):
    """Magnus formula as a Spark expression (same constants as the Python services)."""
    a, b = 17.62, 243.12
    rh = F.greatest(F.least(F.col(humidity), F.lit(100.0)), F.lit(0.1))
    gamma = F.log(rh / 100.0) + (a * F.col(temperature)) / (b + F.col(temperature))
    return F.round(b * gamma / (a - gamma), 2)


def hourly_aggregates(readings: DataFrame) -> DataFrame:
    """Per device and clock hour: sample count and avg/min/max of each metric."""
    stats = [F.count(F.lit(1)).alias("samples")]
    for column, prefix in METRICS.items():
        stats.append(F.avg(column).alias(f"{prefix}_avg"))
        if column != "cpu_temp_c":
            stats += [F.min(column).alias(f"{prefix}_min"), F.max(column).alias(f"{prefix}_max")]
    return (
        readings.groupBy("device_id", F.window("event_time", "1 hour").alias("window"))
        .agg(*stats)
        .withColumn("hour", F.col("window.start"))
        .drop("window")
    )


# --- sinks: idempotent writes to PostgreSQL ---------------------------------------------
# Timestamps cross from Spark to PostgreSQL as Unix seconds (to_timestamp() on the SQL side),
# which sidesteps the time zone guessing of Python datetime conversions.

HOURLY_COLUMNS = ["device_id", "hour", "samples"] + [
    f"{prefix}_{stat}"
    for column, prefix in METRICS.items()
    for stat in (("avg",) if column == "cpu_temp_c" else ("avg", "min", "max"))
]


def _placeholders(columns, timestamps) -> str:
    return ", ".join(f"to_timestamp(%({c})s)" if c in timestamps else f"%({c})s" for c in columns)


INSERT_READING = f"""
INSERT INTO readings ({", ".join(READING_COLUMNS)})
VALUES ({_placeholders(READING_COLUMNS, TIMESTAMP_COLUMNS)})
ON CONFLICT (device_id, event_time, seq) DO NOTHING
"""

UPSERT_HOURLY = f"""
INSERT INTO readings_hourly ({", ".join(HOURLY_COLUMNS)})
VALUES ({_placeholders(HOURLY_COLUMNS, ("hour",))})
ON CONFLICT (device_id, hour) DO UPDATE SET
  {", ".join(f"{c} = EXCLUDED.{c}" for c in HOURLY_COLUMNS[2:])}, updated_at = now()
"""


def _as_epoch(frame: DataFrame, columns) -> DataFrame:
    for column in columns:
        frame = frame.withColumn(column, F.unix_micros(column) / 1e6)
    return frame


def _execute_partition(sql: str, dsn: str, rows) -> None:
    """Runs on the executors: one connection and one batched statement per partition."""
    import psycopg  # imported on the executor

    batch = [row.asDict() for row in rows]
    if not batch:
        return
    with psycopg.connect(dsn) as conn, conn.cursor() as cursor:
        cursor.executemany(sql, batch)


def write_readings(batch: DataFrame, batch_id: int) -> None:
    """foreachBatch sink for raw readings: re-running a batch inserts nothing twice."""
    rows = _as_epoch(batch, TIMESTAMP_COLUMNS)
    rows.foreachPartition(partial(_execute_partition, INSERT_READING, POSTGRES_DSN))


def write_hourly(batch: DataFrame, batch_id: int) -> None:
    """foreachBatch sink for aggregates. Update mode emits the full, current value of every
    changed window, so an upsert that overwrites is idempotent."""
    rows = _as_epoch(batch.select(*HOURLY_COLUMNS), ("hour",))
    rows.foreachPartition(partial(_execute_partition, UPSERT_HOURLY, POSTGRES_DSN))
