"""Tests for dashboard_logic — the format-classification and benchmark-band
decisions that color the Direct campaigns table.

These exist because this logic produced two silent production bugs while it
lived inline in dashboard.py (untestable next to Streamlit): the "Video
Preroll >30s" bump running before ad_format existed (#156), and benchmark
fallbacks misgrading long-form video under plain Video thresholds. The
"Patek test" below replays that incident end-to-end.
"""

from __future__ import annotations

import datetime
import math

import pandas as pd

from dashboard_logic import (LONG_PREROLL_FORMAT, band, bench_red_cut,
                             bench_target, bump_video_format,
                             matches_long_preroll, ttd_cpa_summary)

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
    # advertiser(7) — campaign(8); format(10) is NOT used (it's the chip's job).
    full = "NW_Direct_2026_Q2_US_Display_300x250_VGW_Camp_US_Interscroller_IO1_1_Team-USA_RH"
    assert dn(full) == "VGW — Camp"
    assert dn("#2  " + full) == "VGW — Camp"              # strips the #N badge first
    assert dn(full.replace("VGW", "Cartier-UK")) == "Cartier UK — Camp"  # hyphen→space
    # format token is irrelevant to the name now
    assert dn(full.replace("Interscroller", "Display")) == "VGW — Camp"
    assert dn("a_b_c_d_e_f") == "c_d_e"                   # no advertiser/campaign → mid tokens
    assert dn("Hello") == "Hello"                         # <3 tokens → cleaned name
    assert dn(None) == ""


def test_line_item_display_name_real_prod_names():
    """Real prod Infiniti LIs that all rendered identical 'Infiniti - Display'
    under the old format-based name — the campaign field separates them and
    carries the placement/product."""
    from dashboard_logic import line_item_display_name as dn
    base = "Newsweek_Direct_Automotive_NA_NA_Omnicom_OMD_Infiniti_{}_US_{}_{}_Team-USA_THern"
    # advertiser prefix on the campaign token is stripped; dashes → spaces
    assert dn(base.format("Infiniti-Newsmakers-Centerstage-June", "Display", "IO1104-22")) \
        == "Infiniti — Newsmakers Centerstage June"
    assert dn(base.format("Infiniti-Qx65-2026-Apple-News-June", "Display", "IO1104-4")) \
        == "Infiniti — Qx65 2026 Apple News June"
    assert dn(base.format("Infiniti-Qx65-2026-Homepage-Takeover-May", "Centerstage", "IO1104-5")) \
        == "Infiniti — Qx65 2026 Homepage Takeover May"
    # trailing "(Article)" marker is preserved (disambiguates same-campaign LIs)
    assert dn(base.format("Infiniti-Qx65-2026-MANV-Sponsorship-May", "Display", "IO1104-6") + " (Article)") \
        == "Infiniti — Qx65 2026 MANV Sponsorship May (Article)"
    # campaign token == bare product (Test LIs): no advertiser prefix to strip
    assert dn("Newsweek_Test_AUTO_NA_NA_NA_NA_Infiniti_Centerstage_US_Display_NA_Team-USA_NA") \
        == "Infiniti — Centerstage"


