"""Plotly styling shared by the analytics pages: the dashboard's palette, light or dark.

Device colors follow a fixed, colorblind-validated order and are assigned from the full
device list (oldest first), so filtering never repaints a device in another color.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

CATEGORICAL = {
    "light": [
        "#2a78d6",
        "#eb6834",
        "#1baf7a",
        "#eda100",
        "#e87ba4",
        "#008300",
        "#4a3aa7",
        "#e34948",
    ],
    "dark": [
        "#3987e5",
        "#d95926",
        "#199e70",
        "#c98500",
        "#d55181",
        "#008300",
        "#9085e9",
        "#e66767",
    ],
}
# One hue, light -> dark (magnitude). On a dark surface the dark end recedes, so it flips.
SEQUENTIAL = [
    "#cde2fb",
    "#9ec5f4",
    "#86b6ef",
    "#6da7ec",
    "#5598e7",
    "#3987e5",
    "#2a78d6",
    "#256abf",
    "#184f95",
    "#0d366b",
]
CHROME = {
    "light": {"grid": "#e1e0d9", "axis": "#c3c2b7", "text": "#52514e", "surface": "#ffffff"},
    "dark": {"grid": "#2c2c2a", "axis": "#383835", "text": "#c3c2b7", "surface": "#0e1117"},
}
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
MAX_DEVICES = len(CATEGORICAL["light"])


def mode() -> str:
    theme = getattr(st.context, "theme", None)
    return "dark" if getattr(theme, "type", None) == "dark" else "light"


def device_colors(all_device_ids: list[str]) -> dict[str, str]:
    palette = CATEGORICAL[mode()]
    return {device: palette[i % MAX_DEVICES] for i, device in enumerate(all_device_ids)}


def sequential_scale() -> list[list]:
    steps = SEQUENTIAL if mode() == "light" else SEQUENTIAL[::-1]
    return [[i / (len(steps) - 1), color] for i, color in enumerate(steps)]


def rgba(hex_color: str, alpha: float) -> str:
    value = int(hex_color.lstrip("#"), 16)
    return f"rgba({value >> 16}, {(value >> 8) & 255}, {value & 255}, {alpha})"


def surface() -> str:
    return CHROME[mode()]["surface"]


def style(fig: go.Figure, *, unit: str = "", height: int = 300, legend: bool = True) -> go.Figure:
    """Hairline grid, no chart junk, one unified hover listing every series at that time."""
    chrome = CHROME[mode()]
    fig.update_layout(
        height=height,
        margin={"l": 0, "r": 8, "t": 32 if legend else 8, "b": 0},
        hovermode="x unified",
        showlegend=legend,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0, "title_text": ""},
        font={"family": FONT, "size": 12},
    )
    fig.update_xaxes(
        showgrid=False,
        showline=True,
        linecolor=chrome["axis"],
        ticks="outside",
        tickcolor=chrome["axis"],
        title=None,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=chrome["grid"],
        gridwidth=1,
        zeroline=False,
        title=None,
        ticksuffix=f" {unit}" if unit else "",
    )
    return fig


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, theme="streamlit", config={"displayModeBar": False})


def with_gaps(frame: pd.DataFrame, time_column: str, freq: str = "h") -> pd.DataFrame:
    """Insert empty rows for missing periods, so lines break at outages instead of bridging."""
    if frame.empty:
        return frame
    return frame.set_index(time_column).asfreq(freq).reset_index()
