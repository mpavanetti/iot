# The board's two ways to reach IoT Center: USB first, Wi-Fi as the fallback.
#
# A computer running IoT Center that reads the board's USB port writes a host line every few
# seconds (telemetry.parse_host_line). While those keep coming, readings go over USB and the
# Wi-Fi radio stays off. When they stop (unplugged, on a USB charger, server down), the board
# turns Wi-Fi on and sends to SERVER_HOST over TCP instead.

import select
import socket
import sys
import time

import machine
import network
import ntptime
from telemetry import Backoff, clock_is_set, iso8601, parse_host_line

CLOCK_SYNC_EVERY_MS = 3600 * 1000
SOCKET_TIMEOUT_S = 5  # well under the 8 s watchdog
HOST_TIMEOUT_MS = 15000  # the host writes every 5 s: three missed lines and USB counts as unread
MAX_LINES_PER_LOOP = 50  # deliver a backlog in slices, so buttons and the display stay responsive
MAX_HOST_LINE = 200

# Seconds from 1970 to this port's epoch (MicroPython ports use 1970 or 2000).
UNIX_TO_EPOCH_S = 946684800 if time.gmtime(0)[0] == 2000 else 0


def utc_now():
    """ISO-8601 UTC timestamp, or None until NTP or the USB host has set the clock (the
    server then uses its own arrival time)."""
    tm = time.gmtime()
    return iso8601(tm) if clock_is_set(tm) else None


def set_clock(unix_s):
    """Set the real-time clock to a Unix time (UTC), the way ntptime does."""
    tm = time.gmtime(int(unix_s) - UNIX_TO_EPOCH_S)
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3], tm[4], tm[5], 0))


class UsbLink:
    """The USB serial port: readings out as JSON lines, host lines in. Never blocks."""

    name = "usb"

    def __init__(self, feed, stream=None):
        self.feed = feed  # feeds the watchdog
        self.stream = stream or sys.stdin
        self.poller = select.poll()
        self.poller.register(self.stream, select.POLLIN)
        self.pending = ""
        self.last_heard = None

    def poll(self):
        """Read whatever the host has sent so far."""
        for _ in range(256):
            if not self.poller.poll(0):
                return
            char = self.stream.read(1)
            if not char:
                return
            if char == "\n":
                self.heard(parse_host_line(self.pending))
                self.pending = ""
            elif len(self.pending) < MAX_HOST_LINE:
                self.pending += char

    def heard(self, message):
        if message is None:
            return
        now = time.ticks_ms()
        if self.last_heard is None:
            print("[iot] USB host found: sending over USB, Wi-Fi off")
        self.last_heard = now
        # Follow the host's clock whenever ours is more than 2 s off (unset, or drifting).
        if "now" in message and abs(int(message["now"]) - UNIX_TO_EPOCH_S - time.time()) > 2:
            set_clock(message["now"])
            print("[iot] clock set by USB host:", iso8601(time.gmtime()))

    def host_present(self):
        if self.last_heard is None:
            return False
        if time.ticks_diff(time.ticks_ms(), self.last_heard) < HOST_TIMEOUT_MS:
            return True
        print("[iot] USB host gone")
        self.last_heard = None
        return False

    def deliver(self, outbox):
        """Print waiting readings, oldest first. Returns how many were sent."""
        sent = 0
        while len(outbox) and sent < MAX_LINES_PER_LOOP:
            self.feed()
            print(outbox.pop().decode().strip())
            sent += 1
        return sent


