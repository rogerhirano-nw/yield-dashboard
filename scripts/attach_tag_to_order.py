#!/usr/bin/env python3
"""Traffic an agency tag onto a real order's line items.

Takes a tag file (e.g. the Matchbox Flashtalking tag proven on the
?nwdemocr= demo) and:
  - creates ONE ThirdPartyCreative under the order's advertiser, at the given
    size, SafeFrame as given (OFF for on-page breakout tags), with its ad
    technology DECLARED (Flashtalking = ATP 209 — CLAUDE.md);
  - applies creative labels — always `interstitial` when the targets are
    interstitial lines — and, for interstitials, the Comscore impression
    pixel (scripts/orders/pixels/comscore_interstitial.txt) as a third-party
    impression tracker (both production rules, CLAUDE.md);
  - associates it with every target line item: the ids passed with
    --line-items, or else every non-archived line item on the order whose
    creative placeholders include that size.

The dry run lists every line item on the order (status, flight, type,
placeholders, targeting summary, current creatives) so the targets can be
checked before --apply. Lookup-first: an existing creative of the same name
under the advertiser is reused and existing LICAs are skipped.

Usage:
    python3 scripts/attach_tag_to_order.py --order 4187974224 \\
        --tag scripts/orders/tags/matchbox_ft11167131_js_https.html \\
        --name <placement name> --size 2x1 --atp 209          # dry run
    ... --line-items 123,456 --apply                         # write
"""
from __future__ import annotations

