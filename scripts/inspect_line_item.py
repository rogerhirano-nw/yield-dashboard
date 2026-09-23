#!/usr/bin/env python3
"""Dump one GAM line item's full setup — the thing to copy when building a
new line or a test page "like this one": order, flight, type/goal, placeholders,
every targeting block (inventory, geo, device, custom key-values resolved to
names), and each associated creative with its type, size, SafeFrame flag and
full snippet / third-party tag. Read-only.

Usage:
    python3 scripts/inspect_line_item.py 7431888847
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_envp = Path(__file__).resolve().parent.parent / ".env"
if _envp.exists():
    for _line in _envp.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from googleads import ad_manager, oauth2  # noqa: E402

V = "v202605"


def _client():
    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        kf = f.name
    oc = oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp")
    return ad_manager.AdManagerClient(oc, "NewsweekDashboard/1.0",
                                      network_code=os.environ["GAM_NETWORK_ID"])


def _q(svc, method, where, **binds):
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(500)
    for k, v in binds.items():
        sb = sb.WithBindVariable(k, v)
    return list(getattr(getattr(svc, method)(sb.ToStatement()), "results", []) or [])


def _dt(v):
    if v is None:
        return "—"
    d = v.date
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} {v.hour:02d}:{v.minute:02d} {v.timeZoneId}"


def _kv_names(client, custom):
    """Resolve every keyId/valueId in a custom-targeting tree to its name."""
    keys, vals = set(), set()

    def walk(n):
        if n is None:
            return
        if getattr(n, "keyId", None) is not None:
            keys.add(n.keyId)
            vals.update(n.valueIds or [])
        for c in (getattr(n, "children", None) or []):
            walk(c)
    walk(custom)
    if not keys:
        return {}, {}
    ct = client.GetService("CustomTargetingService", version=V)
    kn, vn = {}, {}
    ids = ",".join(str(k) for k in keys)
    for k in _q(ct, "getCustomTargetingKeysByStatement", f"id IN ({ids})"):
        kn[k.id] = k.name
    for k in keys:
        for v in _q(ct, "getCustomTargetingValuesByStatement", f"customTargetingKeyId = {k}"):
            if v.id in vals:
                vn[v.id] = v.name
    return kn, vn


def _print_custom(n, kn, vn, depth=0):
    pad = "  " * (depth + 2)
    if getattr(n, "keyId", None) is not None:
        names = [f"{vn.get(v, v)}" for v in (n.valueIds or [])]
        print(f"{pad}{kn.get(n.keyId, n.keyId)} {n.operator} {names}")
        return
    print(f"{pad}{n.logicalOperator}:")
    for c in (n.children or []):
        _print_custom(c, kn, vn, depth + 1)


def main() -> int:
    li_id = int(sys.argv[1])
    client = _client()
    li_svc = client.GetService("LineItemService", version=V)
    o_svc = client.GetService("OrderService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    inv_svc = client.GetService("InventoryService", version=V)

    lis = _q(li_svc, "getLineItemsByStatement", "id = :i", i=li_id)
    if not lis:
        print(f"!! line item {li_id} not found")
        return 1
    li = lis[0]
    order = _q(o_svc, "getOrdersByStatement", "id = :i", i=li.orderId)[0]

    print("=" * 78)
    print(f"LINE ITEM {li.id}: {li.name}")
    print("=" * 78)
    print(f"order:      {order.id} {order.name} (status {order.status})")
    print(f"status:     {li.status}   type {li.lineItemType} p{li.priority}")
    print(f"flight:     {_dt(li.startDateTime)} → "
          f"{'unlimited' if li.unlimitedEndDateTime else _dt(li.endDateTime)}")
    g = li.primaryGoal
    print(f"goal:       {g.goalType}/{g.unitType}/{g.units}   cost {li.costType} "
          f"{li.costPerUnit.currencyCode} {li.costPerUnit.microAmount / 1e6:,.2f}")
    print(f"env:        {li.environmentType}   rotation {li.creativeRotationType}   "
          f"roadblock {li.roadblockingType}   delivery {li.deliveryRateType}")
    print(f"freq caps:  {[(f.maxImpressions, f.numTimeUnits, f.timeUnit) for f in (li.frequencyCaps or [])]}")
    print("placeholders:")
    for p in (li.creativePlaceholders or []):
        print(f"  {p.size.width}x{p.size.height} {p.creativeSizeType} "
              f"expected={p.expectedCreativeCount}")
    if li.notes:
        print(f"notes:      {li.notes}")

    t = li.targeting
    print("\nTARGETING")
    inv = t.inventoryTargeting
    if inv is not None:
        for a in (inv.targetedAdUnits or []):
            au = _q(inv_svc, "getAdUnitsByStatement", "id = :i", i=int(a.adUnitId))
            name = au[0].adUnitCode if au else "?"
            print(f"  ad unit:   {a.adUnitId} {name}{' +descendants' if a.includeDescendants else ''}")
        for p in (inv.targetedPlacementIds or []):
            print(f"  placement: {p}")
        for a in (inv.excludedAdUnits or []):
            print(f"  excluded:  {a.adUnitId}")
    if t.geoTargeting is not None:
        print(f"  geo incl:  {[(x.id, x.displayName) for x in (t.geoTargeting.targetedLocations or [])]}")
        print(f"  geo excl:  {[(x.id, x.displayName) for x in (t.geoTargeting.excludedLocations or [])]}")
    tech = t.technologyTargeting
    if tech is not None and tech.deviceCategoryTargeting is not None:
        dc = tech.deviceCategoryTargeting
        print(f"  devices:   incl {[d.id for d in (dc.targetedDeviceCategories or [])]} "
              f"excl {[d.id for d in (dc.excludedDeviceCategories or [])]}")
    if t.customTargeting is not None:
        kn, vn = _kv_names(client, t.customTargeting)
        print("  custom:")
        _print_custom(t.customTargeting, kn, vn)
    for k in dir(t):
        if k not in ("inventoryTargeting", "geoTargeting", "technologyTargeting",
                     "customTargeting") and t[k] is not None:
            print(f"  {k}: {t[k]}")

    licas = _q(lica_svc, "getLineItemCreativeAssociationsByStatement",
               "lineItemId = :i", i=li_id)
    print(f"\nCREATIVES ({len(licas)})")
    for la in licas:
        c = _q(cr_svc, "getCreativesByStatement", "id = :i", i=la.creativeId)[0]
        print("-" * 78)
        print(f"{c.id} {c.name}  [{type(c).__name__ if not hasattr(c, '_xsd_type') else c._xsd_type.name}]"
              f"  LICA {la.status}")
        print(f"  size {c.size.width}x{c.size.height}  safeframe={getattr(c, 'isSafeFrameCompatible', None)}"
              f"  dest={getattr(c, 'destinationUrl', None)}")
        for field in ("htmlSnippet", "snippet", "expandedSnippet"):
            s = getattr(c, field, None) if field in dir(c) else None
            if s:
                print(f"  --- {field} ---")
                print(s)
        vals = getattr(c, "creativeTemplateVariableValues", None) if "creativeTemplateVariableValues" in dir(c) else None
        if vals:
            print(f"  template {getattr(c, 'creativeTemplateId', None)} values:")
            for v in vals:
                val = getattr(v, "value", None) if "value" in dir(v) else None
                print(f"    {v.uniqueName} = {val if val is not None else '(asset)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
