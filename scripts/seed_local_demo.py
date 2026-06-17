#!/usr/bin/env python3
"""Seed a *throwaway* local SQLite DB with synthetic data so the dashboard
renders end-to-end with **no production database**.

This exists purely to develop/QA layout locally (e.g. the Campaigns "Cockpit"
work): it fabricates the tables the dashboard reads, with enough fake rows that
the Direct + PMP views, KPIs, Needs-attention rail, ending-soon card and PMP
signals all render. None of this data is real.

Usage:
    python scripts/seed_local_demo.py                 # writes ./local_demo.db
    python scripts/seed_local_demo.py --out /tmp/x.db

Then run the dashboard against it (no prod, no secrets):
    DATABASE_URL="sqlite:///$(pwd)/local_demo.db" streamlit run dashboard.py

The DV tables (dv_attention / dv_ivt) are intentionally NOT seeded — the
dashboard's pre-aggregation queries use Postgres-only syntax and fall back to
empty on SQLite, so Attention / SIVT / GIVT simply show "—". Everything else
renders.
"""
from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta

import pandas as pd
import sqlalchemy

TODAY = date.today()
YDAY = TODAY - timedelta(days=1)


def _name(prefix, vertical, exch, dsp, holding, agency, adv, camp, geo, fmt,
          floor, ae):
    """Build a 14-token Newsweek-convention name (advertiser=token7,
    campaign=token8) so dl.*_display_name parses it."""
    return "_".join([prefix, vertical, exch, dsp, holding, agency, adv, camp,
                     geo, fmt, floor, "Team-USA", ae])


# ── Direct line items (gam_campaigns) ────────────────────────────────────────
# Varied pacing / viewability / flight end so the KPI strip, Needs-attention
# (under/over-pacing + viewability), and the ending-soon landing-risk card all
# populate. order_id groups lines under an advertiser (badge numbering).
_DIRECT = [
    # advertiser, campaign, fmt_token, inv_format, order_id, order_name,
    #   goal, delivered, imp_1d, pace, view_rate, ctr, vcr, days_to_end, ae
    ("Infiniti", "Newsmakers-Centerstage", "Centerstage", "Banner", 4071001,
     103, 64.0, 0.41, None, 40, "SCarroll"),
    ("Infiniti", "QX65-Homepage-Takeover", "Display", "Banner", 4071001,
     82, 58.1, 0.22, None, 9, "SCarroll"),
    ("Infiniti", "Apple-News", "Apple-News", "Banner", 4071001,
     108, 71.0, 0.55, None, 60, "SCarroll"),
    ("Infiniti", "Custom-Audience-Pre-roll", "Video", "In-stream video", 4071001,
     97, 70.2, 0.30, 68.0, 22, "SCarroll"),
    ("Jeep", "Wagoneer-Apple-News", "Apple-News", "Banner", 4068002,
     110, 72.0, 0.48, None, 35, "JGentile"),
    ("Jeep", "Grand-Cherokee-Display", "Display", "Banner", 4068002,
     67, 55.0, 0.18, None, 12, "JGentile"),
    ("Jeep", "Gladiator-Preroll", "Video", "In-stream video", 4068002,
     125, 74.0, 0.33, 71.0, 48, "JGentile"),
    ("Cartier", "Holiday-Display", "Display", "Banner", 4055003,
     96, 75.4, 0.31, None, 5, "BRobinson"),
    ("Cartier", "Tank-Interstitial", "Interstitial", "Banner", 4055003,
     88, 62.0, 0.27, None, 6, "BRobinson"),
    ("Patek-Philippe", "Calatrava-Male", "Display", "Banner", 4055009,
     101, 73.0, 0.29, None, 30, "BRobinson"),
    ("Patek-Philippe", "Calatrava-Female", "Display", "Banner", 4055009,
     104, 76.0, 0.30, None, 30, "BRobinson"),
    ("AT&T", "AlwaysOn-Video", "Video", "In-stream video", 4090004,
     100, 70.0, 0.34, 72.0, 25, "ILee"),
    ("AT&T", "National-Mobility-Display", "Display", "Banner", 4090004,
     59, 51.0, 0.20, None, 14, "ILee"),
    ("Fidelity", "Workplace-FITO", "FITO", "Banner", 4071010,
     112, 68.0, 0.44, None, 9, "SCarroll"),
    ("H&R-Block", "Small-Business-Display", "Display", "Banner", 4071011,
     86, 69.0, 0.28, None, 11, "SCarroll"),
    ("BitDefender", "MidFunnel-Interscroller", "Interscroller", "Banner",
     4090012, 118, 60.0, 0.50, None, 40, "ILee"),
]


