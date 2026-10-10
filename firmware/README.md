# Firmware (Raspberry Pi Pico W, MicroPython)

Every `INTERVAL_S` seconds the board reads the BME280, builds one JSON line ([the contract](../docs/protocol.md))
and sends it to IoT Center. **USB comes first**: while a computer running IoT Center reads the board's USB port,
readings go over USB and the Wi-Fi radio stays off. Unplugged from that computer (or powered by a plain USB
charger, or with the server down), the board joins Wi-Fi and sends to `SERVER_HOST` over TCP instead. While no
link is up, readings wait in a bounded buffer and are delivered in order when one comes back. A hardware
watchdog reboots the board if the program ever hangs.

| File | What it does |
|---|---|
| `main.py` | the loop: read, build, pick the link, send, show. Runs at boot |
| `telemetry.py` | pure logic: messages, the outbox buffer, backoff, host lines, display text (unit-tested on CPython) |
| `link.py` | the two links: USB (host lines in, readings out) and Wi-Fi (NTP and the TCP connection) |
| `hardware.py` | the sensor, display, LEDs, buttons, board health and the watchdog |
| `config.example.py` | settings: copy to `config.py` |
| `lib/bme280.py`, `lib/ssd1306.py` | sensor and display drivers (MIT, vendored) |

Wiring, buttons and what the display shows: [docs/hardware.md](../docs/hardware.md).

## How the board picks a link

IoT Center (Lite or the platform gateway) writes a **host line** to the board's USB port every 5 seconds while
it reads that port: `#iot {"now": 1791480000}` (see [the protocol](../docs/protocol.md#usb-host-lines)).

| The board... | Link | Wi-Fi radio | Clock set by |
|---|---|---|---|
| has heard a host line in the last 15 s | USB | off | the host (whenever it is more than 2 s off) |
| has not, for the first 8 s after boot | none yet: readings wait | off | |
| has not (or stopped hearing them) | Wi-Fi, TCP to `SERVER_HOST` | on | NTP, hourly |
| has `WIFI_ENABLED = False` | USB | never on | the host |

Switching is automatic in both directions and nothing is sent twice: readings waiting for Wi-Fi are drained
over USB as soon as a host appears. On the dashboard, a board's *Connection* shows `USB serial` or `Wi-Fi (TCP)`.

## 1. Flash MicroPython

MicroPython 1.29 or later (anything from 1.21 works).

1. Download the latest `.uf2` for your board: [Pico W](https://micropython.org/download/RPI_PICO_W/)
   or [Pico 2 W](https://micropython.org/download/RPI_PICO2_W/).
2. Hold **BOOTSEL** while plugging the board into USB. It appears as a drive named `RPI-RP2`.
   (A board already running MicroPython can also be sent there with `mpremote bootloader`.)
3. Copy the `.uf2` onto it. The board reboots into MicroPython. Files you uploaded before are kept.

Why MicroPython and not CircuitPython: MicroPython is what Raspberry Pi documents for the Pico, its `network`,
`machine.WDT` and `ntptime` modules are what this firmware uses, and it shows up on USB as a plain serial port
only. CircuitPython also presents a USB drive and, with Adafruit's HID library, a keyboard, which is what
"rubber ducky" keystroke-injection tools are built on and why some corporate antivirus tools flag it.

## 2. Configure

```bash
cp firmware/config.example.py firmware/config.py
```

Edit `config.py`: `WIFI_SSID`, `WIFI_PASSWORD`, `WIFI_COUNTRY` and `SERVER_HOST` (the IP of the machine running
IoT Center; port 1500 for both editions). Wi-Fi is only the fallback, but without it a board away from its host
has nowhere to send. `config.py` is git-ignored.

## 3. Upload

With [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html) (installed by `make install`):

```bash
make firmware     # pauses IoT Center Lite in Docker while it runs, since both need the USB port
```

or by hand:

```bash
cd firmware
mpremote cp -r lib : + cp config.py main.py telemetry.py link.py hardware.py : + reset
mpremote          # optional: watch the log (Ctrl-] to leave)
```

On Linux, your user needs access to serial ports once: `sudo usermod -aG dialout $USER`, then log in again.
Only one program can use the port at a time: stop IoT Center (or anything else reading it) before `mpremote`.

Or with [Thonny](https://thonny.org): select the *MicroPython (Raspberry Pi Pico)* interpreter, then upload the
same files (keep `lib/` as a folder) through the Files panel.

After a reset, with IoT Center reading the port, the log shows:

```
[iot] pico-e6614c311b7a1234 (pico-w): USB first, then Wi-Fi to 192.168.1.50:1500
[iot] USB host found: sending over USB, Wi-Fi off
[iot] clock set by USB host: 2026-10-08T17:46:12Z
{"v": 2, "device_id": "pico-e6614c311b7a1234", "seq": 0, "ts": "2026-10-08T17:46:12Z", ...}
```

and without a host:

```
[iot] Wi-Fi on, joining Home
[iot] clock synced: 2026-10-08T17:41:02Z
[iot] connected to 192.168.1.50:1500
```

## Reading a board over USB

```bash
iotcenter ports                          # finds the board, e.g. /dev/ttyACM0 (Linux), /dev/cu.usbmodem101 (macOS), COM3 (Windows)
iotcenter lite --serial /dev/ttyACM0
```

In Docker, use the USB overlay of [IoT Center Lite](../lite/README.md#run-it-with-docker). The port's number
can change when the board reboots (`ttyACM0` becomes `ttyACM1`); `/dev/serial/by-id/usb-MicroPython_…` does not.

## While developing

- **Ctrl-C turns the watchdog off.** MicroPython cannot stop the RP2040's watchdog once started, but the chip
  can, so the firmware clears its enable bit when you stop it (Ctrl-C, Stop in Thonny, or `mpremote`
  connecting). The REPL and uploads then work without the board rebooting under you. `WATCHDOG = False` skips
  the watchdog altogether.
- The pure logic runs on your computer: `pytest tests/test_firmware.py` also runs `main.py` end to end on
  CPython, with fake hardware, against a real ingest server, over both links.
- `python simulator/simulate_picow.py` produces the same messages without any hardware.
