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

from datetime import date, timedelta

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

    # 2) SOAP ProposalLineItemService — PG inventory lives here. Dump the fields
    #    + a full serialization of the first gambling PLI so we can spot whatever
    #    field carries the deal id.
    try:
        from googleads import ad_manager  # type: ignore
        from zeep.helpers import serialize_object  # type: ignore
        sc = client._get_soap_client()
        svc = sc.GetService("ProposalLineItemService", version=client._SOAP_API_VERSION)
        sb = ad_manager.StatementBuilder(version=client._SOAP_API_VERSION)
        sb.Where("isArchived = false").Limit(500)
        printed = 0
        while True:
            resp = svc.getProposalLineItemsByStatement(sb.ToStatement())
            results = list(getattr(resp, "results", None) or [])
            if not results:
                break
            for li in results:
                if not _is_gambling(getattr(li, "name", "")):
                    continue
                if printed == 0:
                    fields = sorted(a for a in dir(li) if not a.startswith("_"))
                    print("\n=== ProposalLineItem fields ===\n  " + ", ".join(fields))
                    print("\n=== First gambling PLI (full serialize) ===")
                    print(serialize_object(li))
                print(f"\n[PLI] id={getattr(li, 'id', None)} | name={getattr(li, 'name', None)}")
                printed += 1
            sb.offset += sb.limit
            if sb.offset >= int(getattr(resp, "totalResultSetSize", 0) or 0):
                break
        print(f"\n[PLI] {printed} gambling proposal line items")
    except Exception as e:
        print(f"\n[SOAP] ProposalLineItem dump failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
