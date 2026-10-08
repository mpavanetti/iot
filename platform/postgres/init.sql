-- IoT Center Platform schema. Runs once, when the PostgreSQL volume is first created.
-- Spark Structured Streaming writes these tables; the dashboard and Streamlit read them.

-- Every valid reading. The primary key makes inserts idempotent: Spark can safely replay a
-- micro-batch after a crash, and a message resent by a device is stored only once.
CREATE TABLE readings (
    device_id       text             NOT NULL,
    event_time      timestamptz      NOT NULL,  -- device clock if NTP-synced, else arrival
    seq             bigint           NOT NULL,  -- per-boot message counter from the device
    name            text,
    ts              timestamptz,                -- device clock as sent (NULL if unsynced)
    received_at     timestamptz      NOT NULL,  -- stamped by the gateway
    source          text,                       -- tcp | usb | simulator
    temperature_c   double precision NOT NULL,
    humidity_pct    double precision NOT NULL,
    pressure_hpa    double precision NOT NULL,
    dew_point_c     double precision,
    cpu_temp_c      double precision,
    mem_free_bytes  bigint,
    mem_alloc_bytes bigint,
    storage_free_kb double precision,
    cpu_freq_mhz    double precision,
    uptime_s        bigint,
    wifi_rssi_dbm   integer,
    ip              text,
    firmware        text,
    kafka_partition integer,                    -- lineage: where Spark read it from
    kafka_offset    bigint,
    processed_at    timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, event_time, seq)
);
CREATE INDEX readings_event_time ON readings (event_time);

-- Per device and clock hour, maintained by the streaming job (update mode + upsert) and
-- recomputable from `readings` with the rebuild job.
CREATE TABLE readings_hourly (
    device_id       text             NOT NULL,
    hour            timestamptz      NOT NULL,
    samples         integer          NOT NULL,
    temperature_avg double precision, temperature_min double precision, temperature_max double precision,
    humidity_avg    double precision, humidity_min    double precision, humidity_max    double precision,
    pressure_avg    double precision, pressure_min    double precision, pressure_max    double precision,
    dew_point_avg   double precision, dew_point_min   double precision, dew_point_max   double precision,
    cpu_temp_avg    double precision,
    updated_at      timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, hour)
);

-- One row per streaming query, updated after every micro-batch (pipeline health).
CREATE TABLE stream_progress (
    query_name      text PRIMARY KEY,
    batch_id        bigint           NOT NULL,
    input_rows      bigint           NOT NULL,
    rows_per_second double precision,
    duration_ms     bigint,
    watermark       timestamptz,
    updated_at      timestamptz      NOT NULL DEFAULT now()
);

-- Convenience for exploring with psql or Streamlit: one row per board.
CREATE VIEW devices AS
SELECT h.device_id,
       latest.name,
       h.first_hour AS first_seen,
       latest.received_at AS last_seen,
       h.messages,
       latest.ip,
       latest.firmware,
       latest.source
FROM (
    SELECT device_id, min(hour) AS first_hour, sum(samples) AS messages
    FROM readings_hourly
    GROUP BY device_id
) h
CROSS JOIN LATERAL (
    SELECT r.name, r.received_at, r.ip, r.firmware, r.source
    FROM readings r
    WHERE r.device_id = h.device_id
    ORDER BY r.event_time DESC
    LIMIT 1
) latest;
