# Generic Variables
localhost = "127.0.0.1"
kafka_broker_list = [f"{localhost}:29092"]

services = {
    "iotcenter": {"description": "IoT Center", "port": 80},
    "analytics": {"description": "Analytics Dashboard", "port": 8501},
    "vnc": {"description": "VNC Server (raspberrypi:1)", "port": 5901},
    "vnc2": {"description": "VNC Server (raspberrypi:2)", "port": 5902},
    "ftp": {"description": "FTP Server", "port": 21},
    "sftp": {"description": "SFTP Server", "port": 22},
    "ssh": {"description": "SSH Server", "port": 22},
    "rasap": {"description": "Raspberry Pi Access Point", "port": 8005},
    "mariadb": {"description": "MariaDB Database", "port": 3306},
    "jupyerlab": {"description": "Jupyter Notebook Lab", "port": 8888},
    "sparkui": {"description": "Spark UI", "port": 4040},
    "sparkmasterui": {"description": "Spark Master UI", "port": 8080},
    "sparkmaster": {"description": "Spark Master", "port": 7077},
    "sparkworker1": {"description": "Spark Worker 1", "port": 8081},
    "sparkworker2": {"description": "Spark Worker 2", "port": 8082},
    "kafkainternal": {"description": "Kafka Broker (Internal)", "port": 9092},
    "kafkaexternal": {"description": "Kafka Broker (External)", "port": 29092},
    "pythonsocket": {"description": "Python Socket Agent", "port": 1500},
}

hosts = {
    "rasp4": {"ip": "", "alive": "", "description": "Raspberry Pi 4"},
    "picow": {"ip": "", "alive": "", "description": "Raspberry Pi Pico W"},
}