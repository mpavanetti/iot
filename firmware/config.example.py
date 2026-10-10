# Copy this file to config.py, fill in your Wi-Fi and server, and upload both to the Pico W.
# config.py is git-ignored, so your Wi-Fi password never ends up in the repository.

# --- Links: USB first, Wi-Fi as the fallback -----------------------------------------------
# While a computer running IoT Center reads the board's USB port, readings go over USB and the
# Wi-Fi radio stays off. Otherwise (unplugged, on a USB charger, server down) the board joins
# Wi-Fi and sends them to SERVER_HOST below.
WIFI_ENABLED = True  # False: USB only, the radio never turns on
WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"
WIFI_COUNTRY = "CA"  # two-letter country code for the radio's regulatory settings

# --- Where readings go ----------------------------------------------------------------------
SERVER_HOST = "192.168.1.80"  # the machine running IoT Center (Lite, or the platform gateway)
SERVER_PORT = 1500
USB_OUTPUT = True  # while on Wi-Fi, also print each reading on USB (to watch with mpremote)

# --- Behaviour -----------------------------------------------------------------------------
DEVICE_NAME = "living-room"  # friendly name shown on the dashboards (optional)
INTERVAL_S = 2  # seconds between readings
BUFFER_SIZE = 300  # readings kept while no link is up (~10 min at 2 s)
NTP_HOST = "pool.ntp.org"  # on Wi-Fi, the clock is synced hourly (on USB, the host sets it)
WATCHDOG = True  # reboot automatically if the program ever hangs (> 8 s); Ctrl-C turns it off

# --- Wiring (GPIO numbers; None disables a part) --------------------------------------------
I2C_ID = 0  # BME280 and SSD1306 share one I2C bus
I2C_SDA = 0
I2C_SCL = 1
BME280_ADDRESS = 0x76  # 0x77 on some boards
OLED = True  # 128x64 SSD1306 at address 0x3C; False if not fitted
BUTTON_PAUSE = 7  # pause / resume (to GND, internal pull-up); streaming starts by itself
BUTTON_PAGE = 8  # switch the display between readings and details
LED_CONNECTED = 2  # on while connected to the server
LED_SENDING = 3  # flashes on every delivery
LED_PROBLEM = 15  # on while Wi-Fi or the server is unreachable
