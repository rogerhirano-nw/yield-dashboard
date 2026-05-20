"""Parser unit tests — field-count variants, N/A handling, Unknown-SSP rule."""

from __future__ import annotations

import pytest

from deal_health.parser import (
    canonical_dsp,
    canonical_seller,
    canonical_ssp,
    parse_deal,
)


# ── canonical_ssp ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected_canonical, expected_unknown", [
    ("Adx",                  "AdX",         False),
    ("ADX",                  "AdX",         False),
    ("Magnite",              "Magnite",     False),
    ("magnite",              "Magnite",     False),
    ("Pubmatic",             "Pubmatic",    False),
    # Triggers for Unknown SSP:
    ("DV360",                "Unknown SSP", True),   # DSP in SSP slot
    ("TTD",                  "Unknown SSP", True),
    ("GAM",                  "Unknown SSP", True),
    ("SSP",                  "Unknown SSP", True),
    ("N/A",                  "Unknown SSP", True),
    ("",                     "Unknown SSP", True),
    (None,                   "Unknown SSP", True),
    ("totally-unknown-thing", "Unknown SSP", True),
])
def test_canonical_ssp(raw, expected_canonical, expected_unknown):
    canonical, is_unknown = canonical_ssp(raw)
    assert canonical == expected_canonical
    assert is_unknown == expected_unknown


# ── canonical_seller ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("ILee",   "Ivy Lee"),
    ("Ilee",   "Ivy Lee"),    # case variant
    ("ilee",   "Ivy Lee"),
    ("Ivy",    "Ivy Lee"),
    ("BKaretny", "Ben Karetny"),
    ("Bkaretny", "Ben Karetny"),
    ("KWebb",  "House"),       # explicit House routing
    ("Unknown-Person", "Unknown"),
    (None,     "Unknown"),
    ("",       "Unknown"),
    ("N/A",    "Unknown"),
])
def test_canonical_seller(raw, expected):
    assert canonical_seller(raw) == expected


# ── canonical_dsp ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("DV360",         "DV360"),
    ("dv360",         "DV360"),
    ("TTD",           "The Trade Desk"),
    ("Tara-Group",    "TaraGroup"),
    ("Some New DSP",  "Some New DSP"),       # passthrough when unknown
    (None,            None),
    ("N/A",           None),
])
def test_canonical_dsp(raw, expected):
    assert canonical_dsp(raw) == expected


# ── parse_deal: field-count variants ───────────────────────────────────────

def test_parse_canonical_14_fields():
    raw = "Newsweek_PA_Finance_Adx_DV360_N/A_N/A_Paypal_N/A_US_Display_$6_Team-USA_ILee"
    p = parse_deal(raw)
    assert p.field_count == 14
    assert p.deal_type == "PA"
    assert p.vertical == "Finance"
    assert p.ssp == "AdX"
    assert p.ssp_is_unknown is False
    assert p.dsp == "DV360"
    assert p.agency_holdco is None       # "N/A" → None
    assert p.agency is None              # "N/A" → None
    assert p.advertiser == "Paypal"
    assert p.campaign is None
    assert p.geo == "US"
    assert p.ad_format == "Display"
    assert p.floor == "$6"
    assert p.team == "USA"
    assert p.seller_initials == "ILee"
    assert p.seller == "Ivy Lee"
    assert p.suffix is None


def test_parse_13_fields_no_agency_holdco():
    """13-field variant — agency_holdco missing, positions 5+ shift left."""
    raw = "Newsweek_PD_Travel_Magnite_TTD_NA_Tour-Trav_NA_US_Display_$5_Team-INTL_RShore"
    p = parse_deal(raw)
    assert p.field_count == 13
    assert p.deal_type == "PD"
    assert p.ssp == "Magnite"
    assert p.dsp == "The Trade Desk"
    assert p.agency_holdco is None       # absent by schema
    assert p.agency is None              # was "NA"
    assert p.advertiser == "Tour-Trav"
    assert p.team == "INTL"
    assert p.seller == "Rob Shore"


def test_parse_15_fields_with_suffix():
    raw = "Newsweek_PD_Travel_Magnite_BasisTechnologies_NA_Miles-Media_NA_Tour-Trav-CVB_US_Video_$18_Team-USA_BRobinson_Newsweek 1PD"
    p = parse_deal(raw)
    assert p.field_count == 15
    assert p.deal_type == "PD"
    assert p.suffix == "Newsweek 1PD"
    assert p.seller_initials == "BRobinson"
    assert p.seller == "Brian Robinson"


def test_parse_unknown_ssp_dsp_in_ssp_slot():
    """The data-quality defect: DSP name (DV360) is in the SSP field."""
    raw = "Newsweek_PA_Multi_DV360_TTD_NA_NA_State-Farm_AlwaysOn_US_Display_$4_Team-USA_RShore"
    p = parse_deal(raw)
    assert p.ssp == "Unknown SSP"
    assert p.ssp_is_unknown is True
    # Other fields still parse around it.
    assert p.advertiser == "State-Farm"
    assert p.seller == "Rob Shore"


def test_parse_legacy_name_no_newsweek_prefix():
    raw = "PM_25_Q3_TTD_Crossmedia_RON_Display_WebApp"
    p = parse_deal(raw)
    # Legacy names parse as best-effort; the SSP slot doesn't match any
    # known SSP, so it's flagged Unknown.
    assert p.ssp == "Unknown SSP"
    assert p.ssp_is_unknown is True


def test_parse_empty_or_none():
    assert parse_deal(None).raw == ""
    assert parse_deal("").raw == ""
    assert parse_deal(None).ssp_is_unknown is True