def build_gam_campaigns() -> pd.DataFrame:
    rows = []
    for i, (adv, camp, fmt_tok, inv_fmt, order_id, pace, view, ctr, vcr,
            days_end, ae) in enumerate(_DIRECT):
        goal = 500_000 + (i * 73_000) % 1_500_000
        delivered = int(goal * pace / 100.0 * 0.9)  # rough; pace col is source of truth
        imp_1d = max(1, int(delivered / 30))
        # one "new line item": its lifetime == its 1-day delivery
        if adv == "BitDefender":
            delivered = imp_1d
        measurable = max(delivered, 1)
        viewable = int(measurable * view / 100.0)
        li_id = 7300000000 + i
        name = _name("Newsweek_Direct", "Auto" if adv in ("Infiniti", "Jeep") else "Lifestyle",
                     "GAM", "NA", "NA", "NA", adv, camp, "US", fmt_tok, "NA", ae)
        rows.append({
            "line_item_id": str(li_id),
            "line_item_name": name,
            "order_id": str(order_id),
            "order_name": f"Newsweek_Direct_{adv}_2026",
            "advertiser": adv.replace("-", " "),
            "campaign_name": camp.replace("-", " "),
            "seller_ae": {"SCarroll": "Summer Carroll", "JGentile": "Jeremy Gentile",
                          "BRobinson": "Brian Robinson", "ILee": "Ivy Lee"}.get(ae, ae),
            "salesperson": ae,
            "team": "Team-USA",
            "ad_format": inv_fmt,
            "status": "Delivering",
            "start_date": (TODAY - timedelta(days=20)).isoformat(),
            "end_date": (TODAY + timedelta(days=days_end)).isoformat(),
            "report_start": (TODAY - timedelta(days=20)).isoformat(),
            "impressions_goal": goal,
            "cpm_rate": round(6 + (i % 5) * 2.5, 2),
            "lifetime_impressions_delivered": delivered,
            "remaining_impressions": max(goal - delivered, 0),
            "ad_server_clicks": int(delivered * ctr / 100.0),
            "pacing_pct": float(pace),
            "pacing_delta": float((i % 7) - 3) * 8.0,
            "ad_server_active_view_viewable_impressions_rate": view / 100.0,
            "lifetime_viewable_imps": viewable,
            "lifetime_measurable_imps": measurable,
            "ad_server_ctr": ctr / 100.0,
            "vcr": (vcr / 100.0) if vcr is not None else None,
            "ad_server_cpm_and_cpc_revenue": round(delivered / 1000.0 * (6 + (i % 5) * 2.5), 2),
            "impressions_1d": imp_1d,
        })
    return pd.DataFrame(rows)


# ── PMP deals ────────────────────────────────────────────────────────────────
_PMP_GAM = [
    ("Newsweek_PD_Finance_AdX_DV360_WPP_Mindshare_BlackRock_iShares-2026_US_Display_$8_Team-USA_BKaretny",
     "DV360", "Banner", 8.9),
    ("Newsweek_PG_Tech_AdX_DV360_NA_NA_BitDefender_MidFunnel_US_Display_$8_Team-USA_ILee",
     "DV360", "Banner", 8.2),
    ("Newsweek_PD_Finance_AdX_DV360_NA_Kepler_Fidelity_2026-Workplace_US_Display_$9_Team-USA_BKaretny",
     "DV360", "Banner", 9.4),
]
_PMP_MAG = [
    ("Newsweek_PA_Finance_Magnite_TTD_Publicis-Groupe_SparkFoundry_Vanguard_RON-NonNews_US_Native_$5_Team-USA_BKaretny",
     "The Trade Desk", "Native", 4.1),
    ("Newsweek_PA_Health_Magnite_AdTheorent_Omnicom_GSD&M_MD-Anderson_FY26-Breast-Treatment-Intent-Tier1_US_Display_$7.48_Team-USA_BKaretny",
     "AdTheorent", "Banner", 7.6),
]
_PMP_PUB = [
    ("Newsweek_PA_Pharma_Pubmatic_TTD_IPG_Kinesso_Kenvue_RON-Lifestyle_US_Video_$12_Team-USA_BKaretny",
     "The Trade Desk", "Video", 12.4),
    ("Newsweek_PA_Multi-Category_Pubmatic_TTD_NA_Coegi-Partners_Multi-Client_ROS_US_Display_$2_Team-USA_BKaretny",
     "The Trade Desk", "Banner", 2.3),
]


def _daily(n_days=14):
    return [(YDAY - timedelta(days=k)).isoformat() for k in range(n_days)]


def build_gam_pmp_deals() -> pd.DataFrame:
    rows = []
    for name, dsp, fmt, ecpm in _PMP_GAM:
        for d in _daily():
            impr = 40_000 + abs(hash(name + d)) % 60_000
            rows.append({
                "programmatic_deal_name": name,
                "order_name": name,
                "line_item_name": name,
                "dsp": dsp,
                "ad_format": fmt,
                "ad_server_impressions": impr,
                "ad_server_cpm_and_cpc_revenue": round(impr / 1000.0 * ecpm, 2),
                "ad_server_average_ecpm": ecpm,
                "date": d,
            })
    return pd.DataFrame(rows)


def build_gam_deal_bid_daily() -> pd.DataFrame:
    rows = []
    for name, *_ in _PMP_GAM:
        for d in _daily():
            req = 200_000 + abs(hash(name + d)) % 300_000
            rows.append({
                "programmatic_deal_name": name,
                "deals_bid_requests": req,
                "deals_bids": int(req * 0.35),
                "date": d,
            })
    return pd.DataFrame(rows)


