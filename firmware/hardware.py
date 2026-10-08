# The Pico W's hardware: BME280 sensor, optional OLED, LEDs, buttons and board health.

import binascii
import gc
import os
import time

import machine


def device_id():
    """A stable, unique name from the flash chip's ID, e.g. 'pico-e6614103e7473b2a'."""
    return "pico-" + binascii.hexlify(machine.unique_id()).decode()


class Led:
    def __init__(self, pin):
        self.pin = None if pin is None else machine.Pin(pin, machine.Pin.OUT)

    def set(self, on):
        if self.pin is not None:
            self.pin.value(1 if on else 0)


class Button:
    """A push button wired between the GPIO and GND (internal pull-up: pressed reads 0)."""

    def __init__(self, pin):
        self.pin = None if pin is None else machine.Pin(pin, machine.Pin.IN, machine.Pin.PULL_UP)

    def pressed(self):
        return self.pin is not None and self.pin.value() == 0


class Board:
    def __init__(self, config):
        self.i2c = machine.I2C(
            config.I2C_ID,
            sda=machine.Pin(config.I2C_SDA),
            scl=machine.Pin(config.I2C_SCL),
            freq=400000,
        )
        from bme280 import BME280  # lib/bme280.py

        self.bme = BME280(i2c=self.i2c, address=config.BME280_ADDRESS)
        self.oled = None
        if config.OLED:
            try:
                from ssd1306 import SSD1306_I2C  # lib/ssd1306.py

                self.oled = SSD1306_I2C(128, 64, self.i2c)
            except OSError:
                print("[iot] no OLED display found, continuing without one")
        self.cpu_sensor = machine.ADC(getattr(machine.ADC, "CORE_TEMP", 4))
        self.led_connected = Led(config.LED_CONNECTED)
        self.led_sending = Led(config.LED_SENDING)
        self.led_problem = Led(config.LED_PROBLEM)
        self.button_start = Button(config.BUTTON_START)
        self.button_stop = Button(config.BUTTON_STOP)
        self.onboard_led = machine.Pin("LED", machine.Pin.OUT)
        # Once started, the RP2040 watchdog cannot be stopped: if the loop ever stalls for
        # 8 s (or the program is interrupted), the board reboots and starts over.
        self.watchdog = machine.WDT(timeout=8000) if config.WATCHDOG else None
        self.uptime_ms = 0
        self.last_tick = time.ticks_ms()

    def tick(self):
        """Call every loop: feeds the watchdog and keeps uptime (ticks_ms wraps every ~6 days,
        so uptime is accumulated in small steps)."""
        if self.watchdog:
            self.watchdog.feed()
        now = time.ticks_ms()
        self.uptime_ms += time.ticks_diff(now, self.last_tick)
        self.last_tick = now

    def environment(self):
        # The driver returns fixed-point integers: 0.01 °C, Pa * 256 and %RH * 1024.
        temperature, pressure, humidity = self.bme.read_compensated_data()
        return {
            "temperature_c": temperature / 100,
            "pressure_hpa": pressure / 25600,
            "humidity_pct": humidity / 1024,
        }

    def health(self):
        gc.collect()  # report steady-state memory, not garbage waiting to be collected
        fs = os.statvfs("/")
        return {
            "cpu_temp_c": round(self.cpu_temperature(), 2),
            "mem_free_bytes": gc.mem_free(),
            "mem_alloc_bytes": gc.mem_alloc(),
            "storage_free_kb": fs[0] * fs[3] / 1024,
            "cpu_freq_mhz": machine.freq() // 1000000,
            "uptime_s": self.uptime_ms // 1000,
        }

    def cpu_temperature(self):
        # RP2040 datasheet: 27 °C reads 0.706 V, and the slope is -1.721 mV per degree.
        volts = self.cpu_sensor.read_u16() * 3.3 / 65535
        return 27 - (volts - 0.706) / 0.001721

    def show(self, lines):
        """Up to 8 lines of 16 characters on the 128x64 OLED."""
        if self.oled is None:
            return
        self.oled.fill(0)
        for row, text in enumerate(lines[:8]):
            self.oled.text(text[:16], 0, row * 8)
        self.oled.show()

    def lights(self, connected, problem):
        self.led_connected.set(connected)
        self.led_problem.set(problem)

    def blink_sent(self):
        self.led_sending.set(True)
        self.onboard_led.toggle()
        time.sleep_ms(30)
        self.led_sending.set(False)
