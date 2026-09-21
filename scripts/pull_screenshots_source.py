#!/usr/bin/env python3
"""Read-only pull of everything needed to build a Screenshots Document.

Given an order id (and optionally a single line item id to highlight), dumps:
  - order: name, advertiser, trafficker, flight dates, status
  - each line item: name, status, type, flight, sizes, goal, targeted ad units
  - each creative on those LIs: name, type, size, destination URL, preview URL,
    and the image asset URL when GAM hosts the asset

Writes JSON to --out (default /tmp/screenshots_source.json) and prints a
human-readable summary. No writes to GAM.

Usage:
  python scripts/pull_screenshots_source.py --order 4198147401 [--line-item 7432006947]
"""
import argparse
import json
import os
import sys
from pathlib import Path

# --- load .env into os.environ when running locally (no-op in Actions) ---
envp = Path(__file__).resolve().parent.parent / ".env"
if envp.exists():
    for line in envp.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient  # noqa: E402
from googleads import ad_manager  # noqa: E402

V = "v202605"


def _g(obj, *names):
    """First non-None attribute among names."""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _date(d):
    if d is None:
        return None
    dd = getattr(d, "date", d)
    y, m, day = getattr(dd, "year", None), getattr(dd, "month", None), getattr(dd, "day", None)
    if y is None:
        return str(d)
    s = f"{y:04d}-{m:02d}-{day:02d}"
    hh, mm = getattr(d, "hour", None), getattr(d, "minute", None)
    if hh is not None:
        s += f" {hh:02d}:{mm or 0:02d}"
    return s


def _size(sz):
    if sz is None:
        return None
    return f"{getattr(sz, 'width', '?')}x{getattr(sz, 'height', '?')}"


