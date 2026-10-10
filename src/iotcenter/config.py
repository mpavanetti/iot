"""Settings for every service, read from `IOT_*` environment variables.

One class on purpose: the full list of knobs lives in one place (and in docs/configuration.md).
Each service only reads the fields it needs. Command-line flags override these values.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IOT_", extra="ignore")

    # --- Device ingestion (Lite and the platform gateway) -------------------------------
    tcp_enabled: bool = True
    tcp_host: str = "0.0.0.0"
    tcp_port: int = 1500
    serial_port: str | None = None  # e.g. /dev/ttyACM0 (Linux), /dev/cu.usbmodem1101 (macOS)
    serial_baud: int = 115200

    # --- HTTP (dashboard API; the gateway uses it for /health and /stats) ---------------
    http_host: str = "0.0.0.0"
    http_port: int = 8000

    # --- Lite storage ---------------------------------------------------------------------
    db_path: Path = Path("data/iot-lite.db")
    retention_days: int = 30  # raw readings (every message). 0 = forever
    hourly_retention_days: int = 730  # hourly aggregates (the 7d to 1y charts). 0 = forever

    # --- Dashboard ------------------------------------------------------------------------
    offline_after_s: float = 30.0  # a device is "offline" after this long without data
    altitude_m: float | None = None  # of the sensors: shows sea-level pressure (e.g. 1045)
    timezone: str = "UTC"  # how Streamlit shows times, e.g. America/New_York (browsers use local)

    # --- Platform -------------------------------------------------------------------------
    kafka_bootstrap: str = "localhost:9094"
    kafka_topic: str = "iot.readings"
    kafka_dlq_topic: str = "iot.readings.dlq"
    database_url: str = "postgresql://iot:iot@localhost:5432/iot"
    replay_messages: int = 900  # per partition, replayed from Kafka when the web app starts
    gateway_url: str = "http://localhost:8001"
    spark_master_url: str = "http://localhost:8080"
    analytics_url: str = "http://localhost:8501"
    # Host ports the *browser* uses for the links in the dashboard header
    analytics_public_port: int = 8501
    spark_public_port: int = 8080
    spark_app_public_port: int = 4040

    @field_validator("serial_port", "altitude_m", mode="before")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None
