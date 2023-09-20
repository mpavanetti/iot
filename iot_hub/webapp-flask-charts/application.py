import json
import logging
import random
import sys
import time
from datetime import datetime
from typing import Iterator

from flask import Flask, Response, render_template, request, stream_with_context

from kafka import KafkaConsumer
from json import loads

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

application = Flask(__name__)
random.seed()  # Initialize the random number generator

consumer = KafkaConsumer(
    'iot_source',
    bootstrap_servers=['localhost:29092'],
    auto_offset_reset = 'latest',
    group_id=None,
)


@application.route("/")
def index() -> str:
    return render_template("index.html")


def generate_random_data() -> Iterator[str]:
    """
    Generates random value between 0 and 100

    :return: String containing current timestamp (YYYY-mm-dd HH:MM:SS) and randomly generated data.
    """
    if request.headers.getlist("X-Forwarded-For"):
        client_ip = request.headers.getlist("X-Forwarded-For")[0]
    else:
        client_ip = request.remote_addr or ""

    try:
        logger.info("Client %s connected", client_ip)
        #while True:
        for msg in consumer:
            data = loads(msg.value.decode("utf-8"))
            json_data = json.dumps({"time": data["bme280"]["read_datetime"],
                                    "value": float(data["bme280"]["temperature"].replace("C",""))})

            
            yield f"data:{json_data}\n\n"
            time.sleep(1)
    except GeneratorExit:
        logger.info("Client %s disconnected", client_ip)


@application.route("/chart-data")
def chart_data() -> Response:
    response = Response(stream_with_context(generate_random_data()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


if __name__ == "__main__":
    application.run(host="0.0.0.0", threaded=True)
