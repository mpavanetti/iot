from flask import Flask, render_template, redirect, Response, request, stream_with_context
from application.Hardware import Hardware

import logging
import sys
import time
import json
from datetime import datetime
from typing import Iterator
from kafka import KafkaConsumer

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


app = Flask(__name__)

@app.route('/')
def routing():
    return redirect('/home')

@app.route('/home')
def home():
    return render_template('home.html')

@app.route('/api/check_host_status')
def host_status():
    hardware = Hardware()
    dataset = {"local_ip": hardware.get_local_ip(),
               "picow_ip": hardware.picow_ip,
                "local_alive": hardware.check_if_host_alive(hardware.get_local_ip()),
                "picow_alive": hardware.check_if_host_alive(hardware.picow_ip)}
    return dataset

@app.route('/api/load_host_hardware')
def host_hardware():
    hardware = Hardware()
    dataset = {"cpu_usage": hardware.get_cpu_usage(),
                "mem_usage": hardware.get_mem_usage(),
                "disk_usage": hardware.get_disk_usage()}
    return dataset
 

@app.route('/hardware')
def hardware():
    hardware= Hardware()
    return render_template('hardware.html',
                           network=hardware.get_all_network(),
                           hardware= hardware.get_all_hardware(),
                           python=hardware.get_all_python())

@app.route('/streaming')
def streaming():
     consumer = KafkaConsumer(
                'iot_source',
                bootstrap_servers=['localhost:29092'],
                auto_offset_reset = 'latest',
                group_id=None,
)

     return render_template('streaming.html')



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
            data = json.loads(msg.value.decode("utf-8"))
            json_data = json.dumps({"time": data["bme280"]["read_datetime"],
                                    "value": float(data["bme280"]["temperature"].replace("C",""))})

            
            yield f"data:{json_data}\n\n"
            time.sleep(1)
    except GeneratorExit:
        logger.info("Client %s disconnected", client_ip)


@app.route("/chart-data")
def chart_data() -> Response:
    response = Response(stream_with_context(generate_random_data()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

if __name__ == '__main__':
	app.run(debug=True, port=8001, host='0.0.0.0') 