def test_pmp_deal_display_name():
    """PMP deal name → (primary, subline). Newsweek convention puts advertiser
    at token 7 and campaign at token 8 (same as Direct); agency(6)·holding(5)
    is the subline. SSP-native names have no such structure → shown whole."""
    from dashboard_logic import pmp_deal_display_name as dn
    # GAM Newsweek convention: Advertiser — Campaign · agency · holding
    assert dn("Newsweek_PA_Automotive_Adx_DV360_WPP_Mindshare_Ford Motor Company_Ford-Always-On_US_Display_$3_Team-USA_THern") \
        == ("Ford Motor Company — Ford Always On", "Mindshare · WPP")
    # Magnite exchange variant — same token positions; distinguishes sibling tiers
    assert dn("Newsweek_PA_Health_Magnite_AdTheorent_Omnicom_GSD&M_MD-Anderson_FY26-Brand-National-Intent-Tier2_US_Display_$7.48_Team-USA_BKaretny") \
        == ("MD Anderson — FY26 Brand National Intent Tier2", "GSD&M · Omnicom")
    # N/A agency + holding → empty subline; N/A campaign → advertiser only
    assert dn("Newsweek_PD_Multi_Adx_RTB House_N/A_N/A_RTB-House-US_N/A_Global_Display_$10_Team-USA_BKaretny") \
        == ("RTB House US", "")
    # SSP-native / non-convention: whole name, underscores → spaces, no subline
    assert dn("3PS_Pubmatic_DE_Display_High CTR") == ("3PS Pubmatic DE Display High CTR", "")
    assert dn("ABS-CBN - PH - Display") == ("ABS-CBN - PH - Display", "")
    assert dn(" PM_25_Q3_TTD_PowerDigital_US_Display_WebApp") == ("PM 25 Q3 TTD PowerDigital US Display WebApp", "")
    # empties
    assert dn("N/A") == ("—", "")
    assert dn("") == ("—", "")
    assert dn(None) == ("—", "")


def test_pmp_deal_floor():
    """Configured floor parsed from the deal-name `$<floor>` token (token 11).
    The SSP delivery feeds carry no per-deal floor, but Newsweek's convention
    embeds it in the name (same names as the display-name test)."""
    from dashboard_logic import pmp_deal_floor as f
    assert f("Newsweek_PA_Automotive_Adx_DV360_WPP_Mindshare_Ford Motor Company_Ford-Always-On_US_Display_$3_Team-USA_THern") == 3.0
    assert f("Newsweek_PA_Health_Magnite_AdTheorent_Omnicom_GSD&M_MD-Anderson_FY26-Brand-National-Intent-Tier2_US_Display_$7.48_Team-USA_BKaretny") == 7.48
    assert f("Newsweek_PD_Multi_Adx_RTB House_N/A_N/A_RTB-House-US_N/A_Global_Display_$10_Team-USA_BKaretny") == 10.0
    # the Google Evergreen PD deal from the drawer
    assert f("Newsweek_PD_Tech_Adx_DV360_WPP_Media-Futures-Group_Google_Evergreen_US_Video_$14_Team-USA_ILee") == 14.0
    # SSP-native / non-convention names carry no floor token
    assert f("3PS_Pubmatic_DE_Display_High CTR") is None
    assert f("ABS-CBN - PH - Display") is None
    # NA floor token / empties / None / truncated convention name
    assert f("Newsweek_PA_Auto_Adx_DV360_WPP_Agency_Adv_Camp_US_Display_NA_Team-USA_THern") is None
    assert f("Newsweek_PD_Multi_Adx_RTB House_N/A_N/A_RTB-House-US_N/A_Global_Display_N/A_Team-USA_BKaretny") is None
    assert f("N/A") is None
    assert f("") is None
    assert f(None) is None
    assert f("Newsweek_PD_Tech_Adx") is None


def test_revenue_daily_series_by_deal():
    import pandas as pd
    from datetime import date
    from dashboard_logic import revenue_daily_series_by_deal as f
    df = pd.DataFrame({
        "ssp":     ["GAM", "GAM", "GAM", "Magnite", "Magnite", "GAM"],
        "deal":    ["A",   "A",   "A",   "B",       "B",       "A"],
        "date":    ["2026-06-10", "2026-06-12", "2026-06-12",
                    "2026-06-11", "2026-06-12", "2026-06-09"],
        "revenue": [100,   50,    25,    200,       300,        999],
    })
    series, dates = f(df, n=3)
    # window = last 3 days ending at the max date present (06-12); 06-09 excluded
    assert dates == [date(2026, 6, 10), date(2026, 6, 11), date(2026, 6, 12)]
    # 06-12 sums the two same-day rows (50+25); the missing 06-11 fills to 0.0
    assert series[("GAM", "A")] == [100.0, 0.0, 75.0]
    assert series[("Magnite", "B")] == [0.0, 200.0, 300.0]
    # missing columns / empty / None → empty pair
    assert f(pd.DataFrame(), n=3) == ({}, [])
    assert f(None) == ({}, [])


