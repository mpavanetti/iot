"""Host metrics for the dashboard's Pipeline page (Raspberry Pi, laptop, or a container)."""

from __future__ import annotations

import os
import platform
import socket
import time

import psutil

psutil.cpu_percent(interval=None)  # prime the counter: the first reading is always 0.0


def host_metrics(disk_path: str = "/") -> dict:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(disk_path)
    return {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "cpu_pct": psutil.cpu_percent(interval=None),
        "load_1m": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
        "mem_pct": memory.percent,
        "mem_used_bytes": memory.total - memory.available,
        "mem_total_bytes": memory.total,
        "disk_pct": disk.percent,
        "disk_used_bytes": disk.used,
        "disk_total_bytes": disk.total,
        "cpu_temp_c": cpu_temperature(),
        "uptime_s": round(time.time() - psutil.boot_time()),
    }


def cpu_temperature() -> float | None:
    """SoC temperature where the OS exposes it (`cpu_thermal` on a Raspberry Pi)."""
    reader = getattr(psutil, "sensors_temperatures", None)
    if reader is None:
        return None
    try:
        sensors = reader()
    except OSError:
        return None
    for name in ("cpu_thermal", "coretemp", "k10temp", "acpitz"):
        if sensors.get(name):
            return round(sensors[name][0].current, 1)
    for entries in sensors.values():
        if entries:
            return round(entries[0].current, 1)
    return None
