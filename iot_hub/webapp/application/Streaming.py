from kafka import KafkaConsumer
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
        consumer = KafkaConsumer(
            topic, bootstrap_servers=servers, auto_offset_reset="latest", group_id=None
        )
        self.consumer = consumer

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
                        "bme_280_temperature": float(data["bme280"]["temperature"].replace("C", "")),
                        "bme_280_pressure": float(data["bme280"]["pressure"].replace("hPa", "")),
                        "picow_temperature": float(data["picow"]["temperature"])
                    }
                )

                yield f"data:{json_data}\n\n"
                time.sleep(1)
        except GeneratorExit:
            logger.info("Client %s disconnected", client_ip)