def test_window_last_n_days():
    import pandas as pd
    from dashboard_logic import window_last_n_days
    df = pd.DataFrame({
        "date": ["2026-06-01", "2026-06-07", "2026-06-08", "2026-06-14"],
        "rev":  [10, 20, 30, 40],
    })
    # n=7 anchored at the frame's max date (06-14) → window [06-08 … 06-14].
    # The boundary day 06-08 (= 06-14 − 6) is kept (>=); 06-07 and 06-01 drop.
    out = window_last_n_days(df, n=7)
    assert out["date"].tolist() == ["2026-06-08", "2026-06-14"]
    # No usable date column / empty frame → returned unchanged (never raises).
    no_date = pd.DataFrame({"x": [1, 2]})
    assert window_last_n_days(no_date, n=7).equals(no_date)
    assert window_last_n_days(pd.DataFrame(), n=7).empty


def test_spend_momentum_seven_vs_seven():
    import pandas as pd
    from dashboard_logic import spend_momentum
    # 14 distinct dates → adaptive window w = min(7, 14//2) = 7.
    # recent = 06-08…06-14, prior = 06-01…06-07.
    rows = []
    for di in range(1, 15):
        d = f"2026-06-{di:02d}"
        recent = di >= 8
        rows.append({"deal": "GAIN",  "_date": d, "_rev": 40 if recent else 10})  # +$210
        rows.append({"deal": "LOSE",  "_date": d, "_rev": 5  if recent else 50})  # −$315
        rows.append({"deal": "FLAT",  "_date": d, "_rev": 25 if recent else 20})  # +$35 (≤100)
        rows.append({"deal": "NOISE", "_date": d, "_rev": 0})                     # $0/$0
    summ, gaining, losing = spend_momentum(pd.DataFrame(rows), "deal", "_rev")
    # FLAT (|Δ| ≤ $100) and NOISE ($0 both windows) drop; sorted by recent rev.
    assert summ["deal"].tolist() == ["GAIN", "LOSE"]
    assert (gaining, losing) == (1, 1)
    g = summ[summ["deal"] == "GAIN"].iloc[0]
    assert (g["_recent_rev"], g["_prior_rev"], g["_delta"]) == (280, 70, 210)
    assert round(g["_pct"]) == 300  # 210 / 70 × 100
    l = summ[summ["deal"] == "LOSE"].iloc[0]
    assert (l["_recent_rev"], l["_prior_rev"], l["_delta"]) == (35, 350, -315)


def test_spend_momentum_degrades_to_3v3_on_seven_days():
    import pandas as pd
    from dashboard_logic import spend_momentum
    # Only 7 distinct dates cached → w = min(7, 7//2) = 3 (behaviour-identical
    # to the old inline 3-vs-3 split). recent = 06-12…06-14, prior = 06-09…06-11;
    # the oldest day 06-08 falls OUTSIDE both windows and must be ignored.
    rows = []
    for di in range(8, 15):  # 06-08 … 06-14
        d = f"2026-06-{di:02d}"
        rev = 200 if di >= 12 else (1 if di >= 9 else 9999)  # 9999 only on 06-08
        rows.append({"deal": "X", "_date": d, "_rev": rev})
    summ, gaining, losing = spend_momentum(pd.DataFrame(rows), "deal", "_rev")
    x = summ[summ["deal"] == "X"].iloc[0]
    assert x["_recent_rev"] == 600  # 3 × 200
    assert x["_prior_rev"] == 3     # 3 × 1  (proves 06-08's 9999 is excluded)
    assert (gaining, losing) == (1, 0)
    # Guards: missing column / None → empty result, never raises.
    assert spend_momentum(pd.DataFrame({"deal": ["a"]}), "deal", "_rev")[0].empty
    _none = spend_momentum(None, "deal", "_rev")
    assert _none[0].empty and _none[1:] == (0, 0)


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


