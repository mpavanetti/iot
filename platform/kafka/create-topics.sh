#!/bin/bash
# Creates the platform's Kafka topics (idempotent: safe to run on every start).
#   iot.readings      valid readings, keyed by device_id, kept 7 days
#   iot.readings.dlq  lines the gateway rejected, with the reason, kept 14 days
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-kafka:9092}"
TOPICS=/opt/kafka/bin/kafka-topics.sh

"$TOPICS" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic iot.readings --partitions 3 --replication-factor 1 \
  --config retention.ms=604800000

"$TOPICS" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic iot.readings.dlq --partitions 1 --replication-factor 1 \
  --config retention.ms=1209600000

"$TOPICS" --bootstrap-server "$BOOTSTRAP" --describe --exclude-internal
echo "Kafka topics ready."
