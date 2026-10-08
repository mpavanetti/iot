# One Spark image for the master, the worker and the jobs: the official Apache Spark image
# plus the Kafka connector, the PostgreSQL JDBC driver and psycopg, with our jobs baked in.
FROM spark:4.2.0-scala2.13-java21-python3-ubuntu

USER root

# Connector jars, checksum-verified: a tampered or truncated download fails the build.
ARG MAVEN=https://repo1.maven.org/maven2
RUN set -eux; cd /opt/spark/jars; \
    for jar in \
      "org/apache/spark/spark-sql-kafka-0-10_2.13/4.2.0/spark-sql-kafka-0-10_2.13-4.2.0.jar 64539c45e5d39f7ddb747312c239f38f5208d08c1a0ddf7d70beb2fbd9373705" \
      "org/apache/spark/spark-token-provider-kafka-0-10_2.13/4.2.0/spark-token-provider-kafka-0-10_2.13-4.2.0.jar 641c7947afc8aa1189e433b7c2d305293993c8691bc1c2062d6d4f513b12518c" \
      "org/apache/kafka/kafka-clients/3.9.2/kafka-clients-3.9.2.jar 014b4ef36884478a932641818b080c36a8f8e2e92dc771e800c9d2694f4f83d8" \
      "org/apache/commons/commons-pool2/2.13.1/commons-pool2-2.13.1.jar f77a5060d6936a9144023584232f9de3f2248f1abfe156e9d31795f18770674f" \
      "org/postgresql/postgresql/42.7.14/postgresql-42.7.14.jar 73914527305a40cce504b0d3d90b23caf565136912d607ae8ae7c5895512332c"; \
    do \
      set -- $jar; \
      curl -fsSLO "$MAVEN/$1"; \
      echo "$2  $(basename "$1")" | sha256sum -c -; \
    done; \
    chmod 644 /opt/spark/jars/*.jar

# psycopg lets the executors upsert into PostgreSQL (INSERT ... ON CONFLICT).
RUN pip install --no-cache-dir "psycopg[binary]==3.3.6"

# Shared by the driver and the worker: stateful streaming writes state from the executors.
RUN mkdir -p /opt/spark/checkpoints && chown spark:spark /opt/spark/checkpoints

COPY --chown=spark:spark jobs/ /opt/spark/jobs/
ENV PYTHONPATH=/opt/spark/jobs \
    TZ=UTC

USER spark
WORKDIR /opt/spark/work-dir