def test_is_new_line_item():
    from dashboard_logic import is_new_line_item as f
    # First delivery is the latest day (all lifetime == latest day) → new.
    assert f(500, 500) is True
    assert f(200, 200) is True
    # Delivered before the latest day (lifetime > latest) → not new.
    assert f(500, 200) is False
    # No latest-day delivery → not new (even with nothing before it).
    assert f(200, 0) is False
    assert f(0, 0) is False
    # Missing / junk inputs → not new, never raises.
    assert f(None, 100) is False
    assert f(100, None) is False
    assert f("x", 5) is False


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


def test_recently_seen_mask():
    import pandas as pd
    from dashboard_logic import recently_seen_mask
    df = pd.DataFrame({
        "last_seen_date": ["2026-06-10", "2026-01-01", pd.NA, "2026-03-14"],
    })
    mask = recently_seen_mask(df, "2026-03-14")  # 90d "seen" cutoff
    # seen recently → keep; not seen 90d → hide; unknown (NA) → keep;
    # seen exactly on the cutoff → keep (>=).
    assert mask.tolist() == [True, False, True, True]
    # column absent (old cached frame) → keep everything (no-op)
    no_col = pd.DataFrame({"deal_key": ["a", "b"]})
    assert recently_seen_mask(no_col, "2026-03-14").tolist() == [True, True]


def test_recently_seen_short_window_drops_paused_deal():
    # Replays 2026-06-17: a deal paused ~10 days ago (last seen 2026-06-07)
    # vs one still live (seen yesterday). Under the old 90-day "seen" window
    # the paused deal lingers; under the tightened ~7-day window it drops
    # while the live deal stays. today = 2026-06-17.
    import pandas as pd
    from dashboard_logic import recently_seen_mask
    df = pd.DataFrame({"last_seen_date": ["2026-06-07", "2026-06-16"]})  # paused, live
    assert recently_seen_mask(df, "2026-03-19").tolist() == [True, True]   # 90d: both linger (the bug)
    assert recently_seen_mask(df, "2026-06-10").tolist() == [False, True]  # 7d: paused drops, live stays


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


# ── merge_lookups (PMP DV join fallback) ───────────────────────────────────

def test_merge_lookups_primary_wins_secondary_fills():
    from dashboard_logic import merge_lookups
    primary = {"a": 1.0, "b": 2.0}            # order_name lookup
    secondary = {"b": 99.0, "c": 3.0}         # line_item_name fallback
    out = merge_lookups(primary, secondary)
    assert out == {"a": 1.0, "b": 2.0, "c": 3.0}  # b stays primary; c filled
    # inputs not mutated
    assert primary == {"a": 1.0, "b": 2.0}
    assert secondary == {"b": 99.0, "c": 3.0}


def test_merge_lookups_edge_cases():
    from dashboard_logic import merge_lookups
    assert merge_lookups({"a": 1}, {}) == {"a": 1}
    assert merge_lookups({}, {"c": 3}) == {"c": 3}
    assert merge_lookups({}, {}) == {}


def test_merge_lookups_resolves_the_tech_vs_technology_deal():
    # The reported case: Deal key uses "Tech" (programmatic_deal_name) but
    # DV's Order column has "Technology"; DV's Line Item has "Tech".
    from dashboard_logic import merge_lookups
    deal_key = "Newsweek_PD_Tech_Adx_DV360_WPP_..._Video_$14_Team-USA_ILee"
    by_order = {"Newsweek_PD_Technology_Adx_DV360_WPP_..._Video_$14_Team-USA_ILee": 140.3}
    by_li_name = {deal_key: 140.3}
    merged = merge_lookups(by_order, by_li_name)
    assert merged.get(deal_key) == 140.3   # was None before the fallback


# ── landing_projection / landing_at_risk (ending-soon under-delivery) ──────

def test_landing_projection_basic():
    from dashboard_logic import landing_projection as lp
    # Infiniti Custom-Audience: 30,202 delivered, 3,656/day, 14 days, goal 100k
    r = lp(100000, 30202, 3656, 14)
    assert round(r["projected"]) == 81386
    assert round(r["projected_pct"]) == 81
    assert round(r["short"]) == 18614


