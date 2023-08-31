import network
import socket
import json
import time
from machine import ADC, Pin, I2C
from bme280 import BME280

# Wifi, can be overwriten
wifi_sid="Matheus’s iPhone"
wifi_pswd="matheus22"

# Remote Host, can be overwriten
remote_port = 8080
remote_address = "4.205.25.155"

# Sensor variables
i2c=I2C(0,sda=Pin(0), scl=Pin(1), freq=400000)

class Data:
    def __init__(self, port=remote_port, host=remote_address, sid=wifi_sid, pwd=wifi_pswd):
        self.wifi_sid = sid
        self.wifi_pswd = pwd
        self.remote_port = port
        self.remote_address = host
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)
        self.i2c = i2c
        self.bme = BME280(i2c=self.i2c)
        
    def __enter__(self):
        Pin("LED", Pin.OUT).on()
        self.wifi_connect()
        return self
        
    def __exit__(self, exc_type, exc_value, exc_traceback):
        Pin("LED", Pin.OUT).off()
        Pin(2, Pin.OUT).off()
        Pin(3, Pin.OUT).off()
    
    def wifi_connect(self):
        try:
            self.wlan.connect(self.wifi_sid, self.wifi_pswd)
            print(f"* Local IP: {self.wlan.ifconfig()[0]}")
            Pin(2, Pin.OUT).on()
            return self.wlan
        except Exception as error:
            print(f'[*Exception] has been occured while connecting to wifi.: {error}')
            
    def remoteHost_send(self, dictionary: dict):
        try:
            sock = socket.socket()
            sock.connect((self.remote_address, self.remote_port))
            jsondata = json.dumps(dictionary)
            print(f"[*] Sending json to remote host {self.remote_address}:\n{jsondata}")
            Pin(3, Pin.OUT).on()
            sock.send(jsondata)
            sock.close()
            time.sleep(0.5)
            Pin(3, Pin.OUT).off()
        except Exception as error:
            print(f'[*Exception] has been occured while sending data to remote host.: {error}')
            
    def get_board_temperature(self) -> float:
        adc = ADC(4) 
        ADC_voltage = adc.read_u16() * (3.3 / (65536))
        return 27 - (ADC_voltage - 0.706)/0.001721
    
    def read_bme280(self):
        temp, press, hum = self.bme.values
        return {"temperature": temp,
                "pressure": press,
                "humidity": hum}
               
        
        
    
        
        
