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


def test_idle_band_boundaries():
    from dashboard_logic import idle_band
    assert idle_band(89) == ""        # below the 90-day staleness floor
    assert idle_band(90) == "amber"
    assert idle_band(179) == "amber"
    assert idle_band(180) == "red"    # 6+ months gone
    assert idle_band(365) == "red"


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


# ── per-LI daily series (drawer sparklines) ─────────────────────────────────

def test_attention_daily_series_by_li_per_date_mean():
    import pandas as pd
    from datetime import date as d
    from dashboard_logic import attention_daily_series_by_li
    # Line A: date1 two creatives (100, 200 → day mean 150), date2 one (90).
    # Per-date means, oldest first; B is a separate single-date line.
    df = pd.DataFrame({
        "line_item_id": ["A", "A", "A", "B"],
        "attention_index": [100.0, 200.0, 90.0, 999.0],
        "date": [d(2026, 6, 1), d(2026, 6, 1), d(2026, 6, 2), d(2026, 6, 1)],
    })
    out = attention_daily_series_by_li(df, "line_item_id")
    assert out["A"] == [150.0, 90.0]
    assert out["B"] == [999.0]
    assert "missing" not in out
    assert attention_daily_series_by_li(pd.DataFrame({"x": [1]}), "line_item_id") == {}


def test_ivt_daily_series_by_li_impression_weighted_per_day():
    import pandas as pd
    from datetime import date as d
    from dashboard_logic import ivt_daily_series_by_li
    # Line A, date1: 1 SIVT / (97+1+2)=100 → 1.0%; date2: 5 SIVT / (95+5)=100 → 5.0%.
    # Oldest-first [1.0, 5.0]; GIVT is 2/100 = 2.0% on date1, then a real 0.0%
    # on date2 (traffic, no GIVT). Line Z has zero monitored ads → omitted.
    df = pd.DataFrame({
        "line_item_id":     ["A"] * 5 + ["Z"],
        "traffic_validity": ["Valid Traffic", "Fraud/SIVT", "Fraud/GIVT",
                             "Valid Traffic", "Fraud/SIVT", "Valid Traffic"],
        "monitored_ads":    [97, 1, 2, 95, 5, 0],
        "date": [d(2026, 6, 1)] * 3 + [d(2026, 6, 2)] * 2 + [d(2026, 6, 1)],
    })
    sivt = ivt_daily_series_by_li(df, "line_item_id", "Fraud/SIVT")
    assert sivt["A"] == [1.0, 5.0]
    assert "Z" not in sivt
    givt = ivt_daily_series_by_li(df, "line_item_id", "Fraud/GIVT")
    assert givt["A"] == [2.0, 0.0]
    assert ivt_daily_series_by_li(pd.DataFrame({"x": [1]}), "line_item_id", "Fraud/SIVT") == {}


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


# ── parse_gam_salesperson / li_part / token regexes ────────────────────────

def test_parse_gam_salesperson():
    from dashboard_logic import parse_gam_salesperson as p
    assert p("Newsweek - Sales - Theresa Hern") == "Theresa Hern"
    assert p("Newsweek - Sales- Jeremy Makin (jmakin@newsweek.com)") == "Jeremy Makin"
    assert p("Just A Name") == "Just A Name"   # no prefix → stripped passthrough
    assert p("") is None
    assert p("   ") is None
    assert p(None) is None
    assert p(123) is None


def test_li_part_token_extraction():
    from dashboard_logic import li_part
    name = "Newsweek_Direct_Gambling_NA_NA_NA_NA_Spinfinite_Spinfinite-Digital-Campaign_US_Display_IO1109_1_Team-USA_RShore"
    assert li_part(name, 7) == "Spinfinite"          # advertiser
    assert li_part(name, 8) == "Spinfinite-Digital-Campaign"  # campaign
    assert li_part(name, 10) == "Display"            # format fallback
    assert li_part("too_short", 10) is None
    assert li_part(None, 7) is None


def test_line_item_display_name():
    from dashboard_logic import line_item_display_name as dn
    full = "NW_Direct_2026_Q2_US_Display_300x250_VGW_Camp_US_Interscroller_IO1_1_Team-USA_RH"
    assert dn(full) == "VGW - Interscroller"             # client(7) - media(10)
    assert dn("#2  " + full) == "VGW - Interscroller"    # strips the #N badge first
    assert dn(full.replace("VGW", "Cartier-UK")) == "Cartier UK - Interscroller"  # hyphen→space
    assert dn(full.replace("Interscroller", "NA")) == "VGW"   # NA media → client only
    assert dn("a_b_c_d_e_f") == "c_d_e"                  # no client/media → mid tokens
    assert dn("Hello") == "Hello"                        # <3 tokens → cleaned name
    assert dn(None) == ""