def test_landing_projection_clamps_and_guards():
    from dashboard_logic import landing_projection as lp
    import math
    assert lp(0, 100, 5, 3) is None          # no positive goal → not graded
    assert lp(None, 100, 5, 3) is None
    assert lp(1000, math.nan, 5, 3) is None   # delivered unknown → can't project
    # ended (days_left ≤ 0) projects to exactly delivered, no negative time
    assert lp(1000, 900, 50, -2)["projected"] == 900
    assert lp(1000, 900, 50, 0)["projected"] == 900
    # missing daily rate → assume no further delivery, not an error
    assert lp(1000, 900, None, 5)["projected"] == 900
    # already over goal → short clamps to 0
    assert lp(1000, 1200, 0, 3)["short"] == 0


def test_landing_at_risk_window_and_threshold():
    from dashboard_logic import landing_at_risk as risk
    # owner defaults: within 7 days, projected < 100%
    assert risk(2, 98.0)                       # Poker Power — close miss, urgent
    assert risk(7, 81.0)                        # on the window boundary
    assert not risk(8, 81.0)                    # outside 7-day window
    assert not risk(2, 100.0)                   # exactly on goal → not at risk
    assert not risk(2, 105.0)                   # over-pacing
    assert not risk(-1, 50.0)                   # already ended → not "ending soon"
    assert risk(None, 50.0) is False            # no end date
    assert risk(3, None) is False               # no projection
    # a wider window catches the early big shortfall (Infiniti at 14d/81%)
    assert risk(14, 81.0, window_days=14)
    assert not risk(14, 81.0, window_days=7)


# ─── ttd_cpa_summary ────────────────────────────────────────────────────────

