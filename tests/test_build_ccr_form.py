"""Tests for scripts/build_ccr_form.py — the Comscore CCR setup-form builder.

The GAM pull needs credentials, so these cover the pure logic and the template
fill. The fill test reads the real template, which also pins that the leftover
Verizon media-plan sheet stays out of anything we send Comscore.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_ccr_form as ccr  # noqa: E402

JEEP = ("Newsweek_Direct_Automotive_NA_NA_Stellantis_Publicis_Jeep_"
        "Jeep-Unconventional-Pre-roll_US_Multi_IO1040_6_Team-USA_THearn-March")
SLOW = ("Newsweek_PG_Entertainment_ADX_DV360_Omnicom_OMD_AppleTv-Slow-Horses-S6_"
        "Q426_US_Interstitial_$16_Team-USA_ILee")


def _li(i, name="Newsweek_Direct_x", **kw):
    base = dict(id=str(i), order_id="1", name=name, status="DELIVERING",
                is_archived=False, line_item_type="STANDARD", environment="BROWSER",
                start=date(2026, 9, 16), end=date(2026, 9, 21),
                goal_type="LIFETIME", unit_type="IMPRESSIONS", goal_units=100_000)
    base.update(kw)
    return base


def _order(name=JEEP):
    return {"id": "1", "name": name, "advertiser": "[nw] Stellantis",
            "start": date(2026, 9, 16), "end": date(2026, 9, 21)}


def test_parse_order_name_direct_and_pg():
    assert ccr.parse_order_name(JEEP) == {
        "category": "Automotive", "advertiser": "Jeep", "brand": "Jeep",
        "product": "Jeep Unconventional Pre roll"}
    # PG name: token 8 is a quarter code, so the title in token 7 is the product.
    assert ccr.parse_order_name(SLOW) == {
        "category": "Entertainment", "advertiser": "Apple TV", "brand": "Apple TV",
        "product": "AppleTv Slow Horses S6"}
    # Quarter code at the end of token 7, geo at token 8 (order 4187974224).
    matchbox = ("Newsweek_PG_Entertainment_ADX_Amazon_Omnicom_OMD_AppleTv-Matchbox-Q127_"
                "US_Interstitial_$16_Team-USA_ILee")
    p = ccr.parse_order_name(matchbox)
    assert (p["advertiser"], p["product"]) == ("Apple TV", "AppleTv Matchbox")
    assert ccr.parse_order_name("Some one-off order")["advertiser"] == ""
    assert ccr.parse_order_name(JEEP.replace("Automotive", "Tech"))["category"] == "Technology"


def test_exclusions_match_what_comscore_does_not_track():
    # The two placements Kael excluded on 2026-08-28.
    apple = ("Newsweek_Direct_Technology_NA_NA_Cognizant-Technology-Solutions_NA_Cognizant_"
             "Cognizant-AI-Summit_US_Multi-Branded-Article3-Apple-news_IO1053-23_Team-USA_ILee")
    news = ("Newsweek_Direct_Automotive_NA_NA_Omnicom_OMD_Infiniti_Infiniti-Newsmakers-"
            "TheBulletin-Newsletter-Richie-ep3_US_Video_IO1104-25_Team-USA_THern")
    assert "Apple News" in ccr.exclusion_reason(_li(1, apple))
    assert "newsletter" in ccr.exclusion_reason(_li(2, news))
    assert ccr.exclusion_reason(_li(3, status="CANCELED")) == "canceled"
    assert ccr.exclusion_reason(_li(4, is_archived=True)) == "archived"
    assert ccr.exclusion_reason(_li(5, JEEP)) is None


def test_impression_estimate():
    assert ccr.impression_estimate(_li(1)) == 100_000
    assert ccr.impression_estimate(_li(1, goal_type="DAILY", goal_units=1000)) == 6000
    # Sponsorship: daily % of traffic, not impressions.
    assert ccr.impression_estimate(_li(1, line_item_type="SPONSORSHIP",
                                       goal_type="DAILY", goal_units=100)) is None
    assert ccr.impression_estimate(_li(1, unit_type="CLICKS")) is None
    assert ccr.impression_estimate(_li(1, goal_units=-1)) is None


def test_mix_and_split_sum_exactly():
    mix = ccr.normalize_mix({"Desktop": 300, "Smartphone": 600, "Tablet": 100,
                             "Connected TV": 0, "Unknown": 50})
    assert mix == pytest.approx({"desktop": 0.3, "mobile": 0.7, "ctv": 0.0})
    parts = ccr.split_impressions(100_001, {"desktop": 1 / 3, "mobile": 1 / 3, "ctv": 1 / 3})
    assert sum(parts.values()) == 100_001
    assert ccr.normalize_mix({}) is None


def test_reporting_periods_chunk_at_92_days():
    assert ccr.reporting_periods(date(2026, 9, 16), date(2026, 9, 21)) == [
        ("End of campaign report", date(2026, 9, 16), date(2026, 9, 21))]
    parts = ccr.reporting_periods(date(2026, 1, 1), date(2026, 12, 31))
    assert len(parts) == 4
    assert all((e - s).days + 1 <= 92 for _, s, e in parts)
    assert parts[0][1] == date(2026, 1, 1) and parts[-1][2] == date(2026, 12, 31)


def test_build_facts_mix_fallbacks_and_exclusions():
    lis = [
        _li(1, JEEP),                                       # own mix
        _li(2, JEEP, goal_units=50_000),                    # order mix
        _li(3, JEEP + "-Apple-News"),                       # excluded
        _li(4, JEEP, line_item_type="SPONSORSHIP", goal_type="DAILY", goal_units=100),
    ]
    f = ccr.build_facts(
        [_order()], lis,
        li_mix={"1": {"desktop": 0.5, "mobile": 0.5, "ctv": 0.0}},
        order_mix={"1": {"desktop": 0.2, "mobile": 0.8, "ctv": 0.0}},
        network_mix=None)
    assert f.impressions == {"desktop": 60_000, "mobile": 90_000, "ctv": 0}
    assert [li["id"] for li, _ in f.excluded] == ["3"]
    assert any("LI 4" in w for w in f.warnings)
    assert f.kpis == "VCR, Viewability"   # "Pre-roll" in the name
    assert f.advertiser == "Jeep" and f.category == "Automotive"


def test_build_facts_overrides_and_name_cap():
    f = ccr.build_facts([_order("x" * 200)], [_li(1)], {}, {},
                        {"desktop": 1.0, "mobile": 0.0, "ctv": 0.0},
                        overrides={"advertiser": "Apple TV", "kpis": None})
    assert len(f.campaign_name) == ccr.NAME_MAX
    assert f.advertiser == "Apple TV"      # override wins
    assert f.kpis == "CTR, Viewability"    # None override ignored; display default


def test_build_facts_rejects_order_with_nothing_measurable():
    with pytest.raises(ValueError):
        ccr.build_facts([_order()], [_li(1, "x-Newsletter")], {}, {}, None)


def test_template_has_no_leftover_sheets():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(ccr.TEMPLATE)
    assert "Q4 2024 - $128k" not in wb.sheetnames
    hidden = {ws.title for ws in wb if ws.sheet_state != "visible"}
    assert hidden <= ccr.ALLOWED_HIDDEN


def test_fill_template_writes_the_form(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    pytest.importorskip("PIL")
    f = ccr.build_facts([_order()], [_li(1, JEEP)], {}, {},
                        {"desktop": 0.25, "mobile": 0.75, "ctv": 0.0})
    out = ccr.fill_template(f, tmp_path / "ccr.xlsx")
    wb = openpyxl.load_workbook(out)
    sd, md = wb["Study Details"], wb["Media Details"]
    assert sd["C3"].value == JEEP
    assert sd["C4"].value == "September 16, 2026 to September 21, 2026"
    assert sd["C5"].value == "National Advertiser"
    assert sd["C10"].value == "End of campaign report"
    assert sd["E10"].value.date() == date(2026, 9, 16)
    assert sd["C17"].value == "Jeep"
    assert md["B6"].value == 1
    assert (md["B9"].value, md["C9"].value, md["E9"].value) == (25_000, 75_000, 0)
    assert "Q4 2024 - $128k" not in wb.sheetnames
    # The Comscore logos survive the round-trip.
    assert len(wb["Study Details"]._images) == 2
