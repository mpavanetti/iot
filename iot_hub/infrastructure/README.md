# Infrastructure Notes

## Setup Raspberry Pi 4

### Download latest version of raspbian os
https://downloads.raspberrypi.org/raspios_arm64/images/

### Update your Raspbian OS 64 bits
```
sudo apt-get update && sudo apt-get upgrade
```

#### Setup Raspberry pi as a router
```
curl -sL https://install.raspap.com | bash
```

#### Install Docker
```
curl -sSL https://get.docker.com | sh
```

### Install Docker-Compose
```
pip install docker-compose
```


### Docker
```
# Run the following commands:
sudo docker network create spark-network

sudo docker-compose up -d
```

### Apache Kafka
```
# Access container (Acessing bash)
sudo docker exec -it infrastructure-kafka-1 /bin/bash
cd /opt/bitnami/kafka/bin

# List kafka topics
sudo docker exec -it infrastructure-kafka-1 /opt/bitnami/kafka/bin/kafka-topics.sh --list --bootstrap-server localhost:9092

# Create Topic
sudo docker exec -it infrastructure-kafka-1 /opt/bitnami/kafka/bin/kafka-topics.sh  --create --topic iot_source --if-not-exists --partitions 1 --replication-factor 1 --bootstrap-server localhost:9092

# Produce a message (console)
sudo docker exec -it infrastructure-kafka-1 /opt/bitnami/kafka/bin/kafka-console-producer.sh --topic iot_source --bootstrap-server localhost:9092

# Consume a message (console)
sudo docker exec -it infrastructure-kafka-1 /opt/bitnami/kafka/bin/kafka-console-consumer.sh -topic iot_source --from-beginning --bootstrap-server localhost:9092

```
  

### Pico W Json Sample:
```
{"bme280": {"pressure": "890.07hPa", "temperature": "21.92C", "humidity": "44.83%", "read_datetime": "2023-9-6 16:39:51"}, "picow": {"local_ip": "192.168.1.74", "temperature": 24.24184}}
```
  
### Real VNC server
```
vncserver-virtual -geometry 1920x1080
```