#!/usr/bin/env python3
"""Monthly delivery + flight for given GAM order ids (read-only).

Usage: python scripts/probe_order_delivery.py START END ORDER_ID [ORDER_ID ...]
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient

start, end, *ids = sys.argv[1:]
g = GAMClient()
df = g._run_report(
    dimensions=["ORDER_ID", "ORDER_NAME", "DATE"],
    metrics=["AD_SERVER_IMPRESSIONS"],
    start_date=date.fromisoformat(start), end_date=date.fromisoformat(end),
    filters=[("ORDER_ID", "IN", [int(i) for i in ids])],
)
if df.empty:
    print("no delivery in window for", ids)
    sys.exit(0)
df["month"] = df["date"].str[:7]
for (oid, name), o in df.groupby(["order_id", "order_name"]):
    print(f"\n{oid}  {name}")
    print(f"  first/last delivery day: {o['date'].min()} → {o['date'].max()}")
    for m, v in o.groupby("month")["ad_server_impressions"].sum().items():
        print(f"  {m}: {int(v):>12,}")
