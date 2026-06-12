"""Tests for dashboard_logic — the format-classification and benchmark-band
decisions that color the Direct campaigns table.

These exist because this logic produced two silent production bugs while it
lived inline in dashboard.py (untestable next to Streamlit): the "Video
Preroll >30s" bump running before ad_format existed (#156), and benchmark
fallbacks misgrading long-form video under plain Video thresholds. The
"Patek test" below replays that incident end-to-end.
"""

from __future__ import annotations

import math

from dashboard_logic import (LONG_PREROLL_FORMAT, band, bench_red_cut,
                             bench_target, bump_video_format,
                             matches_long_preroll)

# Mirrors the production benchmarks_by_format settings at the time of the
# 2026-06-10 incident: long-form video green ≥35 / red <34.9, plain video
# green ≥70 / red <69.9.
BENCH = {
    "Video":              {"vcr_pct": 70.0, "vcr_red_below": 69.9,
                           "viewability_pct": 70.0, "viewability_red_below": None},
    "Video Preroll >30s": {"vcr_pct": 35.0, "vcr_red_below": 34.9},
    "Display":            {"viewability_pct": 70.0, "viewability_red_below": None,
                           "ctr_pct": 0.10, "ctr_red_below": 0.09},
}


# ── bump_video_format ──────────────────────────────────────────────────────

def test_bump_long_video():
    assert bump_video_format("In-stream video", 60.0) == LONG_PREROLL_FORMAT


def test_no_bump_at_exactly_30s():
    assert bump_video_format("In-stream video", 30.0) == "In-stream video"


def test_no_bump_short_video():
    assert bump_video_format("In-stream video", 15) == "In-stream video"


def test_no_bump_when_duration_unknown():
    assert bump_video_format("In-stream video", None) == "In-stream video"
    assert bump_video_format("In-stream video", math.nan) == "In-stream video"
    assert bump_video_format("In-stream video", "n/a") == "In-stream video"


def test_no_bump_non_video_even_with_long_duration():
    assert bump_video_format("Display", 60.0) == "Display"
    assert bump_video_format(None, 60.0) is None


def test_bump_accepts_numeric_strings():
    assert bump_video_format("Video", "60") == LONG_PREROLL_FORMAT


# ── matches_long_preroll ───────────────────────────────────────────────────

ROW = {"line_item_id": "7306352098",
       "order_name": "Newsweek_Direct_Luxury_Patek",
       "line_item_name": "Newsweek_Direct_Luxury_..._Video_IO1086_1"}


def test_lp_rule_line_item_id_exact():
    assert matches_long_preroll(ROW, [{"match_field": "line_item_id",
                                       "match_value": "7306352098"}])
    assert not matches_long_preroll(ROW, [{"match_field": "line_item_id",
                                           "match_value": "730635209"}])


def test_lp_rule_order_name_substring_case_insensitive_with_wildcard():
    assert matches_long_preroll(ROW, [{"match_field": "order_name",
                                       "match_value": "luxury_PATEK*"}])


def test_lp_rule_line_item_name_substring():
    assert matches_long_preroll(ROW, [{"match_field": "line_item_name",
                                       "match_value": "io1086"}])


def test_lp_malformed_rules_are_skipped():
    assert not matches_long_preroll(ROW, [None, "junk", {},
                                          {"match_field": "order_name"},
                                          {"match_field": "", "match_value": "x"}])
    assert not matches_long_preroll(ROW, None)


# ── bench_target / bench_red_cut ───────────────────────────────────────────

def test_target_direct_then_fallback_key_then_literal():
    assert bench_target(BENCH, "Video Preroll >30s", "vcr_pct") == 35.0
    # format missing the key → fallback_key's value
    assert bench_target(BENCH, "Video Preroll >30s", "viewability_pct",
                        fallback_key="Display") == 70.0
    # unknown format → literal fallback
    assert bench_target(BENCH, "Interscroller", "vcr_pct",
                        fallback_key="Video", fallback=60.0) == 70.0
    assert bench_target({}, "Video", "vcr_pct", fallback=60.0) == 60.0
    assert bench_target({}, "Video", "vcr_pct") is None


