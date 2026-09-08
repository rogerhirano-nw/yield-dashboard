#!/usr/bin/env python3
"""Stand up an on-site DEMO of the fixed-size Insights banners, gated by ?nwdemocr=.

The network already demos the *fluid* Insights unit this way: line item
7330346837 ("Newsweek_Test_Auto_..._Infiniti_Insight-Unit_...") sits on the
Newsweek_Test-2 order, targets `homepage3`, and is gated to
`nwdemocr=insighttest`. Loading the homepage with that param shows the unit;
without it, nothing changes for real traffic.

This does the same for the three fixed-size styles, on its OWN nwdemocr value
so the existing `insighttest` demo is left exactly as it is:

  1. custom targeting value  nwdemocr=<--demo-value>   (default insightsbanner)
  2. the three native styles, gated to that value
  3. a demo line item on the test order, same gate, 970x250 + 728x90 + 300x250
  4. a LICA to an existing 1x1 Insights native creative

Everything is gated, so nothing serves to a reader who doesn't have the param.

Two GAM facts this encodes, both learned by getting them wrong first:

  - A native creative associates ONLY to a line item whose placeholder is
    **1x1 with creativeSizeType NATIVE**, bound to the creative template.
    Banner-sized placeholders are rejected with
    `RequiredSizeError.NOT_ALLOWED @ size; trigger:'1x1-NATIVE'`. The banner
    sizes live on the *native styles*, not on the line item -- the LI stays
    native-shaped and the style decides how each slot size renders.
  - `skipInventoryCheck` / `allowOverbook` must be re-asserted on **update**,
    not only at create. A 1x1 native placeholder forecasts ~no inventory, so an
    update without them is refused with `ForecastingError.NOT_ENOUGH_INVENTORY`.

Also: `createNativeStyles` returns styles as **INACTIVE**, and a new line item
is INACTIVE until activated -- so this activates both explicitly. Skipping that
leaves a demo that is fully built and silently serves nothing.

Caveat worth knowing: GAM's NativeStyle may not honour customTargeting at serve
time (inventory targeting certainly is). The script reads each style back after
creating it and SAYS whether the gate stuck. If it didn't, the styles are live
for template 12412102 generally -- which today means only the already-gated
demo line item above could render through them, and `--undo` deactivates them.

Usage:
    python3 scripts/setup_insights_native_demo.py                # dry run
    python3 scripts/setup_insights_native_demo.py --apply
    python3 scripts/setup_insights_native_demo.py --apply --undo # deactivate
"""

import argparse
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parent.parent

_env = REPO / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from googleads import ad_manager, oauth2  # noqa: E402

V = "v202605"
CREATIVE_TEMPLATE_ID = 12412102
NWDEMOCR_KEY_ID = 14518983
ORDER_ID = 4082002976            # Newsweek_Test-2
ADVERTISER_ID = 5131205161       # must match the order's advertiser for the LICA
DEMO_CREATIVE_ID = 138562612084  # "Infiniti test page" -- 1x1 native, same advertiser
NEWSWEEK_ROOT_AD_UNIT = 23207092721
SIZES = [(970, 250), (728, 90), (300, 250)]
# The line item is native-shaped; the SIZES above are the styles' business.
NATIVE_PLACEHOLDER = [{
    "size": {"width": 1, "height": 1, "isAspectRatio": False},
    "creativeSizeType": "NATIVE",
    "creativeTemplateId": CREATIVE_TEMPLATE_ID,
}]
STYLE_NAME = "Insights Premium Spotlight DEMO ({w}x{h})"
LI_NAME = "Newsweek_Test_Insights-Native-Banner-DEMO"

HTML_PATH = REPO / "docs" / "snippets" / "insights_native_style.html"
CSS_PATH = REPO / "docs" / "snippets" / "insights_native_style.css"

DEMO_PAGES = [
    ("Homepage", "https://www.newsweek.com/"),
    ("Insights article",
     "https://www.newsweek.com/insights/the-sovereignty-imperative-why-institutional-"
     "intelligence-must-outlive-any-model"),
]


