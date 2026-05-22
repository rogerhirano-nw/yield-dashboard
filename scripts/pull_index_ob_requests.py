"""
One-off: pull Index Exchange ad requests as an Open Bidding Yield Partner
for the past 7 days (yesterday minus 6 → yesterday, per CLAUDE.md).

Run locally from the repo root, where .env with GAM_SERVICE_ACCOUNT_JSON
and GAM_NETWORK_ID exists:

    python3 scripts/pull_index_ob_requests.py
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

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

end_date   = date.today() - timedelta(days=1)
start_date = end_date - timedelta(days=6)

client = GAMClient()
# HEADER_BIDDER_INTEGRATION_TYPE_NAME is incompatible with YIELD_GROUP_*
# metrics (GAM returns REPORT_ERROR_CONSTRAINTS_INCOMPATIBILITY). Use
# YIELD_GROUP_NAME instead — at Newsweek the yield-group name typically
# encodes the integration type (e.g. "OpenBidding_…") which is enough to
# spot OB vs mediation rows.
df = client._run_report(
    dimensions=["DATE", "YIELD_GROUP_NAME", "YIELD_GROUP_BUYER_NAME"],
    metrics=["YIELD_GROUP_CALLOUTS", "YIELD_GROUP_BIDS", "YIELD_GROUP_AUCTIONS_WON", "YIELD_GROUP_IMPRESSIONS"],
    start_date=start_date,
    end_date=end_date,
)

mask = df["yield_group_buyer_name"].str.contains("Index", case=False, na=False)
df = df.loc[mask].sort_values(["date", "yield_group_name"]).reset_index(drop=True)

print(f"Index Exchange — Yield Group activity, {start_date} → {end_date}\n")
if df.empty:
    print("(no rows for Index — check buyer name spelling in the GAM UI)")
else:
    print(df.to_string(index=False))
    print(f"\nTotal callouts (ad requests): {int(df['yield_group_callouts'].sum()):,}")
    print(f"Total bids:                   {int(df['yield_group_bids'].sum()):,}")
    print(f"Total auctions won:           {int(df['yield_group_auctions_won'].sum()):,}")
    print(f"Total impressions:            {int(df['yield_group_impressions'].sum()):,}")
    print()
    print("Distinct yield groups Index appears in:")
    for yg in sorted(df["yield_group_name"].dropna().unique()):
        print(f"  - {yg}")