def test_red_cut_configured_then_fallback_then_85pct_of_target():
    assert bench_red_cut(BENCH, "Video Preroll >30s", "vcr", 35.0) == 34.9
    # no red configured anywhere → 85% of target
    assert bench_red_cut(BENCH, "Display", "viewability", 70.0) == 70.0 * 0.85
    # no target at all → no red cut
    assert bench_red_cut(BENCH, "Native", "vcr", None) is None


# ── band ───────────────────────────────────────────────────────────────────

def test_band_boundaries():
    assert band(34.89, 35.0, 34.9) == "red"
    assert band(34.9, 35.0, 34.9) == "amber"   # at the red ceiling = amber
    assert band(35.0, 35.0, 34.9) == "green"   # at the green floor = green
    assert band(50.0, 35.0, None) == "green"   # no red cut → never red


# ── The Patek test — replays the 2026-06-10 incident end-to-end ───────────

def test_long_form_video_grades_under_its_own_band():
    """A 60s in-stream video line at 36.6% VCR must be green under the
    configured >30s band — it rendered red when the bump silently no-op'd
    and the line fell back to plain Video thresholds (#156)."""
    fmt = bump_video_format("In-stream video", 60.0)
    assert fmt == LONG_PREROLL_FORMAT
    target = bench_target(BENCH, fmt, "vcr_pct", fallback_key="Video", fallback=60.0)
    red_cut = bench_red_cut(BENCH, fmt, "vcr", target, fallback_key="Video")
    assert band(36.6, target, red_cut) == "green"
    assert band(34.2, target, red_cut) == "red"   # genuinely below the red line


def test_misclassified_long_form_renders_red_under_plain_video():
    """Documents the failure mode: the same 36.6% VCR under plain 'Video'
    thresholds (red <69.9) is red — which is what users saw before #156."""
    target = bench_target(BENCH, "In-stream video", "vcr_pct",
                          fallback_key="Video", fallback=60.0)
    red_cut = bench_red_cut(BENCH, "In-stream video", "vcr", target,
                            fallback_key="Video")
    assert band(36.6, target, red_cut) == "red"


# ── attention_band / ivt_band ──────────────────────────────────────────────

def test_attention_band_boundaries():
    from dashboard_logic import attention_band
    assert attention_band(84.9) == "red"
    assert attention_band(85) == "amber"
    assert attention_band(99.9) == "amber"
    assert attention_band(100) == "green"
    assert attention_band(165) == "green"


def test_ivt_band_boundaries():
    from dashboard_logic import ivt_band
    assert ivt_band(0) == "green"
    assert ivt_band(0.99) == "green"
    assert ivt_band(1.0) == "amber"   # exactly 1 rounds against us
    assert ivt_band(2.99) == "amber"
    assert ivt_band(3.0) == "red"


# ── choose_join_col ────────────────────────────────────────────────────────

def test_choose_join_col_prefers_ids_when_present():
    import pandas as pd
    from dashboard_logic import choose_join_col
    assert choose_join_col(pd.DataFrame({"line_item_id": ["1", None],
                                         "line_item_name": ["a", "b"]})) == "line_item_id"
    assert choose_join_col(pd.DataFrame({"line_item_id": [None, None],
                                         "line_item_name": ["a", "b"]})) == "line_item_name"
    assert choose_join_col(pd.DataFrame({"line_item_name": ["a"]})) == "line_item_name"


# ── attention_current_and_prior ────────────────────────────────────────────

