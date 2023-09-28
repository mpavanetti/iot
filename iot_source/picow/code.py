import board, busio, displayio, os, terminalio
import adafruit_displayio_ssd1306
import time
import os
import wifi
import random
import gc
import socketpool
import json
import microcontroller
from adafruit_display_text import label
from adafruit_bme280 import basic as adafruit_bme280

TEXT_WHITE = 0xFFFFFF

displayio.release_displays()

s = os.statvfs('/')

board_type = os.uname().machine
print(f"Board: {board_type}")

if 'Pico' in board_type:
    sda, scl = board.GP0, board.GP1
    print("Supported.")
    
print(f"Connecting to {os.getenv('CIRCUITPY_WIFI_SSID')}")
wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
print(f"Connected to {os.getenv('CIRCUITPY_WIFI_SSID')}")
print(f"My IP address: {wifi.radio.ipv4_address}")
pool = socketpool.SocketPool(wifi.radio)
    
i2c = busio.I2C(scl, sda)
display_bus = displayio.I2CDisplay(i2c, device_address=0x3C)
display = adafruit_displayio_ssd1306.SSD1306(display_bus, width=128, height=64)


#bme280 sensor
bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x76)
# change this to match the location's pressure (hPa) at sea level
bme280.sea_level_pressure = 1013.25



# Text labels for display
hello_label = label.Label(terminalio.FONT)
hello_label.anchor_point = (0.0, 0.0)
hello_label.anchored_position = (0, 0)
hello_label.scale = 1
hello_label.color = TEXT_WHITE

# Temperature
temp_label = label.Label(terminalio.FONT)
temp_label.anchor_point = (0.0, 0.0)
temp_label.anchored_position = (0, 10)
temp_label.scale = 1
temp_label.color = TEXT_WHITE

# Humidity
humidity_label = label.Label(terminalio.FONT)
humidity_label.anchor_point = (0.0, 0.0)
humidity_label.anchored_position = (0, 30)
humidity_label.scale = 1
humidity_label.color = TEXT_WHITE

# Pressure
pressure_label = label.Label(terminalio.FONT)
pressure_label.anchor_point = (0.0, 0.0)
pressure_label.anchored_position = (0, 40)
pressure_label.scale = 1
pressure_label.color = TEXT_WHITE

# Altitude
altitude_label = label.Label(terminalio.FONT)
altitude_label.anchor_point = (0.0, 0.0)
altitude_label.anchored_position = (0, 50)
altitude_label.scale = 1
altitude_label.color = TEXT_WHITE


# Create DisplayIO Group Layer
layer1 = displayio.Group()
layer1.append(hello_label)
layer1.append(temp_label)
layer1.append(humidity_label)
layer1.append(pressure_label)
layer1.append(altitude_label)
display.show(layer1)

while True:
    temperature = ("\nTemperature: %0.1f C" % bme280.temperature)
    humidity = ("Humidity: %0.1f %%" % bme280.relative_humidity)
    pressure = ("Pressure: %0.1f hPa" % bme280.pressure)
    altitude = ("Altitude = %0.2f meters" % bme280.altitude)
    
    hello_label.text =f"IP: {wifi.radio.ipv4_address}"
    temp_label.text=temperature
    humidity_label.text=humidity
    pressure_label.text=pressure
    altitude_label.text=altitude
    
    print(temperature)
    print(humidity)
    print(pressure)
    print(altitude)
    
    now = time.localtime()
    
    payload = {
            "id": random.randrange(60000000),
            "picow": {
                "local_ip": str(wifi.radio.ipv4_address),
                "temperature": microcontroller.cpu.temperature,
                "free_storage_kb": 0,
                "mem_alloc_bytes":gc.mem_alloc(),
                "mem_free_bytes":gc.mem_free(),
                "cpu_freq_mhz": 0,

            },
            "bme280": {
            "temperature": bme280.temperature,
            "pressure": bme280.pressure,
            "humidity": bme280.relative_humidity,
            "altitude": bme280.altitude,
            "read_datetime": f"{now[0]}-{now[1]}-{now[2]} {now[3]}:{now[4]}:{now[5]}",
        },
    }
 
    
    with pool.socket(pool.AF_INET, pool.SOCK_STREAM) as s:
        s.settimeout(None)
        s.connect(("192.168.50.1", 1500))
        print("Sending...")
        jsondata = json.dumps(payload)
        s.send(jsondata)
    
    time.sleep(2)