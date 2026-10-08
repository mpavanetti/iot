"""Explorer: the raw readings, newest first, ready to filter and download."""

import streamlit as st

from iotcenter.analytics import data

devices = data.devices()
names = dict(zip(devices["device_id"], devices["name"], strict=True))
selected = st.session_state["devices"]

st.title("Explorer")
limit = st.select_slider("Rows", options=[1_000, 5_000, 20_000, 50_000], value=5_000)
frame = data.readings(selected, data.since(st.session_state["range"]), limit)
if frame.empty:
    st.info("No readings in this range.")
    st.stop()

st.caption(f"{len(frame):,} newest readings for {len(selected)} device(s).")
st.dataframe(
    frame,
    hide_index=True,
    height=560,
    column_config={
        "event_time": st.column_config.DatetimeColumn("Time", format="YYYY-MM-DD HH:mm:ss"),
        "device_id": "Device ID",
        "name": "Name",
        "temperature_c": st.column_config.NumberColumn("Temperature", format="%.2f °C"),
        "humidity_pct": st.column_config.NumberColumn("Humidity", format="%.1f %%"),
        "pressure_hpa": st.column_config.NumberColumn("Pressure", format="%.2f hPa"),
        "dew_point_c": st.column_config.NumberColumn("Dew point", format="%.1f °C"),
        "cpu_temp_c": st.column_config.NumberColumn("Board temp.", format="%.1f °C"),
        "wifi_rssi_dbm": st.column_config.NumberColumn("Wi-Fi", format="%d dBm"),
        "mem_free_bytes": st.column_config.NumberColumn("Free memory", format="bytes"),
        "uptime_s": st.column_config.NumberColumn("Uptime (s)", format="localized"),
        "seq": "Seq",
        "source": "Source",
        "received_at": st.column_config.DatetimeColumn("Received", format="HH:mm:ss"),
    },
)
st.download_button(
    "Download CSV",
    frame.to_csv(index=False).encode(),
    file_name="iot-readings.csv",
    mime="text/csv",
    icon=":material/download:",
)