def test_attention_means_are_per_date_then_across_dates():
    import pandas as pd
    from datetime import date as d
    from dashboard_logic import attention_current_and_prior
    # Line A: date1 has two creative rows (100, 200 → day mean 150),
    # date2 has one (90). Current must be mean of day-means = 120
    # (NOT the row mean 130); prior excludes the latest date → 150.
    df = pd.DataFrame({
        "line_item_id": ["A", "A", "A", "B"],
        "attention_index": [100.0, 200.0, 90.0, 110.0],
        "date": [d(2026, 6, 1), d(2026, 6, 1), d(2026, 6, 2), d(2026, 6, 1)],
    })
    cur, prior = attention_current_and_prior(df, "line_item_id")
    assert cur["A"] == 120.0
    assert prior["A"] == 150.0
    assert cur["B"] == 110.0
    assert "B" not in prior          # single-date lines have no prior


def test_attention_missing_column_or_empty_returns_empty():
    import pandas as pd
    from dashboard_logic import attention_current_and_prior
    assert attention_current_and_prior(pd.DataFrame({"x": [1]}), "line_item_id") == ({}, {})


# ── ivt_share_with_prior ───────────────────────────────────────────────────

def test_ivt_share_is_impression_weighted_per_mrc():
    import pandas as pd
    from datetime import date as d
    from dashboard_logic import ivt_share_with_prior
    # Line A, date1: 99 valid + 1 SIVT (1%); date2: 95 valid + 5 SIVT (5%).
    # Whole-window share = 6/200 = 3.0%; prior (excl. latest) = 1/100 = 1.0%.
    # The GIVT row on date1 must not leak into the SIVT bucket.
    df = pd.DataFrame({
        "line_item_id":     ["A"] * 5 + ["Z"],
        "traffic_validity": ["Valid Traffic", "Fraud/SIVT", "Fraud/GIVT",
                             "Valid Traffic", "Fraud/SIVT", "Valid Traffic"],
        "monitored_ads":    [97, 1, 2, 95, 5, 0],
        "date": [d(2026, 6, 1)] * 3 + [d(2026, 6, 2)] * 2 + [d(2026, 6, 1)],
    })
    cur, prior = ivt_share_with_prior(df, "line_item_id", "Fraud/SIVT")
    assert cur["A"] == 3.0
    assert prior["A"] == 1.0
    assert "Z" not in cur            # zero monitored imps → omitted, not 0%
    g_cur, _ = ivt_share_with_prior(df, "line_item_id", "Fraud/GIVT")
    assert g_cur["A"] == 1.0         # 2/200


# ── classify_delta ─────────────────────────────────────────────────────────

def test_classify_delta_noise_new_and_polarity():
    import math
    from dashboard_logic import classify_delta
    assert classify_delta(None) is None
    assert classify_delta(math.nan) is None
    assert classify_delta(0.04) is None                       # inside noise band
    assert classify_delta(150.0) == "new"                     # new-line magnitude
    assert classify_delta(150.0, new_line_threshold=None) == ("▲", True)
    assert classify_delta(2.0) == ("▲", True)                 # higher-is-better up
    assert classify_delta(-2.0) == ("▼", False)
    assert classify_delta(2.0, lower_is_worse=False) == ("▲", False)   # IVT rising = bad
    assert classify_delta(-2.0, lower_is_worse=False) == ("▼", True)


# ── lt_minus_1d_ratio / volume_pct_delta ───────────────────────────────────

def test_lt_minus_1d_ratio():
    import math
    from dashboard_logic import lt_minus_1d_ratio
    # lifetime 80/100 incl. latest day 30/40 → prior 50/60 = 83.33…%
    assert abs(lt_minus_1d_ratio(80, 30, 100, 40) - 50 / 60 * 100) < 1e-9
    assert lt_minus_1d_ratio(80, 30, 40, 40) is None          # no prior denominator
    assert lt_minus_1d_ratio(80, 30, 30, 40) is None          # clamped negative den
    assert lt_minus_1d_ratio(math.nan, 30, 100, 40) is None
    assert lt_minus_1d_ratio(None, 30, 100, 40) is None


def test_volume_pct_delta():
    import math
    from dashboard_logic import volume_pct_delta
    assert volume_pct_delta(110, 10) == 10.0                  # 10 vs prior 100
    assert volume_pct_delta(10, 10) is None                   # no prior volume
    assert volume_pct_delta(math.nan, 10) is None
