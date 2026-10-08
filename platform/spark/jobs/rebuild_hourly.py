"""Spark batch job: recompute every hourly aggregate from the `readings` table.

The streaming job only aggregates data that arrives within its watermark (1 hour late at
most). Run this after backfilling old readings, or after changing the aggregation logic:

    make rebuild-hourly        (docker compose run --rm spark-rebuild)
"""

from urllib.parse import urlparse

import iot_spark as job


def jdbc_options(dsn: str) -> dict:
    url = urlparse(dsn)
    return {
        "url": f"jdbc:postgresql://{url.hostname}:{url.port or 5432}{url.path}",
        "user": url.username,
        "password": url.password,
        "driver": "org.postgresql.Driver",
    }


def main():
    spark = job.spark_session("iot-rebuild-hourly")
    spark.sparkContext.setLogLevel("WARN")

    readings = (
        spark.read.format("jdbc")
        .options(**jdbc_options(job.POSTGRES_DSN))
        .option("dbtable", "readings")
        .load()
    )
    hourly = job.hourly_aggregates(readings).cache()

    job.write_hourly(hourly, batch_id=-1)
    print(f"Rebuilt {hourly.count()} hourly aggregates from {readings.count()} readings.")
    spark.stop()


if __name__ == "__main__":
    main()
