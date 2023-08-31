from data import Data
from machine import I2C
import time


with Data() as data:
    
    local_ip = data.wlan.ifconfig()[0]
    
    try:
        while True:
            payload = {"picow":{"local_ip": local_ip,
                        "temperature": data.get_board_temperature()},
                        "bme280": data.read_bme280()}
            
            data.remoteHost_send(payload)
            time.sleep(2)
        
    except Exception as e:
        print("[*] Program Aborted.")
        print(f"Exception(Exception): {e}")
    except KeyboardInterrupt:
        print("[*] Keyboard Program Interrupted.")
        print(f"Exception(KeyboardInterrupt)")
        
    




