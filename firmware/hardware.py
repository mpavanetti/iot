# The Pico W's hardware: BME280 sensor, optional OLED, LEDs, buttons and board health.

import binascii
import gc
import os
import sys
import time

import framebuf
import machine

# The watchdog's CTRL register and its ENABLE bit (RP2040 / RP2350 datasheets, "Watchdog").
WATCHDOG_CTRL = {"RP2040": 0x40058000, "RP2350": 0x400D8000}
WATCHDOG_ENABLE = 1 << 30


def device_id():
    """A stable, unique name from the flash chip's ID, e.g. 'pico-e6614103e7473b2a'."""
    return "pico-" + binascii.hexlify(machine.unique_id()).decode()


def boot_reason():
    """Why the board last started: 'power on', or 'watchdog' (a hang, or machine.reset()
    after a crash: on the RP2040 both go through the watchdog)."""
    if not hasattr(machine, "reset_cause"):
        return "unknown"
    cause = machine.reset_cause()
    if cause == getattr(machine, "PWRON_RESET", -1):
        return "power on"
    if cause == getattr(machine, "WDT_RESET", -1):
        return "watchdog"
    return "reset"


def stop_watchdog():
    """Turn the hardware watchdog off. MicroPython has no API for that once it is started,
    but the chip has a register bit. Used after Ctrl-C, so the REPL and file uploads are not
    cut short by a reboot. Returns False on a chip it does not know."""
    for chip, address in WATCHDOG_CTRL.items():
        if chip in sys.implementation._machine:
            machine.mem32[address] &= ~WATCHDOG_ENABLE
            return True
    return False


class Led:
    def __init__(self, pin):
        self.pin = None if pin is None else machine.Pin(pin, machine.Pin.OUT)

    def set(self, on):
        if self.pin is not None:
            self.pin.value(1 if on else 0)


class Button:
    """A push button wired between the GPIO and GND (internal pull-up: pressed reads 0)."""

    DEBOUNCE_MS = 50

    def __init__(self, pin):
        self.pin = None if pin is None else machine.Pin(pin, machine.Pin.IN, machine.Pin.PULL_UP)
        self.down = False
        self.changed = time.ticks_ms()

    def clicked(self):
        """True once per press, however long the button is held (contacts bounce: changes
        within DEBOUNCE_MS of the last one are ignored)."""
        if self.pin is None:
            return False
        down = self.pin.value() == 0
        now = time.ticks_ms()
        if down == self.down or time.ticks_diff(now, self.changed) < self.DEBOUNCE_MS:
            return False
        self.down, self.changed = down, now
        return down


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
        self.bme.read_compensated_data()  # the first conversion after power-up reads high
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
        self.button_pause = Button(config.BUTTON_PAUSE)
        self.button_page = Button(config.BUTTON_PAGE)
        self.onboard_led = machine.Pin("LED", machine.Pin.OUT)
        # Once started, the RP2040 watchdog cannot be stopped: if the loop ever stalls for
        # 8 s (or the program is interrupted), the board reboots and starts over.
        self.watchdog = machine.WDT(timeout=8000) if config.WATCHDOG else None
        self.uptime_ms = 0
        self.last_tick = time.ticks_ms()
        self.boot_reason = boot_reason()

    def feed(self):
        if self.watchdog:
            self.watchdog.feed()

    def tick(self):
        """Call every loop: feeds the watchdog and keeps uptime (ticks_ms wraps every ~6 days,
        so uptime is accumulated in small steps)."""
        self.feed()
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

    def show(self, title, big=None, lines=()):
        """One screen on the 128x64 OLED: an inverted title bar, an optional line at double
        size, then lines of 16 characters (3 below a big line, 5 without one)."""
        oled = self.oled
        if oled is None:
            return
        oled.fill(0)
        oled.fill_rect(0, 0, 128, 10, 1)
        oled.text(title[:16], 0, 1, 0)
        y = 14
        if big:
            self.big_text(big[:8], 13)
            y = 33
        for text in lines:
            if y > 56:
                break
            oled.text(text[:16], 0, y)
            y += 10
        oled.show()

    def big_text(self, text, y):
        """Centered text at twice the font size: drawn small off-screen, then every pixel
        is copied as a 2x2 block."""
        width = len(text) * 8
        small = framebuf.FrameBuffer(bytearray(width), width, 8, framebuf.MONO_VLSB)
        small.text(text, 0, 0, 1)
        x = (128 - 2 * width) // 2
        for px in range(width):
            for py in range(8):
                if small.pixel(px, py):
                    self.oled.fill_rect(x + 2 * px, y + 2 * py, 2, 2, 1)

    def lights(self, connected, problem):
        self.led_connected.set(connected)
        self.led_problem.set(problem)

    def blink_sent(self):
        self.led_sending.set(True)
        self.onboard_led.toggle()
        time.sleep_ms(30)
        self.led_sending.set(False)
