"""
One-off pull: GAM Open Bidding ad requests per yield partner.

Usage:
    python scripts/pull_open_bidding.py                    # last 7 days, all partners
    python scripts/pull_open_bidding.py --partner Index    # filter to partners containing "Index"
    python scripts/pull_open_bidding.py --days 14          # last 14 days

Reads GAM_SERVICE_ACCOUNT_JSON + GAM_NETWORK_ID from .env (same as the
dashboard). Reports yesterday-back N days to avoid same-day latency.

Dimension YIELD_GROUP_BUYER_NAME is the Open Bidding partner identity GAM's
"Yield groups" / "Open Bidding" reports key on. Metric YIELD_GROUP_CALLOUTS
is the API equivalent of the UI's "Ad requests" column for OB partners
(bid requests sent to that partner).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from gam_client import GAMClient


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--partner", default="Index",
                    help="Substring match on YIELD_GROUP_BUYER_NAME (case-insensitive). "
                         "Use '' to skip filtering. Default: Index.")
    ap.add_argument("--days", type=int, default=7,
                    help="Number of days back to pull, ending yesterday. Default: 7.")
    args = ap.parse_args()

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=args.days - 1)

    client = GAMClient()
    df = client._run_report(
        dimensions=["DATE", "YIELD_GROUP_BUYER_NAME"],
        metrics=[
            "YIELD_GROUP_CALLOUTS",
            "YIELD_GROUP_BIDS",
            "YIELD_GROUP_IMPRESSIONS",
            "YIELD_GROUP_ESTIMATED_REVENUE",
        ],
        start_date=start,
        end_date=end,
    )

    if df.empty:
        print(f"No Open Bidding data returned for {start} → {end}.")
        return 1

    df["yield_group_buyer_name"] = df["yield_group_buyer_name"].fillna("").str.strip()
    df = df[df["yield_group_buyer_name"] != ""].copy()

    if args.partner:
        mask = df["yield_group_buyer_name"].str.contains(args.partner, case=False, na=False)
        matched = df[mask].copy()
        if matched.empty:
            print(f"No yield partners matching '{args.partner}' in {start} → {end}.")
            print("Partners present in the report:")
            for n in sorted(df["yield_group_buyer_name"].unique()):
                print(f"  - {n}")
            return 1
        df = matched

    df["yield_group_callouts"] = pd.to_numeric(df["yield_group_callouts"], errors="coerce").fillna(0).astype("int64")
    df["yield_group_bids"] = pd.to_numeric(df["yield_group_bids"], errors="coerce").fillna(0).astype("int64")
    df["yield_group_impressions"] = pd.to_numeric(df["yield_group_impressions"], errors="coerce").fillna(0).astype("int64")
    df["yield_group_estimated_revenue"] = pd.to_numeric(df["yield_group_estimated_revenue"], errors="coerce").fillna(0.0)

    print(f"\nGAM Open Bidding — yield partner '{args.partner or 'ALL'}' — {start} → {end}\n")

    by_partner_day = (
        df.groupby(["yield_group_buyer_name", "date"], as_index=False)
          .agg(ad_requests=("yield_group_callouts", "sum"),
               bids=("yield_group_bids", "sum"),
               impressions=("yield_group_impressions", "sum"),
               est_revenue_usd=("yield_group_estimated_revenue", "sum"))
          .sort_values(["yield_group_buyer_name", "date"])
    )
    print(by_partner_day.to_string(index=False))

    totals = (
        df.groupby("yield_group_buyer_name", as_index=False)
          .agg(ad_requests=("yield_group_callouts", "sum"),
               bids=("yield_group_bids", "sum"),
               impressions=("yield_group_impressions", "sum"),
               est_revenue_usd=("yield_group_estimated_revenue", "sum"))
          .sort_values("ad_requests", ascending=False)
    )
    totals["bid_rate_pct"] = (totals["bids"] / totals["ad_requests"].where(totals["ad_requests"] > 0) * 100).round(2)
    totals["win_rate_pct"] = (totals["impressions"] / totals["bids"].where(totals["bids"] > 0) * 100).round(2)

    print(f"\nTotals over {args.days} days:\n")
    print(totals.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
