#!/usr/bin/env python3
"""Create a Direct GAM order + its line items from a signed-IO spec.

The IO itself is a PDF; its facts (advertiser, PO#, per-line dates, quantity,
CPM) are transcribed once into a JSON spec under `scripts/orders/<IO#>.json`,
so the numbers that reach GAM are reviewable in a diff before anything is
written. Names follow the Direct naming convention (see CLAUDE.md, "Dashboard
ad-format taxonomy": advertiser at token 7, campaign at token 8, format at
token 10), which is what `dl.derive_format` / `dl.line_item_display_name` read.

Settings an IO doesn't specify — line-item type/priority, inventory and
custom targeting (the interstitial placement), creative placeholders,
frequency caps, roadblocking — are CLONED from a template line item of a
prior flight of the same product (`template_line_item_like`, e.g. the AppleTv
"Cape Fear" interstitial lines, SO01090). The template's geo targeting is
replaced by the IO's (US); everything the template carries is printed in the
dry run so a mismatch is caught before --apply.

Lookup-first and idempotent: an existing order / line item of the same name is
reused, never duplicated, so a re-run after a partial failure only fills gaps.

New line items land as DRAFT with no creatives. The service account cannot
approve orders, so the order is approved in the GAM UI once creatives are in.

Usage:
    python3 scripts/setup_io_order.py scripts/orders/SO01190.json           # dry run
    python3 scripts/setup_io_order.py scripts/orders/SO01190.json --apply   # create in GAM
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# --- load .env into os.environ when running locally (no-op in Actions) ---
_envp = Path(__file__).resolve().parent.parent / ".env"
if _envp.exists():
    for _line in _envp.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

V = "v202605"
TZ = "America/New_York"      # GAM network tz — flights are ET days
US_GEO_ID = 2840             # Geo_Target criteria id for United States

# Fields copied verbatim from the template line item (when set there).
CLONE_FIELDS = [
    "creativePlaceholders", "frequencyCaps", "deliveryRateType",
    "creativeRotationType", "roadblockingType", "environmentType",
    "companionDeliveryOption", "childContentEligibility",
    "videoMaxDuration", "allowOverbook", "skipInventoryCheck",
    "disableSameAdvertiserCompetitiveExclusion",
]


def _client():
    from googleads import ad_manager, oauth2
    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        kf = f.name
    oc = oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp")
    return ad_manager.AdManagerClient(oc, "NewsweekDashboard/1.0",
                                      network_code=os.environ["GAM_NETWORK_ID"])


def _page(svc, method, where, **binds):
    from googleads import ad_manager
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(200)
    for k, v in binds.items():
        sb = sb.WithBindVariable(k, v)
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


def _dt(iso: str, end: bool) -> dict:
    y, m, d = (int(x) for x in iso.split("-"))
    hh, mm, ss = (23, 59, 0) if end else (0, 0, 0)
    return {"date": {"year": y, "month": m, "day": d},
            "hour": hh, "minute": mm, "second": ss, "timeZoneId": TZ}


def _fmt_dt(v) -> str:
    if v is None:
        return "—"
    d = v.date
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} {v.hour:02d}:{v.minute:02d} {v.timeZoneId}"


def _money(m) -> str:
    if m is None:
        return "—"
    return f"{m.currencyCode} {m.microAmount / 1e6:,.2f}"


def _validate(spec: dict) -> None:
    """The spec is a transcription; check it against itself before GAM."""
    total_amt = total_imp = 0
    for li in spec["line_items"]:
        calc = li["impressions"] * li["cpm"] / 1000
        if abs(calc - li["amount"]) > 1.0:
            raise SystemExit(f"!! {li['io_line']}: {li['impressions']:,} @ ${li['cpm']} "
                             f"= ${calc:,.2f}, spec says ${li['amount']:,.2f}")
        if li["start"] > li["end"]:
            raise SystemExit(f"!! {li['io_line']}: start {li['start']} after end {li['end']}")
        tok = li["name"].split("_")
        if len(tok) < 11 or tok[1] != "Direct" or "SO" + spec["io_number"][2:] not in li["name"]:
            raise SystemExit(f"!! {li['name']!r} doesn't follow the Direct naming convention")
        total_amt += li["amount"]
        total_imp += li["impressions"]
    print(f"spec totals: {total_imp:,} impr · ${total_amt:,.2f}")


def _pick_template(templates, match: str):
    """'Pre-Avail' → the Pre-Avail LI; 'Avail' → an Avail LI that is NOT Pre-Avail."""
    def ok(name: str) -> bool:
        n = name.lower()
        if match.lower() == "avail":
            return "avail" in n and "pre-avail" not in n
        return match.lower() in n
    # Prefer a paid line over a $0 added-value ("AV") sibling.
    paid = lambda t: -(t.costPerUnit.microAmount if t.costPerUnit else 0)
    hits = sorted((t for t in templates if ok(t.name)), key=paid)
    return (hits or sorted(templates, key=paid))[0], bool(hits)


def _describe_targeting(t) -> list[str]:
    out = []
    if t is None:
        return ["(no targeting)"]
    inv = t.inventoryTargeting
    if inv is not None:
        au = [f"{a.adUnitId}{'+' if a.includeDescendants else ''}"
              for a in (inv.targetedAdUnits or [])]
        pl = list(inv.targetedPlacementIds or [])
        ex = [a.adUnitId for a in (inv.excludedAdUnits or [])]
        out.append(f"inventory: adUnits={au} placements={pl} excluded={ex}")
    geo = t.geoTargeting
    if isinstance(geo, dict):      # our IO override, not yet packed by zeep
        out.append(f"geo: incl={[g['id'] for g in geo['targetedLocations']]} (IO override)")
    elif geo is not None:
        out.append("geo: incl=" + str([(g.id, g.displayName) for g in (geo.targetedLocations or [])])
                   + " excl=" + str([(g.id, g.displayName) for g in (geo.excludedLocations or [])]))
    tech = t.technologyTargeting
    if tech is not None:
        dc = tech.deviceCategoryTargeting
        if dc is not None:
            out.append("device: incl=" + str([d.id for d in (dc.targetedDeviceCategories or [])])
                       + " excl=" + str([d.id for d in (dc.excludedDeviceCategories or [])]))
        other = [k for k in dir(tech) if k != "deviceCategoryTargeting" and tech[k] is not None]
        if other:
            out.append(f"technology (other): {other}")
    if t.customTargeting is not None:
        out.append(f"custom: {t.customTargeting}")
    for k in dir(t):
        if k not in ("inventoryTargeting", "geoTargeting", "technologyTargeting",
                     "customTargeting") and t[k] is not None:
            out.append(f"{k}: {t[k]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text())
    print("=" * 78)
    print(f"IO {spec['io_number']} → GAM  ({'APPLY' if args.apply else 'DRY RUN'})")
    print("=" * 78)
    _validate(spec)

    client = _client()
    co_svc = client.GetService("CompanyService", version=V)
    us_svc = client.GetService("UserService", version=V)
    o_svc = client.GetService("OrderService", version=V)
    li_svc = client.GetService("LineItemService", version=V)

    # ---------- advertiser ----------
    advs = _page(co_svc, "getCompaniesByStatement", "name = :n", n=spec["advertiser_name"])
    if len(advs) != 1:
        print(f"!! expected exactly one company named {spec['advertiser_name']!r}, "
              f"found {[(c.id, c.name, c.type) for c in advs]}")
        return 1
    adv = advs[0]
    print(f"\nadvertiser: {adv.name} (id {adv.id}, type {adv.type})")

    # ---------- template ----------
    templates = _page(li_svc, "getLineItemsByStatement", "name LIKE :p",
                      p=spec["template_line_item_like"])
    if not templates:
        print(f"!! no template line items match {spec['template_line_item_like']!r}")
        return 1
    t_order = _page(o_svc, "getOrdersByStatement", "id = :i", i=templates[0].orderId)[0]
    t_adv = _page(co_svc, "getCompaniesByStatement", "id = :i", i=t_order.advertiserId)
    print(f"\ntemplate order: {t_order.name} (id {t_order.id})")
    print(f"  advertiser: {t_adv[0].name if t_adv else '?'} (id {t_order.advertiserId})"
          + ("  ✓ same" if t_order.advertiserId == adv.id else "  ⚠ DIFFERENT advertiser"))
    print(f"  agencyId={t_order.agencyId} traffickerId={t_order.traffickerId} "
          f"salespersonId={t_order.salespersonId} po={t_order.poNumber!r}")
    for t in templates:
        print(f"  template LI {t.id}: {t.name}")
        print(f"    type={t.lineItemType} p{t.priority} cost={t.costType} {_money(t.costPerUnit)} "
              f"goal={t.primaryGoal.goalType}/{t.primaryGoal.unitType}/{t.primaryGoal.units} "
              f"status={t.status}")
        print(f"    flight {_fmt_dt(t.startDateTime)} → {_fmt_dt(t.endDateTime)}")
        print(f"    placeholders={[(p.size.width, p.size.height, p.creativeSizeType) for p in (t.creativePlaceholders or [])]}")
        print(f"    env={t.environmentType} rotation={t.creativeRotationType} "
              f"roadblock={t.roadblockingType} delivery={t.deliveryRateType} "
              f"freqcaps={[(f.maxImpressions, f.numTimeUnits, f.timeUnit) for f in (t.frequencyCaps or [])]}")
        for line in _describe_targeting(t.targeting):
            print(f"    {line}")

    # ---------- salesperson ----------
    salesperson_id = t_order.salespersonId
    sp = _page(us_svc, "getUsersByStatement", "name = :n", n=spec["salesperson_name"])
    if salesperson_id:
        cur = _page(us_svc, "getUsersByStatement", "id = :i", i=salesperson_id)
        if cur:
            print(f"\ntemplate salesperson: {cur[0].name} (id {salesperson_id})")
    if len(sp) == 1:
        salesperson_id = sp[0].id
        print(f"\nsalesperson: {sp[0].name} (id {sp[0].id})")
    else:
        print(f"⚠ salesperson {spec['salesperson_name']!r} → {len(sp)} users; "
              f"keeping template's salespersonId {salesperson_id}")

    # ---------- order ----------
    existing = _page(o_svc, "getOrdersByStatement", "name = :n", n=spec["order_name"])
    order_body = {
        "name": spec["order_name"],
        "advertiserId": adv.id,
        "traffickerId": t_order.traffickerId,
        "salespersonId": salesperson_id,
        "poNumber": spec["po_number"],
        "notes": spec["notes"],
    }
    if t_order.agencyId:
        order_body["agencyId"] = t_order.agencyId
    print(f"\nORDER {'[exists: id=' + str(existing[0].id) + ']' if existing else '[will create]'}")
    for k, v in order_body.items():
        if v is not None:
            print(f"  {k}: {v}")

    # ---------- line items ----------
    new_lis = []
    for li in spec["line_items"]:
        tmpl, matched = _pick_template(templates, li["template_match"])
        body = {k: tmpl[k] for k in CLONE_FIELDS if tmpl[k] is not None}
        lit = str(tmpl.lineItemType)
        body.update({
            "name": li["name"],
            "lineItemType": lit,
            "priority": tmpl.priority,
            "startDateTime": _dt(li["start"], end=False),
            "endDateTime": _dt(li["end"], end=True),
            "costType": "CPM",
            "costPerUnit": {"currencyCode": "USD", "microAmount": int(round(li["cpm"] * 1e6))},
            "notes": (f"IO {spec['io_number']} line {li['io_line']}: "
                      f"{li['impressions']:,} impr @ ${li['cpm']:.2f} CPM = ${li['amount']:,.2f}"),
        })
        if lit == "SPONSORSHIP":
            # Mirror the template's share-of-voice goal (the prior flights ran
            # the interstitial as a 100% daily takeover); the IO quantity rides
            # in the notes. GAM rejects a LIFETIME goal on SPONSORSHIP.
            g = tmpl.primaryGoal
            body["primaryGoal"] = {"goalType": g.goalType, "unitType": g.unitType, "units": g.units}
        else:
            body["primaryGoal"] = {"goalType": "LIFETIME", "unitType": "IMPRESSIONS",
                                   "units": li["impressions"]}
        tg = tmpl.targeting
        tg.geoTargeting = {"targetedLocations": [{"id": US_GEO_ID}]}   # IO: US
        body["targeting"] = tg
        new_lis.append(body)

        print(f"\nLINE ITEM  {li['io_line']}")
        print(f"  name:     {li['name']}")
        print(f"  template: {tmpl.id} {tmpl.name}" + ("" if matched else "  ⚠ no name match, using first"))
        print(f"  flight:   {li['start']} 00:00 → {li['end']} 23:59 ET")
        g = body["primaryGoal"]
        print(f"  goal:     {g['goalType']}/{g['unitType']}/{g['units']} · ${li['cpm']:.2f} CPM "
              f"· IO qty {li['impressions']:,} = ${li['amount']:,.2f}")
        print(f"  type:     {body['lineItemType']} p{body['priority']}")
        for line in _describe_targeting(tg):
            print(f"  {line}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to create in GAM.")
        return 0

    # ---------- write ----------
    if existing:
        order = existing[0]
        print(f"\norder exists: {order.id}")
    else:
        order = o_svc.createOrders([order_body])[0]
        print(f"\ncreated order {order.id}: {order.name}")

    have = {x.name: x for x in _page(li_svc, "getLineItemsByStatement",
                                     "orderId = :o", o=order.id)}
    for body in new_lis:
        if body["name"] in have:
            print(f"line item exists: {have[body['name']].id} {body['name']}")
            continue
        body["orderId"] = order.id
        made = li_svc.createLineItems([body])[0]
        print(f"created line item {made.id} ({made.status}): {made.name}")

    print(f"\nNEXT: add creatives, then approve in the GAM UI "
          f"(Delivery > Orders > {order.id}) — the service account can't approve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
