"""Print where IoT Center is reachable and how each part of it is doing.

    iotcenter status [URL]               (or: python3 src/iotcenter/status.py [URL])

URL is the dashboard as seen from this machine (default http://localhost:8000). Works for
both editions, since both serve the same API. Standard library only, so `make` can run it
with any Python 3, even where IoT Center itself is not installed (Docker setups).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MARKS = {"up": ("✓", "32"), "down": ("✗", "31"), "disabled": ("–", "90")}


def get(base: str, path: str) -> Any:
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return json.load(response)


def show(key: str, value: Any) -> str:
    if key == "size" and isinstance(value, int | float):
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
    return f"{value:,}" if isinstance(value, int) else str(value)


def wait_until_up(base: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            get(base, "/health")
            return True
        except (OSError, ValueError):
            if time.monotonic() > deadline:
                return False
            time.sleep(1)


def lan_ip() -> str | None:
    """This machine's address on the local network (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1: only picks the outgoing interface
            return sock.getsockname()[0]
        except OSError:
            return None


def report(base: str, color: bool) -> str:
    info = get(base, "/api/info")
    status = get(base, "/api/status")
    devices = get(base, "/api/devices")

    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    url = urllib.parse.urlsplit(base)
    host, port = url.hostname or "localhost", url.port or 80
    ip = lan_ip() if host in ("localhost", "127.0.0.1") else None

    def link(port: int, path: str = "") -> str:
        local = f"{url.scheme}://{host}:{port}{path}"
        return f"{local}   (network: {url.scheme}://{ip}:{port}{path})" if ip else local

    lines = [paint(f"IoT Center {info['edition'].title()} {info['version']}", "1"), ""]
    rows = [("Dashboard", link(port)), ("API docs", link(port, "/api/docs"))]
    rows += [(extra["label"], link(extra["port"])) for extra in info.get("links", [])]
    if info.get("ingest_port"):
        rows.append(("Boards (Wi-Fi)", f"tcp://{ip or host}:{info['ingest_port']}"))
    width = max(len(label) for label, _ in rows)
    lines += [f"  {label:<{width}}  {value}" for label, value in rows]

    lines += ["", paint("Pipeline", "1")]
    width = max(len(c["name"]) for c in status["components"])
    for component in status["components"]:
        mark, code = MARKS.get(component["status"], ("?", "33"))
        facts = [component.get("detail")] + [
            f"{key} {show(key, value)}" for key, value in (component.get("metrics") or {}).items()
        ]
        detail = " · ".join(str(f) for f in facts if f not in (None, ""))
        lines.append(f"  {paint(mark, code)} {component['name']:<{width}}  {detail}")

    if info.get("camera"):
        camera = get(base, "/api/camera")
        lines += ["", paint("Camera", "1")]
        mark, code = MARKS["up" if camera["state"] == "streaming" else "down"]
        if camera["state"] == "streaming":
            fps = f"{camera['fps']:.1f} fps" if camera.get("fps") else "no frames this second"
            facts = f"{camera['width']}×{camera['height']} {camera['format']} · {fps}"
            facts += f" · {camera['viewers']} watching"
        else:
            facts = f"{camera['state']}: {camera.get('error') or 'starting'}"
        lines.append(f"  {paint(mark, code)} {camera['name']}  {facts}")
        for zone in (camera.get("activity") or {}).get("zones", []):
            lines.append(f"      zone {zone['name']} ({zone['kind']}): {zone['state']}")
    if info.get("microphone"):
        sound = get(base, "/api/sound")
        lines += ["", paint("Sound", "1")]
        mark, code = MARKS["up" if sound["state"] == "listening" else "down"]
        if sound["state"] == "listening":
            facts = f"{sound['level_db']:.0f} dBFS (background {sound['background_db']:.0f})"
            if sound.get("alarm"):
                facts += f" · ALARM: {sound['alarm']['pattern']} pattern"
        else:
            facts = f"{sound['state']}: {sound.get('error') or 'starting'}"
        lines.append(f"  {paint(mark, code)} {sound['device']}  {facts}")

    forecast = (status.get("storage") or {}).get("forecast")
    if forecast:
        lines += ["", paint("Storage", "1")]
        lines += ["  " + line for line in growth_summary(status["storage"], forecast)]

    lines += ["", paint("Boards", "1")]
    if not devices:
        lines.append("  none yet: plug a Pico W into USB, or point its Wi-Fi at the address above")
    for device in devices:
        latest = device.get("latest") or {}
        mark, code = ("●", "32") if device.get("online") else ("○", "90")
        link_name = {"usb": "USB", "tcp": "Wi-Fi"}.get(device.get("source"), device.get("source"))
        reading = (
            f"{latest['temperature_c']:.1f} °C  {latest['humidity_pct']:.0f} %RH  "
            f"{latest['pressure_hpa']:.1f} hPa"
            if latest
            else "no readings"
        )
        name = device.get("name") or device["device_id"]
        state = "online" if device.get("online") else "offline"
        lines.append(f"  {paint(mark, code)} {name}  {state} via {link_name} · {reading}")
    return "\n".join(lines)


def size(value: float) -> str:
    return show("size", value)


def kept(days: int) -> str:
    if days <= 0:
        return "forever"
    if days % 365 == 0:
        return f"{days // 365} year{'' if days == 365 else 's'}"
    return f"{days} days"


def growth_summary(storage: dict[str, Any], forecast: dict[str, Any]) -> list[str]:
    """The retention policy, the pace and the ceiling, as lines of the Storage section."""
    now = f"{size(storage.get('size_bytes') or 0)} now"
    if forecast["readings_per_day"]:
        now += f", growing {size(forecast['growth_bytes_per_day'])} a day"
    policy = (
        f"kept: raw readings {kept(forecast['retention_days'])}, "
        f"hourly averages {kept(forecast['hourly_retention_days'])}"
    )
    if forecast["levels_off_bytes"] is None:
        return [now, policy, "never levels off: a retention of 0 keeps that data forever"]
    full = time.strftime("%b %d %Y", time.localtime(forecast["raw_full_at"]))
    ceiling = (
        f"levels off at about {size(forecast['levels_off_bytes'])} (raw readings full by {full})"
    )
    return [now, policy, ceiling]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", nargs="?", default="http://localhost:8000", help="the dashboard")
    parser.add_argument(
        "--wait", type=float, default=0, metavar="S", help="wait up to S seconds for it to start"
    )
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")
    if args.wait and not wait_until_up(base, args.wait):
        print(f"IoT Center did not answer on {base} within {args.wait:.0f} s", file=sys.stderr)
        return 1
    try:
        print(report(base, color=sys.stdout.isatty()))
    except (OSError, ValueError) as exc:
        print(f"Cannot reach IoT Center on {base}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