def _page(svc, method, where, version=V, limit=200):
    """Run a paged PQL query, returning all results."""
    sb = ad_manager.StatementBuilder(version=version).Where(where).Limit(limit)
    out = []
    while True:
        resp = getattr(svc, method)(sb.ToStatement())
        results = list(getattr(resp, "results", []) or [])
        out.extend(results)
        if not results:
            break
        sb.offset += sb.limit
        if sb.offset >= getattr(resp, "totalResultSetSize", 0):
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", required=True)
    ap.add_argument("--line-item", default=None)
    ap.add_argument("--out", default="/tmp/screenshots_source.json")
    args = ap.parse_args()

    gc = GAMClient()
    client = gc._get_soap_client()

    # ---------------- order ----------------
    o_svc = client.GetService("OrderService", version=V)
    orders = _page(o_svc, "getOrdersByStatement", f"id = {int(args.order)}")
    if not orders:
        print(f"!! order {args.order} not found", file=sys.stderr)
        return 1
    o = orders[0]

    company_id = _g(o, "advertiserId")
    advertiser = None
    if company_id:
        c_svc = client.GetService("CompanyService", version=V)
        cs = _page(c_svc, "getCompaniesByStatement", f"id = {int(company_id)}")
        if cs:
            advertiser = getattr(cs[0], "name", None)

    order = {
        "id": str(_g(o, "id")),
        "name": _g(o, "name"),
        "status": str(_g(o, "status")),
        "advertiser_id": str(company_id) if company_id else None,
        "advertiser": advertiser,
        "start": _date(_g(o, "startDateTime")),
        "end": _date(_g(o, "endDateTime")),
        "is_archived": _g(o, "isArchived"),
        "notes": _g(o, "notes"),
        "po_number": _g(o, "poNumber"),
    }

    # ---------------- line items ----------------
    li_svc = client.GetService("LineItemService", version=V)
    lis = _page(li_svc, "getLineItemsByStatement", f"orderId = {int(args.order)}")

    # ad unit id -> name, resolved once for every ad unit we touch
    au_ids: set[int] = set()
    for li in lis:
        inv = _g(_g(li, "targeting"), "inventoryTargeting")
        for au in (getattr(inv, "targetedAdUnits", None) or []):
            aid = getattr(au, "adUnitId", None)
            if aid:
                au_ids.add(int(aid))
    au_names: dict[int, str] = {}
    if au_ids:
        au_svc = client.GetService("InventoryService", version=V)
        ids_str = ", ".join(str(i) for i in sorted(au_ids))
        for au in _page(au_svc, "getAdUnitsByStatement", f"id IN ({ids_str})"):
            aid = getattr(au, "id", None)
            if aid:
                au_names[int(aid)] = getattr(au, "adUnitCode", None) or getattr(au, "name", "")

    line_items = []
    for li in lis:
        inv = _g(_g(li, "targeting"), "inventoryTargeting")
        units = []
        for au in (getattr(inv, "targetedAdUnits", None) or []):
            aid = getattr(au, "adUnitId", None)
            units.append({
                "ad_unit_id": str(aid) if aid else None,
                "ad_unit": au_names.get(int(aid)) if aid else None,
                "include_descendants": getattr(au, "includeDescendants", None),
            })
        goal = _g(li, "primaryGoal")
        line_items.append({
            "id": str(_g(li, "id")),
            "name": _g(li, "name"),
            "status": str(_g(li, "status")),
            "line_item_type": str(_g(li, "lineItemType")),
            "start": _date(_g(li, "startDateTime")),
            "end": _date(_g(li, "endDateTime")),
            "sizes": [
                _size(getattr(ph, "size", None))
                for ph in (_g(li, "creativePlaceholders") or [])
                if getattr(ph, "size", None) is not None
            ],
            "goal_units": _g(goal, "units") if goal is not None else None,
            "goal_type": str(_g(goal, "goalType")) if goal is not None else None,
            "cost_type": str(_g(li, "costType")),
            "targeted_ad_units": units,
            "is_highlight": str(_g(li, "id")) == str(args.line_item),
        })

    li_ids = [li["id"] for li in line_items]

    # ---------------- LICAs + creatives ----------------
    creatives_by_li: dict[str, list] = {lid: [] for lid in li_ids}
    creative_ids: set[int] = set()
    lica_pairs = []
    if li_ids:
        lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
        ids_str = ", ".join(str(int(i)) for i in li_ids)
        for la in _page(lica_svc, "getLineItemCreativeAssociationsByStatement",
                        f"lineItemId IN ({ids_str})"):
            lid, cid = _g(la, "lineItemId"), _g(la, "creativeId")
            if lid is None or cid is None:
                continue
            creative_ids.add(int(cid))
            lica_pairs.append({
                "line_item_id": str(lid),
                "creative_id": str(cid),
                "status": str(_g(la, "status")),
                "destination_url": _g(la, "destinationUrl"),
            })

    creatives: dict[str, dict] = {}
    if creative_ids:
        cr_svc = client.GetService("CreativeService", version=V)
        ids_str = ", ".join(str(i) for i in sorted(creative_ids))
        for c in _page(cr_svc, "getCreativesByStatement", f"id IN ({ids_str})"):
            cid = str(_g(c, "id"))
            asset = _g(c, "primaryImageAsset", "imageAsset", "asset")
            creatives[cid] = {
                "id": cid,
                "name": _g(c, "name"),
                "type": type(c).__name__,
                "size": _size(_g(c, "size")),
                "preview_url": _g(c, "previewUrl"),
                "destination_url": _g(c, "destinationUrl"),
                "asset_url": _g(asset, "assetUrl") if asset is not None else None,
                "asset_size": _size(_g(asset, "size")) if asset is not None else None,
                "third_party_snippet": bool(_g(c, "snippet") or _g(c, "htmlSnippet")),
                "vast_redirect_url": _g(c, "vastXmlUrl"),
            }

    for p in lica_pairs:
        creatives_by_li.setdefault(p["line_item_id"], []).append({
            **p, **creatives.get(p["creative_id"], {"id": p["creative_id"]}),
        })

    payload = {
        "order": order,
        "line_items": line_items,
        "creatives_by_line_item": creatives_by_li,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str))

    # ---------------- summary ----------------
    print(f"ORDER {order['id']}  {order['name']}")
    print(f"  advertiser : {order['advertiser']}")
    print(f"  status     : {order['status']}   flight: {order['start']} → {order['end']}")
    print(f"  line items : {len(line_items)}")
    for li in line_items:
        mark = " <<< requested" if li["is_highlight"] else ""
        print(f"\n  LI {li['id']}  {li['name']}{mark}")
        print(f"     status={li['status']}  type={li['line_item_type']}  "
              f"flight={li['start']} → {li['end']}")
        print(f"     sizes={li['sizes']}  goal={li['goal_units']} {li['goal_type']} ({li['cost_type']})")
        for u in li["targeted_ad_units"]:
            print(f"     ad unit: {u['ad_unit']} ({u['ad_unit_id']}) "
                  f"descendants={u['include_descendants']}")
        for cr in creatives_by_li.get(li["id"], []):
            print(f"     creative {cr['id']}  {cr.get('name')}")
            print(f"        type={cr.get('type')} size={cr.get('size')} "
                  f"lica_status={cr.get('status')}")
            print(f"        click  : {cr.get('destination_url')}")
            print(f"        asset  : {cr.get('asset_url')}")
            print(f"        preview: {cr.get('preview_url')}")
    print(f"\nJSON → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
