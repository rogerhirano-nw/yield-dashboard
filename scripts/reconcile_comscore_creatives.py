#!/usr/bin/env python3
"""Creative-level Comscore check for a CCR invoice (read-only against GAM).

Comscore counts only impressions on creatives that actually carry its pixel
(sb.scorecardresearch.com, c2=6972086). The order-level reconciler
(reconcile_comscore_invoice.py) approximates that by dropping Apple News /
newsletter lines; this one checks every creative that delivered in the billing
month:

  * GAM AD_SERVER_IMPRESSIONS per order × line item × creative for the month
  * each creative's full definition (snippet, template values, third-party
    impression trackers, VAST tracking) searched for the Comscore pixel

Each creative is classed as
  TAGGED    pixel found on the creative
  UNTAGGED  nothing found (e.g. a plain image creative)
  OPAQUE    third-party VAST redirect / custom tag whose content GAM can't see —
            the agency's own wrapper may still fire Comscore
and the per-order sums are compared to Comscore's billed impressions.

Usage:
  python scripts/reconcile_comscore_creatives.py --invoice scripts/invoices/CINV-CUS-143077.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

V = "v202605"
_PIXEL = re.compile(r"scorecardresearch|comscore|6972086", re.I)
_OPAQUE_TYPES = {"VastRedirectCreative", "ThirdPartyCreative", "CustomCreative"}


def _soap():
    from googleads import ad_manager, oauth2

    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        kf = f.name
    oc = oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp")
    return ad_manager, ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"])


def classify(ctype: str, blob: str) -> str:
    if _PIXEL.search(blob):
        return "TAGGED"
    # A third-party tag/VAST can carry Comscore inside the agency's own payload,
    # which GAM never sees — don't call that untagged.
    if ctype in _OPAQUE_TYPES and re.search(r"https?://|<script|<iframe", blob, re.I):
        return "OPAQUE"
    return "UNTAGGED"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoice", required=True)
    args = ap.parse_args()
    inv = json.loads(Path(args.invoice).read_text())
    start, end = date.fromisoformat(inv["period_start"]), date.fromisoformat(inv["period_end"])
    billed = {str(l["order_id"]): int(l["impressions"]) for l in inv["lines"]}

    from gam_client import GAMClient
    df = GAMClient()._run_report(
        dimensions=["ORDER_ID", "LINE_ITEM_ID", "LINE_ITEM_NAME", "CREATIVE_ID", "CREATIVE_NAME"],
        metrics=["AD_SERVER_IMPRESSIONS"],
        start_date=start, end_date=end,
        filters=[("ORDER_ID", "IN", [int(o) for o in billed])],
    )
    for c in ("order_id", "line_item_id", "creative_id"):
        df[c] = df[c].astype(str).str.replace(r"\.0$", "", regex=True)
    df["ad_server_impressions"] = df["ad_server_impressions"].fillna(0).astype(int)

    ad_manager, client = _soap()
    from zeep.helpers import serialize_object
    svc = client.GetService("CreativeService", version=V)
    ids = sorted({i for i in df["creative_id"] if i.isdigit()})
    info: dict[str, tuple[str, str]] = {}
    for k in range(0, len(ids), 200):
        chunk = ids[k:k + 200]
        sb = ad_manager.StatementBuilder(version=V).Where(f"id IN ({','.join(chunk)})").Limit(500)
        for c in getattr(svc.getCreativesByStatement(sb.ToStatement()), "results", []) or []:
            ctype = type(c).__name__ if not hasattr(c, "_xsd_type") else c._xsd_type.name
            blob = json.dumps(serialize_object(c), default=str)
            info[str(c.id)] = (ctype, classify(ctype, blob))
    df["ctype"] = df["creative_id"].map(lambda i: info.get(i, ("?", "UNKNOWN"))[0])
    df["status"] = df["creative_id"].map(lambda i: info.get(i, ("?", "UNKNOWN"))[1])

    print(f"Invoice {inv['invoice']} · {start} → {end} · creative-level\n")
    hdr = (f"{'Order':<11} {'Comscore':>11} {'GAM tagged':>11} {'Δ':>7} "
           f"{'+opaque':>11} {'Δ':>7} {'untagged':>10} {'GAM all':>11}")
    print(hdr); print("-" * len(hdr))

    def pct(a, b):
        return "n/a" if not b else f"{(a - b) / b * 100:+.1f}%"

    T = dict(cs=0, t=0, to=0, u=0, a=0)
    for oid, cs in billed.items():
        o = df[df["order_id"] == oid]
        s = o.groupby("status")["ad_server_impressions"].sum()
        t, op = int(s.get("TAGGED", 0)), int(s.get("OPAQUE", 0))
        u = int(s.get("UNTAGGED", 0) + s.get("UNKNOWN", 0))
        a = int(o["ad_server_impressions"].sum())
        for key, v in zip("cs t to u a".split(), (cs, t, t + op, u, a)):
            T[key] += v
        print(f"{oid:<11} {cs:>11,} {t:>11,} {pct(cs, t):>7} {t + op:>11,} {pct(cs, t + op):>7} "
              f"{u:>10,} {a:>11,}")
    print("-" * len(hdr))
    print(f"{'TOTAL':<11} {T['cs']:>11,} {T['t']:>11,} {pct(T['cs'], T['t']):>7} {T['to']:>11,} "
          f"{pct(T['cs'], T['to']):>7} {T['u']:>10,} {T['a']:>11,}")
    print("\nΔ = (Comscore − GAM) / GAM. '+opaque' adds third-party tags whose content GAM can't inspect.")

    print("\nPer creative (August, by order → line item):")
    for oid in billed:
        o = df[df["order_id"] == oid].sort_values(["line_item_id", "ad_server_impressions"],
                                                   ascending=[True, False])
        print(f"\n=== {oid}")
        if o.empty:
            print("  (no August delivery in GAM)")
        last = None
        for _, r in o.iterrows():
            if r["line_item_id"] != last:
                print(f"  LI {r['line_item_id']}  {r['line_item_name']}")
                last = r["line_item_id"]
            print(f"    {r['status']:<8} {int(r['ad_server_impressions']):>11,}  "
                  f"{r['creative_id']:>12} [{r['ctype']}] {r['creative_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
