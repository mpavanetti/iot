"""Spark Structured Streaming job: Kafka -> PostgreSQL, continuously.

Two streaming queries share the Kafka source:

  readings  every valid reading, inserted idempotently into `readings`
  hourly    per-device hourly aggregates, kept up to date as data arrives (watermark 1 h)

A listener records each micro-batch in `stream_progress`, which the dashboard uses to show
whether the job is alive and how fresh the data is. Offsets and aggregation state live in
the checkpoint directory, so a restart resumes exactly where the job stopped.
"""

import logging

import iot_spark as job
from pyspark.sql.streaming import StreamingQueryListener

log = logging.getLogger("stream_readings")

UPSERT_PROGRESS = """
INSERT INTO stream_progress (query_name, batch_id, input_rows, rows_per_second, duration_ms,
                             watermark, updated_at)
VALUES (%(name)s, %(batch_id)s, %(rows)s, %(rate)s, %(duration)s, %(watermark)s::timestamptz, now())
ON CONFLICT (query_name) DO UPDATE SET
  batch_id = EXCLUDED.batch_id, input_rows = EXCLUDED.input_rows,
  rows_per_second = EXCLUDED.rows_per_second, duration_ms = EXCLUDED.duration_ms,
  watermark = COALESCE(EXCLUDED.watermark, stream_progress.watermark), updated_at = now()
"""


class ProgressToPostgres(StreamingQueryListener):
    """Called by Spark on the driver after every micro-batch."""

    def onQueryStarted(self, event):
        log.warning("Query %s started", event.name)

    def onQueryProgress(self, event):
        p = event.progress
        self._save(
            name=p.name,
            batch_id=p.batchId,
            rows=p.numInputRows,
            rate=p.processedRowsPerSecond,
            duration=p.batchDuration,
            watermark=(p.eventTime or {}).get("watermark"),
        )

    def onQueryIdle(self, event):  # no new data: still alive
        self._touch(event.name if hasattr(event, "name") else None)

    def onQueryTerminated(self, event):
        log.warning("Query %s terminated: %s", event.id, event.exception)

    def _save(self, **values):
        try:
            import psycopg

            with psycopg.connect(job.POSTGRES_DSN) as conn:
                conn.execute(UPSERT_PROGRESS, values)
        except Exception as exc:  # monitoring must never break the pipeline
            log.warning("Could not record progress: %s", exc)

    def _touch(self, name):
        if not name:
            return
        try:
            import psycopg

            with psycopg.connect(job.POSTGRES_DSN) as conn:
                conn.execute(
                    "UPDATE stream_progress SET updated_at = now() WHERE query_name = %s", (name,)
                )
        except Exception as exc:
            log.warning("Could not record idle progress: %s", exc)


def main():
    spark = job.spark_session("iot-stream-readings")
    spark.sparkContext.setLogLevel("WARN")
    spark.streams.addListener(ProgressToPostgres())

    kafka = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", job.KAFKA_BOOTSTRAP)
        .option("subscribe", job.KAFKA_TOPIC)
        .option("startingOffsets", "earliest")  # first run: everything Kafka still retains
        .option("maxOffsetsPerTrigger", 50_000)  # keeps a big backfill in digestible batches
        .option("failOnDataLoss", "false")  # after a long outage retention may have moved on
        .load()
    )
    readings = job.parse_readings(kafka)

    (
        readings.writeStream.queryName("readings")
        .foreachBatch(job.write_readings)
        .option("checkpointLocation", f"{job.CHECKPOINTS}/readings")
        .trigger(processingTime=job.TRIGGER)
        .start()
    )

    hourly = job.hourly_aggregates(readings.withWatermark("event_time", job.WATERMARK))
    (
        hourly.writeStream.queryName("hourly")
        .outputMode("update")
        .foreachBatch(job.write_hourly)
        .option("checkpointLocation", f"{job.CHECKPOINTS}/hourly")
        .trigger(processingTime=job.TRIGGER)
        .start()
    )

    log.warning("Streaming %s -> PostgreSQL every %s", job.KAFKA_TOPIC, job.TRIGGER)
    spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
