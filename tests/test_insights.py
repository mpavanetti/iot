import pytest

from iotcenter.api import empty_history
from iotcenter.insights import comfort, pressure_tendency, sea_level_pressure, summarize


@pytest.mark.parametrize(
    ("change", "trend"),
    [
        (0.4, "steady"),
        (-0.9, "steady"),
        (2.0, "rising slowly"),
        (-2.0, "falling slowly"),
        (4.5, "rising"),
        (-4.5, "falling"),
        (-7.0, "falling quickly"),
    ],
)
def test_pressure_tendency_uses_the_barometer_bands(change, trend):
    tendency = pressure_tendency(change)
    assert tendency["trend"] == trend
    assert tendency["outlook"]
    assert tendency["hours"] == 3


def test_sea_level_pressure_matches_weather_reports_at_1045_m():
    # 899 hPa at the station (1,045 m) is about 1019 hPa at sea level
    assert sea_level_pressure(899.0, 1045) == pytest.approx(1019.1, abs=0.5)
    assert sea_level_pressure(1013.25, 0) == 1013.2


def test_comfort_words():
    assert [comfort(h)["label"] for h in (22, 45, 70)] == ["Dry", "Comfortable", "Humid"]


def history(points):
    """A 5-minute bucketed history from (t, pressure, humidity) tuples."""
    data = empty_history()
    for t, pressure, humidity in points:
        data["t"].append(t)
        data["pressure_hpa"]["avg"].append(pressure)
        data["humidity_pct"]["avg"].append(humidity)
    return data


def test_summary_compares_with_three_hours_ago():
    points = [(t, 900.0 - t / 3600, 45.0) for t in range(0, 3 * 3600 + 1, 300)]
    summary = summarize(history(points), altitude_m=1045)
    assert summary["pressure_tendency"]["trend"] == "falling slowly"
    assert summary["pressure_tendency"]["change_hpa"] == -3.0
    assert summary["sea_level_pressure_hpa"] == sea_level_pressure(897.0, 1045)
    assert summary["comfort"]["label"] == "Comfortable"


def test_summary_waits_for_three_hours_of_data():
    points = [(t, 900.0, 25.0) for t in range(0, 3600, 300)]
    summary = summarize(history(points), altitude_m=None)
    assert summary["pressure_tendency"] is None
    assert summary["sea_level_pressure_hpa"] is None
    assert summary["comfort"]["label"] == "Dry"
    assert summarize(empty_history(), None)["comfort"] is None
