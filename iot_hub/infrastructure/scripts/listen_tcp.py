from socket import socket
from kafka import KafkaProducer
from os import getenv
from datetime import datetime
from logging import info, warning, error
from time import sleep

# Socket Port
port = int(getenv("SOCKET_PORT", 1500))

# Define server with port
#bootstrap_servers = getenv("BOOTSTRAP_SERVERS", "kafka:9092") # internal
bootstrap_servers = getenv("BOOTSTRAP_SERVERS", "localhost:29092") # external

# Define topic name where the message will publish
topicName = getenv("KAFKA_TOPIC", "iot_source")

# get sleep time before start poking
wait_time = int(getenv("WAIT_TIME", 50))

# Create Socket
sock = socket()
warning(f"[KAFKA] bootstrap servers: {bootstrap_servers}")
warning(f"[KAFKA] Topic: {topicName}")

# Socket Listening
sock.bind(("", port))
sock.listen(5)
warning(f"[*] Socket is listening at port {port}.")

# wait kafka kraf startup before start the kafka producer
sleep(wait_time)

# Initialize producer variable
producer = KafkaProducer(bootstrap_servers=bootstrap_servers,
                         security_protocol="PLAINTEXT")

# Infinite Loop
while True:
    c, addr = sock.accept()
    warning(f"[CONNECTION] Received from: {addr} at {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}")

    payload = c.recv(1024)
    info(f"[PAYLOAD] Received:  ", payload)

    try:
    # Kafka Produce message
        producer.send(topicName, payload)
        info(f"Message Sent at {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}.")
        producer.flush()
        
    except BaseException as e:
        error(f"[ERROR] Exception raised  while producing a kafka message:\n[*]{e}")

    c.close()

