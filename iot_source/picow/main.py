# install module micropython_bme280
from machine import Pin, I2C        #importing relevant modules & classes
from time import sleep
import bme280       #importing BME280 library

i2c=I2C(0,sda=Pin(0), scl=Pin(1), freq=400000)    #initializing the I2C method 
led = Pin(2, Pin.OUT)

while True:
  led.value(1)  
  bme = bme280.BME280(i2c=i2c)
  print(bme.values)
  led.value(0)
  sleep(5)           
