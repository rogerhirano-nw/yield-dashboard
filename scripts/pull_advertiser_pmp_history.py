#!/usr/bin/env python3
"""
Every PMP / programmatic deal that delivered for one advertiser over a date
range, from GAM — e.g. "all Prudential PMPs in 2024 and 2025".

A deal can be tied to an advertiser two ways, and both are reported:
  A. **By deal name** — the Newsweek convention puts the advertiser at token 7
     (`Newsweek_PMP_<vertical>_..._<advertiser>_<campaign>_...`), so a
     case-insensitive substring match on DEAL_NAME / ORDER_NAME / buyer finds
     the deals that were set up for the advertiser.
  B. **By the ad that served** — GAM's Ad Exchange advertiser / classified
     brand on each impression. This catches the advertiser buying through a
     generic deal (e.g. an agency always-on PMP whose name doesn't say
     "Prudential"). Rows without a deal (open auction) are excluded from the
     deal table and shown only as one context total.

Uses the programmatic metric set (IMPRESSIONS / REVENUE_WITHOUT_CPD), same as
`GAMClient.run_deals_report` — AD_SERVER_* excludes Private Auction.

Each report runs independently; a dimension GAM rejects (or data past its
retention) is logged and skipped rather than failing the whole pull.

GAM creds are CI-only: run via `.github/workflows/pull_advertiser_pmp_history.yml`.

Usage:
    python scripts/pull_advertiser_pmp_history.py --match prudential \
        --start 2024-01-01 --end 2025-12-31 --xlsx out/prudential_pmps.xlsx
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gam_client import GAMClient  # noqa: E402

METRICS = ["IMPRESSIONS", "REVENUE_WITHOUT_CPD"]
NO_DEAL = {"", "(Not applicable)", "-", "None", "nan"}


def _decode_month(code) -> str:
    """MONTH_YEAR int code = (year - 1900) * 12 + (month - 1)."""
    code = int(code)
    return f"{code // 12 + 1900:04d}-{code % 12 + 1:02d}"


def _run(client: GAMClient, label: str, dims: list[str], start: date, end: date) -> pd.DataFrame | None:
    try:
        df = client._run_report(dimensions=dims, metrics=METRICS, start_date=start, end_date=end)
    except Exception as e:  # noqa: BLE001 — one rejected report shouldn't sink the rest
        print(f"[{label}] {dims} failed: {type(e).__name__}: {str(e)[:300]}")
        return None
    for c in df.select_dtypes(include=["object", "str"]).columns:
        df[c] = df[c].astype(str).str.strip()
    df["month"] = df["month_year"].map(_decode_month)
    df["year"] = df["month"].str[:4]
    df["impressions"] = pd.to_numeric(df["impressions"], errors="coerce").fillna(0).astype("int64")
    df["revenue"] = pd.to_numeric(df["revenue_without_cpd"], errors="coerce").fillna(0.0)
    cov = df.groupby("month")["impressions"].sum()
    print(f"[{label}] {len(df)} rows; months {cov.index.min()}..{cov.index.max()} "
          f"({len(cov)} with data); impressions by year: "
          f"{df.groupby('year')['impressions'].sum().to_dict()}")
    return df.drop(columns=["month_year", "revenue_without_cpd"])


def _match(df: pd.DataFrame, cols: list[str], needle: str) -> pd.Series:
    m = pd.Series(False, index=df.index)
    for c in cols:
        if c in df.columns:
            m |= df[c].str.contains(needle, case=False, na=False, regex=False)
    return m


def _has_deal(df: pd.DataFrame) -> pd.Series:
    return ~df["deal_name"].isin(NO_DEAL)


def _rollup(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    g = (df.groupby(keys, dropna=False)[["impressions", "revenue"]].sum().reset_index())
    g["ecpm"] = (g["revenue"] / g["impressions"].where(g["impressions"] > 0) * 1000).round(2)
    g["revenue"] = g["revenue"].round(2)
    return g


def _print(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} — {len(df)} rows ===")
    if df.empty:
        print("  (none)")
        return
    with pd.option_context("display.max_rows", None, "display.max_columns", None,
                           "display.width", 250, "display.max_colwidth", 110):
        print(df.to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", required=True, help="case-insensitive substring, e.g. prudential")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--xlsx", help="write the tables to this workbook")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    needle = a.match
    client = GAMClient()
    sheets: dict[str, pd.DataFrame] = {}

    # ── A. deals named for the advertiser ─────────────────────────────────
    deal_dims = ["MONTH_YEAR", "DEAL_ID", "DEAL_NAME", "DEAL_BUYER_NAME",
                 "PROGRAMMATIC_CHANNEL_NAME", "ORDER_NAME"]
    deals = _run(client, "deals", deal_dims, start, end)
    if deals is None:  # DEAL_ID is the only dim not already proven in run_deals_report
        deals = _run(client, "deals-noid", [d for d in deal_dims if d != "DEAL_ID"], start, end)
    if deals is not None:
        hit = deals[_has_deal(deals) & _match(deals, ["deal_name", "order_name", "deal_buyer_name"], needle)]
        id_cols = [c for c in ["deal_id", "deal_name", "programmatic_channel_name", "deal_buyer_name"]
                   if c in hit.columns]
        by_year = _rollup(hit, id_cols + ["year"]).sort_values(["year", "revenue"], ascending=[True, False])
        monthly = _rollup(hit, ["month", "deal_name"]).sort_values(["month", "revenue"], ascending=[True, False])
        totals = _rollup(hit, ["year"])
        _print(f"A. Deals named '{needle}' — totals by year", totals)
        _print(f"A. Deals named '{needle}' — by deal × year", by_year)
        _print(f"A. Deals named '{needle}' — by month × deal", monthly)
        sheets.update({"A_totals": totals, "A_by_deal_year": by_year, "A_by_month": monthly,
                       "A_raw": hit})

    # ── B. deal delivery where the served ad was the advertiser ───────────
    for label, brand_dim in [("adx-advertiser", "ADVERTISER_NAME"),
                             ("classified-advertiser", "CLASSIFIED_ADVERTISER_NAME"),
                             ("classified-brand", "CLASSIFIED_BRAND_NAME")]:
        df = _run(client, label, ["MONTH_YEAR", brand_dim, "DEAL_NAME", "PROGRAMMATIC_CHANNEL_NAME"],
                  start, end)
        if df is None:
            continue
        bcol = brand_dim.lower()
        hit = df[_match(df, [bcol], needle)]
        names = sorted(hit[bcol].unique())
        print(f"[{label}] '{needle}' matched {bcol} values: {names}")
        in_deal = hit[_has_deal(hit)]
        by_year = _rollup(in_deal, [bcol, "deal_name", "programmatic_channel_name", "year"]) \
            .sort_values(["year", "revenue"], ascending=[True, False])
        chan = _rollup(hit, ["year", "programmatic_channel_name"])
        _print(f"B. {brand_dim} ~ '{needle}' — deal delivery by deal × year", by_year)
        _print(f"B. {brand_dim} ~ '{needle}' — all delivery by channel (context; incl. open auction)", chan)
        sheets[f"B_{label}"[:31]] = by_year
        sheets[f"B_{label}_channel"[:31]] = chan

    if a.xlsx and sheets:
        Path(a.xlsx).parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(a.xlsx) as xw:
            for name, df in sheets.items():
                df.to_excel(xw, sheet_name=name, index=False)
        print(f"\nWrote {a.xlsx}")


if __name__ == "__main__":
    main()
