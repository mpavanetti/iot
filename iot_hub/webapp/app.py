from flask import Flask, render_template, redirect, Response, stream_with_context
from application.Hardware import Hardware
from application.Streaming import Streaming

# Generic Variables
kafka_broker_list = ["localhost:29092"]

app = Flask(__name__)


@app.route("/")
def routing():
    return redirect("/home")

@app.route("/home")
def home():
    return render_template("home.html")

@app.route("/api/check_host_status")
def host_status():
    hardware = Hardware()
    dataset = {
        "local_ip": hardware.get_local_ip(),
        "picow_ip": hardware.picow_ip,
        "local_alive": hardware.check_if_host_alive(hardware.get_local_ip()),
        "picow_alive": hardware.check_if_host_alive(hardware.picow_ip),
    }
    return dataset

@app.route("/api/load_host_hardware")
def host_hardware():
    hardware = Hardware()
    dataset = {
        "cpu_usage": hardware.get_cpu_usage(),
        "mem_usage": hardware.get_mem_usage(),
        "disk_usage": hardware.get_disk_usage(),
    }
    return dataset

@app.route("/hardware")
def hardware():
    hardware = Hardware()
    return render_template(
        "hardware.html",
        network=hardware.get_all_network(),
        hardware=hardware.get_all_hardware(),
        python=hardware.get_all_python(),
    )

@app.route("/streaming")
def streaming():
    return render_template("streaming.html")

@app.route("/chart-data")
def chart_data() -> Response:
    streaming = Streaming()
    streaming.kafka_connect("iot_source", kafka_broker_list)
    response = Response(
        stream_with_context(streaming.iterate_kafka_data()),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


if __name__ == "__main__":
    app.run(debug=True, port=8001, host="0.0.0.0")
