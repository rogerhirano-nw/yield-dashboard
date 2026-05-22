"""
Index Exchange Open Bidding ad-request volume, YTD by month, split between
the `display` and `video` yield groups.

GAM-side notes:
- HEADER_BIDDER_INTEGRATION_TYPE_NAME is incompatible with YIELD_GROUP_*
  metrics in the report definition, so we filter by YIELD_GROUP_NAME instead.
  At Newsweek both `display` and `video` yield groups are confirmed
  Open Bidding (every ad source has yieldIntegrationType: OPEN_BIDDING).
- YIELD_GROUP_CALLOUTS is the GAM UI's "Ad requests" column for a yield
  partner.
- Per CLAUDE.md, today's data has latency — end date is yesterday.
"""

import os
import sys
from datetime import date, timedelta
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

end_date   = date.today() - timedelta(days=1)
start_date = date(end_date.year, 1, 1)

client = GAMClient()
df = client._run_report(
    dimensions=["DATE", "YIELD_GROUP_NAME", "YIELD_GROUP_BUYER_NAME"],
    metrics=[
        "YIELD_GROUP_CALLOUTS",
        "YIELD_GROUP_BIDS",
        "YIELD_GROUP_AUCTIONS_WON",
        "YIELD_GROUP_IMPRESSIONS",
    ],
    start_date=start_date,
    end_date=end_date,
)

df = df[df["yield_group_buyer_name"].str.contains("Index", case=False, na=False)].copy()
df["date"]  = pd.to_datetime(df["date"])
df["month"] = df["date"].dt.strftime("%Y-%m")

metric_cols = [
    "yield_group_callouts",
    "yield_group_bids",
    "yield_group_auctions_won",
    "yield_group_impressions",
]
for c in metric_cols:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")

monthly = (
    df.groupby(["month", "yield_group_name"], as_index=False)[metric_cols]
      .sum()
      .sort_values(["month", "yield_group_name"])
      .reset_index(drop=True)
)

print(f"Index Exchange — Open Bidding, YTD by month, {start_date} → {end_date}\n")

# Wide view: month rows, display vs video columns for the ad-request metric.
wide_requests = monthly.pivot(
    index="month", columns="yield_group_name", values="yield_group_callouts"
).fillna(0).astype("int64")
wide_requests["total"] = wide_requests.sum(axis=1)
print("=== Ad requests (YIELD_GROUP_CALLOUTS) ===")
print(wide_requests.to_string())

# Full breakdown — every metric, every month, both yield groups.
print("\n=== Full breakdown per month / yield group ===")
print(monthly.to_string(index=False))

# YTD totals per yield group.
ytd = monthly.groupby("yield_group_name", as_index=False)[metric_cols].sum()
print("\n=== YTD totals per yield group ===")
print(ytd.to_string(index=False))

# Grand total.
grand = monthly[metric_cols].sum()
print(f"\nYTD grand total — ad requests: {int(grand['yield_group_callouts']):,}")
print(f"YTD grand total — bids:        {int(grand['yield_group_bids']):,}")
print(f"YTD grand total — wins:        {int(grand['yield_group_auctions_won']):,}")
print(f"YTD grand total — impressions: {int(grand['yield_group_impressions']):,}")
