# IoT Center firmware for the Raspberry Pi Pico W (MicroPython). Runs at boot.
#
# Every INTERVAL_S seconds: read the BME280, build one JSON line and send it to IoT Center.
# USB comes first: while a computer running IoT Center reads the board's USB port, readings
# go over USB and Wi-Fi stays off. Otherwise they go over Wi-Fi to SERVER_HOST. While no link
# is up, readings wait in a bounded outbox and are delivered in order once one is back.
# Settings live in config.py (copy config.example.py). Wiring: docs/hardware.md.

import gc
import time

import machine
from hardware import Board, device_id, stop_watchdog
from link import UsbLink, WifiLink, utc_now
from telemetry import (
    FIRMWARE_VERSION,
    Outbox,
    bar,
    build_message,
    comfort,
    dew_point,
    duration,
    encode,
)

# At boot, listen this long for a USB host before turning Wi-Fi on (it writes every 5 s).
USB_FIRST_MS = 8000


def run(config, max_readings=None, usb_stream=None):
    """The main loop. `max_readings` and `usb_stream` are for the tests; on the board it runs
    forever and listens to the host on sys.stdin."""
    board = Board(config)
    board.show("IoT Center", None, ["", "starting..."])
    usb = UsbLink(board.feed, usb_stream)
    wifi = WifiLink(config, board.feed)
    outbox = Outbox(config.BUFFER_SIZE)
    identity = device_id()
    print(
        "[iot] %s (%s): USB first, then Wi-Fi to %s:%s"
        % (identity, config.DEVICE_NAME, config.SERVER_HOST, config.SERVER_PORT)
    )

    seq = 0  # restarts at 0 on every boot: the server counts gaps and restarts from it
    sent = 0
    sensor_errors = 0
    # How hard the loop works between two readings: time not spent in its idle sleep, and the
    # longest single pass (a stall detector). MicroPython runs this loop on one core, so this
    # is the board's CPU usage; the second core is unused.
    busy_us = loop_max_us = 0
    window_start = time.ticks_us()
    streaming = True  # from boot; the pause button pauses and resumes
    page = 0  # the display: 0 readings, 1 details (the page button switches)
    environment = None
    interval_ms = int(config.INTERVAL_S * 1000)
    booted = next_reading = time.ticks_ms()

    while max_readings is None or seq < max_readings:
        pass_start = time.ticks_us()
        board.tick()
        usb.poll()
        now = time.ticks_ms()
        if usb.host_present() or not wifi.enabled:
            link = usb
            wifi.stop()
        elif time.ticks_diff(now, booted) < USB_FIRST_MS:
            link = None  # still listening for a USB host: readings wait in the outbox
        else:
            link = wifi
            wifi.start()
        wifi.maintain()
        redraw = False
        if board.button_pause.clicked():
            streaming = not streaming
            print("[iot] streaming" if streaming else "[iot] paused")
            redraw = True
        if board.button_page.clicked():
            page = 1 - page
            redraw = True

        if time.ticks_diff(now, next_reading) >= 0:
            # Schedule the next reading; if we fell far behind (e.g. a slow reconnect),
            # skip ahead instead of firing a burst of catch-up readings.
            next_reading = time.ticks_add(next_reading, interval_ms)
            if time.ticks_diff(now, next_reading) > 0:
                next_reading = time.ticks_add(now, interval_ms)

            try:
                environment = board.environment()
            except OSError as exc:  # a loose wire or an I2C glitch: try again next time
                print("[iot] sensor read failed:", exc)
                sensor_errors += 1
                environment = None
            window_us = time.ticks_diff(time.ticks_us(), window_start)
            cpu_busy_pct = round(min(100, 100 * busy_us / window_us), 1) if window_us > 0 else None
            loop_max_ms = loop_max_us // 1000
            busy_us = loop_max_us = 0
            window_start = time.ticks_us()
            if environment is not None:
                health = board.health()
                health["wifi_rssi_dbm"] = wifi.rssi()
                health["ip"] = wifi.ip()
                health["cpu_busy_pct"] = cpu_busy_pct
                health["loop_max_ms"] = loop_max_ms
                health["sensor_errors"] = sensor_errors
                health["boot_reason"] = board.boot_reason
                message = build_message(
                    identity, config.DEVICE_NAME, seq, utc_now(), environment, health
                )
                seq += 1
                if streaming:
                    line = encode(message)
                    outbox.push(line)
                    if link is wifi and config.USB_OUTPUT:
                        print(line.decode().strip())  # also on USB, to watch with mpremote
            redraw = True

        if link is not None:
            delivered = link.deliver(outbox)
            if delivered:
                sent += delivered
                board.blink_sent()

        if redraw:
            board.lights(
                connected=usb.last_heard is not None or wifi.sock is not None,
                problem=link is wifi and wifi.sock is None,
            )
            title = title_bar(link, usb, wifi)
            if page == 0:
                board.show(title, *readings_page(environment, outbox, sent, streaming))
            else:
                board.show(title, None, details_page(config, board, link, wifi))
            gc.collect()

        work_us = time.ticks_diff(time.ticks_us(), pass_start)
        busy_us += work_us
        loop_max_us = max(loop_max_us, work_us)
        time.sleep_ms(20)
    wifi.close()


# --- The display: 16 characters per line ----------------------------------------------------


def title_bar(link, usb, wifi):
    """Which link the readings take, and how it is doing."""
    if link is usb:
        return bar("USB", "connected" if usb.last_heard is not None else "no host")
    if link is wifi:
        rssi = wifi.rssi()
        if wifi.sock is not None and rssi is not None:
            return bar("Wi-Fi", "%d dBm" % rssi)
        return bar("Wi-Fi", wifi.state)
    return bar("USB?", "listening")


def readings_page(environment, outbox, sent, streaming):
    """(big, lines) for Board.show: the temperature at double size, then the rest."""
    if environment is None:
        big, lines = "--.-C", ["sensor error", "check the wiring"]
    else:
        temperature, humidity = environment["temperature_c"], environment["humidity_pct"]
        big = "%.1fC" % temperature
        lines = [
            bar("%.0f%%RH" % humidity, "%.1fhPa" % environment["pressure_hpa"]),
            bar("dew %.1fC" % dew_point(temperature, humidity), comfort(humidity)),
        ]
    if streaming:
        lines.append(bar("sent %d" % sent, "wait %d" % len(outbox) if len(outbox) else ""))
    else:
        lines.append("PAUSED: press 1")
    return big, lines


def details_page(config, board, link, wifi):
    if link is wifi:
        where = [wifi.ip() or "no IP yet", "to " + config.SERVER_HOST]
    elif link is None:
        where = ["looking for a", "USB host first"]
    else:
        where = ["over USB", "Wi-Fi radio off" if wifi.enabled else "Wi-Fi disabled"]
    now = utc_now()
    return where + [
        now[11:19] + " UTC" if now else "clock not set",
        "up " + duration(board.uptime_ms // 1000),
        bar("v" + FIRMWARE_VERSION, board.boot_reason),  # why it last started
    ]


if __name__ == "__main__":
    import config

    try:
        run(config)
    except KeyboardInterrupt:  # Ctrl-C, Stop in Thonny, or mpremote connecting
        if stop_watchdog():
            print("[iot] stopped; watchdog off, so the REPL and uploads are safe")
        else:
            print("[iot] stopped")
    except Exception as exc:  # never sit dead in a cupboard: log, wait, start over
        print("[iot] crashed:", exc)
        time.sleep(5)
        machine.reset()
