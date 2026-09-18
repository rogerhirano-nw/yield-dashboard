"""
Magnite Open Bidding video ad-request volume, to cross-check the SSP's own
"Seller Integration Type" report against GAM's view of the same callouts.

Context (2026-09-18): Magnite's video report for 2026-08-18 → 2026-09-16 shows
Open Bidding at 265,819,907 "Ad Requests" — 2.07x Prebid Server (RP Hosted) —
but only 37.5% of those become auctions, against 98.2% for Prebid Server. The
hypothesis is that Magnite counts an OB ad request at the Google callout,
before any auction decision, so the two bars are a callout count and an auction
count and are not comparable. If GAM's callouts to Magnite over the same window
land near 266M, that is confirmed outright.

GAM-side notes (see CLAUDE.md "GAM facts"):
- HEADER_BIDDER_INTEGRATION_TYPE_NAME is incompatible with every YIELD_GROUP_*
  metric, so we filter by YIELD_GROUP_NAME instead. Both yield groups at
  Newsweek are 100% Open Bidding (every ad source is OPEN_BIDDING), so a
  callout count filtered to `video` is OB-only by construction.
- YIELD_GROUP_CALLOUTS is the GAM UI's "Ad requests" column for a yield partner.
- Funnel: CALLOUTS -> BIDS -> AUCTIONS_WON -> IMPRESSIONS.
"""

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from gam_client import GAMClient  # noqa: E402

# The exact window of the Magnite report we are reconciling against.
START = date(2026, 8, 18)
END = date(2026, 9, 16)

# What the Magnite "Seller Integration Type" report claims for this window.
MAGNITE_CLAIMS = {
    "ad_requests": 265_819_907,
    "auctions": 99_800_117,
    "paid_impressions": 4_658_480,
}

METRICS = [
    "YIELD_GROUP_CALLOUTS",
    "YIELD_GROUP_BIDS",
    "YIELD_GROUP_AUCTIONS_WON",
    "YIELD_GROUP_IMPRESSIONS",
]
METRIC_COLS = [m.lower() for m in METRICS]


def main() -> None:
    client = GAMClient()
    df = client._run_report(
        dimensions=["DATE", "YIELD_GROUP_NAME", "YIELD_GROUP_BUYER_NAME"],
        metrics=METRICS,
        start_date=START,
        end_date=END,
    )

    for c in METRIC_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")

    print(f"GAM Open Bidding callouts, {START} -> {END}\n")

    # 1. Every buyer, so the exact spelling of Magnite's name is visible and we
    #    can see whether it is split across several ad sources.
    by_buyer = (
        df.groupby(["yield_group_name", "yield_group_buyer_name"], as_index=False)[METRIC_COLS]
        .sum()
        .sort_values(["yield_group_name", "yield_group_callouts"], ascending=[True, False])
    )
    print("=== All OB buyers by yield group (window totals) ===")
    print(by_buyer.to_string(index=False))

    # 2. Magnite only. Match loosely: the ad source may be named Magnite,
    #    Rubicon, or carry a DV+/Demand Manager suffix.
    mask = df["yield_group_buyer_name"].str.contains(
        "magnite|rubicon", case=False, na=False
    )
    mag = df[mask]
    if mag.empty:
        print("\n!! No buyer matched magnite|rubicon — check the names listed above.")
        return

    print(f"\nMatched buyer names: {sorted(mag['yield_group_buyer_name'].unique())}")

    mag_by_group = mag.groupby("yield_group_name", as_index=False)[METRIC_COLS].sum()
    print("\n=== Magnite by yield group ===")
    print(mag_by_group.to_string(index=False))

    video = mag[mag["yield_group_name"].str.contains("video", case=False, na=False)]
    if video.empty:
        print("\n!! No `video` yield group rows for Magnite.")
        return

    tot = video[METRIC_COLS].sum()
    callouts = int(tot["yield_group_callouts"])
    claim = MAGNITE_CLAIMS["ad_requests"]

    print("\n=== VIDEO yield group — Magnite, window totals ===")
    print(f"  GAM callouts (ad requests) : {callouts:,}")
    print(f"  GAM bids                   : {int(tot['yield_group_bids']):,}")
    print(f"  GAM auctions won           : {int(tot['yield_group_auctions_won']):,}")
    print(f"  GAM impressions            : {int(tot['yield_group_impressions']):,}")

    print("\n=== Reconciliation against Magnite's own report ===")
    print(f"  Magnite 'Ad Requests'      : {claim:,}")
    print(f"  GAM callouts               : {callouts:,}")
    diff = callouts - claim
    print(f"  Difference                 : {diff:+,} ({diff / claim:+.1%} vs Magnite)")
    print(f"  Magnite 'Auctions'         : {MAGNITE_CLAIMS['auctions']:,}")
    print(
        "  GAM callouts / Magnite auctions: "
        f"{callouts / MAGNITE_CLAIMS['auctions']:.2f}x"
    )
    print(f"  Magnite 'Paid Impressions' : {MAGNITE_CLAIMS['paid_impressions']:,}")
    print(
        f"  GAM impressions            : {int(tot['yield_group_impressions']):,} "
        "(GAM counts the OB impression it served; Magnite counts its own paid impression)"
    )
    print(
        "\n  Verdict: if the difference above is within a few percent, Magnite's "
        "'Ad Requests' for Open Bidding IS the Google callout count, and its 37.5% "
        "auction rate is a definitional boundary rather than a delivery problem."
    )

    # 3. Daily series, so a partial-day or gap at either end is visible rather
    #    than silently skewing the window total.
    daily = (
        video.groupby("date", as_index=False)[METRIC_COLS]
        .sum()
        .sort_values("date")
    )
    print("\n=== VIDEO yield group — Magnite, by day ===")
    print(daily.to_string(index=False))


if __name__ == "__main__":
    main()