def _ttd_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal ttd_luckyland-shaped DataFrame for test fixtures."""
    return pd.DataFrame(rows)


def test_ttd_cpa_summary_empty():
    s = ttd_cpa_summary(pd.DataFrame())
    assert s["impressions"] == 0
    assert s["clicks"] == 0
    assert s["conversions"] == 0
    assert s["spend_usd"] == 0.0
    assert s["cpa"] is None
    assert s["conv_rate"] is None
    assert s["by_media_type"] == []
    assert s["daily_conversions"] == []
    assert s["daily_cpa"] == []
    assert s["delta_conversions"] is None
    assert s["delta_cpa"] is None
    assert s["delta_spend"] is None


def test_ttd_cpa_summary_basic():
    df = _ttd_df([
        {"date": "2026-06-01", "impressions": 10_000, "clicks": 200,
         "spend_usd": 500.0, "attributed_conversions": 10},
        {"date": "2026-06-02", "impressions": 12_000, "clicks": 240,
         "spend_usd": 600.0, "attributed_conversions": 12},
    ])
    s = ttd_cpa_summary(df)
    assert s["impressions"] == 22_000
    assert s["clicks"] == 440
    assert s["conversions"] == 22
    assert abs(s["spend_usd"] - 1100.0) < 0.01
    assert abs(s["cpa"] - round(1100.0 / 22, 2)) < 0.01
    assert abs(s["conv_rate"] - round(22 / 440 * 100, 3)) < 0.001
    assert s["date_min"] == datetime.date(2026, 6, 1)
    assert s["date_max"] == datetime.date(2026, 6, 2)
    # daily series
    assert len(s["daily_conversions"]) == 2
    assert s["daily_conversions"][0] == (datetime.date(2026, 6, 1), 10)
    assert s["daily_conversions"][1] == (datetime.date(2026, 6, 2), 12)
    assert len(s["daily_cpa"]) == 2


def test_ttd_cpa_summary_no_conversions_column():
    """When attributed_conversions is absent the function degrades to 0."""
    df = _ttd_df([
        {"date": "2026-06-01", "impressions": 5000, "clicks": 100, "spend_usd": 250.0},
    ])
    s = ttd_cpa_summary(df)
    assert s["conversions"] == 0
    assert s["cpa"] is None
    # conv_rate is 0.0 (not None) when clicks > 0 — a genuine 0% rate
    assert s["conv_rate"] == 0.0


def test_ttd_cpa_summary_zero_clicks():
    df = _ttd_df([
        {"date": "2026-06-01", "impressions": 5000, "clicks": 0,
         "spend_usd": 300.0, "attributed_conversions": 5},
    ])
    s = ttd_cpa_summary(df)
    assert s["conv_rate"] is None
    assert abs(s["cpa"] - 60.0) < 0.01


def test_ttd_cpa_summary_by_media_type():
    df = _ttd_df([
        {"date": "2026-06-01", "impressions": 8000, "clicks": 160,
         "spend_usd": 400.0, "attributed_conversions": 8, "media_type": "Display"},
        {"date": "2026-06-01", "impressions": 4000, "clicks": 80,
         "spend_usd": 300.0, "attributed_conversions": 6, "media_type": "Video"},
    ])
    s = ttd_cpa_summary(df)
    assert len(s["by_media_type"]) == 2
    # sorted by spend descending: Display ($400) before Video ($300)
    assert s["by_media_type"][0]["media_type"] == "Display"
    assert s["by_media_type"][1]["media_type"] == "Video"
    d = s["by_media_type"][0]
    assert d["impressions"] == 8000
    assert d["clicks"] == 160
    assert d["conversions"] == 8
    assert abs(d["spend_usd"] - 400.0) < 0.01
    assert abs(d["cpa"] - 50.0) < 0.01


def test_ttd_cpa_summary_deltas_require_6_dates():
    """Window-half deltas are None when fewer than 6 distinct dates."""
    rows = [
        {"date": f"2026-06-0{i+1}", "impressions": 1000, "clicks": 20,
         "spend_usd": 50.0, "attributed_conversions": 2}
        for i in range(5)
    ]
    s = ttd_cpa_summary(_ttd_df(rows))
    assert s["delta_conversions"] is None
    assert s["delta_cpa"] is None
    assert s["delta_spend"] is None


def test_ttd_cpa_summary_deltas_computed():
    """With ≥6 dates, prior-half vs recent-half deltas are computed."""
    # 6 days: prior half = days 1-3, recent half = days 4-6.
    # Prior: 3 days × 2 conv, $50 spend → 6 conv, $150
    # Recent: 3 days × 4 conv, $80 spend → 12 conv, $240
    rows = (
        [{"date": f"2026-06-0{i+1}", "impressions": 1000, "clicks": 20,
          "spend_usd": 50.0, "attributed_conversions": 2} for i in range(3)]
        + [{"date": f"2026-06-0{i+4}", "impressions": 1000, "clicks": 20,
            "spend_usd": 80.0, "attributed_conversions": 4} for i in range(3)]
    )
    s = ttd_cpa_summary(_ttd_df(rows))
    # conversions: (12-6)/6*100 = 100%
    assert abs(s["delta_conversions"] - 100.0) < 0.1
    # spend: (240-150)/150*100 = 60%
    assert abs(s["delta_spend"] - 60.0) < 0.1
    # CPA prior = 150/6 = 25, recent = 240/12 = 20 → delta = -5.0
    assert abs(s["delta_cpa"] - (-5.0)) < 0.01


def test_ttd_cpa_summary_daily_cpa_only_nonzero():
    """daily_cpa excludes days with 0 conversions."""
    df = _ttd_df([
        {"date": "2026-06-01", "impressions": 1000, "clicks": 20,
         "spend_usd": 100.0, "attributed_conversions": 0},
        {"date": "2026-06-02", "impressions": 1000, "clicks": 20,
         "spend_usd": 100.0, "attributed_conversions": 5},
    ])
    s = ttd_cpa_summary(df)
    assert len(s["daily_conversions"]) == 2
    assert len(s["daily_cpa"]) == 1          # only day 2 had conversions
    assert s["daily_cpa"][0][1] == 20.0      # $100 / 5 conv
