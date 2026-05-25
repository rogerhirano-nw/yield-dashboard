"""Tests for betting_daily_update.compose_digest — exercises the data-shape
matrix the digest can encounter without hitting the DB or network.

Cases covered:
1. Empty data — no Improvado rows yet (first run)
2. Pre-sub_id_2 data (today's state) — sub_id_1 = creative size only
3. Post-sub_id_2 data — sub_id_1 = size, sub_id_2 = li<id>
4. Broken-attribution rows — macro-test '%eaid!%' leak in sub_id_1
"""
from __future__ import annotations

from datetime import date
import pandas as pd
import pytest

from betting_daily_update import compose_digest, _li_from_sub_id_2


# --- li parser ---

def test_li_from_sub_id_2_parses_well_formed():
    assert _li_from_sub_id_2("li7306352098") == "7306352098"
    assert _li_from_sub_id_2("li12345") == "12345"


def test_li_from_sub_id_2_rejects_garbage():
    assert _li_from_sub_id_2(None) is None
    assert _li_from_sub_id_2("") is None
    assert _li_from_sub_id_2("320x50") is None
    assert _li_from_sub_id_2("li") is None
    assert _li_from_sub_id_2("liabc") is None
    assert _li_from_sub_id_2("li7306352098_x") is None  # strict — no suffix


# --- empty case ---

def test_compose_digest_empty():
    data = {
        "conv": pd.DataFrame(columns=["date","sub_id_1","sub_id_2","clicks",
                                      "registrations","ftps","net_cash"]),
        "deliv": pd.DataFrame(columns=["date","line_item_id","line_item_name",
                                       "impressions","clicks","spend"]),
        "start": date(2026, 5, 18),
        "end":   date(2026, 5, 24),
    }
    subject, body = compose_digest(data)
    assert "2026-05-24" in subject  # end date in subject
    assert "2026-05-18 to 2026-05-24" in body  # full range in body header
    assert "No Improvado betting report rows found" in body
    assert "ash@and1.tech" in body  # the unblock hint


# --- pre-sub_id_2 (current state) ---

def _make_conv(rows):
    df = pd.DataFrame(rows, columns=["date","sub_id_1","sub_id_2","clicks",
                                     "registrations","ftps","net_cash"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def _make_deliv(rows):
    df = pd.DataFrame(rows, columns=["date","line_item_id","line_item_name",
                                     "impressions","clicks","spend"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["line_item_id"] = df["line_item_id"].astype(str)
    return df


def test_compose_digest_pre_subid2():
    conv = _make_conv([
        ("2026-05-23", "320x50",  None, 18, 0, 0, 0.00),
        ("2026-05-23", "300x250", None,  6, 0, 0, 0.00),
        ("2026-05-22", "320x50",  None, 31, 0, 0, 0.00),
        ("2026-05-19", "728x90",  None,  6, 1, 1, 20.00),
    ])
    deliv = _make_deliv([
        ("2026-05-23", "7306352098", "Newsweek_Direct_Gambling_...IO1109_1_Team-USA_RShore",
         45000, 24, 360.00),
        ("2026-05-22", "7306352098", "Newsweek_Direct_Gambling_...IO1109_1_Team-USA_RShore",
         55000, 31, 440.00),
        ("2026-05-19", "7306352098", "Newsweek_Direct_Gambling_...IO1109_1_Team-USA_RShore",
         60000, 6,  480.00),
    ])
    data = {"conv": conv, "deliv": deliv,
            "start": date(2026, 5, 17), "end": date(2026, 5, 23)}
    subject, body = compose_digest(data, cpa_target=150.0)

    assert "7-DAY TOTALS" in body
    assert "Clicks:" in body
    assert "FTPs:" in body
    # 1 FTP across the window, $1280 spend → CPA $1280 → over target → arrow ↑
    assert "↑" in body or "1,280" in body
    assert "DAILY BREAKDOWN" in body
    assert "PER CREATIVE SIZE" in body
    # Sub_id_2 absent everywhere — section should say so
    assert "sub_id_2 not yet populated" in body


# --- post-sub_id_2 (future) ---

def test_compose_digest_post_subid2():
    conv = _make_conv([
        ("2026-05-26", "320x50",  "li7306352098", 20, 0, 0, 0.00),  # control
        ("2026-05-26", "320x50",  "li9999999991", 30, 1, 0, 0.00),  # Aud-Basketball
        ("2026-05-26", "300x250", "li9999999992", 10, 0, 0, 0.00),  # Aud-SBEnthusiast
        ("2026-05-25", "320x50",  "li9999999991", 28, 1, 1, 20.00), # Aud-Basketball converted
    ])
    deliv = _make_deliv([
        ("2026-05-26", "7306352098", "control LI", 45000, 20, 360.00),
        ("2026-05-26", "9999999991", "Aud-Basketball LI", 22000, 30, 176.00),
        ("2026-05-26", "9999999992", "Aud-SBEnthusiast LI", 8000, 10, 64.00),
        ("2026-05-25", "9999999991", "Aud-Basketball LI", 19000, 28, 152.00),
    ])
    data = {"conv": conv, "deliv": deliv,
            "start": date(2026, 5, 20), "end": date(2026, 5, 26)}
    _, body = compose_digest(data, cpa_target=150.0)

    assert "PER LINE ITEM" in body
    # Basketball LI should appear with its 1 FTP and $328 spend → CPA $328
    assert "9999999991" in body
    assert "Aud-Basketball" in body
    assert "$328.00" in body or "328.00" in body
    # Control LI also appears
    assert "7306352098" in body


# --- broken-attribution leaks ---

def test_compose_digest_flags_broken_attribution():
    conv = _make_conv([
        ("2026-05-23", "320x50",            None,  4, 0, 0, 0.00),
        ("2026-05-23", "320x50_li%eaid!%",  None,  3, 0, 0, 0.00),  # leaked macro
        ("2026-05-22", "InitialTest",       None,  2, 0, 0, 0.00),  # adhoc test
    ])
    deliv = _make_deliv([])
    data = {"conv": conv, "deliv": deliv,
            "start": date(2026, 5, 17), "end": date(2026, 5, 23)}
    _, body = compose_digest(data)
    assert "ALERTS" in body
    assert "Broken-attribution" in body
    # All 3 broken values surfaced
    assert "320x50_li%eaid!%" in body
    assert "InitialTest" in body


# --- target-met case (clean delivery for tomorrow's regression) ---

def test_compose_digest_meets_target():
    conv = _make_conv([
        ("2026-05-23", "320x50", "li9999999991", 60, 5, 5, 100.00),  # 5 FTPs
    ])
    deliv = _make_deliv([
        ("2026-05-23", "9999999991", "Aud-Basketball", 30000, 60, 240.00),
    ])
    data = {"conv": conv, "deliv": deliv,
            "start": date(2026, 5, 17), "end": date(2026, 5, 23)}
    _, body = compose_digest(data, cpa_target=150.0)
    # CPA = $240/5 = $48 → under target → ✓ arrow
    assert "✓" in body
    assert "$48.00" in body
