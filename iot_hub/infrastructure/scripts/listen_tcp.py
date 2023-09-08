from socket import socket
from kafka import KafkaProducer
from os import getenv
from datetime import datetime
from logging import info, warning, error

# Socket Port
port = int(getenv("SOCKET_PORT", 1500))

# Define server with port
#bootstrap_servers = getenv("BOOTSTRAP_SERVERS", "kafka:9092")
bootstrap_servers = getenv("BOOTSTRAP_SERVERS", "localhost:29092") # external

# Define topic name where the message will publish
topicName = getenv("KAFKA_TOPIC", "iot_source")

now = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

# Create Socket
sock = socket()
warning(f"[KAFKA] bootstrap servers: {bootstrap_servers}")
warning(f"[KAFKA] Topic: {topicName}")

# Socket Listening
sock.bind(("", port))
sock.listen(5)
warning(f"[*] Socket is listening at port {port}.")

# Initialize producer variable
producer = KafkaProducer(bootstrap_servers=bootstrap_servers,security_protocol="PLAINTEXT")

# Infinite Loop
while True:
    c, addr = sock.accept()
    warning(f"[CONNECTION] Received from: {addr} at {now}")

    payload = c.recv(1024)
    print(f"[PAYLOAD] Received:  ", payload)

    try:
    # Kafka Produce message
        producer.send(topicName, payload)
        info(f"Message Sent at {now}.")
        producer.flush()
        
    except BaseException as e:
        error(f"[ERROR] Exception raised  while producing a kafka message:\n[*]{e}")

    c.close()