def build_magnite_deal_daily() -> pd.DataFrame:
    rows = []
    for name, partner, fmt, ecpm in _PMP_MAG:
        for d in _daily():
            impr = 20_000 + abs(hash(name + d)) % 40_000
            req = impr * 6
            rows.append({
                "deal": name,
                "deal_id": str(abs(hash(name)) % 10_000_000),
                "partner": partner,
                "ad_format": fmt,
                "paid_impression": impr,
                "publisher_gross_revenue": round(impr / 1000.0 * ecpm, 2),
                "ecpm": ecpm,
                "bid_requests": req,
                "bid_responses": int(req * 0.4),
                "date": d,
            })
    return pd.DataFrame(rows)


def build_magnite_deal_demand() -> pd.DataFrame:
    rows = []
    for name, partner, fmt, ecpm in _PMP_MAG:
        rows.append({
            "deal": name,
            "deal_id": str(abs(hash(name)) % 10_000_000),
            "partner": partner,
            "date": YDAY.isoformat(),
            "bid_requests": 100_000,
            "bid_responses": 40_000,
        })
    return pd.DataFrame(rows)


def build_pubmatic_deals() -> pd.DataFrame:
    rows = []
    for name, dsp, fmt, ecpm in _PMP_PUB:
        for d in _daily():
            impr = 15_000 + abs(hash(name + d)) % 30_000
            req = impr * 5
            rows.append({
                "deal": name,
                "deal_label": name,
                "publisher_deal_id": str(abs(hash(name)) % 9_000_000),
                "deal_meta_id": str(abs(hash(name + "m")) % 9_000_000),
                "dsp": dsp,
                "ad_format": fmt,
                "paid_impressions": impr,
                "revenue": round(impr / 1000.0 * ecpm, 2),
                "ecpm": ecpm,
                "win_rate": 0.12,
                "total_requests": req,
                "non_zero_bid_responses": int(req * 0.3),
                "date": d,
            })
    return pd.DataFrame(rows)


def build_gam_pa_metadata() -> pd.DataFrame:
    # A couple of PA deals set up but not delivering (no-delivery signal).
    rows = [
        {"auction_name":
            "Newsweek_PA_Travel_AdX_TTD_NA_NA_TravelDesk_Lifestyle-2026_US_Display_$8_Team-USA_BKaretny",
         "order_name": "Newsweek_PA_Travel_TravelDesk",
         "deal_status": "ACTIVE", "floor_price": 8.0,
         "create_time": (TODAY - timedelta(days=140)).isoformat()},
        {"auction_name":
            "Newsweek_PA_Health_AdX_TTD_Publicis_PHM_UCB_Bimzelx-ROS_US_Display_$2_Team-USA_ILee",
         "order_name": "Newsweek_PA_Health_UCB",
         "deal_status": "PENDING", "floor_price": 2.0,
         "create_time": (TODAY - timedelta(days=30)).isoformat()},
    ]
    return pd.DataFrame(rows)


def build_pmp_last_bid_date() -> pd.DataFrame:
    # One stale deal (no bids 90+ days, still seen recently).
    rows = [
        {"ssp": "Magnite",
         "deal_key": "Newsweek_PA_Media_Magnite_Adelphic-DV360_Vivendi_Havas_Multi-Client_SocialEquity_US_Display_$2_Team-USA_BKaretny",
         "last_bid_date": (TODAY - timedelta(days=120)).isoformat(),
         "last_seen_date": (TODAY - timedelta(days=3)).isoformat(),
         "first_seen_date": (TODAY - timedelta(days=300)).isoformat(),
         "updated_at": datetime.utcnow().isoformat()},
    ]
    return pd.DataFrame(rows)


def build_dashboard_settings() -> pd.DataFrame:
    # Empty — the app falls back to settings.json. Created so the SELECT works.
    return pd.DataFrame(columns=["key", "value", "updated_at"])


_BUILDERS = {
    "gam_campaigns": build_gam_campaigns,
    "gam_pmp_deals": build_gam_pmp_deals,
    "gam_deal_bid_daily": build_gam_deal_bid_daily,
    "magnite_deal_daily": build_magnite_deal_daily,
    "magnite_deal_demand": build_magnite_deal_demand,
    "pubmatic_deals": build_pubmatic_deals,
    "gam_pa_metadata": build_gam_pa_metadata,
    "pmp_last_bid_date": build_pmp_last_bid_date,
    "dashboard_settings": build_dashboard_settings,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="local_demo.db", help="SQLite path to write")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    if os.path.exists(out):
        os.remove(out)
    engine = sqlalchemy.create_engine(f"sqlite:///{out}")
    for table, builder in _BUILDERS.items():
        df = builder()
        df.to_sql(table, engine, if_exists="replace", index=False)
        print(f"  {table:24} {len(df):4d} rows")
    print(f"\nWrote {out}")
    print('Run:  DATABASE_URL="sqlite:///%s" streamlit run dashboard.py' % out)


if __name__ == "__main__":
    main()
