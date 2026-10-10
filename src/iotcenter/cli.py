"""Command-line entry point: `iotcenter <command>` (or `python -m iotcenter <command>`).

    iotcenter lite        Lite edition: TCP/USB ingest + SQLite + dashboard, one process
    iotcenter gateway     Platform: devices (TCP/USB) -> Kafka
    iotcenter web         Platform: dashboard (live from Kafka, history from PostgreSQL)
    iotcenter analytics   Platform: Streamlit analytics app
    iotcenter ports       List serial ports, to find a Pico W plugged in over USB
    iotcenter status      Where IoT Center is reachable, and how each part is doing

Settings come from IOT_* environment variables (see config.py); flags override them.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# numpy's OpenBLAS starts a thread per core that spins between operations. IoT Center's arrays
# (sound, small camera thumbnails) are tiny, so one thread does the work for a twentieth of the
# CPU. It must be set before numpy is first imported.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from . import __version__
from .config import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="iotcenter",
        description="IoT Center: Pico W sensor data, from the breadboard to the dashboard.",
    )
    parser.add_argument("--version", action="version", version=f"iotcenter {__version__}")
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING (default INFO)")
    commands = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    lite = commands.add_parser("lite", help="run the Lite edition (ingest + SQLite + dashboard)")
    lite.add_argument("--http-port", type=int, help="dashboard port (default 8000)")
    lite.add_argument("--tcp-port", type=int, help="device TCP port (default 1500)")
    lite.add_argument("--no-tcp", action="store_true", help="do not listen for devices on TCP")
    lite.add_argument(
        "--serial", metavar="PORT", help="also read a USB serial port, e.g. /dev/ttyACM0"
    )
    lite.add_argument("--baud", type=int, help="serial baud rate (default 115200)")
    lite.add_argument(
        "--camera", metavar="DEVICE", help="stream a webcam, e.g. /dev/video0 ('demo': test scene)"
    )
    lite.add_argument(
        "--record", action="store_true", help="record a short clip of each motion (with --camera)"
    )
    lite.add_argument(
        "--microphone",
        metavar="DEVICE",
        help="listen to an ALSA device, e.g. plughw:CARD=C960,DEV=0 ('demo': test sounds)",
    )
    lite.add_argument("--db", type=Path, help="SQLite file (default data/iot-lite.db)")
    lite.add_argument("--retention-days", type=int, help="days of raw readings to keep (0 = all)")
    lite.add_argument(
        "--hourly-retention-days", type=int, help="days of hourly aggregates to keep (0 = all)"
    )

    gateway = commands.add_parser("gateway", help="run the platform gateway (devices -> Kafka)")
    gateway.add_argument("--serial", metavar="PORT", help="also read a USB serial port")

    commands.add_parser("web", help="run the platform dashboard")
    analytics = commands.add_parser("analytics", help="run the Streamlit analytics app")
    analytics.add_argument("--port", type=int, default=8501)
    commands.add_parser("ports", help="list serial ports")
    status = commands.add_parser("status", help="links, pipeline health and boards")
    status.add_argument("url", nargs="?", default="http://localhost:8000", help="the dashboard")
    status.add_argument("--wait", type=float, default=0, metavar="S", help="wait for it to start")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = Settings()

    if args.command == "lite":
        _apply(
            settings,
            args,
            http_port="http_port",
            tcp_port="tcp_port",
            serial="serial_port",
            baud="serial_baud",
            camera="camera_device",
            microphone="microphone_device",
            db="db_path",
            retention_days="retention_days",
            hourly_retention_days="hourly_retention_days",
        )
        if args.no_tcp:
            settings.tcp_enabled = False
        if args.record:
            settings.camera_record = True
        from .lite.app import create_lite_app

        _serve(create_lite_app(settings), settings)
    elif args.command == "gateway":
        _apply(settings, args, serial="serial_port")
        from .gateway import run_gateway

        run_gateway(settings)
    elif args.command == "web":
        from .web.app import create_web_app

        _serve(create_web_app(settings), settings)
    elif args.command == "analytics":
        from .analytics import run_streamlit

        run_streamlit(args.port)
    elif args.command == "ports":
        _list_ports()
    elif args.command == "status":
        from .status import main as status_main

        sys.exit(status_main([args.url, "--wait", str(args.wait)]))


def _apply(settings: Settings, args: argparse.Namespace, **mapping: str) -> None:
    """Copy each flag the user actually passed onto its settings field."""
    for flag, field in mapping.items():
        value = getattr(args, flag)
        if value is not None:
            setattr(settings, field, value)


def _serve(app: object, settings: Settings) -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=settings.http_host,
        port=settings.http_port,
        log_level="warning",
        timeout_graceful_shutdown=3,  # do not wait forever on open live streams
    )


def _list_ports() -> None:
    from serial.tools import list_ports

    ports = sorted(list_ports.comports(), key=lambda p: p.device)
    if not ports:
        print("No serial ports found. Is the Pico W plugged in with a data-capable USB cable?")
        return
    for port in ports:
        hint = "  <- Raspberry Pi Pico" if port.vid == 0x2E8A else ""
        print(f"{port.device:24} {port.description}{hint}")


if __name__ == "__main__":
    main(sys.argv[1:])