import argparse
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
COMSCORE_ATP = 62   # "comScore" in Google's ATP dictionary (covers sb.scorecardresearch.com)
COMSCORE_INTERSTITIAL = Path(__file__).resolve().parent / "orders" / "pixels" / "comscore_interstitial.txt"


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
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} {v.hour:02d}:{v.minute:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", type=int, required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--size", default="2x1")
    ap.add_argument("--atp", default="209", help="comma-separated ad technology provider ids")
    ap.add_argument("--safeframe", action="store_true", help="mark SafeFrame compatible")
    ap.add_argument("--line-items", default="", help="comma-separated LI ids (default: all matching the size)")
    ap.add_argument("--labels", default="",
                    help="comma-separated creative label names to apply "
                         "(interstitial is added automatically for interstitial lines)")
    ap.add_argument("--trackers", default="",
                    help="comma-separated extra impression-tracking URLs "
                         "(the Comscore interstitial pixel is added automatically)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    tag = Path(args.tag).read_text()
    w, h = (int(x) for x in args.size.lower().split("x"))
    atp = [int(x) for x in args.atp.split(",") if x.strip()]
    if not atp:
        print("!! refusing to traffic a third-party tag with no ad technology declared")
        return 1

    client = _client()
    o_svc = client.GetService("OrderService", version=V)
    co_svc = client.GetService("CompanyService", version=V)
    li_svc = client.GetService("LineItemService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
    inv_svc = client.GetService("InventoryService", version=V)

    print("=" * 78)
    print(f"ATTACH TAG TO ORDER  ({'APPLY' if args.apply else 'DRY RUN'})")
    print("=" * 78)
    orders = _q(o_svc, "getOrdersByStatement", "id = :i", i=args.order)
    if not orders:
        print(f"!! order {args.order} not found")
        return 1
    order = orders[0]
    adv = _q(co_svc, "getCompaniesByStatement", "id = :i", i=order.advertiserId)
    print(f"order {order.id}: {order.name}")
    print(f"  status {order.status}  advertiser {order.advertiserId} {adv[0].name if adv else '?'}"
          f"  agency {order.agencyId}  po {order.poNumber!r}")

    lis = [x for x in _q(li_svc, "getLineItemsByStatement", "orderId = :o", o=order.id)
           if not x.isArchived]
    au_names = {}
    targets = []
    want_ids = {int(x) for x in args.line_items.split(",") if x.strip()}
    print(f"\nLINE ITEMS ({len(lis)})")
    for li in lis:
        sizes = [(p.size.width, p.size.height) for p in (li.creativePlaceholders or [])]
        inv = li.targeting.inventoryTargeting
        units = []
        for a in (inv.targetedAdUnits or []) if inv else []:
            if a.adUnitId not in au_names:
                r = _q(inv_svc, "getAdUnitsByStatement", "id = :i", i=int(a.adUnitId))
                au_names[a.adUnitId] = r[0].adUnitCode if r else "?"
            units.append(au_names[a.adUnitId])
        ct = "custom KVs" if li.targeting.customTargeting else "no KVs"
        licas = _q(lica_svc, "getLineItemCreativeAssociationsByStatement",
                   "lineItemId = :i", i=li.id)
        match = (li.id in want_ids) if want_ids else ((w, h) in sizes)
        if match:
            targets.append(li)
        print(f"{'→' if match else ' '} {li.id} [{li.status}] {li.lineItemType} p{li.priority} "
              f"{_dt(li.startDateTime)} → {_dt(li.endDateTime)}")
        print(f"    {li.name}")
        print(f"    sizes {sizes}  units {units}  {ct}  env {li.environmentType}  "
              f"creatives {len(licas)}")
    missing = want_ids - {li.id for li in lis}
    if missing:
        print(f"!! line items not on this order: {sorted(missing)}")
        return 1
    bad = [li.id for li in targets
           if (w, h) not in [(p.size.width, p.size.height) for p in (li.creativePlaceholders or [])]]
    if bad:
        print(f"!! {args.size} is not a placeholder on {bad} — GAM would reject the LICA")
        return 1
    if not targets:
        print(f"\n!! no line item on this order has a {args.size} placeholder — nothing to attach")
        return 1

    existing = _q(cr_svc, "getCreativesByStatement", "advertiserId = :a AND name = :n",
                  a=order.advertiserId, n=args.name)
    print(f"\nCREATIVE ThirdPartyCreative {args.size} safeframe={args.safeframe} "
          f"ATP {atp}  {len(tag):,} chars  "
          + (f"[exists: {existing[0].id}]" if existing else "[will create]"))
    print(f"  name: {args.name}")
    print(f"  attach to: {[li.id for li in targets]}")

    # Rule (Roger, 2026-09-23): on production orders every interstitial
    # creative carries the "interstitial" label. A target is interstitial when
    # its name or the order name says so, or it targets the interstitial unit.
    label_names = [x.strip() for x in args.labels.split(",") if x.strip()]
    is_interstitial = "interstitial" in order.name.lower() or any(
        "interstitial" in li.name.lower()
        or any(au_names.get(a.adUnitId, "").lower() == "interstitial"
               for a in ((li.targeting.inventoryTargeting.targetedAdUnits or [])
                         if li.targeting.inventoryTargeting else []))
        for li in targets)
    if is_interstitial and "interstitial" not in [n.lower() for n in label_names]:
        label_names.append("interstitial")
    lab_svc = client.GetService("LabelService", version=V)
    label_ids = []
    for n in label_names:
        found = [x for x in _q(lab_svc, "getLabelsByStatement", "name = :n", n=n) if x.isActive]
        if len(found) != 1:
            print(f"!! expected one active label named {n!r}, found "
                  f"{[(x.id, x.name, x.types) for x in found]}")
            return 1
        label_ids.append(found[0].id)
        print(f"  label: {found[0].name} ({found[0].id}, types {list(found[0].types or [])})")

    # Rule (Roger, 2026-09-23): every interstitial campaign carries the
    # Comscore pixel, as a third-party impression tracker on the creative
    # (never spliced into the agency tag). Kept verbatim in a file.
    trackers = [x.strip() for x in args.trackers.split(",") if x.strip()]
    if is_interstitial:
        trackers.append(COMSCORE_INTERSTITIAL.read_text().strip())
    # A tracker is a vendor on the creative, so it must be declared too
    # (Roger, 2026-09-23): the Comscore pixel brings ATP 62 with it.
    if any("scorecardresearch.com" in t for t in trackers) and COMSCORE_ATP not in atp:
        atp.append(COMSCORE_ATP)
    print(f"  ad technology to declare: {atp}")
    trackers = list(dict.fromkeys(trackers))
    for t in trackers:
        print(f"  impression tracker: {t[:90]}…")
    if existing:
        have_t = list(existing[0]["thirdPartyImpressionTrackingUrls"] or []) \
            if "thirdPartyImpressionTrackingUrls" in dir(existing[0]) else None
        if have_t is None:
            print("!! this creative type has no thirdPartyImpressionTrackingUrls field")
            return 1
        print(f"  existing creative trackers: {len(have_t)}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write to GAM.")
        return 0

    decl = {"declarationType": "DECLARED", "thirdPartyCompanyIds": atp}
    if existing:
        cr = existing[0]
        print(f"reusing creative {cr.id}")
        have = {a.labelId for a in (cr.appliedLabels or []) if not a.isNegated}
        missing_labels = [i for i in label_ids if i not in have]
        if missing_labels:
            cr.appliedLabels = list(cr.appliedLabels or []) + [
                {"labelId": i, "isNegated": False} for i in missing_labels]
            cr = cr_svc.updateCreatives([cr])[0]
            print(f"labelled creative {cr.id}: + {missing_labels}")
        cur = cr["thirdPartyDataDeclaration"] if "thirdPartyDataDeclaration" in dir(cr) else None
        have_atp = list(cur.thirdPartyCompanyIds or []) if cur is not None else []
        missing_atp = [i for i in atp if i not in have_atp]
        if missing_atp or cur is None or str(cur.declarationType) != "DECLARED":
            cr.thirdPartyDataDeclaration = {"declarationType": "DECLARED",
                                            "thirdPartyCompanyIds": have_atp + missing_atp}
            cr = cr_svc.updateCreatives([cr])[0]
            print(f"declared ad technology on creative {cr.id}: "
                  f"{list(cr.thirdPartyDataDeclaration.thirdPartyCompanyIds or [])}")
        have_t = list(cr.thirdPartyImpressionTrackingUrls or [])
        missing_t = [t for t in trackers if t not in have_t]
        if missing_t:
            cr.thirdPartyImpressionTrackingUrls = have_t + missing_t
            cr = cr_svc.updateCreatives([cr])[0]
            print(f"added {len(missing_t)} impression tracker(s) to creative {cr.id}; "
                  f"now {len(cr.thirdPartyImpressionTrackingUrls or [])}")
    else:
        cr = cr_svc.createCreatives([{
            "xsi_type": "ThirdPartyCreative",
            "name": args.name,
            "advertiserId": order.advertiserId,
            "size": {"width": w, "height": h, "isAspectRatio": False},
            "snippet": tag,
            "isSafeFrameCompatible": args.safeframe,
            "thirdPartyDataDeclaration": decl,
            "appliedLabels": [{"labelId": i, "isNegated": False} for i in label_ids],
            "thirdPartyImpressionTrackingUrls": trackers,
        }])[0]
        print(f"created creative {cr.id}")
    for li in targets:
        try:
            la = lica_svc.createLineItemCreativeAssociations(
                [{"lineItemId": li.id, "creativeId": cr.id}])[0]
            print(f"LICA {li.id} ← {cr.id}: {la.status}")
        except Exception as e:  # noqa: BLE001
            if "ALREADY_EXISTS" in str(e):
                print(f"LICA {li.id} ← {cr.id}: already exists")
            else:
                raise
    print(f"\nNEXT: check order {order.id} in the GAM UI — approve if it needs it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
