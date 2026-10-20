#!/usr/bin/env python3
"""Chumba (VGW) by-line-item performance pull for a date window.

GAM delivery (impressions, clicks, revenue) per Chumba line item, joined to the
TTD Chumba feed's conversions/spend by the shared deal id (GAM DEAL_ID == TTD
deal_id for our PG flights). Prints CSV blocks to stdout and writes an .xlsx.

GAM creds + DATABASE_URL are CI-only, so this runs via
`.github/workflows/chumba_li_report.yml`.

    python scripts/chumba_li_report.py --start 2026-10-01 --end 2026-10-19
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import sqlalchemy

from gam_client import GAMClient

NEEDLES = ("chumba", "vgw")


def _is_chumba(s) -> bool:
    s = str(s or "").lower()
    return any(n in s for n in NEEDLES)


def gam_frame(start: date, end: date) -> pd.DataFrame:
    client = GAMClient()
    df = client._run_report(
        dimensions=["LINE_ITEM_ID", "LINE_ITEM_NAME", "ORDER_ID", "ORDER_NAME"],
        metrics=["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_REVENUE"],
        start_date=start, end_date=end,
    )
    df = df[df["order_name"].map(_is_chumba) | df["line_item_name"].map(_is_chumba)].copy()
    df["line_item_id"] = df["line_item_id"].astype(str).str.strip()
    for c in ("ad_server_impressions", "ad_server_clicks", "ad_server_revenue"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    dm = client.run_li_deal_map_report(start, end)
    df = df.merge(dm, on="line_item_id", how="left")
    return df


def ttd_frame(start: date, end: date) -> tuple[pd.DataFrame, object]:
    eng = sqlalchemy.create_engine(os.environ["DATABASE_URL"])
    with eng.connect() as conn:
        mx = conn.execute(sqlalchemy.text("SELECT MIN(date), MAX(date) FROM ttd_chumba")).fetchone()
        cols = [r[0] for r in conn.execute(sqlalchemy.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='ttd_chumba'"))]
        print("TTD columns:", cols)
        print("TTD date range:", mx)
        df = pd.read_sql(sqlalchemy.text(
            "SELECT * FROM ttd_chumba WHERE date >= :s AND date <= :e"),
            conn, params={"s": str(start), "e": str(end)})
    return df, mx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="chumba_li_report.xlsx")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)

    g = gam_frame(start, end)
    print(f"\n=== GAM Chumba line items {start}..{end}: {len(g)} rows ===")
    print("GAMCSV_BEGIN")
    print(g.to_csv(index=False), end="")
    print("GAMCSV_END")

    t, _ = ttd_frame(start, end)
    print(f"\n=== TTD Chumba rows {start}..{end}: {len(t)} ===")
    if not t.empty:
        t["deal_id"] = t["deal_id"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        num = [c for c in t.columns if c not in ("deal_id",) and pd.api.types.is_numeric_dtype(t[c])]
        by_deal = t.groupby("deal_id")[num].sum().reset_index()
        print("TTDDEAL_BEGIN")
        print(by_deal.to_csv(index=False), end="")
        print("TTDDEAL_END")
        by_day = t.groupby("date")[num].sum().reset_index()
        print("TTDDAY_BEGIN")
        print(by_day.to_csv(index=False), end="")
        print("TTDDAY_END")
        str_cols = [c for c in t.columns if t[c].dtype == object and c not in ("date", "deal_id")]
        for c in str_cols:
            print(f"distinct {c}: {sorted(t[c].dropna().astype(str).unique())[:40]}")
    with pd.ExcelWriter(a.out) as xw:
        g.to_excel(xw, sheet_name="gam", index=False)
        t.to_excel(xw, sheet_name="ttd_raw", index=False)


if __name__ == "__main__":
    main()
