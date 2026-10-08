"""IoT Center Analytics: a Streamlit app over the PostgreSQL tables written by Spark."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).parent


def run_streamlit(port: int = 8501) -> None:
    """Start Streamlit on app.py (`iotcenter analytics`)."""
    from streamlit.web import cli as streamlit_cli

    os.chdir(APP_DIR)  # Streamlit reads .streamlit/config.toml (the theme) from here
    options = {
        "server.port": port,
        "server.address": "0.0.0.0",
        "server.headless": "true",
        "browser.gatherUsageStats": "false",
    }
    flags = [f"--{name}={value}" for name, value in options.items()]
    sys.argv = ["streamlit", "run", str(APP_DIR / "app.py"), *flags]
    sys.exit(streamlit_cli.main())
