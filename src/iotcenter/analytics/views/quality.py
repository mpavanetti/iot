"""Data quality: is every reading arriving, on time, and is the pipeline keeping up?"""

import plotly.graph_objects as go
import streamlit as st

from iotcenter.analytics import charts, data

devices = data.devices()
names = dict(zip(devices["device_id"], devices["name"], strict=True))
colors = charts.device_colors(devices["device_id"].tolist())
selected = st.session_state["devices"]
start = data.since(st.session_state["range"])

st.title("Data quality")

# --- The streaming job ---------------------------------------------------------------------
st.subheader("Spark Structured Streaming")
progress = data.progress()
if progress.empty:
    st.info("The streaming job has not completed a micro-batch yet.")
else:
    for column, row in zip(st.columns(len(progress)), progress.itertuples(), strict=True):
        column.metric(
            f"Query `{row.query_name}`",
            f"{row.age_s:,.0f} s ago",
            f"{row.input_rows:,} rows in batch {row.batch_id:,}",
            delta_color="off",
            delta_arrow="off",
            border=True,
            help="Time since the last micro-batch. Batches run every 10 seconds; with no new "
            "data Spark reports progress less often.",
        )

# --- Delivery per device -------------------------------------------------------------------
st.subheader("Delivery")
quality = data.quality(selected, start)
if quality.empty:
    st.info("No readings in this range.")
    st.stop()

quality["name"] = quality["device_id"].map(names)
quality["delivered"] = quality["readings"] / (quality["readings"] + quality["missing"])
st.dataframe(
    quality[
        [
            "name",
            "device_id",
            "readings",
            "missing",
            "delivered",
            "restarts",
            "delay_p50_s",
            "late_readings",
            "last_reading",
        ]
    ],
    hide_index=True,
    column_config={
        "name": "Device",
        "device_id": "ID",
        "readings": st.column_config.NumberColumn("Readings", format="localized"),
        "missing": st.column_config.NumberColumn(
            "Lost", help="Gaps in the device's sequence numbers: sent but never stored."
        ),
        "delivered": st.column_config.ProgressColumn(
            "Delivered", format="percent", min_value=0, max_value=1
        ),
        "restarts": st.column_config.NumberColumn(
            "Restarts", help="The sequence number went back: the board rebooted."
        ),
        "delay_p50_s": st.column_config.NumberColumn(
            "Typical delay",
            format="%.1f s",
            help="Median of arrival time minus device time, for readings delivered on time.",
        ),
        "late_readings": st.column_config.NumberColumn(
            "Late (>1 min)", help="Buffered while offline, or backfilled."
        ),
        "last_reading": st.column_config.DatetimeColumn("Last reading", format="distance"),
    },
)

# --- Messages per hour: drops show outages at a glance ---------------------------------------
st.subheader("Readings per hour")
hourly = data.hourly(selected, start)
fig = go.Figure()
for device in selected:
    frame = charts.with_gaps(hourly[hourly["device_id"] == device], "hour")
    if frame.empty:
        continue
    fig.add_scatter(
        x=frame["hour"],
        y=frame["samples"].fillna(0),
        mode="lines",
        name=names[device],
        line={"color": colors[device], "width": 2, "shape": "hv"},
        hovertemplate="%{y:,} readings",
    )
charts.show(charts.style(fig, height=300, legend=len(selected) > 1))
st.caption("At one reading every 2 s a healthy board sends 1,800 readings per hour.")
