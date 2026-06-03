"""
Pull a 30-day Magnite auction-funnel report and save it as a CSV.

Dimensions: date, site, device_type_name_v1
Metrics:    ad_requests, bid_requests, bid_responses, auctions,
            impressions, publisher_gross_revenue, ecpm

Derived columns added locally:
  bid_rate   = bid_responses / bid_requests
  fill_rate  = impressions   / ad_requests
  win_rate   = impressions   / bid_responses

Usage:
    python scripts/magnite_auction_report.py
    python scripts/magnite_auction_report.py --by date          # daily summary
    python scripts/magnite_auction_report.py --by site          # site rollup
    python scripts/magnite_auction_report.py --by date,site     # date × site
    python scripts/magnite_auction_report.py --out report.csv   # custom output path

Requires env vars: MAGNITE_KEY, MAGNITE_SECRET, MAGNITE_PUBLISHER_ID
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Make sure the project root is on the path when run from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from client import MagniteClient

METRICS = [
    "ad_requests",
    "bid_requests",
    "bid_responses",
    "auctions",
    "impressions",
    "publisher_gross_revenue",
    "ecpm",
]

GRANULARITY_DIMS = {
    "date":       ["date"],
    "site":       ["site"],
    "device":     ["device_type_name_v1"],
    "date,site":  ["date", "site"],
    "date,device": ["date", "device_type_name_v1"],
    "full":       ["date", "site", "device_type_name_v1"],
}


def _pct(n: pd.Series, d: pd.Series) -> pd.Series:
    return (n / d.replace(0, pd.NA) * 100).round(2)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bid_rate_%"]  = _pct(df["bid_responses"], df["bid_requests"])
    df["fill_rate_%"] = _pct(df["impressions"],   df["ad_requests"])
    df["win_rate_%"]  = _pct(df["impressions"],   df["bid_responses"])
    return df


def print_summary(df: pd.DataFrame, start: str, end: str) -> None:
    totals = {
        "ad_requests":             df["ad_requests"].sum(),
        "bid_requests":            df["bid_requests"].sum(),
        "bid_responses":           df["bid_responses"].sum(),
        "auctions":                df["auctions"].sum(),
        "impressions":             df["impressions"].sum(),
        "publisher_gross_revenue": df["publisher_gross_revenue"].sum(),
    }
    avg_ecpm = (df["publisher_gross_revenue"].sum() / df["impressions"].sum() * 1000
                if df["impressions"].sum() else 0)

    print(f"\n{'='*60}")
    print(f"  Magnite Auction Report — {start} to {end}")
    print(f"{'='*60}")
    print(f"  Ad Requests:      {totals['ad_requests']:>15,.0f}")
    print(f"  Bid Requests:     {totals['bid_requests']:>15,.0f}")
    print(f"  Bid Responses:    {totals['bid_responses']:>15,.0f}")
    print(f"  Auctions:         {totals['auctions']:>15,.0f}")
    print(f"  Impressions:      {totals['impressions']:>15,.0f}")
    print(f"  Revenue (USD):    {totals['publisher_gross_revenue']:>15,.2f}")
    print(f"  eCPM:             {avg_ecpm:>14.2f}")
    print(f"  Bid Rate:         {totals['bid_responses'] / max(totals['bid_requests'], 1) * 100:>13.1f}%")
    print(f"  Fill Rate:        {totals['impressions']   / max(totals['ad_requests'],  1) * 100:>13.1f}%")
    print(f"  Win Rate:         {totals['impressions']   / max(totals['bid_responses'], 1) * 100:>13.1f}%")
    print(f"{'='*60}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="30-day Magnite auction report")
    ap.add_argument(
        "--by",
        default="date",
        choices=list(GRANULARITY_DIMS),
        help="Grouping dimensions (default: date)",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output CSV path (default: magnite_auction_30d_<start>_<end>.csv)",
    )
    ap.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back from yesterday (default: 30)",
    )
    args = ap.parse_args()

    api_key    = os.environ.get("MAGNITE_KEY")
    api_secret = os.environ.get("MAGNITE_SECRET")
    pub_id     = os.environ.get("MAGNITE_PUBLISHER_ID")

    missing = [k for k, v in {"MAGNITE_KEY": api_key, "MAGNITE_SECRET": api_secret,
                               "MAGNITE_PUBLISHER_ID": pub_id}.items() if not v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}")

    yesterday = date.today() - timedelta(days=1)
    start_d   = yesterday - timedelta(days=args.days - 1)
    start     = start_d.isoformat()
    end       = yesterday.isoformat()

    dims = GRANULARITY_DIMS[args.by]
    # Always include date in the API pull so the API doesn't aggregate everything
    # into one row; we'll drop it later if the user didn't ask for it.
    api_dims = dims if "date" in dims else ["date"] + dims

    out_path = args.out or f"magnite_auction_30d_{start}_{end}.csv"

    print(f"Pulling Magnite auction report: {start} → {end}  (grouped by: {args.by})")
    print("Submitting report to Magnite API — may take 1-3 minutes to process…")

    client = MagniteClient(
        api_key=api_key,
        api_secret=api_secret,
        account_id=pub_id,
    )

    df = client.run_report(
        dimensions=api_dims,
        metrics=METRICS,
        date_range=None,
        start=start,
        end=end,
    )

    if df.empty:
        sys.exit("Magnite returned 0 rows — check credentials or date range.")

    # Coerce numeric columns (API returns strings for some fields)
    for col in METRICS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # If user asked for a rollup without date, collapse it now
    if "date" not in dims and "date" in df.columns:
        agg = {m: "sum" for m in METRICS if m in df.columns and m != "ecpm"}
        df = df.groupby(dims, as_index=False).agg(agg)
        # Recompute eCPM from rolled-up revenue + impressions
        df["ecpm"] = (df["publisher_gross_revenue"] / df["impressions"].replace(0, pd.NA) * 1000).round(4)
    elif "date" in df.columns:
        df = df.sort_values("date")

    df = add_derived(df)
    df.to_csv(out_path, index=False)

    print_summary(df, start, end)
    print(f"Saved {len(df):,} rows → {out_path}")

    # Print a quick top-10 preview
    preview_cols = [c for c in ["date", "site", "device_type_name_v1",
                                 "ad_requests", "impressions", "publisher_gross_revenue",
                                 "fill_rate_%", "win_rate_%"] if c in df.columns]
    print(df[preview_cols].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
