"""Patterns: daily rhythms (hour-of-day heatmap), daily ranges and how metrics relate."""

import plotly.graph_objects as go
import streamlit as st

from iotcenter.analytics import charts, data

devices = data.devices()
names = dict(zip(devices["device_id"], devices["name"], strict=True))
colors = charts.device_colors(devices["device_id"].tolist())
selected = st.session_state["devices"]
hourly = data.hourly(selected, data.since(st.session_state["range"]))

st.title("Patterns")
if hourly.empty:
    st.info("No hourly aggregates in this range yet.")
    st.stop()

left, right = st.columns([1, 2])
device = left.selectbox("Device", selected, format_func=names.get, key="patterns_device")
metric = right.segmented_control(
    "Metric",
    list(data.METRICS),
    format_func=lambda m: data.METRICS[m][1],
    default="temperature_c",
    required=True,
    key="patterns_metric",
)
prefix, label, unit = data.METRICS[metric]
rows = hourly[hourly["device_id"] == device].copy()
rows["date"] = rows["hour"].dt.date
rows["hour_of_day"] = rows["hour"].dt.hour

# --- Heatmap: one row per day, one column per hour of the day ----------------------------
st.subheader(f"{label} by hour of day")
grid = rows.pivot_table(index="date", columns="hour_of_day", values=f"{prefix}_avg")
grid = grid.reindex(columns=range(24))
heatmap = go.Figure(
    go.Heatmap(
        z=grid.values,
        x=list(grid.columns),
        y=[f"{d:%a %b %d}" for d in grid.index],
        colorscale=charts.sequential_scale(),
        colorbar={"title": {"text": unit}, "thickness": 12},
        xgap=2,
        ygap=2,
        hovertemplate="%{y}, %{x}:00<br><b>%{z:.1f} " + unit + "</b><extra></extra>",
    )
)
heatmap.update_layout(hovermode="closest")
heatmap.update_xaxes(
    tickvals=list(range(0, 24, 3)), ticktext=[f"{h:02d}:00" for h in range(0, 24, 3)]
)
heatmap.update_yaxes(autorange="reversed")  # newest day at the bottom, like a calendar
charts.show(charts.style(heatmap, height=max(220, 26 * len(grid) + 80), legend=False))
st.caption("Each cell is an hourly average. Darker means higher.")

# --- Daily range: a slim min–max line per day, the daily average as a dot ------------------
st.subheader(f"Daily {label.lower()} range")
daily = rows.groupby("date").agg(
    low=(f"{prefix}_min", "min"),
    high=(f"{prefix}_max", "max"),
    weighted=(f"{prefix}_avg", lambda s: (s * rows.loc[s.index, "samples"]).sum()),
    samples=("samples", "sum"),
)
daily["average"] = daily["weighted"] / daily["samples"]
color = colors[device]
days = [f"{d:%a %b %d}" for d in daily.index]
ranges = go.Figure(
    go.Scatter(
        x=days,
        y=daily["average"],
        mode="markers",
        name="Daily average, with min–max range",
        marker={"color": color, "size": 10, "line": {"color": charts.surface(), "width": 2}},
        error_y={
            "type": "data",
            "symmetric": False,
            "array": daily["high"] - daily["average"],
            "arrayminus": daily["average"] - daily["low"],
            "thickness": 8,  # a fixed, slim range bar however many days are shown
            "width": 0,
            "color": charts.rgba(color, 0.35),
        },
        customdata=daily[["low", "high"]],
        hovertemplate=f"avg %{{y:.1f}} {unit}<br>range %{{customdata[0]:.1f}} – "
        f"%{{customdata[1]:.1f}} {unit}<extra></extra>",
    )
)
ranges.update_layout(hovermode="closest")
charts.show(charts.style(ranges, unit=unit, height=300, legend=False))
st.caption("Dots are daily averages; bars span the day's lowest and highest readings.")

# --- How two metrics move together --------------------------------------------------------
if metric != "humidity_pct":
    st.subheader(f"{label} vs relative humidity")
    scatter = go.Figure()
    for each in selected[:3]:  # beyond three, colors stop being reliably distinguishable
        points = hourly[hourly["device_id"] == each]
        scatter.add_scatter(
            x=points[f"{prefix}_avg"],
            y=points["humidity_avg"],
            mode="markers",
            name=names[each],
            marker={
                "color": colors[each],
                "size": 8,
                "opacity": 0.75,
                "line": {"color": charts.surface(), "width": 1},
            },
            hovertemplate=f"%{{x:.1f}} {unit} · %{{y:.1f}} %",
        )
    scatter.update_layout(hovermode="closest")
    scatter.update_xaxes(ticksuffix=f" {unit}")
    charts.show(charts.style(scatter, unit="%", height=320))
    if len(selected) > 3:
        st.caption("Showing the first three selected devices.")
