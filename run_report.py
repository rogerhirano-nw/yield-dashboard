import logging
import os
import sys
from pathlib import Path

import pandas as pd

from client import MagniteClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

key = os.environ["MAGNITE_KEY"]
secret = os.environ["MAGNITE_SECRET"]
publisher = os.environ["MAGNITE_PUBLISHER_ID"]

client = MagniteClient(api_key=key, api_secret=secret, account_id=publisher)

df = client.run_report(
    dimensions=["date", "deal", "deal_id"],
    metrics=["bid_requests", "bid_responses", "impressions", "paid_impression", "publisher_gross_revenue", "seller_net_revenue", "ecpm"],
    date_range="yesterday",
)

out_path = "magnite_yesterday_deals.csv"
df.to_csv(out_path, index=False)

print(f"\nSaved {len(df):,} rows to {out_path}", file=sys.stderr)
print(f"Columns: {list(df.columns)}\n", file=sys.stderr)

with pd.option_context("display.max_columns", None, "display.width", 200):
    print(df.head(20).to_string(index=False))
