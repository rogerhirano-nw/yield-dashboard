#!/usr/bin/env python3
"""One-off diagnostic: gather GAM deal IDs for the gambling (Luckyland / Chumba)
PG line items, to compare against the TTD feed's `deal_id`.

GAM credentials are CI-only, so this runs via
`.github/workflows/gam_gambling_deal_ids.yml` (which prints stdout to the run
log). Goal: find the GAM field that equals a TTD `deal_id` (e.g. 4211124) so the
TTD ↔ GAM-LI CPA join can switch from brittle name-tokens to deal_id.

Throwaway — not wired into the app; safe to delete after we read the output.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

# Run as `python scripts/…`, so only scripts/ is on sys.path — add the repo root
# so `gam_client` (at the root) imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.ads import admanager_v1

from gam_client import GAMClient

GAMBLING = ("luckyland", "chumba", "vgw")
# The TTD-side deal_ids we're trying to match (from ttd_luckyland / ttd_chumba).
TTD_DEAL_IDS = {
    "luckyland": [4189848, 4215584, 4215587, 4216952],
    "chumba": [4138066, 4138135, 4138162, 4149263, 4149266, 4149272, 4149284, 4211124],
}


def _is_gambling(name) -> bool:
    n = str(name or "").lower()
    return any(t in n for t in GAMBLING)


def main() -> None:
    print("=== TTD deal_ids to match ===")
    for k, v in TTD_DEAL_IDS.items():
        print(f"  {k}: {v}")

    Dim = admanager_v1.ReportDefinition.Dimension
    dim_names = [d.name for d in Dim]
    deal_dims = sorted(n for n in dim_names if "DEAL" in n)
    print("\n=== DEAL* report dimensions available in v1 ===")
    print("  " + (", ".join(deal_dims) or "(none)"))

    client = GAMClient()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=2)

    # 1) Delivery report joining the delivery LINE_ITEM_ID to a deal id/name.
    for deal_dim in ("DEAL_ID", "PROGRAMMATIC_DEAL_ID"):
        if deal_dim not in dim_names:
            print(f"\n[report] {deal_dim}: not a valid dimension — skipping")
            continue
        try:
            df = client._run_report(
                dimensions=["ORDER_NAME", "LINE_ITEM_ID", "LINE_ITEM_NAME", deal_dim, "DEAL_NAME"],
                metrics=["AD_SERVER_IMPRESSIONS"],
                start_date=start, end_date=end,
            )
            g = df[df["order_name"].map(_is_gambling)]
            cols = [c for c in ["line_item_id", "line_item_name", deal_dim.lower(), "deal_name"]
                    if c in g.columns]
            print(f"\n=== Report LINE_ITEM_ID <-> {deal_dim} (gambling, {start}..{end}) — {len(g)} rows ===")
            for _, r in g[cols].drop_duplicates().iterrows():
                print("  " + " | ".join(f"{c}={r[c]}" for c in cols))
        except Exception as e:
            print(f"\n[report] {deal_dim} report failed: {type(e).__name__}: {e}")

    # 1b) Gambling LI→deal backfill map. Validates gam_client.run_li_deal_map_report
    #     (count of numeric-deal LIs network-wide), then prints the gambling-only
    #     rows last as `DEALMAP <li> <deal> <name>` so they're fully visible in a
    #     tailed run log. Wide window so every recently-delivering gambling LI
    #     shows. (The earlier 3-day report already proved DEAL_ID == TTD deal_id;
    #     the SOAP ProposalLineItem dump proved it carries no deal-id field, so
    #     both are dropped here to keep the output small.)
    import re
    wide_start = end - timedelta(days=90)
    try:
        dm = client.run_li_deal_map_report(wide_start, end)
        print(f"\n=== run_li_deal_map_report ({wide_start}..{end}) — {len(dm)} LIs with a numeric deal id ===")
    except Exception as e:
        print(f"\n[map] run_li_deal_map_report failed: {type(e).__name__}: {e}")
    try:
        named = client._run_report(
            dimensions=["LINE_ITEM_ID", "LINE_ITEM_NAME", "DEAL_ID"],
            metrics=["AD_SERVER_IMPRESSIONS"],
            start_date=wide_start, end_date=end,
        )
        named["line_item_id"] = named["line_item_id"].astype(str).str.strip()
        named["deal_id"] = named["deal_id"].astype(str).str.strip()
        g = named[named["line_item_name"].map(_is_gambling)
                  & named["deal_id"].map(lambda s: bool(re.fullmatch(r"\d+", s)) and s != "0")]
        g = g.drop_duplicates(subset=["line_item_id", "deal_id"]).sort_values("line_item_id")
        print(f"=== Gambling LI→deal backfill map — {len(g)} rows ===")
        for _, r in g.iterrows():
            print(f"DEALMAP {r['line_item_id']} {r['deal_id']} {r['line_item_name']}")
    except Exception as e:
        print(f"\n[map] gambling named report failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
