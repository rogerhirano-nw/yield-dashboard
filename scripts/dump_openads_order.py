#!/usr/bin/env python3
"""Read-only diagnostic: dump the OpenAds GAM order (3907413105) —
order info, line items (video focus: environmentType, placeholders,
video settings, custom targeting with resolved key/value names), and
every associated creative's config (VAST url / duration / snippet).

Runs locally (loads ../.env if present) or in Actions with
GAM_SERVICE_ACCOUNT_JSON / GAM_NETWORK_ID in the environment — see
.github/workflows/dump_openads_order.yml (pull_index_ob_requests
pattern). No writes.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
envp = ROOT / ".env"
if envp.exists():
    for line in envp.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(ROOT))
from gam_client import GAMClient  # noqa: E402
from googleads import ad_manager  # noqa: E402

ORDER_ID = 3907413105
V = "v202605"

gc = GAMClient()
client = gc._get_soap_client()


def g(obj, attr, default=None):
    return getattr(obj, attr, default)


# ---------- 1) Order ----------
order_svc = client.GetService("OrderService", version=V)
sb = ad_manager.StatementBuilder(version=V).Where(f"id = {ORDER_ID}").Limit(1)
resp = order_svc.getOrdersByStatement(sb.ToStatement())
orders = list(g(resp, "results", []) or [])
if not orders:
    print(f"!! Order {ORDER_ID} not found")
    sys.exit(1)
o = orders[0]
print("=" * 72)
print(f"ORDER {g(o,'id')}: {g(o,'name')}")
print(f"  status={g(o,'status')}  advertiserId={g(o,'advertiserId')}  "
      f"traffickerId={g(o,'traffickerId')}")
print("=" * 72)

# ---------- 2) Line items ----------
li_svc = client.GetService("LineItemService", version=V)
sb = ad_manager.StatementBuilder(version=V).Where(f"orderId = {ORDER_ID}").Limit(500)
resp = li_svc.getLineItemsByStatement(sb.ToStatement())
lis = list(g(resp, "results", []) or [])
print(f"\n{len(lis)} line item(s) on order\n")

key_ids, val_ids = set(), set()


def walk_custom(node, depth=0, out=None):
    """Collect (keyId, valueIds, operator) tuples from the customTargeting tree."""
    if node is None:
        return
    kids = g(node, "children", None)
    if kids:
        for ch in kids:
            walk_custom(ch, depth + 1, out)
        return
    kid = g(node, "keyId", None)
    if kid is not None:
        vids = list(g(node, "valueIds", []) or [])
        key_ids.add(kid)
        val_ids.update(vids)
        if out is not None:
            out.append((kid, vids, g(node, "operator", "IS")))


li_custom = {}
video_li_ids, all_li_ids = set(), []
for li in lis:
    lid = g(li, "id")
    all_li_ids.append(lid)
    env = g(li, "environmentType")
    if env == "VIDEO_PLAYER":
        video_li_ids.add(lid)
    crumbs = []
    walk_custom(g(g(li, "targeting", None), "customTargeting", None), out=crumbs)
    li_custom[lid] = crumbs

    sizes = []
    for ph in g(li, "creativePlaceholders", []) or []:
        sz = g(ph, "size", None)
        s = f"{g(sz,'width')}x{g(sz,'height')}" if sz else "?"
        cst = g(ph, "creativeSizeType", "")
        comp = g(ph, "companions", None)
        sizes.append(s + (f"[{cst}]" if cst and cst != "PIXEL" else "")
                     + (f"+{len(comp)}comp" if comp else ""))
    vmd = g(li, "videoMaxDuration", None)
    print(f"--- LI {lid}  '{g(li,'name')}'")
    print(f"    status={g(li,'status')}  type={g(li,'lineItemType')}  "
          f"priority={g(li,'priority')}  env={env}")
    cpu = g(li, "costPerUnit", None)
    rate = None
    if cpu is not None:
        micro = g(cpu, "microAmount", None)
        rate = f"{g(cpu,'currencyCode','')} {micro/1e6:.2f}" if micro else None
    print(f"    costType={g(li,'costType')}  rate={rate}  "
          f"goal={g(g(li,'goal',None),'goalType',None)}")
    print(f"    placeholders: {', '.join(sizes) or '(none)'}")
    if vmd:
        print(f"    videoMaxDuration={vmd}ms")
    tgt = g(li, "targeting", None)
    inv = g(tgt, "inventoryTargeting", None)
    aus = [g(a, "adUnitId") for a in (g(inv, "targetedAdUnits", []) or [])]
    if aus:
        print(f"    targeted adUnits: {aus[:12]}{' …' if len(aus) > 12 else ''}")
    vpos = g(tgt, "videoPositionTargeting", None)
    if vpos:
        vps = []
        for vp in g(vpos, "targetedPositions", []) or []:
            p = g(vp, "videoPosition", None)
            vps.append(f"{g(p,'positionType',None)}")
        print(f"    video positions: {vps}")
    req = g(tgt, "requestPlatformTargeting", None)
    if req:
        print(f"    requestPlatforms: {list(g(req,'targetedRequestPlatforms',[]) or [])}")
    if crumbs:
        print(f"    customTargeting: {len(crumbs)} criteria (resolved below)")
    print()

# ---------- 3) Resolve custom targeting key/value names ----------
key_names, val_names = {}, {}
if key_ids:
    ct_svc = client.GetService("CustomTargetingService", version=V)
    ids = ", ".join(str(int(k)) for k in key_ids)
    sb = ad_manager.StatementBuilder(version=V).Where(f"id IN ({ids})").Limit(500)
    for k in g(ct_svc.getCustomTargetingKeysByStatement(sb.ToStatement()),
               "results", []) or []:
        key_names[g(k, "id")] = g(k, "name")
    vids = [str(int(v)) for v in val_ids]
    for i in range(0, len(vids), 400):
        chunk = ", ".join(vids[i:i + 400])
        sb = ad_manager.StatementBuilder(version=V).Where(
            f"customTargetingKeyId IN ({ids}) AND id IN ({chunk})").Limit(500)
        for v in g(ct_svc.getCustomTargetingValuesByStatement(sb.ToStatement()),
                   "results", []) or []:
            val_names[g(v, "id")] = g(v, "name")
    print("=" * 72)
    print("CUSTOM TARGETING (resolved)")
    for lid, crumbs in li_custom.items():
        if not crumbs:
            continue
        print(f"  LI {lid}:")
        for kid, vv, op in crumbs:
            kn = key_names.get(kid, f"key:{kid}")
            vn = [val_names.get(v, str(v)) for v in vv]
            print(f"    {kn} {op} {vn}")

# ---------- 4) LICAs + creatives ----------
print("\n" + "=" * 72)
lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
ids = ", ".join(str(int(i)) for i in all_li_ids)
sb = ad_manager.StatementBuilder(version=V).Where(f"lineItemId IN ({ids})").Limit(500)
licas = list(g(lica_svc.getLineItemCreativeAssociationsByStatement(sb.ToStatement()),
               "results", []) or [])
li_by_creative = {}
for la in licas:
    li_by_creative.setdefault(g(la, "creativeId"), []).append(
        (g(la, "lineItemId"), g(la, "status")))
print(f"CREATIVES ({len(licas)} association(s))")

cre_svc = client.GetService("CreativeService", version=V)
cids = ", ".join(str(int(c)) for c in li_by_creative)
sb = ad_manager.StatementBuilder(version=V).Where(f"id IN ({cids})").Limit(500)
for c in g(cre_svc.getCreativesByStatement(sb.ToStatement()), "results", []) or []:
    cid = g(c, "id")
    ctype = type(c).__name__
    sz = g(c, "size", None)
    size = f"{g(sz,'width')}x{g(sz,'height')}" if sz else "?"
    on_lis = li_by_creative.get(cid, [])
    on_video = any(l in video_li_ids for l, _ in on_lis)
    print(f"\n--- creative {cid} [{ctype}] '{g(c,'name')}'  size={size}"
          f"{'  [VIDEO LI]' if on_video else ''}")
    print(f"    on LIs: {on_lis}")
    for attr in ("vastXmlUrl", "vastRedirectType", "duration", "isSafeFrameCompatible",
                 "sslScanResult", "sslManualOverride", "destinationUrl"):
        v = g(c, attr, None)
        if v is not None:
            print(f"    {attr} = {v}")
    snippet = g(c, "htmlSnippet", None) or g(c, "snippet", None) or ""
    if snippet:
        # Full snippet for creatives on video LIs (what the Dmitriy ask needs);
        # head only for the known display set.
        if on_video:
            print(f"    snippet ({len(snippet)} chars, full):")
            print("      " + snippet.replace("\n", "\n      "))
        else:
            print(f"    snippet ({len(snippet)} chars), first 400:")
            print("      " + snippet[:400].replace("\n", "\n      "))
