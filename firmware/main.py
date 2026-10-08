# IoT Center firmware for the Raspberry Pi Pico W (MicroPython). Runs at boot.
#
# Every INTERVAL_S seconds: read the BME280, build one JSON line, print it on USB serial and
# send it to the server over Wi-Fi. While the server is unreachable, readings wait in a
# bounded outbox and are delivered in order once the connection is back.
# Settings live in config.py (copy config.example.py). Wiring: docs/hardware.md.

import gc
import time

import machine
from hardware import Board, device_id
from link import Link
from telemetry import Outbox, build_message, encode


def run(config, max_readings=None):
    """The main loop. `max_readings` lets the tests stop it; on the board it runs forever."""
    board = Board(config)
    board.show(["IoT Center", "", "starting..."])
    link = Link(config)
    outbox = Outbox(config.BUFFER_SIZE)
    identity = device_id()
    print(
        "[iot] %s (%s) -> %s:%s"
        % (identity, config.DEVICE_NAME, config.SERVER_HOST, config.SERVER_PORT)
    )

    seq = 0  # restarts at 0 on every boot: the server counts gaps and restarts from it
    sent = 0
    streaming = config.START_STREAMING
    interval_ms = int(config.INTERVAL_S * 1000)
    next_reading = time.ticks_ms()

    while max_readings is None or seq < max_readings:
        board.tick()
        link.maintain()
        if board.button_start.pressed():
            streaming = True
        if board.button_stop.pressed():
            streaming = False

        now = time.ticks_ms()
        if time.ticks_diff(now, next_reading) >= 0:
            # Schedule the next reading; if we fell far behind (e.g. a slow reconnect),
            # skip ahead instead of firing a burst of catch-up readings.
            next_reading = time.ticks_add(next_reading, interval_ms)
            if time.ticks_diff(now, next_reading) > 0:
                next_reading = time.ticks_add(now, interval_ms)

            environment = board.environment()
            health = board.health()
            health["wifi_rssi_dbm"] = link.rssi()
            health["ip"] = link.ip()
            message = build_message(
                identity, config.DEVICE_NAME, seq, link.utc_now(), environment, health
            )
            seq += 1

            if streaming:
                line = encode(message)
                if config.USB_OUTPUT:
                    print(line.decode().strip())  # one JSON line on USB serial
                if link.enabled:
                    outbox.push(line)
            delivered = link.deliver(outbox)
            if delivered:
                sent += delivered
                board.blink_sent()

            board.lights(
                connected=link.sock is not None, problem=link.enabled and link.sock is None
            )
            board.show(screen(environment, link, outbox, sent, streaming))
            gc.collect()

        time.sleep_ms(20)
    link.close()


def screen(environment, link, outbox, sent, streaming):
    """The 8 lines of 16 characters on the OLED."""
    return [
        "%.1fC  %.0f%%RH" % (environment["temperature_c"], environment["humidity_pct"]),
        "%.1f hPa" % environment["pressure_hpa"],
        "",
        (link.ip() or "no Wi-Fi") if link.enabled else "USB only",
        "srv: " + link.state,
        "sent %d" % sent,
        "waiting %d" % len(outbox),
        "STREAMING" if streaming else "PAUSED (btn 1)",
    ]


if __name__ == "__main__":
    import config

    try:
        run(config)
    except KeyboardInterrupt:
        print("[iot] stopped")  # with WATCHDOG on, the board reboots ~8 s later
    except Exception as exc:  # never sit dead in a cupboard: log, wait, start over
        print("[iot] crashed:", exc)
        time.sleep(5)
        machine.reset()
