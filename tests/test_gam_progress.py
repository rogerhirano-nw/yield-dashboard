"""Tests for gam_client.gam_progress_pct — GAM's UI "Progress" figure from a
LineItem.deliveryIndicator (actual ÷ expected delivery percentage).

seller-comms' tracker card reads this (via gam_li_delivery_indicator) instead of
its own straight-line pacing, which showed 169% on AppleTv 7472702553's launch
day while GAM showed 124% (2026-11-13).

Imports gam_client, which pulls google-ads-admanager — runs in CI (full
requirements.txt) though not in a bare local env.
"""
from __future__ import annotations

import pytest

gam_client = pytest.importorskip("gam_client")


@pytest.mark.parametrize("expected,actual,want", [
    (6.75, 8.37, 124.0),          # the 2026-11-13 AppleTv case, to GAM's rounding
    (50.0, 50.0, 100.0),
    (0.0675, 0.0837, 124.0),      # unit-free: fractions give the same ratio
])
def test_progress_is_actual_over_expected(expected, actual, want):
    assert gam_client.gam_progress_pct(expected, actual) == pytest.approx(want, abs=0.1)


@pytest.mark.parametrize("expected,actual", [
    (None, 8.0), (8.0, None), (0, 5.0), (-1, 5.0), ("x", 5.0),
])
def test_progress_none_when_not_meaningful(expected, actual):
    assert gam_client.gam_progress_pct(expected, actual) is None
