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
