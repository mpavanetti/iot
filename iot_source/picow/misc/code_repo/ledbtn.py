from machine import Pin
import time

led = Pin(0, Pin.OUT)
led2 = Pin(1, Pin.OUT)
btn = Pin(2, Pin.IN, Pin.PULL_UP)
btn2 = Pin(7, Pin.IN, Pin.PULL_UP)

while True:
    if btn.value() == 0:
        led.value(1)
        time.sleep(0.5)
    elif btn2.value() == 0:
        led2.value(1)
        time.sleep(0.5)
    else:
        led.value(0)
        led2.value(0)