def one(resp):
    return (list(getattr(resp, "results", []) or []) or [None])[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", action="store_true", help="deactivate the demo styles + pause the LI")
    ap.add_argument("--demo-value", default="insightsbanner")
    args = ap.parse_args()

    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        keyfile = f.name
    oc = oauth2.GoogleServiceAccountClient(keyfile, "https://www.googleapis.com/auth/dfp")
    client = ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"])

    ct = client.GetService("CustomTargetingService", version=V)
    ns = client.GetService("NativeStyleService", version=V)
    li_svc = client.GetService("LineItemService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)

    print("=" * 72)
    print(f"INSIGHTS BANNER DEMO  ({'APPLY' if args.apply else 'DRY RUN'}"
          f"{' / UNDO' if args.undo else ''})")
    print("=" * 72)
    print(f"gate       : nwdemocr={args.demo_value}")
    print(f"order      : {ORDER_ID} (Newsweek_Test-2)")
    print(f"creative   : {DEMO_CREATIVE_ID} (1x1 Insights native)")
    print()

    # ---- 1. the nwdemocr value ------------------------------------------------
    val = one(ct.getCustomTargetingValuesByStatement(
        ad_manager.StatementBuilder(version=V)
        .Where("customTargetingKeyId = :k AND name = :n")
        .WithBindVariable("k", NWDEMOCR_KEY_ID).WithBindVariable("n", args.demo_value)
        .Limit(1).ToStatement()))
    print(f"nwdemocr value {args.demo_value!r}: "
          + (f"exists id={val.id}" if val else "will create"))

    if not args.apply:
        for w, h in SIZES:
            print(f"  style {STYLE_NAME.format(w=w, h=h)}")
        print(f"  line item {LI_NAME} placeholders={SIZES}")
        print("\nRe-run with --apply to write to GAM.")
        return 0

    if args.undo:
        for w, h in SIZES:
            st = one(ns.getNativeStylesByStatement(
                ad_manager.StatementBuilder(version=V).Where("name = :n")
                .WithBindVariable("n", STYLE_NAME.format(w=w, h=h)).Limit(1).ToStatement()))
            if st:
                st.status = "INACTIVE"
                ns.updateNativeStyles([st])
                print(f"deactivated style {st.id}")
        li = one(li_svc.getLineItemsByStatement(
            ad_manager.StatementBuilder(version=V).Where("orderId = :o AND name = :n")
            .WithBindVariable("o", ORDER_ID).WithBindVariable("n", LI_NAME).Limit(1).ToStatement()))
        if li:
            li.status = "PAUSED"
            li_svc.updateLineItems([li])
            print(f"paused line item {li.id}")
        return 0

    if val is None:
        val = ct.createCustomTargetingValues([{
            "customTargetingKeyId": NWDEMOCR_KEY_ID,
            "name": args.demo_value,
            "displayName": args.demo_value,
            "matchType": "EXACT",
        }])[0]
        print(f"created nwdemocr value id={val.id}")

    gate = {
        "xsi_type": "CustomCriteriaSet", "logicalOperator": "OR",
        "children": [{
            "xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
            "children": [{"xsi_type": "CustomCriteria", "keyId": NWDEMOCR_KEY_ID,
                          "valueIds": [val.id], "operator": "IS"}],
        }],
    }
    inventory = {"targetedAdUnits": [
        {"adUnitId": str(NEWSWEEK_ROOT_AD_UNIT), "includeDescendants": True}]}

    # ---- 2. the three gated native styles -------------------------------------
    html, css = HTML_PATH.read_text(), CSS_PATH.read_text()
    for w, h in SIZES:
        name = STYLE_NAME.format(w=w, h=h)
        st = one(ns.getNativeStylesByStatement(
            ad_manager.StatementBuilder(version=V).Where("name = :n")
            .WithBindVariable("n", name).Limit(1).ToStatement()))
        if st:
            st.htmlSnippet, st.cssSnippet, st.status = html, css, "ACTIVE"
            st = ns.updateNativeStyles([st])[0]
            print(f"refreshed style id={st.id}  {name}")
        else:
            st = ns.createNativeStyles([{
                "name": name, "htmlSnippet": html, "cssSnippet": css,
                "creativeTemplateId": CREATIVE_TEMPLATE_ID, "isFluid": False,
                "size": {"width": w, "height": h, "isAspectRatio": False},
                "targeting": {"inventoryTargeting": inventory, "customTargeting": gate},
            }])[0]
            print(f"created style id={st.id}  {name}")
        if getattr(st, "status", None) != "ACTIVE":
            st.status = "ACTIVE"           # createNativeStyles returns INACTIVE
            st = ns.updateNativeStyles([st])[0]
        print(f"    status={st.status}")
        stuck = getattr(getattr(st, "targeting", None), "customTargeting", None) is not None
        print(f"    nwdemocr gate honoured by the style: {'YES' if stuck else 'NO -- see docstring'}")

    # ---- 3. the demo line item -------------------------------------------------
    li = one(li_svc.getLineItemsByStatement(
        ad_manager.StatementBuilder(version=V).Where("orderId = :o AND name = :n")
        .WithBindVariable("o", ORDER_ID).WithBindVariable("n", LI_NAME).Limit(1).ToStatement()))
    if li is None:
        li = li_svc.createLineItems([{
            "orderId": ORDER_ID, "name": LI_NAME,
            "lineItemType": "SPONSORSHIP", "priority": 4,
            "costType": "CPD", "costPerUnit": {"currencyCode": "USD", "microAmount": 0},
            "startDateTimeType": "IMMEDIATELY", "unlimitedEndDateTime": True,
            "creativeRotationType": "EVEN", "roadblockingType": "ONE_OR_MORE",
            "skipInventoryCheck": True, "allowOverbook": True,
            "primaryGoal": {"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 100},
            "creativePlaceholders": NATIVE_PLACEHOLDER,
            "targeting": {"inventoryTargeting": inventory, "customTargeting": gate},
        }])[0]
        print(f"created line item id={li['id']}  status={li['status']}")
    else:
        print(f"line item exists id={li['id']}  status={li['status']}")
        # zeep objects are not dicts: .get() raises AttributeError, so read the
        # optional field with getattr (this crashed the first repair run).
        shapes = [(p["size"]["width"], p["size"]["height"],
                   getattr(p, "creativeSizeType", None))
                  for p in li["creativePlaceholders"]]
        if sorted(shapes) != [(1, 1, "NATIVE")]:
            li["creativePlaceholders"] = NATIVE_PLACEHOLDER
            # both required again on update, not just at create -- see docstring
            li["skipInventoryCheck"] = True
            li["allowOverbook"] = True
            li = li_svc.updateLineItems([li])[0]
            print(f"    repaired placeholders {shapes} -> [(1, 1, 'NATIVE')]")

    try:
        a = lica_svc.createLineItemCreativeAssociations(
            [{"lineItemId": li["id"], "creativeId": DEMO_CREATIVE_ID}])[0]
        print(f"LICA status={a['status']}")
    except Exception as e:
        print("LICA already exists" if "ALREADY_EXISTS" in str(e) else f"LICA error: {e}")

    # a fresh line item is INACTIVE; without this the demo is built but dead
    li_svc.performLineItemAction(
        {"xsi_type": "ActivateLineItems"},
        ad_manager.StatementBuilder(version=V)
        .Where("id = :i").WithBindVariable("i", li["id"]).ToStatement())
    live = one(li_svc.getLineItemsByStatement(
        ad_manager.StatementBuilder(version=V)
        .Where("id = :i").WithBindVariable("i", li["id"]).Limit(1).ToStatement()))
    print(f"line item status now: {live['status']}")

    print("\nDemo URLs:")
    for label, url in DEMO_PAGES:
        sep = "&" if "?" in url else "?"
        print(f"  {label:18s} {url}{sep}nwdemocr={args.demo_value}")
    print(f"\nExisting fluid demo, untouched: ?nwdemocr=insighttest (LI 7330346837)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
