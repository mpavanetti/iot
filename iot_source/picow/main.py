from machine import Pin, I2C
from time import sleep
import bme280 

i2c=I2C(0,sda=Pin(0), scl=Pin(1), freq=400000)
led = Pin(2, Pin.OUT)
pico_led = Pin("LED", Pin.OUT)

try:
    while True:
      pico_led.value(1)
      led.value(1)  
      bme = bme280.BME280(i2c=i2c)
      print(bme.values)
      led.value(0)
      sleep(2)
except Exception as e:
    print("[*] Program Aborted.")
    print(f"Exception(Exception): {e}")
    pico_led.value(0)
except KeyboardInterrupt:
    print("[*] Keyboard Program Interrupted.")
    print(f"Exception(KeyboardInterrupt)")
    pico_led.value(0)
    
    
