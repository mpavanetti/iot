# Required Libraries"
# micropython-bme280
from data import Data
from machine import Pin
import time
import random
import machine
import gc
import os
  
start_btn = Pin(7, Pin.IN, Pin.PULL_UP)
stop_btn = Pin(8, Pin.IN, Pin.PULL_UP)

s = os.statvfs('/')

with Data() as data:
    local_ip = data.wlan.ifconfig()[0]

    try:
        while True:
            time.sleep(1)
            if start_btn.value() == 0:
                while True:
                    time.sleep(2)
                    if stop_btn.value() == 0:
                        break
                    payload = {
                        "id": random.randrange(60000000),
                        "picow": {
                            "local_ip": local_ip,
                            "temperature": data.get_board_temperature(),
                            "free_storage_kb":s[0]*s[3]/1024,
                            "mem_alloc_bytes":gc.mem_alloc(),
                            "mem_free_bytes":gc.mem_free(),
                            "cpu_freq_mhz": machine.freq()/1000000,

                        },
                        "bme280": data.read_bme280(),
                    }
                    data.display()
                    data.remoteHost_send(payload)

    except Exception as e:
        print(f"[*Exception]: {e}")
    except KeyboardInterrupt:
        print("[*] Keyboard Program Interrupted.")
        print(f"Exception(KeyboardInterrupt)")

