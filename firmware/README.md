# Firmware (Raspberry Pi Pico W, MicroPython)

Every `INTERVAL_S` seconds the board reads the BME280, builds one JSON line ([the contract](../docs/protocol.md)),
prints it on USB serial and sends it to IoT Center over Wi-Fi. While the server is unreachable, readings wait
in a bounded buffer and are delivered in order when the connection comes back. A hardware watchdog reboots the
board if the program ever hangs.

| File | What it does |
|---|---|
| `main.py` | the loop: read, build, send, show. Runs at boot |
| `telemetry.py` | pure logic: message building, the outbox buffer, backoff (unit-tested on CPython) |
| `link.py` | Wi-Fi, NTP clock sync and the TCP connection, with reconnects |
| `hardware.py` | the sensor, display, LEDs, buttons and board health |
| `config.example.py` | settings: copy to `config.py` |
| `lib/bme280.py`, `lib/ssd1306.py` | sensor and display drivers (MIT, vendored) |

Wiring and parts: [docs/hardware.md](../docs/hardware.md).

## 1. Flash MicroPython

1. Download the latest MicroPython `.uf2` for your board: [Pico W](https://micropython.org/download/RPI_PICO_W/)
   or [Pico 2 W](https://micropython.org/download/RPI_PICO2_W/).
2. Hold **BOOTSEL** while plugging the board into USB. It appears as a drive named `RPI-RP2`.
3. Copy the `.uf2` onto it. The board reboots into MicroPython.

## 2. Configure

```bash
cp firmware/config.example.py firmware/config.py
```

Edit `config.py`: at least `WIFI_SSID`, `WIFI_PASSWORD`, `WIFI_COUNTRY` and `SERVER_HOST` (the IP of the machine
running IoT Center Lite or the platform gateway; port 1500 for both). `config.py` is git-ignored.

## 3. Upload

With [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html) (`pip install mpremote`):

```bash
cd firmware
mpremote cp -r lib :
mpremote cp config.py main.py telemetry.py link.py hardware.py :
mpremote reset
mpremote          # optional: watch the log (Ctrl-] to leave)
```

Or with [Thonny](https://thonny.org): select the *MicroPython (Raspberry Pi Pico)* interpreter, then upload the
same files (keep `lib/` as a folder) through the Files panel.

After a reset you should see:

```
[iot] pico-e6614103e7473b2a (living-room) -> 192.168.1.80:1500
[iot] clock synced: 2026-10-08T04:19:53Z
[iot] connected to 192.168.1.80:1500
{"v": 2, "device_id": "pico-e6614103e7473b2a", "seq": 0, "ts": "2026-10-08T04:19:55Z", ...}
```

## USB-only mode (no Wi-Fi)

Set `WIFI_ENABLED = False` and keep `USB_OUTPUT = True`: the board prints its readings over USB and nothing else.
Plug it into the machine running IoT Center Lite:

```bash
iotcenter ports                          # finds the board, e.g. /dev/ttyACM0 (Linux), /dev/cu.usbmodem101 (macOS), COM3 (Windows)
iotcenter lite --serial /dev/ttyACM0
```

On Linux, your user needs access to serial ports once: `sudo usermod -aG dialout $USER`, then log in again.
Without NTP the board's clock is not set, so it sends `ts: null` and IoT Center timestamps readings on arrival.

## While developing

- Set `WATCHDOG = False`. Once enabled, the RP2040 watchdog cannot be stopped, so stopping the program
  (Ctrl-C, or Stop in Thonny) reboots the board 8 seconds later.
- The pure logic runs on your computer: `pytest tests/test_firmware.py` also runs `main.py` end to end on
  CPython, with fake hardware, against a real ingest server.
- `python simulator/simulate_picow.py` produces the same messages without any hardware.
