#!/usr/bin/env python3
"""Reconcile a Comscore CCR invoice against GAM delivery.

Comscore bills CCR (Campaign Ratings) per campaign ID = GAM order id, at a CPM
on the impressions *its tag* counted in the month. This pulls GAM's own
impressions for the same orders and month so the two can be compared.

GAM numbers are given two ways:
  * all line items on the order, and
  * excluding lines Comscore does not tag (Apple News, newsletter — Kael,
    2026-08-28; the same rule scripts/build_ccr_form.py uses).
The second is the like-for-like comparison. Report dates are in the network
timezone (America/New_York), which is how the month is bounded.

Read-only against GAM.

Usage:
  python scripts/reconcile_comscore_invoice.py --invoice scripts/invoices/CINV-CUS-143077.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_NOT_TAGGED = re.compile(r"apple[-_ ]?news|newsletter", re.I)


def pct(a: float, b: float) -> str:
    return "n/a" if not b else f"{(a - b) / b * 100:+.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoice", required=True)
    args = ap.parse_args()
    inv = json.loads(Path(args.invoice).read_text())
    start = date.fromisoformat(inv["period_start"])
    end = date.fromisoformat(inv["period_end"])
    cpm = float(inv["cpm"])
    lines = {str(l["order_id"]): l for l in inv["lines"]}

    from gam_client import GAMClient

    gam = GAMClient()
    df = gam._run_report(
        dimensions=["ORDER_ID", "ORDER_NAME", "LINE_ITEM_ID", "LINE_ITEM_NAME"],
        metrics=["AD_SERVER_IMPRESSIONS", "IMPRESSIONS"],
        start_date=start,
        end_date=end,
        filters=[("ORDER_ID", "IN", [int(o) for o in lines])],
    )
    df["order_id"] = df["order_id"].astype(str).str.replace(r"\.0$", "", regex=True)
    for c in ("ad_server_impressions", "impressions"):
        df[c] = df[c].fillna(0).astype(int)
    df["tagged"] = ~df["line_item_name"].fillna("").str.contains(_NOT_TAGGED)

    print(f"Invoice {inv['invoice']} · {start} → {end} (GAM network tz) · CPM ${cpm:.2f}\n")
    hdr = f"{'Order':<11} {'Comscore':>12} {'GAM tagged':>12} {'Δ':>7} {'GAM all LIs':>12} {'Δ':>7} {'Billed $':>9} {'GAM $':>9}"
    print(hdr)
    print("-" * len(hdr))
    tot = dict(cs=0, tag=0, all=0)
    for oid, l in lines.items():
        o = df[df["order_id"] == oid]
        cs = int(l["impressions"])
        tag = int(o.loc[o["tagged"], "ad_server_impressions"].sum())
        al = int(o["ad_server_impressions"].sum())
        tot["cs"] += cs; tot["tag"] += tag; tot["all"] += al
        print(f"{oid:<11} {cs:>12,} {tag:>12,} {pct(cs, tag):>7} {al:>12,} {pct(cs, al):>7} "
              f"{cs * cpm / 1000:>9,.2f} {tag * cpm / 1000:>9,.2f}")
    print("-" * len(hdr))
    print(f"{'TOTAL':<11} {tot['cs']:>12,} {tot['tag']:>12,} {pct(tot['cs'], tot['tag']):>7} "
          f"{tot['all']:>12,} {pct(tot['cs'], tot['all']):>7} "
          f"{tot['cs'] * cpm / 1000:>9,.2f} {tot['tag'] * cpm / 1000:>9,.2f}")
    print("\nΔ = (Comscore − GAM) / GAM. Positive = Comscore counted more than GAM served.")

    print("\nPer line item (August):")
    for oid in lines:
        o = df[df["order_id"] == oid].sort_values("ad_server_impressions", ascending=False)
        name = o["order_name"].iloc[0] if len(o) else "(no August delivery in GAM)"
        print(f"\n{oid}  {name}")
        for _, r in o.iterrows():
            flag = "" if r["tagged"] else "  [not Comscore-tagged]"
            print(f"  {r['line_item_id']:>12}  {int(r['ad_server_impressions']):>11,}  "
                  f"(total impr {int(r['impressions']):>11,})  {r['line_item_name']}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
