"""
Frozen sample payload for snapshot + Outlook-compat tests. Date is pinned;
no datetime.now() anywhere. Update expected_email.html when this changes.
"""

from __future__ import annotations

from datetime import date

from deal_health.aggregate import build_payload
from deal_health.models import UnhealthyDeal
from deal_health.parser import parse_deal


REPORT_DATE = date(2026, 5, 19)
LOOKBACK_DAYS = 7
CSV_URL = "https://raw.githubusercontent.com/example/yield-dashboard/main/reports/weekly_deal_health_2026-05-19.csv"
DASHBOARD_URL = "https://newsweek-magnite.streamlit.app/"


_RAW_DEALS: list[tuple[str, int, int, str, int]] = [
    # (raw deal name, bid_requests, days_in_data, first_seen, deal_age_days)
    # — Ivy Lee, AdX/DV360
    ("Newsweek_PA_Finance_Adx_DV360_N/A_N/A_Paypal_N/A_US_Display_$6_Team-USA_ILee",
     50_439_758, 7, "2026-05-12", 421),
    ("Newsweek_PD_Travel_Adx_TTD_Expedia Group_N/A_Bex_N/A_US_Display_$5_Team-USA_ILee",
     29_259_398, 7, "2026-05-12", 412),
    ("Newsweek_PD_Telecommunications_Adx_DV360_GroupM_MFG_Google_Apple_US_Display_$7_Team-USA_ILee",
     29_034_459, 7, "2026-05-12", 130),
    # — Ben Karetny, Magnite/MIQ
    ("Newsweek_PA_Multi_Magnite_MIQ-Digital_DV360_General-Market_RON_Pol_US_Display_$5_Team-USA_BKaretny",
     105_695_111, 7, "2026-05-12", 504),
    ("Newsweek_PA_Multi_Magnite_MIQ-Digital_DV360_General-Market_RON_Pol_US_Video_$12_Team-USA_BKaretny",
     55_823_410, 7, "2026-05-12", 504),
    ("Newsweek_PA_Multi_Magnite_TTD_NA_NA_Frankly_Multi_US_Display_$5_Team-USA_BKaretny",
     59_900_001, 7, "2026-05-12", 312),
    # — Julie Amalfi, AdX
    ("Newsweek_PD_Entertainment_Adx_TTD_NA_NA_A24-Films_Eddington_US_Display_$5_Team-USA_JAmalfi",
     28_812_034, 7, "2026-05-12", 119),
    # — Magnite unknown-SSP edge case (DSP slipped into SSP slot)
    ("Newsweek_PA_Multi_DV360_TTD_NA_NA_State-Farm_AlwaysOn_US_Display_$4_Team-USA_RShore",
     106_120_500, 7, "2026-05-12", 220),
    # — Pubmatic legacy name (no Newsweek_ prefix) → Unknown SSP / Unknown seller
    ("PM_25_Q3_TTD_Crossmedia-MoheganSun-Brand_RON_Display_WebApp",
     8_120_503, 7, "2026-05-12", 200),
    # — House-attributed (KWebb), filtered out of per-seller breakouts
    ("Newsweek_PD_Multi_Adx_RTB House_NA_NA_RTB House_Always On_Global_Display_$8_Team-USA_KWebb",
     12_500_100, 7, "2026-05-12", 95),
]


def build_sample_deals() -> list[UnhealthyDeal]:
    out = []
    for raw, req, days, first, age in _RAW_DEALS:
        out.append(UnhealthyDeal(
            parsed=parse_deal(raw),
            bid_requests=req,
            days_in_data=days,
            first_seen=first,
            deal_age_days=age,
        ))
    return out


def build_sample_payload():
    return build_payload(
        build_sample_deals(),
        report_date=REPORT_DATE,
        lookback_days=LOOKBACK_DAYS,
        csv_url=CSV_URL,
        dashboard_url=DASHBOARD_URL,
    )
