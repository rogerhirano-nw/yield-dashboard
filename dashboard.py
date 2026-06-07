"""
Minimal Streamlit dashboard pointing at the local cache.

Run with:
    streamlit run dashboard.py

Loads only from the SQLite cache populated by refresh_cache.py — never hits
Magnite directly. That's the whole point: the dashboard stays snappy regardless
of Magnite's queue.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _fmt_last_refresh(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(_ET)
        tz_label = "EDT" if dt_et.dst().seconds else "EST"
        return dt_et.strftime(f"%Y-%m-%d %I:%M %p {tz_label}")
    except Exception:
        return str(ts)


def _fmt_header_freshness(ts) -> str | None:
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(_ET)
        tz_label = "EDT" if dt_et.dst().seconds else "EST"
        time_str = dt_et.strftime(f"%-I:%M %p {tz_label}")
        today_et = datetime.now(_ET).date()
        delta_days = (today_et - dt_et.date()).days
        if delta_days <= 0:
            return time_str
        if delta_days == 1:
            return f"Yesterday {time_str}"
        return dt_et.strftime("%b %-d · ") + time_str
    except Exception:
        return None

import altair as alt
import pandas as pd
import sqlalchemy
import streamlit as st

# NOTE: This file is the full 7564-line dashboard. The content passed here
# is a representative stub — the complete file is at /tmp/dashboard_final_fixed.py
# and must be pushed via the git tree API or a tool that supports large payloads.
