"""IoT Center Analytics: history and data quality, from the tables Spark writes.

Run with `iotcenter analytics`. The sidebar holds the filters every page shares
(time range and devices); each page lives in views/. (Not "pages/": Streamlit would route
that folder by itself, skipping this file and its filters.)
"""

import streamlit as st

from iotcenter import __version__
from iotcenter.analytics import charts, data

st.set_page_config(
    page_title="IoT Center Analytics", page_icon=":material/monitoring:", layout="wide"
)

navigation = st.navigation(
    [
        st.Page("views/overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("views/patterns.py", title="Patterns", icon=":material/calendar_view_month:"),
        st.Page("views/quality.py", title="Data quality", icon=":material/verified:"),
        st.Page("views/explorer.py", title="Explorer", icon=":material/table_view:"),
    ]
)

with st.sidebar:
    st.markdown("**IoT Center** · Analytics")
    try:
        devices = data.devices()
    except Exception as error:  # PostgreSQL not ready yet
        st.error(f"Cannot read PostgreSQL yet: {error}")
        st.stop()

    if devices.empty:
        st.info(
            "No data yet. Readings appear here a few seconds after Spark processes them.\n\n"
            "Start the simulator: `python simulator/simulate_picow.py --devices 3`"
        )
        st.stop()

    names = dict(zip(devices["device_id"], devices["name"], strict=True))
    st.session_state.setdefault("range", "Last 7 days")
    st.session_state.setdefault("devices", devices["device_id"].tolist()[: charts.MAX_DEVICES])
    st.selectbox("Time range", list(data.RANGES), key="range")
    st.multiselect(
        "Devices",
        devices["device_id"].tolist(),
        key="devices",
        format_func=lambda device_id: names.get(device_id, device_id),
        max_selections=charts.MAX_DEVICES,
        placeholder="Choose devices",
    )
    st.caption(
        f"Times in {data.settings().timezone}. Queries are cached for a minute.\n\n"
        f"IoT Center {__version__}"
    )

if not st.session_state["devices"]:
    st.info("Select at least one device in the sidebar.")
    st.stop()

navigation.run()
