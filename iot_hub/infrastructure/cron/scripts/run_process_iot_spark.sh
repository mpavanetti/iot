docker compose exec spark spark-submit \
--jars /home/jovyan/work/jars/commons-pool2-2.11.1.jar,/home/jovyan/work/jars/spark-sql-kafka-0-10_2.12-3.4.1.jar,/home/jovyan/work/jars/kafka-clients-3.5.1.jar,/home/jovyan/work/jars/spark-token-provider-kafka-0-10_2.12-3.4.1.jar,/home/jovyan/work/jars/mysql-connector-j-8.0.31.jar \
--packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.4.1,org.apache.kafka:kafka-clients:3.2.1 \
/home/jovyan/work/jobs/spark_process_iot_data.py \
--master spark://spark:7077 \
--deploy-mode cluster \
--supervise \
/home/jovyan/work/jobs/spark_process_iot_data.py 