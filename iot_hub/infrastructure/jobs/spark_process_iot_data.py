from pyspark.sql.session import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, IntegerType, DateType, TimestampType
from pyspark.sql.functions import lit, col, from_json, regexp_replace, to_timestamp, current_timestamp, to_utc_timestamp
import logging

spark = (SparkSession
         .Builder()
         .appName(name="process_iot_data")
         .master("spark://spark:7077")
         .getOrCreate())

spark.sparkContext.setLogLevel("WARN")

logging.warning(f"[*] Spark Master at: spark://spark:7077")
logging.warning(f"[*] Spark Cores: {spark.sparkContext.defaultParallelism}")

schema = StructType([
    StructField("id", IntegerType()),
    StructField("bme280", StructType([
        StructField("pressure", StringType()),
        StructField("temperature", StringType()),
        StructField("humidity", StringType()),
        StructField("read_datetime", StringType())
    ])),
    StructField("picow", StructType([
        StructField("local_ip", StringType()),
        StructField("temperature", DoubleType()),
        StructField("free_storage_kb", DoubleType()),
        StructField("mem_alloc_bytes", DoubleType()),
        StructField("mem_free_bytes", DoubleType()),
        StructField("cpu_freq_mhz", DoubleType())
    ]))
])

picow_df = (spark 
              .read
              .format("kafka") 
              .option("kafka.bootstrap.servers", "kafka:9092") 
              .option("subscribe", "iot_source")
              .load())

df = (picow_df
      .where("offset > 2")
      .selectExpr("CAST(offset AS INTEGER)","CAST(timestamp AS TIMESTAMP)","CAST(key AS STRING)", "CAST(value AS STRING)", "CAST(topic AS STRING)"))

stg_df =  (df
            .withColumn('data', from_json(col('value'), schema))
            .selectExpr("offset as kafka_offset",
                        "timestamp as kafka_datetime",
                        "topic as kafka_topic",
                        "data.id as id",
                        "data.bme280.pressure as bme280_pressure",
                        "data.bme280.temperature as bme280_temperature",
                        "data.bme280.humidity as bme280_humidity",
                        "data.bme280.read_datetime as read_datetime",
                        "data.picow.local_ip as picow_local_ip",
                        "data.picow.temperature as picow_temperature",
                        "data.picow.mem_alloc_bytes as picow_mem_alloc_bytes",
                        "data.picow.mem_free_bytes as picow_mem_free_bytes",
                        "data.picow.cpu_freq_mhz as picow_cpu_freq_mhz",
                       )
            .drop("key","value"))

final_df = (stg_df
                .withColumn("kafka_datetime", to_utc_timestamp(col("kafka_datetime"), "UTC"))
                .withColumn("spark_process_datetime", current_timestamp())
                .withColumn("bme280_pressure",regexp_replace(col("bme280_pressure"),"hPa","").cast(DoubleType()))
                .withColumn("bme280_temperature",regexp_replace(col("bme280_temperature"),"C","").cast(DoubleType()))
                .withColumn("bme280_humidity",regexp_replace(col("bme280_humidity"),"%","").cast(DoubleType()))
                .withColumn("read_datetime", to_timestamp(col("read_datetime"), "yyyy-M-d HH:mm:s"))
                .withColumn("read_datetime", to_utc_timestamp(col("kafka_datetime"), "UTC"))
                .withColumn("read_date",col("read_datetime").cast("DATE"))
                .orderBy(col("read_datetime").desc())
                .where("id is not null")
           )
logging.warning(f"Dataframe Records: {final_df.count()}")

from pyspark.sql.functions import count, avg, round, month,year, dayofmonth, min, max, hour, desc

agg_by_hour_df = (
                 final_df
                 .withColumn("day", dayofmonth(col("read_date")))
                 .withColumn("month", month(col("read_date")))
                 .withColumn("year", year(col("read_date")))
                 
                 .groupBy("year", "month", "day", hour(col("read_datetime")).alias("hour"))
                 .agg(
                      count(col("id")).alias("count"),

                      # date columns
                      max(col("read_datetime")).alias("datetime"),
                      max(col("read_date")).alias("date"),
                      
                      # bme temperature metrics
                      round(min(col("bme280_temperature")),2).alias("min_bme_temp"),
                      round(avg(col("bme280_temperature")),2).alias("avg_bme_temp"),
                      round(max(col("bme280_temperature")),2).alias("max_bme_temp"),
                      

                      # picow temperature metrics
                      round(min(col("picow_temperature")),2).alias("min_picow_temp"),
                      round(avg(col("picow_temperature")),2).alias("avg_picow_temp"),
                      round(max(col("picow_temperature")),2).alias("max_picow_temp"),

                     # bme humidity metrics
                      round(min(col("bme280_humidity")),2).alias("min_bme_hum"),
                      round(avg(col("bme280_humidity")),2).alias("avg_bme_hum"),
                      round(max(col("bme280_humidity")),2).alias("max_bme_hum"),

                     # bme pressure metrics
                      round(min(col("bme280_pressure")),2).alias("min_bme_press"),
                      round(avg(col("bme280_pressure")),2).alias("avg_bme_press"),
                      round(max(col("bme280_pressure")),2).alias("max_bme_press")
                      
                     )
                 .sort(desc("year"), desc("month"), desc("day"), desc("hour"))
                 .selectExpr("datetime",
                             "date",
                             "year",
                             "month",
                             "day",
                             "hour",
                             "count",
                             "min_bme_temp",
                             "avg_bme_temp",
                             "max_bme_temp",
                             "min_picow_temp",
                             "avg_picow_temp",
                             "max_picow_temp",
                             "min_bme_hum",
                             "avg_bme_hum",
                             "max_bme_hum",
                             "min_bme_press",
                             "avg_bme_press",
                             "max_bme_press",
                            )
                )

agg_by_ip_df = (final_df
                .groupBy("picow_local_ip")
                .agg(count(col("id")).alias("messages"))
                .selectExpr("picow_local_ip as source_ip", "messages")
               )

logging.warning(f"Storing {agg_by_hour_df.count()} records at mariadb table agg_by_hour_df ...")

(agg_by_hour_df.write 
        .format("jdbc") 
        .mode("overwrite") 
        .option("driver", "com.mysql.jdbc.Driver") 
        .option("url", "jdbc:mysql://mariadb:3306/data?rewriteBatchedStatements=true") 
        .option("user", "mysql") 
        .option("password", "mysql") 
        .option("dbtable", "agg_by_hour_df") 
        .save())

logging.warning(f"Storing {agg_by_ip_df.count()} records at mariadb table agg_by_ip_df ...")

(agg_by_ip_df.write 
        .format("jdbc") 
        .mode("overwrite") 
        .option("driver", "com.mysql.jdbc.Driver") 
        .option("url", "jdbc:mysql://mariadb:3306/data?rewriteBatchedStatements=true") 
        .option("user", "mysql") 
        .option("password", "mysql") 
        .option("dbtable", "agg_by_ip_df") 
        .save())