def test_ae_and_team_token_regexes():
    import re
    from dashboard_logic import AE_TOKEN_RE, TEAM_TOKEN_RE
    name = "Newsweek_Direct_Luxury_..._IO1086_1_Team-INTL_CMamboury"
    assert re.search(AE_TOKEN_RE, name).group(1) == "CMamboury"
    assert re.search(TEAM_TOKEN_RE, name).group(1) == "INTL"
    assert re.search(AE_TOKEN_RE, "no token here") is None


# ── pace_band / prior_pacing ───────────────────────────────────────────────

def test_pace_band_boundaries():
    from dashboard_logic import pace_band
    assert pace_band(74.9, 100.0) == "red"
    assert pace_band(75.0, 100.0) == "amber"   # exactly 75% of target = amber
    assert pace_band(89.9, 100.0) == "amber"
    assert pace_band(90.0, 100.0) == "green"   # exactly 90% = green
    assert pace_band(110.0, 100.0) == "green"  # exactly 110% still green
    assert pace_band(110.1, 100.0) == "over"
    assert pace_band(100.0, None) == "over"    # no target → can't judge
    # bands scale with a non-100 target
    assert pace_band(82.4, 110.0) == "red"     # 82.4/110 ≈ 0.749


def test_prior_pacing_pro_rates_through_day_before_yesterday():
    import pandas as pd
    from dashboard_logic import prior_pacing
    today = pd.Timestamp("2026-06-08")
    # Flight 06-01 → 06-11 (10 days). Day-before-yesterday = 06-06 →
    # 5 elapsed days → pro-rated goal 1000 × 5/10 = 500. Delivery
    # excluding the latest day = 600 − 100 = 500 → pace 100%.
    assert prior_pacing(1000, 600, 100, "2026-06-01", "2026-06-11", today) == 100.0
    # Flight too young for a prior (started yesterday) → None
    assert prior_pacing(1000, 50, 50, "2026-06-07", "2026-06-11", today) is None
    # No goal / missing dates / garbage → None, never raises
    assert prior_pacing(0, 600, 100, "2026-06-01", "2026-06-11", today) is None
    assert prior_pacing(1000, 600, 100, None, "2026-06-11", today) is None
    assert prior_pacing(1000, 600, 100, "junk", "2026-06-11", today) is None
    # Elapsed clamps at the flight end for finished flights
    ended = prior_pacing(1000, 1000, 0, "2026-05-01", "2026-05-11", today)
    assert ended == 100.0   # 10/10 days elapsed, full delivery


# ── stale_deal_mask / idle_days ────────────────────────────────────────────

def test_stale_deal_mask_two_clauses():
    import pandas as pd
    from dashboard_logic import stale_deal_mask
    df = pd.DataFrame({
        "last_bid_date":   ["2026-01-01", "2026-06-01", pd.NA,        pd.NA, pd.NA],
        "first_seen_date": ["2025-12-01", "2026-01-01", "2026-01-01", pd.NA, "2026-06-01"],
    })
    mask = stale_deal_mask(df, "2026-03-14")  # 90d cutoff
    # old last bid → stale; recent bid → fresh (even if seen long ago);
    # never bid + seen long ago → stale; no dates at all → fresh;
    # never bid but seen recently → fresh.
    assert mask.tolist() == [True, False, True, False, False]


def test_idle_days_prefers_last_bid_then_first_seen():
    from datetime import date as d
    from dashboard_logic import idle_days
    today = d(2026, 6, 12)
    assert idle_days("2026-06-01", "2026-01-01", today) == 11
    assert idle_days(None, "2026-06-02", today) == 10
    assert idle_days("nan", "2026-06-02", today) == 10   # junk string skipped
    assert idle_days("not-a-date", None, today) == 0
    assert idle_days(None, None, today) == 0


# ── canonicalize_format ────────────────────────────────────────────────────

# Mirrors the prod format_aliases at the time of writing.
ALIASES = {"Multi": "Display", "Banner": "Display",
           "PreRoll": "Video", "In-stream video": "Video"}


