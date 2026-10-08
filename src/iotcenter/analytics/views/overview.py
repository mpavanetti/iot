"""Overview: the latest hour at a glance, then every metric over the chosen range."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from iotcenter.analytics import charts, data

devices = data.devices()
names = dict(zip(devices["device_id"], devices["name"], strict=True))
colors = charts.device_colors(devices["device_id"].tolist())
selected = st.session_state["devices"]
hourly = data.hourly(selected, data.since(st.session_state["range"]))

st.title("Overview")
if hourly.empty:
    st.info("No hourly aggregates in this range yet.")
    st.stop()

# --- KPI row: the latest complete hour, compared with the hour before -------------------
focus = st.selectbox("Device", selected, format_func=names.get, key="overview_device")
rows = hourly[hourly["device_id"] == focus].sort_values("hour")
complete = rows.iloc[:-1] if len(rows) > 1 else rows  # the newest hour is still filling up
last = complete.iloc[-1]
previous = complete.iloc[-2] if len(complete) > 1 else None

for column, (prefix, label, unit) in zip(st.columns(4), data.METRICS.values(), strict=True):
    value = last[f"{prefix}_avg"]
    delta = None if previous is None else value - previous[f"{prefix}_avg"]
    column.metric(
        label,
        f"{value:,.1f} {unit}",
        None if delta is None else f"{delta:+.2f} {unit}",
        delta_color="off",  # warmer or more humid is neither good nor bad
        border=True,
        chart_data=complete[f"{prefix}_avg"].tail(24).round(2).tolist(),
        chart_type="line",
        help="Average of the latest complete hour; the change is against the hour before. "
        "The sparkline shows the last 24 hours.",
    )
hour_end = last["hour"] + pd.Timedelta(hours=1)
st.caption(
    f"{names[focus]}: hour {last['hour']:%H:%M}–{hour_end:%H:%M} ({last['samples']:,} readings)."
)

# --- Small multiples: one chart per metric, one line per device ---------------------------
# A single device also gets its hourly min–max as a soft band.
st.subheader("Hourly averages")
band = len(selected) == 1
cells = st.columns(2)
for i, (prefix, label, unit) in enumerate(data.METRICS.values()):
    fig = go.Figure()
    for device in selected:
        frame = charts.with_gaps(hourly[hourly["device_id"] == device], "hour")
        if frame.empty:
            continue
        color = colors[device]
        if band:
            fig.add_scatter(
                x=frame["hour"],
                y=frame[f"{prefix}_max"],
                mode="lines",
                line={"width": 0},
                hoverinfo="skip",
                showlegend=False,
            )
            fig.add_scatter(
                x=frame["hour"],
                y=frame[f"{prefix}_min"],
                mode="lines",
                line={"width": 0},
                fill="tonexty",
                fillcolor=charts.rgba(color, 0.14),
                hoverinfo="skip",
                showlegend=False,
            )
        fig.add_scatter(
            x=frame["hour"],
            y=frame[f"{prefix}_avg"].round(2),
            mode="lines",
            name=names[device],
            line={"color": color, "width": 2},
            hovertemplate=f"%{{y:.1f}} {unit}",
        )
    with cells[i % 2]:
        st.markdown(f"**{label}**" + (" · average with hourly range" if band else ""))
        charts.show(charts.style(fig, unit=unit, height=280, legend=not band))
