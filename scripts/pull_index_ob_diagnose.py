"""
Diagnostic: figure out whether the Index Exchange yield groups at Newsweek
are Open Bidding (Exchange Bidding) or Mediation. Tries a few report
combinations because GAM rejects HEADER_BIDDER_INTEGRATION_TYPE_NAME
alongside the full YIELD_GROUP_* dimension set.
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


def try_query(label, dims, metrics):
    print(f"\n=== {label} ===")
    print(f"    dims={dims}")
    print(f"    metrics={metrics}")
    try:
        df = client._run_report(
            dimensions=dims, metrics=metrics,
            start_date=start_date, end_date=end_date,
        )
        if "yield_group_buyer_name" in df.columns:
            df = df[df["yield_group_buyer_name"].str.contains("Index", case=False, na=False)]
        print(f"    OK — {len(df)} rows")
        if not df.empty:
            print(df.to_string(index=False))
    except Exception as e:
        msg = str(e)
        print(f"    FAILED: {msg[:200]}")


# 1) yield group name + integration type (no buyer dim, no per-buyer metrics)
try_query(
    "yield_group_name + integration_type",
    ["YIELD_GROUP_NAME", "HEADER_BIDDER_INTEGRATION_TYPE_NAME"],
    ["YIELD_GROUP_CALLOUTS"],
)

# 2) integration type by itself — coarsest, just confirms OB vs Mediation totals exist
try_query(
    "integration_type only",
    ["HEADER_BIDDER_INTEGRATION_TYPE_NAME"],
    ["YIELD_GROUP_CALLOUTS"],
)

# 3) buyer + integration type
try_query(
    "buyer + integration_type",
    ["YIELD_GROUP_BUYER_NAME", "HEADER_BIDDER_INTEGRATION_TYPE_NAME"],
    ["YIELD_GROUP_CALLOUTS"],
)

# 4) buyer + integration type — different metric (AD_REQUESTS instead of YIELD_GROUP_*)
try_query(
    "buyer + integration_type + AD_REQUESTS metric",
    ["YIELD_GROUP_BUYER_NAME", "HEADER_BIDDER_INTEGRATION_TYPE_NAME"],
    ["AD_REQUESTS"],
)
