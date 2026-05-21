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
df = client._run_report(
    dimensions=["DATE", "YIELD_GROUP_BUYER_NAME", "HEADER_BIDDER_INTEGRATION_TYPE_NAME"],
    metrics=["YIELD_GROUP_CALLOUTS"],
    start_date=start_date,
    end_date=end_date,
)

mask = (
    df["yield_group_buyer_name"].str.contains("Index", case=False, na=False)
    & (df["header_bidder_integration_type_name"] == "Exchange Bidding")
)
df = df.loc[mask].sort_values("date").reset_index(drop=True)

print(f"Index Exchange — Open Bidding ad requests, {start_date} → {end_date}\n")
if df.empty:
    print("(no rows returned — check buyer name spelling in GAM UI)")
else:
    print(df[["date", "yield_group_buyer_name", "yield_group_callouts"]].to_string(index=False))
    print(f"\nTotal: {int(df['yield_group_callouts'].sum()):,}")
