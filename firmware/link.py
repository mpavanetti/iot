# Networking: Wi-Fi, the clock (NTP) and the TCP connection to the IoT Center server.

import socket
import time

import network
import ntptime
import rp2
from telemetry import Backoff, clock_is_set, iso8601

CLOCK_SYNC_EVERY_MS = 3600 * 1000
SOCKET_TIMEOUT_S = 5  # well under the 8 s watchdog


class Link:
    """Keeps Wi-Fi and the server connection up. Nothing here blocks for long: Wi-Fi
    connects in the background, and failed connections are retried with backoff."""

    def __init__(self, config):
        self.config = config
        self.enabled = config.WIFI_ENABLED
        self.sock = None
        self.server = Backoff(time.ticks_diff)
        self.wifi_retry = Backoff(time.ticks_diff, initial_ms=15000, maximum_ms=60000)
        self.last_clock_sync = None
        self.state = "USB only" if not self.enabled else "Wi-Fi..."
        if not self.enabled:
            return
        rp2.country(config.WIFI_COUNTRY)
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)
        if hasattr(network.WLAN, "PM_NONE"):
            self.wlan.config(pm=network.WLAN.PM_NONE)  # no Wi-Fi power saving: steadier links
        self.wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)  # returns immediately
        self.wifi_retry.failed(time.ticks_ms())  # give the first attempt time to finish

    def online(self):
        return self.enabled and self.wlan.isconnected()

    def maintain(self):
        """Call every loop: re-join Wi-Fi if it dropped, sync the clock once an hour."""
        if not self.enabled:
            return
        now = time.ticks_ms()
        if not self.wlan.isconnected():
            self.close()
            self.state = "Wi-Fi..."
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

    def utc_now(self):
        """ISO-8601 UTC timestamp, or None until NTP has set the clock (the server then
        uses its own arrival time)."""
        tm = time.gmtime()
        return iso8601(tm) if clock_is_set(tm) else None

    def ip(self):
        return self.wlan.ifconfig()[0] if self.online() else None

    def rssi(self):
        try:
            return self.wlan.status("rssi") if self.online() else None
        except (OSError, ValueError):
            return None

    def deliver(self, outbox):
        """Send everything waiting in the outbox, oldest first. Returns how many were sent."""
        if not self.online() or not len(outbox):
            return 0
        if self.sock is None and not self.connect():
            return 0
        sent = 0
        try:
            while len(outbox):
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
            self.state = "retry in %ds" % (self.server.wait_ms(now) // 1000 + 1)
            return False
        host, port = self.config.SERVER_HOST, self.config.SERVER_PORT
        try:
            address = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1]
            sock = socket.socket()
            sock.settimeout(SOCKET_TIMEOUT_S)
            sock.connect(address)
        except OSError as exc:
            print("[iot] cannot reach %s:%s (%s)" % (host, port, exc))
            self.server.failed(now)
            self.state = "retry in %ds" % (self.server.delay_ms // 1000)
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
