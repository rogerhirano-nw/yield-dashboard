#!/usr/bin/env python3
"""GAM side of the section-hub "SPONSORED BY <logo>" lockup (oop1).

The CategoryHub (tent-pole) template — e.g. /ai-politics — places the
out-of-page slot #dfp-ad-oop1 inside its header, directly under the dek, and
sends page key-values `page_type=categories` + `categories=<section slug>`.
So one line item on oop1 targeted `categories=<slug>` puts the lockup on
exactly one hub, and the creative (docs/snippets/section_sponsor_lockup_
creative.html) renders it in its own iframe. See docs/section_sponsor_lockup.md.

What this does (lookup-first; re-running only fills gaps):
  1. reports the oop1 ad unit, the `categories` / `nwdemocr` key-values, and
     every other active line item that competes for oop1 — an untargeted one
     wins hub impressions the lockup should get (and renders nothing there);
  2. [--apply] creates a [TEST] Sponsorship line item on Newsweek_Test-2:
     oop1, `categories IS <slug>` AND `nwdemocr IS <demo value>`, priority 3;
  3. with --creative-id: writes the snippet (CFG filled from the flags) into
     that UI-made "Out of page" creative, forces SafeFrame off, and LICAs it.

The creative itself has to be added from the line item in the GAM UI (size
"Out of page", SafeFrame off, logo uploaded as asset PNG1): a 1x1
CustomCreative created through the API does not serve an out-of-page slot.

Usage:
    python3 scripts/setup_section_sponsor_lockup.py                   # dry run
    python3 scripts/setup_section_sponsor_lockup.py --apply           # create LI
    python3 scripts/setup_section_sponsor_lockup.py --creative-id N --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_envp = ROOT / ".env"
if _envp.exists():
    for _line in _envp.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

V = "v202605"
SNIPPET_FILE = ROOT / "docs" / "snippets" / "section_sponsor_lockup_creative.html"
SENTINEL = 'id="nw-ssl"'
OOP1_CODE = "oop1"
OOP1_AD_UNIT_ID = 23207087801          # /22541732127/newsweek/oop1 (verified by lookup)
ORDER_ID = 4082002976                  # Newsweek_Test-2
ADVERTISER_ID = 5131205161             # advertiser on the [TEST] sponsor-logo work
SECTION_KEY = "categories"             # page KV: section slug (e.g. ai-politics)
DEMO_KEY = "nwdemocr"


def render_snippet(label: str, sponsor: str, pixels: list[str]) -> str:
    """The snippet file with its CFG block filled in."""
    html = SNIPPET_FILE.read_text()
    m = re.search(r"/\*CFG\*/(\{.*?\})/\*CFG\*/", html, re.S)
    if not m:
        raise SystemExit(f"!! no /*CFG*/ block in {SNIPPET_FILE.name}")
    cfg = json.loads(m.group(1))
    cfg.update({"label": label, "sponsor": sponsor, "pixels": pixels})
    return html[:m.start(1)] + json.dumps(cfg, indent=4) + html[m.end(1):]


TEMPLATE_FILE = ROOT / "docs" / "snippets" / "section_sponsor_lockup_template.html"

# GAM creative-template variables (created in the UI; the API can only read
# templates). uniqueName -> (type, required, note). Referenced as [%Name%].
TEMPLATE_VARIABLES = [
    ("Logo", "Asset (image)", True, "transparent PNG/SVG, ~3x the 28px display height"),
    ("SponsorName", "Text", True, "logo alt text, e.g. Kia (no double quotes)"),
    ("Label", "List: Sponsored by | Presented by | In partnership with", True,
     "default Sponsored by"),
    ("ClickThroughURL", "URL", True, "advertiser landing page"),
    ("ImpressionPixels", "Text", False,
     "agency impression pixel URLs, space-separated; declare their ad technology"),
]


def render_template() -> str:
    """The creative-template code: the snippet with its per-flight values
    replaced by template variables, so one template serves every sponsor.

    Text values go into the markup (an apostrophe in a sponsor name can't
    break the script); the script then reads them back from the DOM."""
    html = SNIPPET_FILE.read_text()
    m = re.search(r"/\*CFG\*/(\{.*?\})/\*CFG\*/", html, re.S)
    if not m:
        raise SystemExit(f"!! no /*CFG*/ block in {SNIPPET_FILE.name}")
    host = json.loads(m.group(1))["host"]
    cfg = (
        "{\n"
        "    \"label\": document.querySelector('#nw-ssl .ssl-label').textContent,\n"
        "    \"sponsor\": document.querySelector('#nw-ssl .ssl-logo').alt,\n"
        "    \"logo\": \"%%VIEW_URL_UNESC%%[%Logo%]\",\n"
        "    \"href\": \"%%CLICK_URL_UNESC%%[%ClickThroughURL%]\",\n"
        f"    \"host\": {json.dumps(host)},\n"
        "    \"pixels\": \"[%ImpressionPixels%]\".split(/\\s+/).filter(Boolean)\n"
        "  }"
    )
    out = html[:m.start(1)] + cfg + html[m.end(1):]
    markup = ('<a id="nw-ssl" target="_blank" rel="noopener sponsored">'
              '<span class="ssl-label"></span><img class="ssl-logo" alt=""></a>')
    if markup not in out:
        raise SystemExit("!! lockup markup changed; update render_template()")
    out = out.replace(markup, (
        '<a id="nw-ssl" target="_blank" rel="noopener sponsored">'
        '<span class="ssl-label">[%Label%]</span>'
        '<img class="ssl-logo" alt="[%SponsorName%]"></a>'))
    head = re.match(r"<!--.*?-->\n", out, re.S)
    note = (
        "<!--\n"
        "  GAM CREATIVE TEMPLATE \"Section Sponsor Lockup\" — generated from\n"
        "  docs/snippets/section_sponsor_lockup_creative.html by\n"
        "  `scripts/setup_section_sponsor_lockup.py --print-template`; do not edit by\n"
        "  hand (tests/test_section_sponsor_lockup.py pins the two in sync).\n"
        "  Template settings: out-of-page (interstitial) ON, SafeFrame OFF.\n"
        "  Variables: " + ", ".join(v[0] for v in TEMPLATE_VARIABLES) + ".\n"
        "  See docs/section_sponsor_lockup.md.\n"
        "-->\n")
    return note + out[head.end():] if head else note + out


def _client():
    from googleads import ad_manager, oauth2  # type: ignore
    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        kf = f.name
    oc = oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp")
    return ad_manager.AdManagerClient(oc, "NewsweekDashboard/1.0",
                                      network_code=os.environ["GAM_NETWORK_ID"])


def _q(svc, method, where, limit=500, offset=0, **binds):
    from googleads import ad_manager  # type: ignore
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(limit).Offset(offset)
    for k, v in binds.items():
        sb = sb.WithBindVariable(k, v)
    return list(getattr(getattr(svc, method)(sb.ToStatement()), "results", []) or [])


def _key_value(ct_svc, key_name, value):
    keys = _q(ct_svc, "getCustomTargetingKeysByStatement", "name = :n", n=key_name)
    if len(keys) != 1:
        return None, None
    vals = _q(ct_svc, "getCustomTargetingValuesByStatement",
              "customTargetingKeyId = :k AND name = :n", k=keys[0].id, n=value)
    return keys[0], (vals[0] if vals else None)


def _competitors(li_svc, ad_unit_id, own_name):
    """Active line items targeting oop1 directly (other than ours)."""
    out, off = [], 0
    while True:
        page = _q(li_svc, "getLineItemsByStatement",
                  "status IN ('DELIVERING', 'READY', 'PAUSED')", limit=500, offset=off)
        for li in page:
            inv = li.targeting.inventoryTargeting
            units = [str(u.adUnitId) for u in (inv.targetedAdUnits or [])] if inv else []
            if str(ad_unit_id) in units and li.name != own_name:
                out.append(li)
        if len(page) < 500:
            return out
        off += 500


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default="ai-politics",
                    help="`categories` page key-value of the hub (the URL slug)")
    ap.add_argument("--demo-value", default="section-sponsor",
                    help=f"{DEMO_KEY} gate for the [TEST] line ('' = no gate)")
    ap.add_argument("--sponsor", default="Kia", help="logo alt text")
    ap.add_argument("--label", default="Sponsored by")
    ap.add_argument("--pixel", action="append", default=[],
                    help="agency impression pixel URL (repeatable)")
    ap.add_argument("--creative-id", type=int, help="UI-made Out-of-page creative to fill")
    ap.add_argument("--print-template", action="store_true",
                    help="print the GAM creative-template code + variables and exit")
    ap.add_argument("--template-id", type=int,
                    help="create the creative from this GAM creative template instead")
    ap.add_argument("--create-creative", action="store_true",
                    help="create the CustomCreative via the API (mirrors --ref-creative's "
                         "out-of-page fields) instead of adding it in the UI")
    ap.add_argument("--logo-file", default=str(ROOT / "scripts" / "orders" / "assets" /
                                               "kia_logo_trace.png"),
                    help="logo uploaded as asset PNG1 (with --create-creative)")
    ap.add_argument("--click-url", default="https://www.kia.com/us/en")
    ap.add_argument("--ref-creative", type=int, default=138562255517,
                    help="known-serving UI-made oop creative whose size/isInterstitial to mirror")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.print_template:
        print(render_template())
        print("\nVariables (unique name / type / required / note):")
        for n, t, req, note in TEMPLATE_VARIABLES:
            print(f"  {n:<17} {t:<55} {'required' if req else 'optional'}  {note}")
        return 0

    name = f"[TEST] Section Sponsor Lockup - {args.section}"
    snippet = render_snippet(args.label, args.sponsor, args.pixel)

    client = _client()
    inv_svc = client.GetService("InventoryService", version=V)
    li_svc = client.GetService("LineItemService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
    ct_svc = client.GetService("CustomTargetingService", version=V)

    print("=" * 78)
    print(f"SECTION SPONSOR LOCKUP — {args.section}  ({'APPLY' if args.apply else 'DRY RUN'})")
    print("=" * 78)

    units = _q(inv_svc, "getAdUnitsByStatement", "adUnitCode = :c", c=OOP1_CODE)
    unit_ids = [int(u.id) for u in units]
    print(f"ad unit {OOP1_CODE}: {[(u.id, u.name, u.status) for u in units]}")
    if OOP1_AD_UNIT_ID not in unit_ids:
        print(f"!! expected oop1 id {OOP1_AD_UNIT_ID} — update OOP1_AD_UNIT_ID")
        return 1

    sec_key, sec_val = _key_value(ct_svc, SECTION_KEY, args.section)
    if sec_key is None:
        print(f"!! no single '{SECTION_KEY}' custom targeting key — cannot scope to the hub")
        return 1
    print(f"{SECTION_KEY} (key {sec_key.id}, {sec_key.type}) = {args.section}: "
          + (f"value {sec_val.id}" if sec_val else "value missing — will create on --apply"))
    demo_key = demo_val = None
    if args.demo_value:
        demo_key, demo_val = _key_value(ct_svc, DEMO_KEY, args.demo_value)
        if demo_key is None:
            print(f"!! no single '{DEMO_KEY}' key")
            return 1
        print(f"{DEMO_KEY} (key {demo_key.id}) = {args.demo_value}: "
              + (f"value {demo_val.id}" if demo_val else "value missing — will create on --apply"))

    comp = _competitors(li_svc, OOP1_AD_UNIT_ID, name)
    print(f"\nother active line items on oop1: {len(comp)}")
    for li in comp:
        ct = li.targeting.customTargeting
        print(f"  {li.id}  {li.status:<10} {li.lineItemType:<12} p{li.priority}  "
              f"{'KV-targeted' if ct else 'NO KV targeting'}  {li.name[:70]}")
    loose = [li for li in comp if not li.targeting.customTargeting]
    if loose:
        print(f"  → {len(loose)} with no KV targeting can win oop1 on the hub (a"
              " breadcrumb-only creative serves there but renders nothing).")

    tsvc_all = client.GetService("CreativeTemplateService", version=V)
    tpls = _q(tsvc_all, "getCreativeTemplatesByStatement",
              "name LIKE :n", n="%Sponsor Lockup%")
    print(f"\ncreative templates matching 'Sponsor Lockup': {len(tpls)}")
    for t in tpls:
        print(f"  {t.id}  {t.name!r}  status={t.status}  "
              f"vars={[v.uniqueName for v in (t.variables or [])]}  "
              f"isInterstitial={getattr(t, 'isInterstitial', None)}  "
              f"safeframe={getattr(t, 'isSafeFrameCompatible', None)}")

    existing = _q(li_svc, "getLineItemsByStatement", "orderId = :o AND name = :n",
                  o=ORDER_ID, n=name)
    li = existing[0] if existing else None
    print(f"\nline item: {name!r} " + (f"[exists: {li.id}, {li.status}]" if li else "[will create]"))
    cr = None
    if args.creative_id:
        found = _q(cr_svc, "getCreativesByStatement", "id = :i", i=args.creative_id)
        if not found:
            print(f"!! creative {args.creative_id} not found")
            return 1
        cr = found[0]
        cur = getattr(cr, "htmlSnippet", None) or ""
        print(f"creative {cr.id} [{type(cr).__name__}] {cr.name[:60]}  "
              f"safeframe={cr.isSafeFrameCompatible}  "
              + ("snippet current" if cur == snippet else f"snippet {len(cur)} → {len(snippet)} chars"))
        if type(cr).__name__ != "CustomCreative":
            print("!! not a CustomCreative")
            return 1
        if cur.strip() and SENTINEL not in cur:
            print(f"!! creative carries a different snippet (no {SENTINEL}) — refusing to overwrite")
            return 1
    elif args.template_id:
        tsvc = client.GetService("CreativeTemplateService", version=V)
        tpl = _q(tsvc, "getCreativeTemplatesByStatement", "id = :i", i=args.template_id)
        if not tpl:
            print(f"!! creative template {args.template_id} not found")
            return 1
        tpl = tpl[0]
        have = {v.uniqueName for v in (tpl.variables or [])}
        missing = [v[0] for v in TEMPLATE_VARIABLES if v[2] and v[0] not in have]
        print(f"template {tpl.id} {tpl.name!r}: variables {sorted(have)}  "
              f"isInterstitial={getattr(tpl, 'isInterstitial', None)} "
              f"safeframe={getattr(tpl, 'isSafeFrameCompatible', None)}")
        if missing:
            print(f"!! template is missing required variables {missing}")
            return 1
        tname = f"{name} (template)"
        found = _q(cr_svc, "getCreativesByStatement", "name = :n", n=tname)
        logo = Path(args.logo_file)
        print(f"creative: {tname!r} "
              + (f"[exists: {found[0].id}]" if found else
                 f"[will create: TemplateCreative, Logo={logo.name}, "
                 f"SponsorName={args.sponsor}, Label={args.label}, click {args.click_url}]"))
        if found:
            cr = found[0]
    elif args.create_creative:
        ref = _q(cr_svc, "getCreativesByStatement", "id = :i", i=args.ref_creative)
        if not ref:
            print(f"!! reference creative {args.ref_creative} not found")
            return 1
        ref = ref[0]
        ref_int = getattr(ref, "isInterstitial", None)
        print(f"reference creative {ref.id} [{type(ref).__name__}] {ref.name[:50]}: "
              f"size {ref.size.width}x{ref.size.height} aspect={ref.size.isAspectRatio} "
              f"isInterstitial={ref_int} safeframe={ref.isSafeFrameCompatible} "
              f"assets={[a.macroName for a in (getattr(ref, 'customCreativeAssets', None) or [])]}")
        cr_existing = _q(cr_svc, "getCreativesByStatement", "name = :n", n=name)
        logo = Path(args.logo_file)
        print(f"creative: {name!r} "
              + (f"[exists: {cr_existing[0].id}]" if cr_existing else
                 f"[will create: CustomCreative {ref.size.width}x{ref.size.height}, "
                 f"isInterstitial={ref_int}, SafeFrame off, PNG1={logo.name} "
                 f"({logo.stat().st_size} B), click {args.click_url}]"))
        if cr_existing:
            cr = cr_existing[0]
    else:
        print("creative: none given — add it from the line item in the GAM UI (size "
              "\"Out of page\", SafeFrame OFF, logo as asset PNG1), then re-run with --creative-id.")
        print("\n----- snippet to paste -----\n" + snippet + "\n----- end snippet -----")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    def _ensure_value(key, val, value):
        if val:
            return val.id
        made = ct_svc.createCustomTargetingValues([{
            "customTargetingKeyId": key.id, "name": value,
            "displayName": value, "matchType": "EXACT"}])[0]
        print(f"created {key.name} value {made.id} = {value}")
        return made.id

    crit = [{"xsi_type": "CustomCriteria", "keyId": sec_key.id, "operator": "IS",
             "valueIds": [_ensure_value(sec_key, sec_val, args.section)]}]
    if demo_key is not None:
        crit.append({"xsi_type": "CustomCriteria", "keyId": demo_key.id, "operator": "IS",
                     "valueIds": [_ensure_value(demo_key, demo_val, args.demo_value)]})
    custom = {"xsi_type": "CustomCriteriaSet", "logicalOperator": "OR",
              "children": [{"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
                            "children": crit}]}

    if li is None:
        li = li_svc.createLineItems([{
            "orderId": ORDER_ID,
            "name": name,
            "lineItemType": "SPONSORSHIP",
            # Priority 3 outranks the default-priority-4 sponsorships on oop1,
            # so the gated test wins its own page views.
            "priority": 3,
            "costType": "CPD",
            "costPerUnit": {"currencyCode": "USD", "microAmount": 0},
            "startDateTimeType": "IMMEDIATELY",
            "unlimitedEndDateTime": True,
            "creativeRotationType": "EVEN",
            "skipInventoryCheck": True,
            "allowOverbook": True,
            "primaryGoal": {"goalType": "DAILY", "unitType": "IMPRESSIONS", "units": 100},
            # INTERSTITIAL = GAM's "Out of page" size; a plain 1x1 won't serve oop1.
            "creativePlaceholders": [{
                "size": {"width": 1, "height": 1, "isAspectRatio": False},
                "creativeSizeType": "INTERSTITIAL",
            }],
            "targeting": {
                "inventoryTargeting": {"targetedAdUnits": [
                    {"adUnitId": OOP1_AD_UNIT_ID, "includeDescendants": True}]},
                "customTargeting": custom,
            },
            "notes": f"Section-hub sponsor lockup test ({args.section}). "
                     + (f"Gated: ?{DEMO_KEY}={args.demo_value}. " if args.demo_value else "")
                     + "docs/section_sponsor_lockup.md",
        }])[0]
        print(f"created line item {li.id} ({li.status})")

    if cr is None and args.template_id:
        order_svc = client.GetService("OrderService", version=V)
        adv = _q(order_svc, "getOrdersByStatement", "id = :o", o=li.orderId)[0].advertiserId
        vals = [
            {"xsi_type": "AssetCreativeTemplateVariableValue", "uniqueName": "Logo",
             "asset": {"assetByteArray": logo.read_bytes(), "fileName": logo.name}},
            {"xsi_type": "StringCreativeTemplateVariableValue", "uniqueName": "SponsorName",
             "value": args.sponsor},
            {"xsi_type": "StringCreativeTemplateVariableValue", "uniqueName": "Label",
             "value": args.label},
            {"xsi_type": "UrlCreativeTemplateVariableValue", "uniqueName": "ClickThroughURL",
             "value": args.click_url},
        ]
        if args.pixel:
            vals.append({"xsi_type": "StringCreativeTemplateVariableValue",
                         "uniqueName": "ImpressionPixels", "value": " ".join(args.pixel)})
        cr = cr_svc.createCreatives([{
            "xsi_type": "TemplateCreative",
            "name": tname,
            "advertiserId": adv,
            "size": {"width": 1, "height": 1, "isAspectRatio": False},
            "creativeTemplateId": args.template_id,
            "destinationUrl": args.click_url,
            "isInterstitial": True,
            "isSafeFrameCompatible": False,
            "creativeTemplateVariableValues": vals,
        }])[0]
        print(f"created template creative {cr.id}")

    if cr is None and args.create_creative:
        order_svc = client.GetService("OrderService", version=V)
        adv = _q(order_svc, "getOrdersByStatement", "id = :o", o=li.orderId)[0].advertiserId
        body = {
            "xsi_type": "CustomCreative",
            "name": name,
            "advertiserId": adv,
            # Mirror the known-serving UI-made "Out of page" creative: a plain
            # 1x1 (isInterstitial unset) did not serve an OOP slot.
            "size": {"width": ref.size.width, "height": ref.size.height,
                     "isAspectRatio": ref.size.isAspectRatio},
            "destinationUrl": args.click_url,
            "htmlSnippet": snippet,
            "isSafeFrameCompatible": False,   # the iframe resize needs frameElement
            "customCreativeAssets": [{
                "macroName": "PNG1",
                "asset": {"assetByteArray": logo.read_bytes(), "fileName": logo.name},
            }],
        }
        if ref_int is not None:
            body["isInterstitial"] = ref_int
        cr = cr_svc.createCreatives([body])[0]
        print(f"created creative {cr.id} (isInterstitial={getattr(cr, 'isInterstitial', None)}, "
              f"safeframe={cr.isSafeFrameCompatible})")

    if cr is not None and type(cr).__name__ == "CustomCreative":
        changed = False
        if (getattr(cr, "htmlSnippet", None) or "") != snippet:
            cr.htmlSnippet = snippet
            changed = True
        if cr.isSafeFrameCompatible:
            cr.isSafeFrameCompatible = False   # the iframe resize needs frameElement
            changed = True
        if changed:
            cr = cr_svc.updateCreatives([cr])[0]
            print(f"updated creative {cr.id} (snippet {len(cr.htmlSnippet)} chars, SafeFrame off)")

    if cr is not None:
        try:
            lica_svc.createLineItemCreativeAssociations(
                [{"lineItemId": li.id, "creativeId": cr.id}])
            print(f"LICA li={li.id} cr={cr.id}")
        except Exception as e:  # noqa: BLE001
            if "ALREADY_EXISTS" in str(e) or "DUPLICATE" in str(e):
                print("LICA already exists")
            else:
                raise
        try:
            url = lica_svc.getPreviewUrl(li.id, cr.id,
                                         f"https://www.newsweek.com/{args.section}")
            print(f"PREVIEW: {url}")
        except Exception as e:  # noqa: BLE001 — preview needs an approved order
            print(f"(no preview URL yet: {str(e)[:120]})")

    gate = f"?{DEMO_KEY}={args.demo_value}" if args.demo_value else ""
    print(f"\nTEST: https://www.newsweek.com/{args.section}{gate}  "
          f"(QA: https://qa.next.newsweek.com/{args.section}{gate})")
    print(f"NEXT: approve/re-approve order {ORDER_ID} in the GAM UI if the line sits inactive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
