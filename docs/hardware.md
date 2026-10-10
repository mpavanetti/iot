# Hardware

![The Pico W on a Freenove breadboard kit with a BME280 sensor and an SSD1306 display](img/hardware/board.jpg)

## Parts

| Part | Why | Notes |
|---|---|---|
| **Raspberry Pi Pico W** ([docs](https://www.raspberrypi.com/documentation/microcontrollers/raspberry-pi-pico.html), [datasheet](https://datasheets.raspberrypi.com/picow/pico-w-datasheet.pdf)) | RP2040 + Wi-Fi, runs MicroPython | A **Pico 2 W** works too (flash the `RPI_PICO2_W` build) |
| **BME280** breakout, e.g. GY-BME280 3.3 V ([datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf), [Amazon.ca](https://www.amazon.ca/dp/B0BQFV883T)) | temperature, humidity and pressure over I2C | Make sure it is a BME280 (humidity), not a BMP280 |
| **SSD1306** 0.96" 128x64 I2C OLED ([Amazon](https://www.amazon.com/dp/B06XRBYJR8)) | shows readings and connection state | Optional: set `OLED = False` without it |
| **Breadboard kit** with buttons and LEDs, e.g. Freenove Pico breadboard kit ([Amazon.ca](https://www.amazon.ca/dp/B0BJ1PGZCX)) | quick wiring | Optional: any breadboard, two buttons and three LEDs with resistors |
| Micro-USB cable **with data lines** | power, flashing, USB mode | Charge-only cables are a classic source of "the board does not show up" |

| | | | |
|---|---|---|---|
| <img src="img/hardware/picow.jpg" alt="Raspberry Pi Pico W" width="160"> | <img src="img/hardware/bme280.jpg" alt="BME280 breakout" width="160"> | <img src="img/hardware/ssd1306.jpg" alt="SSD1306 OLED" width="160"> | <img src="img/hardware/breadboard.jpg" alt="Pico breadboard kit" width="160"> |
| Pico W | BME280 | SSD1306 | Breadboard kit |

## Wiring

The BME280 and the display share one I2C bus. Every pin can be changed in `config.py`; `None` disables a part.

| From | To Pico W | Physical pin | `config.py` |
|---|---|---|---|
| BME280 VIN, SSD1306 VCC | 3V3(OUT) | 36 | |
| BME280 GND, SSD1306 GND | GND | 38 | |
| BME280 SDA, SSD1306 SDA | GP0 (I2C0 SDA) | 1 | `I2C_SDA = 0` |
| BME280 SCL, SSD1306 SCL | GP1 (I2C0 SCL) | 2 | `I2C_SCL = 1` |
| Button 1 (pause / resume), other leg to GND | GP7 | 10 | `BUTTON_PAUSE = 7` |
| Button 2 (display page), other leg to GND | GP8 | 11 | `BUTTON_PAGE = 8` |
| LED "connected" (+ resistor to GND) | GP2 | 4 | `LED_CONNECTED = 2` |
| LED "sending" | GP3 | 5 | `LED_SENDING = 3` |
| LED "problem" | GP15 | 20 | `LED_PROBLEM = 15` |

Power the sensors from **3V3, not VBUS** (5 V). The buttons need no resistor: the firmware enables the internal pull-ups.
The BME280's address is 0x76 on most breakouts; if yours uses 0x77, set `BME280_ADDRESS = 0x77`.

## What the board shows

| Signal | Meaning |
|---|---|
| Onboard LED | toggles on every delivery |
| LED "connected" (GP2) | on while a USB host is heard, or while connected to the server over Wi-Fi |
| LED "sending" (GP3) | flashes on every delivery |
| LED "problem" (GP15) | on while on Wi-Fi and the server is unreachable |
| Button 1 | pause / resume streaming. Streaming starts by itself at boot; while paused the board keeps reading the sensor and the display stays live |
| Button 2 | switch the display between the readings and the details page |

The display has an inverted title bar that always says which link the readings take, then one of two pages
(button 2 switches). The readings page:

```
USB    connected      title: the link and its state (see below)
     21.1C            temperature, double size
47%RH   899.2hPa      humidity and (station) pressure
dew 9.3C      ok      dew point and indoor comfort: dry (< 30 %), ok, humid (> 60 %)
sent 1234 wait 0      readings delivered since boot, and waiting to be sent; "PAUSED: press 1" when paused
```

The details page:

```
USB    connected
over USB              or the board's IP on Wi-Fi
Wi-Fi radio off       or "to <SERVER_HOST>"
17:46:12 UTC          the board's clock, or "clock not set"
up 3h05m              time since boot
v2.1.0  watchdog      firmware, and why it last started: "power on", or "watchdog" (also after a crash or reset)
```

| Title bar | Meaning |
|---|---|
| `USB    connected` | IoT Center is reading the USB port: readings go over USB, Wi-Fi is off |
| `USB?   listening` | the first 8 s after boot: waiting to hear from a USB host before trying Wi-Fi |
| `Wi-Fi    -61 dBm` | sending over Wi-Fi to the server (signal strength) |
| `Wi-Fi    joining` / `retry 4s` / `reconnecting` | on Wi-Fi, but not (yet) connected to the server |
| `USB      no host` | `WIFI_ENABLED = False` and nothing has been heard on USB yet |

## Readings you can expect

- **Board temperature** (`cpu_temp_c`) reads several degrees above the air: the RP2040 warms itself. The BME280
  also warms slightly if it sits right next to the Pico W, so give it a few centimetres of wire for room-accurate
  readings.
- **Pressure** is station pressure, so it depends on altitude: about 1013 hPa at sea level, about 888 hPa at
  1,045 m. Weather moves it by about ±15 hPa. Set `ALTITUDE_M` (or `IOT_ALTITUDE_M`) and the
  dashboard also shows sea-level pressure, the figure weather reports quote. After 3 hours of data it also shows
  the pressure tendency (rising, steady, falling) with the barometer's rule-of-thumb outlook.
- **Dew point** is computed from temperature and humidity; it is what to watch for condensation (a garage or
  basement surface colder than the dew point gets wet).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `OSError: [Errno 5] EIO` or `ENODEV` at start | sensor not found: check SDA/SCL (not swapped), 3V3 and GND, or set `BME280_ADDRESS = 0x77`. In the REPL, `machine.I2C(0, sda=machine.Pin(0), scl=machine.Pin(1)).scan()` should list `0x76` (118) and `0x3c` (60) |
| Humidity is always missing or 0 | it is a BMP280, which has no humidity sensor |
| `no OLED display found` in the log | the display is not at 0x3C or not wired; the firmware carries on without it |
| Plugged into the IoT Center machine, but the title says `Wi-Fi` | IoT Center is not reading that port: check `iotcenter ports`, `--serial` / `IOT_SERIAL_PORT`, and (Docker) the USB overlay. The dashboard's Pipeline page shows the USB reader's state |
| Stuck on `Wi-Fi    joining` | wrong SSID or password; the Pico W only supports 2.4 GHz networks; check `WIFI_COUNTRY` |
| `Wi-Fi    retry ...` | the server is not reachable: check `SERVER_HOST`/`SERVER_PORT`, that IoT Center is running, and the host firewall (`sudo ufw allow 1500/tcp`) |
| `sensor error` on the display | the BME280 stopped answering (a loose wire): the board keeps trying every reading |
| The board reboots every few seconds | the watchdog or a crash: read the log over USB (`mpremote`); `WATCHDOG = False` rules out the watchdog |