def test_canonicalize_the_live_zoo_into_the_house_taxonomy():
    from dashboard_logic import canonicalize_format as c
    # The six buckets (Roger, 2026-06-12): Display, Video, Interstitial,
    # FITO, Centerstage, Apple News.
    assert c("Banner", ALIASES) == "Display"
    assert c("In-stream video", ALIASES) == "Video"
    assert c("In-stream video", {}) == "Video"     # rule covers it sans alias
    assert c("Display", ALIASES) == "Display"
    assert c("Video", ALIASES) == "Video"
    assert c("Interstitial", ALIASES) == "Interstitial"
    # FITO is its own format — and beats the video/display substrings
    for v in ("FITO", "FITO-Video", "fito Video", "FITO-Display"):
        assert c(v, ALIASES) == "FITO", v
    # Centerstage is its own format — beats the display family
    assert c("Centerstage", ALIASES) == "Centerstage"
    assert c("centerstage", ALIASES) == "Centerstage"
    # Apple News lines — beats the article/multi folds
    for v in ("Apple News", "AV-Apple-News", "Multi-Branded-Article1-Apple-news"):
        assert c(v, ALIASES) == "Apple News", v
    # video family
    for v in ("PreRoll", "Contextual-PreRoll", "Custom-Audience-Contextual-PreRoll",
              "Custom-Audience-PreRoll"):
        assert c(v, ALIASES) == "Video", v
    # Display is the catch-all visual family: native, multi/branded-article
    # promos, scroll units, size-named placements
    for v in ("Native", "Multi", "Multi-Branded-Article2", "Homepage-Insight",
              "AV-Display", "Contextual-Display",
              "Custom-Audience-Contextual-Display", "Editorial Promotion Display",
              "Backfill-970x250",
              "Backfill-1536x864", "Direct-970x250", "Direct--300x600"):
        assert c(v, ALIASES) == "Display", v
    # Interscroller is its own format; Uniscroller is the same product
    for v in ("Interscroller", "Uniscroller", "uniscroller"):
        assert c(v, ALIASES) == "Interscroller", v
    assert c("Multi-Branded-Article3", {}) == "Display"   # no Multi bucket even sans alias
    # junk tokens from non-convention names → None
    for v in ("cpm", "BRobinson", "ILee", "US", "adv", "NA", "Team-USA",
              "Hispanic", "$21", "AA", "pgmpg", "Global", "Fiber",
              "iPhone 17e Launch", "ILee - do not use", ""):
        assert c(v, ALIASES) is None, v
    assert c(None, ALIASES) is None
    assert c(float("nan"), ALIASES) is None


def test_canonicalize_alias_wins_and_folds_rule_results():
    from dashboard_logic import canonicalize_format as c
    # raw alias wins over rules, case-insensitively
    assert c("banner", {"Banner": "Video"}) == "Video"
    # alias re-applies once to the rule result — re-route a whole bucket
    assert c("Centerstage", {"Centerstage": "Display"}) == "Display"
    # the benchmark band is NOT a format — re-canonicalizing it yields
    # the plain Video format (the band lives only on _bench_format)
    assert c("Video Preroll >30s", ALIASES) == "Video"


# ── derive_format ──────────────────────────────────────────────────────────

def test_name_keywords_beat_the_api_format():
    from dashboard_logic import derive_format as d
    # GAM reports web interstitials as "Banner" — the name is the truth.
    # Real AppleTv Cape Fear line: format word at position 11, not 10.
    cape_fear = ("Newsweek_Direct_Tech_NA_NA_Omnicom_OMD_AppleTv_"
                 "'Cape-Fear'-FY26-Q3_Display-AV-Pre-Avail_US_Interstitial_"
                 "SO01090_Team-USA_ILee")
    assert d("Banner", cape_fear, ALIASES) == "Interstitial"
    # FITO video lines come back from the API as "In-stream video"
    assert d("In-stream video", "Newsweek_..._FITO-Video_..._Team-USA_ILee", ALIASES) == "FITO"
    assert d("Banner", "Newsweek_..._Jeep-Unconventional-Centerstage-FullEp2_US_Multi_IO1040", ALIASES) == "Centerstage"
    assert d("Banner", "Newsweek_..._AV-Apple-News_...", ALIASES) == "Apple News"
    # Mobkoi scroll units come back from the API as "Banner" too
    assert d("Banner", "Newsweek_Direct_Luxury_NA_NA_NA_Mobkoi_Cartier-UK_Cartier-Santos-UK-FY27_UK_Interscroller_IO1118_2_Team-INTL_AShah", ALIASES) == "Interscroller"
    assert d("Banner", "Newsweek_..._UK_Uniscroller_IO1118_1_Team-INTL_AShah", ALIASES) == "Interscroller"


def test_api_value_authoritative_when_no_name_keyword():
    from dashboard_logic import derive_format as d
    name = "Newsweek_Direct_Gambling_NA_NA_NA_NA_Spinfinite_Spinfinite-Digital-Campaign_US_Display_IO1109_1_Team-USA_RShore"
    assert d("Banner", name, ALIASES) == "Display"
    assert d("In-stream video", name, ALIASES) == "Video"


def test_token_fallback_when_api_missing():
    from dashboard_logic import derive_format as d
    name = "Newsweek_Direct_Gambling_NA_NA_NA_NA_Spinfinite_Spinfinite-Digital-Campaign_US_Video_IO1109_1_Team-USA_RShore"
    assert d(None, name, ALIASES) == "Video"
    assert d("", name, ALIASES) == "Video"
    # non-convention name (no keywords, no API, no position-10 token) → no format
    assert d(None, "Newsweek_Prebid_Video_$19.00", ALIASES) is None
