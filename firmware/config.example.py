# Copy this file to config.py, fill in your Wi-Fi and server, and upload both to the Pico W.
# config.py is git-ignored, so your Wi-Fi password never ends up in the repository.

# --- Wi-Fi -------------------------------------------------------------------------------
WIFI_ENABLED = True  # False: USB-only mode (pair with `iotcenter lite --serial ...`)
WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"
WIFI_COUNTRY = "CA"  # two-letter country code for the radio's regulatory settings

# --- Where readings go ----------------------------------------------------------------------
SERVER_HOST = "192.168.1.80"  # the machine running IoT Center (Lite, or the platform gateway)
SERVER_PORT = 1500
USB_OUTPUT = True  # also print every reading as a JSON line on USB serial

# --- Behaviour -----------------------------------------------------------------------------
DEVICE_NAME = "living-room"  # friendly name shown on the dashboards (optional)
INTERVAL_S = 2  # seconds between readings
START_STREAMING = True  # stream right after boot (button 1/2 resume/pause anyway)
BUFFER_SIZE = 300  # readings kept while the server is unreachable (~10 min at 2 s)
NTP_HOST = "pool.ntp.org"  # the clock is synced at boot and every hour
WATCHDOG = True  # reboot automatically if the program ever hangs (> 8 s)

# --- Wiring (GPIO numbers; None disables a part) --------------------------------------------
I2C_ID = 0  # BME280 and SSD1306 share one I2C bus
I2C_SDA = 0
I2C_SCL = 1
BME280_ADDRESS = 0x76  # 0x77 on some boards
OLED = True  # 128x64 SSD1306 at address 0x3C; False if not fitted
BUTTON_START = 7  # resume streaming (to GND, internal pull-up)
BUTTON_STOP = 8  # pause streaming
LED_CONNECTED = 2  # on while connected to the server
LED_SENDING = 3  # flashes on every delivery
LED_PROBLEM = 15  # on while Wi-Fi or the server is unreachable
