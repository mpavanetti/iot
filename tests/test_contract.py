"""One message contract, many readers. These tests fail if any of them drifts:
the pydantic model, the Spark schema, the PostgreSQL table and the SQLite table."""

import ast
import re

from iotcenter.lite.storage import READING_COLUMNS as SQLITE_COLUMNS
from iotcenter.protocol import Reading

from .conftest import ROOT

MODEL_FIELDS = set(Reading.model_fields) | set(Reading.model_computed_fields)
WIRE_FIELDS = MODEL_FIELDS - {"event_time"}  # event_time is derived by every reader


def spark_schema_fields() -> set[str]:
    """Field names of READING_SCHEMA, read from the source (no pyspark needed)."""
    tree = ast.parse((ROOT / "platform/spark/jobs/iot_spark.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "StructField":
            names.add(node.args[0].value)
    return names


def postgres_columns(table: str) -> set[str]:
    sql = (ROOT / "platform/postgres/init.sql").read_text()
    body = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", sql, re.S).group(1)
    columns = set()
    for line in body.splitlines():
        line = line.split("--")[0].strip()
        for part in line.split(","):  # several columns may share a line
            words = part.split()
            if words and words[0].isidentifier() and words[0].upper() != "PRIMARY":
                columns.add(words[0])
    return columns


def test_spark_reads_every_field_the_gateway_writes():
    assert spark_schema_fields() == WIRE_FIELDS


def test_postgres_readings_table_stores_every_field():
    lineage = {"kafka_partition", "kafka_offset", "processed_at"}
    assert postgres_columns("readings") - lineage == MODEL_FIELDS - {"v"}


def test_sqlite_readings_table_stores_every_field():
    assert set(SQLITE_COLUMNS) == MODEL_FIELDS - {"v"}


def test_hourly_tables_agree_between_editions():
    from iotcenter.lite.storage import HOURLY_PREFIX

    postgres = postgres_columns("readings_hourly")
    for prefix in HOURLY_PREFIX.values():
        assert {f"{prefix}_avg", f"{prefix}_min", f"{prefix}_max"} <= postgres


def test_firmware_sends_the_contract_field_names():
    source = (ROOT / "firmware/telemetry.py").read_text()
    sent = set(re.findall(r'"([a-z_]+)":', source))
    assert sent <= WIRE_FIELDS
    assert {"device_id", "seq", "temperature_c", "humidity_pct", "pressure_hpa"} <= sent
