"""Tests for gam_client._ts_to_date — the GAM timestamp → calendar-date
conversion that must read in the network timezone (America/New_York), not UTC.

Reading in UTC rolled a line item ending 23:59 ET on 6/30 to 7/1 (every
month-end Direct flight showed a day late, and its derived Completed/Delivering
status lagged a day). These pin the network-tz read.

Imports gam_client, which pulls google-ads-admanager — runs in CI (full
requirements.txt) though not in a bare local env.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("google.ads.admanager_v1")  # skip in a bare env, run in CI

from gam_client import _ts_to_date


class _TS:
    """Minimal stand-in for a protobuf Timestamp (only `.seconds` is read)."""
    def __init__(self, dt: datetime):
        self.seconds = int(dt.timestamp())


def test_end_2359_et_does_not_roll_forward():
    # GAM stores a 6/30 flight end as 23:59 ET = 2026-07-01T03:59Z (EDT, UTC-4).
    end = _TS(datetime(2026, 7, 1, 3, 59, tzinfo=timezone.utc))
    assert _ts_to_date(end) == "2026-06-30"   # ET date, not the UTC 7/1


def test_start_midnight_et_same_day():
    # A 6/1 flight start is 00:00 ET = 2026-06-01T04:00Z — same calendar day.
    start = _TS(datetime(2026, 6, 1, 4, 0, tzinfo=timezone.utc))
    assert _ts_to_date(start) == "2026-06-01"


def test_winter_end_est_offset():
    # Outside DST the offset is UTC-5: 1/31 23:59 EST = 2026-02-01T04:59Z.
    end = _TS(datetime(2026, 2, 1, 4, 59, tzinfo=timezone.utc))
    assert _ts_to_date(end) == "2026-01-31"


def test_aware_datetime_converted_to_network_tz():
    assert _ts_to_date(datetime(2026, 7, 1, 3, 59, tzinfo=timezone.utc)) == "2026-06-30"


def test_naive_datetime_taken_as_is():
    assert _ts_to_date(datetime(2026, 6, 30, 23, 59)) == "2026-06-30"


def test_none_returns_none():
    assert _ts_to_date(None) is None
