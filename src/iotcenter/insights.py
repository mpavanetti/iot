"""Insights shown next to the readings, derived from data that is already stored.

  pressure tendency   the change over the last 3 hours, in the bands barometers use
  sea-level pressure  station pressure corrected for altitude, comparable to weather reports
  comfort             indoor humidity in a word

Nothing here needs new fields from the board: only the recent history and, for sea-level
pressure, the sensor's altitude (IOT_ALTITUDE_M).
"""

from __future__ import annotations

from typing import Any

TENDENCY_HOURS = 3  # the meteorological convention
BUCKET_S = 300  # history is averaged per 5 minutes, which smooths out sensor noise

# (largest 3-hour change in hPa, size) - the WMO bands for pressure tendency
TENDENCY_BANDS = ((1.0, "steady"), (3.5, "slowly"), (6.0, ""), (float("inf"), "quickly"))
OUTLOOK = {
    "steady": "no change in the weather expected",
    "rising slowly": "settled weather",
    "rising": "improving weather",
    "rising quickly": "clearing, often windy",
    "falling slowly": "may turn unsettled",
    "falling": "unsettled weather likely",
    "falling quickly": "stormy weather likely",
}


def pressure_tendency(change_hpa: float) -> dict[str, Any]:
    """Classify a 3-hour pressure change, with the usual rule-of-thumb outlook."""
    size = next(word for limit, word in TENDENCY_BANDS if abs(change_hpa) < limit)
    if size == "steady":
        trend = "steady"
    else:
        trend = " ".join(w for w in ("rising" if change_hpa > 0 else "falling", size) if w)
    return {
        "change_hpa": round(change_hpa, 1),
        "hours": TENDENCY_HOURS,
        "trend": trend,
        "outlook": OUTLOOK[trend],
    }


def sea_level_pressure(station_hpa: float, altitude_m: float) -> float:
    """QNH: station pressure reduced to sea level through the standard atmosphere, the way
    weather reports and phone apps quote it (about +130 hPa at 1,045 m)."""
    return round(station_hpa / (1 - 2.25577e-5 * altitude_m) ** 5.25588, 1)


def comfort(humidity_pct: float) -> dict[str, str]:
    """Indoor relative humidity in a word (the board's display uses the same thresholds)."""
    if humidity_pct < 30:
        return {"label": "Dry", "range": "below 30 %"}
    if humidity_pct > 60:
        return {"label": "Humid", "range": "above 60 %"}
    return {"label": "Comfortable", "range": "30 to 60 %"}


def summarize(history: dict[str, Any], altitude_m: float | None) -> dict[str, Any]:
    """Insights from `TENDENCY_HOURS` and a bit of history, bucketed by `BUCKET_S`."""
    times = history["t"]
    pressure = history["pressure_hpa"]["avg"]
    humidity = history["humidity_pct"]["avg"]
    points = [(t, p) for t, p in zip(times, pressure, strict=True) if p is not None]
    result: dict[str, Any] = {
        "pressure_tendency": None,
        "sea_level_pressure_hpa": None,
        "comfort": None,
        "altitude_m": altitude_m,
    }
    if not points:
        return result
    latest_t, latest_p = points[-1]
    target = latest_t - TENDENCY_HOURS * 3600
    then_t, then_p = min(points, key=lambda point: abs(point[0] - target))
    if abs(then_t - target) <= 2 * BUCKET_S:  # need data from about 3 hours ago
        result["pressure_tendency"] = pressure_tendency(latest_p - then_p)
    if altitude_m is not None:
        result["sea_level_pressure_hpa"] = sea_level_pressure(latest_p, altitude_m)
    last_humidity = next((h for h in reversed(humidity) if h is not None), None)
    if last_humidity is not None:
        result["comfort"] = comfort(last_humidity)
    return result
