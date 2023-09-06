## Notes

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
{"bme280": {"pressure": "890.07hPa", "temperature": "21.92C", "humidity": "44.83%", "read_datetime": "2023/9/6T15:31:51"}, "picow": {"local_ip": "192.168.1.74", "temperature": 24.24184}}
```