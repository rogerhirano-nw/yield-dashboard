#!/usr/bin/env python3
"""Stand up a ?nwdemocr=-gated test of an agency tag, cloned from an existing
demo line item.

Given a template line item (e.g. 7431888847, the Apple TV+ Flashtalking
interstitial demo on Newsweek_Test-2) and a tag file, this creates on the SAME
order:
  - a line item copying the template's type/priority/cost/goal, inventory,
    placeholders, roadblocking and every other targeting block, with its
    custom targeting replaced by `nwdemocr IS <demo value>`;
  - a ThirdPartyCreative carrying the tag verbatim, at the template creative's
    size, advertiser and SafeFrame setting (OFF for on-page breakout tags);
  - the LICA between them.
The nwdemocr value is created if it doesn't exist. Nothing serves to a reader
without the URL param, so the test page is any article URL + `?nwdemocr=<value>`.

Lookup-first: an existing value / line item / creative of the same name is
reused, so re-running only fills gaps. New line items on an approved order sit
until the order is re-approved in the GAM UI (the service account can't).

Usage:
    python3 scripts/setup_demo_creative.py --template-li 7431888847 \\
        --tag scripts/orders/tags/matchbox_ft11167131_js_https.html \\
        --name <placement name> --demo-value 11167131  # = Placement_ID          # dry run
    ... --apply                                                 # create in GAM
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
DEMO_KEY = "nwdemocr"

CLONE_FIELDS = [
    "lineItemType", "priority", "costType", "costPerUnit", "primaryGoal",
    "creativePlaceholders", "frequencyCaps", "deliveryRateType",
    "creativeRotationType", "roadblockingType", "environmentType",
    "allowOverbook", "skipInventoryCheck",
]


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template-li", type=int, required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--name", required=True, help="line item + creative name")
    ap.add_argument("--demo-value", required=True, help="nwdemocr value to gate on")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    tag = Path(args.tag).read_text()
    # Rule (Roger, 2026-09-23): the nwdemocr value is ALWAYS the tag sheet's
    # Placement_ID. Flashtalking tags carry it as ft_keyword / the placement
    # id, so refuse a value the tag doesn't contain as its placement.
    import re
    ids = set(re.findall(r'ft_keyword\s*=\s*"(\d+)"', tag)) | set(
        re.findall(r"data-placement-id='(\d+)'", tag))
    if ids and args.demo_value not in ids:
        print(f"!! --demo-value {args.demo_value} is not this tag's Placement_ID "
              f"{sorted(ids)} — the nwdemocr value must be the Placement_ID")
        return 1
    client = _client()
    li_svc = client.GetService("LineItemService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
    ct_svc = client.GetService("CustomTargetingService", version=V)

    print("=" * 78)
    print(f"DEMO CREATIVE  ({'APPLY' if args.apply else 'DRY RUN'})")
    print("=" * 78)

    tmpl = _q(li_svc, "getLineItemsByStatement", "id = :i", i=args.template_li)[0]
    t_lica = _q(lica_svc, "getLineItemCreativeAssociationsByStatement",
                "lineItemId = :i", i=tmpl.id)
    t_cr = _q(cr_svc, "getCreativesByStatement", "id = :i", i=t_lica[0].creativeId)[0]
    print(f"template LI {tmpl.id} on order {tmpl.orderId}: {tmpl.lineItemType} p{tmpl.priority} "
          f"{tmpl.costType} goal {tmpl.primaryGoal.goalType}/{tmpl.primaryGoal.units}")
    print(f"  placeholders {[(p.size.width, p.size.height) for p in tmpl.creativePlaceholders]}  "
          f"env {tmpl.environmentType}  roadblock {tmpl.roadblockingType}")
    print(f"template creative {t_cr.id}: {t_cr._xsd_type.name} {t_cr.size.width}x{t_cr.size.height} "
          f"advertiser {t_cr.advertiserId} safeframe={t_cr.isSafeFrameCompatible}")

    decl = t_cr["thirdPartyDataDeclaration"] if "thirdPartyDataDeclaration" in dir(t_cr) else None
    comp_svc = client.GetService("CompanyService", version=V)

    def _decl_str(d):
        if d is None:
            return "none"
        ids = list(d.thirdPartyCompanyIds or [])
        names = []
        for i in ids:
            c = _q(comp_svc, "getCompaniesByStatement", "id = :i", i=i)
            names.append(f"{i} {c[0].name if c else '?'}")
        return f"{d.declarationType} {names}"
    print(f"  ad technology declaration: {_decl_str(decl)}")
    if decl is None or str(decl.declarationType) != "DECLARED" or not decl.thirdPartyCompanyIds:
        print("!! template creative declares no ad technology — refusing to create an "
              "undeclared third-party creative")
        return 1
    decl_body = {"declarationType": "DECLARED",
                 "thirdPartyCompanyIds": list(decl.thirdPartyCompanyIds)}

    keys = _q(ct_svc, "getCustomTargetingKeysByStatement", "name = :n", n=DEMO_KEY)
    if len(keys) != 1:
        print(f"!! expected one '{DEMO_KEY}' key, found {len(keys)}")
        return 1
    key = keys[0]
    val = _q(ct_svc, "getCustomTargetingValuesByStatement",
             "customTargetingKeyId = :k AND name = :n", k=key.id, n=args.demo_value)
    print(f"\n{DEMO_KEY} (key {key.id}) = {args.demo_value}: "
          + (f"exists (value {val[0].id})" if val else "will create"))

    existing_li = _q(li_svc, "getLineItemsByStatement", "orderId = :o AND name = :n",
                     o=tmpl.orderId, n=args.name)
    existing_cr = _q(cr_svc, "getCreativesByStatement",
                     "advertiserId = :a AND name = :n", a=t_cr.advertiserId, n=args.name)
    print(f"line item: {args.name}  "
          + (f"[exists: {existing_li[0].id}]" if existing_li else "[will create]"))
    print(f"creative:  ThirdPartyCreative {t_cr.size.width}x{t_cr.size.height}, "
          f"safeframe={t_cr.isSafeFrameCompatible}, {len(tag):,} chars  "
          + (f"[exists: {existing_cr[0].id}]" if existing_cr else "[will create]"))
    print("  tag starts: " + " ".join(tag.split())[:160] + " …")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to create in GAM.")
        return 0

    if not val:
        val = ct_svc.createCustomTargetingValues([{
            "customTargetingKeyId": key.id, "name": args.demo_value,
            "displayName": args.demo_value, "matchType": "EXACT"}])
        print(f"created {DEMO_KEY} value {val[0].id}")
    val_id = val[0].id

    want_ct = {
        "xsi_type": "CustomCriteriaSet", "logicalOperator": "OR",
        "children": [{
            "xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
            "children": [{"xsi_type": "CustomCriteria", "keyId": key.id,
                          "valueIds": [val_id], "operator": "IS"}],
        }],
    }
    if existing_li:
        li = existing_li[0]
        crit = [c for s_ in (li.targeting.customTargeting.children or [])
                for c in (s_.children or [])] if li.targeting.customTargeting else []
        cur_vals = sorted(v for c in crit if c.keyId == key.id for v in (c.valueIds or []))
        if cur_vals != [val_id]:
            li.targeting.customTargeting = want_ct
            li.notes = (f"Demo of {Path(args.tag).name}; cloned from LI {tmpl.id}. "
                        f"Gated: ?{DEMO_KEY}={args.demo_value}")
            li.skipInventoryCheck = True
            li.allowOverbook = True
            li = li_svc.updateLineItems([li])[0]
            print(f"retargeted line item {li.id}: {DEMO_KEY} value ids {cur_vals} → [{val_id}] "
                  f"({li.status})")
        else:
            print(f"line item {li.id} already gated on {DEMO_KEY}={args.demo_value}")
    else:
        body = {k: tmpl[k] for k in CLONE_FIELDS if tmpl[k] is not None}
        tg = tmpl.targeting
        tg.customTargeting = want_ct
        body.update({
            "orderId": tmpl.orderId,
            "name": args.name,
            "startDateTimeType": "IMMEDIATELY",
            "unlimitedEndDateTime": True,
            "targeting": tg,
            # A demo value forecasts ~no inventory, so GAM rejects the line
            # with NOT_ENOUGH_INVENTORY unless both are set at create.
            "skipInventoryCheck": True,
            "allowOverbook": True,
            "notes": f"Demo of {Path(args.tag).name}; cloned from LI {tmpl.id}. "
                     f"Gated: ?{DEMO_KEY}={args.demo_value}",
        })
        li = li_svc.createLineItems([body])[0]
        print(f"created line item {li.id} ({li.status})")

    if existing_cr:
        cr = existing_cr[0]
        cur = cr["thirdPartyDataDeclaration"] if "thirdPartyDataDeclaration" in dir(cr) else None
        have = sorted(cur.thirdPartyCompanyIds or []) if cur is not None else []
        if (cur is None or str(cur.declarationType) != "DECLARED"
                or have != sorted(decl_body["thirdPartyCompanyIds"])):
            cr.thirdPartyDataDeclaration = decl_body
            cr = cr_svc.updateCreatives([cr])[0]
            print(f"updated creative {cr.id}: ad technology declaration → "
                  f"{_decl_str(cr.thirdPartyDataDeclaration)}")
        else:
            print(f"creative {cr.id} already declares {_decl_str(cur)}")
    else:
        cr = cr_svc.createCreatives([{
            "xsi_type": "ThirdPartyCreative",
            "name": args.name,
            "advertiserId": t_cr.advertiserId,
            "size": {"width": t_cr.size.width, "height": t_cr.size.height,
                     "isAspectRatio": False},
            "snippet": tag,
            "isSafeFrameCompatible": t_cr.isSafeFrameCompatible,
            "thirdPartyDataDeclaration": decl_body,
        }])[0]
        print(f"created creative {cr.id}")
    try:
        lica = lica_svc.createLineItemCreativeAssociations(
            [{"lineItemId": li.id, "creativeId": cr.id}])[0]
        print(f"LICA {lica.status}")
    except Exception as e:  # noqa: BLE001
        if "ALREADY_EXISTS" in str(e) or "DUPLICATE" in str(e):
            print("LICA already exists")
        else:
            raise

    print(f"\nTEST: any newsweek.com article + ?{DEMO_KEY}={args.demo_value}")
    print(f"NEXT: re-approve order {tmpl.orderId} in the GAM UI if the new line sits inactive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
