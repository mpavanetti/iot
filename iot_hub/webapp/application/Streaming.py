from kafka import KafkaConsumer, errors
from typing import Iterator
from flask import request
import logging
import json
import time
import sys

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


class Streaming:
    def __init__(self):
        pass

    def kafka_connect(self, topic: str, servers: list):
        try:
            consumer = KafkaConsumer(
                topic, bootstrap_servers=servers, auto_offset_reset="latest", group_id=None
            )
            self.consumer = consumer
            return True
        except errors.NoBrokersAvailable as e:
            print(f"[*] No Kafka Brokers Available.\n{e}")
            return False
        

    def iterate_kafka_data(self) -> Iterator[str]:
        if request.headers.getlist("X-Forwarded-For"):
            client_ip = request.headers.getlist("X-Forwarded-For")[0]
        else:
            client_ip = request.remote_addr or ""

        try:
            logger.info("Client %s connected", client_ip)
            # while True:
            for msg in self.consumer:
                data = json.loads(msg.value.decode("utf-8"))
                json_data = json.dumps(
                    {
                        "time": data["bme280"]["read_datetime"],
                        "bme_280_temperature": float(
                            data["bme280"]["temperature"]
                        ),
                        "bme_280_humidity": float(
                            data["bme280"]["humidity"]
                        ),
                        "bme_280_pressure": float(
                            data["bme280"]["pressure"]
                        ),
                        "bme_280_altitude": float(
                            data["bme280"]["altitude"]
                        ),
                        "picow_temperature": float(data["picow"]["temperature"]),
                        "picow_mem_alloc_bytes": int(data["picow"]["mem_alloc_bytes"]),
                        "picow_mem_free_bytes": int(data["picow"]["mem_free_bytes"]),
                        "picow_free_storage_kb": float(
                            data["picow"]["free_storage_kb"]
                        ),
                        "picow_free_cpu_freq_mhz": float(data["picow"]["cpu_freq_mhz"]),
                        "picow_local_ip": str(data["picow"]["local_ip"]),
                    }
                )

                yield f"data:{json_data}\n\n"
                time.sleep(1)
        except GeneratorExit:
            logger.info("Client %s disconnected", client_ip)