class WifiLink:
    """Wi-Fi, NTP and the TCP connection to the server. Nothing here blocks for long: Wi-Fi
    connects in the background, failed connections are retried with backoff, and the
    watchdog is fed before every network call that can wait."""

    name = "wifi"

    def __init__(self, config, feed):
        self.config = config
        self.feed = feed
        self.enabled = config.WIFI_ENABLED
        self.radio_on = False
        self.sock = None
        self.server = Backoff(time.ticks_diff)
        self.wifi_retry = Backoff(time.ticks_diff, initial_ms=15000, maximum_ms=60000)
        self.last_clock_sync = None
        self.state = "off"
        if not self.enabled:
            return
        if hasattr(network, "country"):
            network.country(config.WIFI_COUNTRY)
        else:  # MicroPython before 1.21
            import rp2

            rp2.country(config.WIFI_COUNTRY)
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(False)  # off until needed (a soft reset can leave it on)

    def start(self):
        """Radio on; joining the network happens in the background."""
        if not self.enabled or self.radio_on:
            return
        print("[iot] Wi-Fi on, joining", self.config.WIFI_SSID)
        self.wlan.active(True)
        if hasattr(network.WLAN, "PM_NONE"):
            self.wlan.config(pm=network.WLAN.PM_NONE)  # no Wi-Fi power saving: steadier links
        self.wlan.connect(self.config.WIFI_SSID, self.config.WIFI_PASSWORD)
        self.wifi_retry.failed(time.ticks_ms())  # give the first attempt time to finish
        self.radio_on = True
        self.state = "joining"

    def stop(self):
        """Close the connection and switch the radio off."""
        if not self.radio_on:
            return
        self.close()
        self.wlan.disconnect()
        self.wlan.active(False)
        self.radio_on = False
        self.wifi_retry.succeeded()
        self.state = "off"
        print("[iot] Wi-Fi off")

    def online(self):
        return self.radio_on and self.wlan.isconnected()

    def maintain(self):
        """Call every loop: re-join Wi-Fi if it dropped, sync the clock once an hour."""
        if not self.radio_on:
            return
        now = time.ticks_ms()
        if not self.wlan.isconnected():
            self.close()
            self.state = "joining"
            if self.wifi_retry.ready(now):
                print("[iot] joining Wi-Fi", self.config.WIFI_SSID)
                self.wlan.disconnect()
                self.wlan.connect(self.config.WIFI_SSID, self.config.WIFI_PASSWORD)
                self.wifi_retry.failed(now)
            return
        self.wifi_retry.succeeded()
        if (
            self.last_clock_sync is None
            or time.ticks_diff(now, self.last_clock_sync) > CLOCK_SYNC_EVERY_MS
        ):
            self.sync_clock(now)

    def sync_clock(self, now):
        self.feed()
        try:
            ntptime.host = self.config.NTP_HOST
            ntptime.timeout = 2
            ntptime.settime()  # sets the real-time clock to UTC
            print("[iot] clock synced:", iso8601(time.gmtime()))
            self.last_clock_sync = now
        except (OSError, OverflowError) as exc:
            print("[iot] clock sync failed:", exc)
            # retry in a minute rather than an hour
            self.last_clock_sync = time.ticks_add(now, 60000 - CLOCK_SYNC_EVERY_MS)

    def ip(self):
        return self.wlan.ifconfig()[0] if self.online() else None

    def rssi(self):
        try:
            return self.wlan.status("rssi") if self.online() else None
        except (OSError, ValueError):
            return None

    def deliver(self, outbox):
        """Send waiting readings, oldest first. Returns how many were sent."""
        if not self.online() or not len(outbox):
            return 0
        if self.sock is None and not self.connect():
            return 0
        sent = 0
        try:
            while len(outbox) and sent < MAX_LINES_PER_LOOP:
                self.feed()
                self.sock.sendall(outbox.peek())
                outbox.pop()  # only once it is safely handed to the network stack
                sent += 1
        except OSError as exc:
            print("[iot] connection lost:", exc)
            self.close()
            self.server.failed(time.ticks_ms())
            self.state = "reconnecting"
        return sent

    def connect(self):
        now = time.ticks_ms()
        if not self.server.ready(now):
            self.state = "retry %ds" % (self.server.wait_ms(now) // 1000 + 1)
            return False
        host, port = self.config.SERVER_HOST, self.config.SERVER_PORT
        self.feed()
        try:
            address = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1]
            sock = socket.socket()
            sock.settimeout(SOCKET_TIMEOUT_S)
            sock.connect(address)
        except OSError as exc:
            print("[iot] cannot reach %s:%s (%s)" % (host, port, exc))
            self.server.failed(now)
            self.state = "retry %ds" % (self.server.delay_ms // 1000)
            return False
        print("[iot] connected to %s:%s" % (host, port))
        self.sock = sock
        self.server.succeeded()
        self.state = "connected"
        return True

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
