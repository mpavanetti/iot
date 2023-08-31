from data import Data
from machine import Pin
import time

start_btn = Pin(7, Pin.IN, Pin.PULL_UP)
stop_btn = Pin(8, Pin.IN, Pin.PULL_UP)


with Data() as data:
    
    local_ip = data.wlan.ifconfig()[0]
    
    try:
        while True:
            time.sleep(1)
            if start_btn.value() == 0:
                
                while True:
                    time.sleep(2)
                    
                    payload = {"picow":{"local_ip": local_ip,
                                        "temperature": data.get_board_temperature(),
                                        "local_datetime": time.gmtime()},
                               "bme280": data.read_bme280()}
                    
                    data.remoteHost_send(payload)
                    if stop_btn.value() == 0:
                        break
        
    except Exception as e:
        print(f"[*Exception]: {e}")
    except KeyboardInterrupt:
        print("[*] Keyboard Program Interrupted.")
        print(f"Exception(KeyboardInterrupt)")
        
    




