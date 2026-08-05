#!/usr/bin/env python3
"""Read-only recon for the click-audience build (default LI 7384069597).

Dumps what decides the click-capture mechanism and the segment setup:
  - the line item (name, order, status, delivered clicks so far)
  - its creatives via LICA -> CreativeService (concrete type, size,
    destinationUrl / snippet head) — a GAM-hosted creative can fire the
    audience pixel from an onclick; a third-party tag cannot be reached
    from outside its iframe
  - Audience Solutions state: existing first-party segments (proves the
    service account can see AudienceSegmentService + shows naming) and
    whether the DFPAudiencePixel ad unit already exists (the UI's
    "Generate tag" creates it once per network; the SA cannot create
    ad units itself)

No writes.
"""
import os, sys
from pathlib import Path

envp = Path(__file__).resolve().parent.parent / ".env"
if envp.exists():
    for line in envp.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient
from googleads import ad_manager

LI_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 7384069597
V = "v202605"

gc = GAMClient()
client = gc._get_soap_client()


def stmt(where, limit=100):
    return (ad_manager.StatementBuilder(version=V)
            .Where(where).Limit(limit).ToStatement())


def dt(v):
    d = getattr(v, "date", None)
    if d is None:
        return None
    return f"{d.year}-{d.month:02d}-{d.day:02d}"


li_svc = client.GetService("LineItemService", version=V)
li = li_svc.getLineItemsByStatement(stmt(f"id = {LI_ID}", 1)).results[0]
print("=== LINE ITEM ===")
print("name:", li.name)
print("id:", li.id, " orderId:", li.orderId, " status:", li.status)
print("type/priority:", li.lineItemType, "/", li.priority)
print("flight:", dt(getattr(li, "startDateTime", None)), "->",
      dt(getattr(li, "endDateTime", None)))
st = getattr(li, "stats", None)
print("delivered: impressions=", getattr(st, "impressionsDelivered", None),
      " clicks=", getattr(st, "clicksDelivered", None))

o_svc = client.GetService("OrderService", version=V)
order = o_svc.getOrdersByStatement(stmt(f"id = {li.orderId}", 1)).results[0]
print("\n=== ORDER ===")
print("name:", order.name)
adv = None
comp = client.GetService("CompanyService", version=V).getCompaniesByStatement(
    stmt(f"id = {order.advertiserId}", 1)).results
if comp:
    adv = comp[0].name
print("advertiser:", adv, f"(id {order.advertiserId})")

lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
licas = (lica_svc.getLineItemCreativeAssociationsByStatement(
    stmt(f"lineItemId = {LI_ID}", 50)).results or [])
cids = [x.creativeId for x in licas]
lica_status = {x.creativeId: x.status for x in licas}
print(f"\n=== CREATIVES ({len(cids)}) ===")
if cids:
    c_svc = client.GetService("CreativeService", version=V)
    creatives = (c_svc.getCreativesByStatement(
        stmt(f"id IN ({', '.join(str(int(i)) for i in cids)})", 50)).results
        or [])
    for c in creatives:
        sz = getattr(c, "size", None)
        print(f"\n- id={c.id} [{type(c).__name__}] lica={lica_status.get(c.id)}")
        print(f"  name: {c.name!r}")
        print(f"  size: {getattr(sz, 'width', None)}x{getattr(sz, 'height', None)}",
              " isSafeFrame:", getattr(c, "isSafeFrameCompatible", None))
        dest = getattr(c, "destinationUrl", None)
        if dest:
            print(f"  destinationUrl: {dest}")
        for field in ("snippet", "htmlSnippet", "expandedSnippet", "codeSnippet"):
            snip = getattr(c, field, None)
            if snip:
                flat = " ".join(str(snip).split())
                print(f"  {field} ({len(str(snip))} chars): {flat[:400]}")
                print(f"  has nw-click-audience block:",
                      "nw-click-audience" in str(snip))
                break

print("\n=== FIRST-PARTY AUDIENCE SEGMENTS ===")
try:
    as_svc = client.GetService("AudienceSegmentService", version=V)
    try:
        res = as_svc.getAudienceSegmentsByStatement(
            stmt("type = 'FIRST_PARTY'", 100))
    except Exception:
        res = as_svc.getAudienceSegmentsByStatement(
            ad_manager.StatementBuilder(version=V).Limit(100).ToStatement())
    total = getattr(res, "totalResultSetSize", 0)
    print("segments visible to SA:", total)
    for s in (getattr(res, "results", None) or []):
        print(f"  id={s.id} [{type(s).__name__}] status={getattr(s, 'status', None)}"
              f" size={getattr(s, 'size', None)} name={s.name!r}")
except Exception as e:
    print("AudienceSegmentService probe FAILED:", repr(e)[:300])

print("\n=== SEGMENT RULE DETAIL (pixel + traffic examples) ===")
# 9265049836/9265053919 = Subscription pixel-style segments; 9443596281 = the
# [nw] FITO rule-based segment. Their rules are the template to replicate for
# the clicker segment (inventoryRule -> DFPAudiencePixel + dc_seg custom
# criteria, if that's how the UI wired them).
try:
    as_svc = client.GetService("AudienceSegmentService", version=V)
    ct_svc = client.GetService("CustomTargetingService", version=V)

    def resolve_kv(crit):
        key_id = getattr(crit, "keyId", None)
        val_ids = list(getattr(crit, "valueIds", None) or [])
        kname = vnames = None
        try:
            kres = ct_svc.getCustomTargetingKeysByStatement(
                stmt(f"id = {key_id}", 1)).results
            kname = kres[0].name if kres else None
            if val_ids:
                vres = ct_svc.getCustomTargetingValuesByStatement(
                    ad_manager.StatementBuilder(version=V)
                    .Where(f"customTargetingKeyId = {key_id} AND id IN "
                           f"({', '.join(str(int(i)) for i in val_ids)})")
                    .Limit(50).ToStatement()).results or []
                vnames = [v.name for v in vres]
        except Exception as e:
            kname = f"<resolve failed: {e!r}"[:80]
        return key_id, kname, val_ids, vnames

    def dump_criteria_node(node, indent="    "):
        tname = type(node).__name__
        if hasattr(node, "children") and getattr(node, "children", None):
            print(f"{indent}{tname} logicalOperator={getattr(node, 'logicalOperator', None)}")
            for ch in node.children:
                dump_criteria_node(ch, indent + "  ")
        elif tname == "AudienceSegmentCriteria" or hasattr(node, "audienceSegmentIds"):
            print(f"{indent}AudienceSegmentCriteria op={getattr(node, 'operator', None)}"
                  f" segIds={list(getattr(node, 'audienceSegmentIds', None) or [])}")
        elif hasattr(node, "keyId"):
            key_id, kname, val_ids, vnames = resolve_kv(node)
            print(f"{indent}CustomCriteria op={getattr(node, 'operator', None)}"
                  f" keyId={key_id} key={kname!r} valueIds={val_ids} values={vnames}")
        else:
            print(f"{indent}{tname}: {node}")

    for sid in (9265049836, 9265053919, 9443596281):
        res = as_svc.getAudienceSegmentsByStatement(stmt(f"id = {sid}", 1))
        segs = getattr(res, "results", None) or []
        if not segs:
            print(f"  segment {sid}: not found")
            continue
        s = segs[0]
        print(f"\n  -- {s.id} {s.name!r} [{type(s).__name__}]")
        print(f"     pageViews={getattr(s, 'pageViews', None)}"
              f" recencyDays={getattr(s, 'recencyDays', None)}"
              f" membershipExpirationDays={getattr(s, 'membershipExpirationDays', None)}")
        rule = getattr(s, "rule", None)
        if rule is None:
            print("     rule: None")
            continue
        inv_rule = getattr(rule, "inventoryRule", None)
        for au in (getattr(inv_rule, "targetedAdUnits", None) or []):
            print(f"     inventoryRule.targetedAdUnit: {getattr(au, 'adUnitId', None)}"
                  f" includeDescendants={getattr(au, 'includeDescendants', None)}")
        for pl in (getattr(inv_rule, "targetedPlacementIds", None) or []):
            print(f"     inventoryRule.targetedPlacementId: {pl}")
        cc = getattr(rule, "customCriteriaRule", None)
        if cc is not None:
            print("     customCriteriaRule:")
            dump_criteria_node(cc)
        else:
            print("     customCriteriaRule: None")
except Exception as e:
    print("rule detail probe FAILED:", repr(e)[:300])

print("\n=== DFPAudiencePixel AD UNIT ===")
try:
    inv = client.GetService("InventoryService", version=V)
    res = inv.getAdUnitsByStatement(stmt("adUnitCode = 'DFPAudiencePixel'", 5))
    units = getattr(res, "results", None) or []
    for u in units:
        print(f"  id={u.id} name={u.name!r} status={getattr(u, 'status', None)}")
    if not units:
        print("  none — the UI's first pixel-segment 'Generate tag' creates it")
except Exception as e:
    print("InventoryService probe FAILED:", repr(e)[:300])

# ---------------------------------------------------------------------------
# PIXEL HIT CHECK (added 2026-08-05): are activity-tag fires reaching GAM?
# Requests on the DFPAudiencePixel unit by date/hour (network tz = ET;
# capture went live 8/4 ~18:07 ET) decouple "pixels arriving" from the
# 30min-48h membership-processing + size-display lag. LI clicks by hour
# double as proof clicking was unaffected by the creative edit.
# ---------------------------------------------------------------------------
from datetime import date, timedelta
import gam_client as gcmod

PIXEL_UNIT = "23277289521"
DCSEG_VALUE_ID = "453691490776"
START, END = date(2026, 8, 3), date.today()

print("\n=== PIXEL HIT CHECK ===")
try:
    dims = {d.name for d in gcmod._D}
    mets = {m.name for m in gcmod._M}
    print("request-ish metrics available:",
          sorted(m for m in mets if "REQUEST" in m)[:12])
    print("custom-targeting dimensions available:",
          sorted(d for d in dims if "CUSTOM" in d or "KEY_VALUE" in d)[:12])

    req_metric = next((m for m in (
        "AD_REQUESTS", "TOTAL_AD_REQUESTS", "UNMATCHED_AD_REQUESTS",
        "RESPONSES_SERVED", "UNFILLED_IMPRESSIONS") if m in mets), None)
    print("using requests metric:", req_metric)

    if req_metric:
        df = gc._run_report(["DATE", "HOUR", "AD_UNIT_ID"], [req_metric],
                            START, END)
        col = req_metric.lower()
        hit = df[df["ad_unit_id"].astype(str) == PIXEL_UNIT]
        tot = hit[col].astype(float).sum() if len(hit) else 0
        print(f"\nDFPAudiencePixel unit {req_metric} {START}->{END}: "
              f"total={tot:.0f} across {len(hit)} date/hour rows")
        for _, r in hit.sort_values(["date", "hour"]).iterrows():
            print(f"  {r['date']} {int(r['hour']):02d}:00 ET  {col}={r[col]}")
        if not len(hit):
            print("  (no rows for the pixel unit — check if ANY unit id "
                  "matched; sample units in report:",
                  df["ad_unit_id"].astype(str).unique()[:5], ")")

    # dc_seg value split, if a value-level dimension exists
    kv_dim = next((d for d in (
        "CUSTOM_TARGETING_VALUE_ID", "KEY_VALUES_ID",
        "CUSTOM_TARGETING_VALUE") if d in dims), None)
    print("\nusing KV dimension:", kv_dim)
    if kv_dim and req_metric:
        try:
            kdf = gc._run_report(["DATE", kv_dim], [req_metric], START, END)
            kcol = kv_dim.lower()
            ours = kdf[kdf[kcol].astype(str) == DCSEG_VALUE_ID]
            print(f"rows for dc_seg value {DCSEG_VALUE_ID} (segment "
                  f"9443004817): {len(ours)}")
            for _, r in ours.sort_values("date").iterrows():
                print(f"  {r['date']}  {req_metric.lower()}={r[req_metric.lower()]}")
            if not len(ours):
                print("  (value not present in report — either no fires or "
                      "dimension reports differently)")
        except Exception as e:
            print("KV-split report failed:", repr(e)[:200])

    # LI clicks by hour around the rollout (proves clicking unaffected)
    cdf = gc._run_report(["DATE", "HOUR", "LINE_ITEM_ID"],
                         ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS"],
                         START, END)
    li = cdf[cdf["line_item_id"].astype(str) == str(LI_ID)]
    print(f"\nLI {LI_ID} delivery by hour (ET; capture live 8/4 18:07 ET):")
    for _, r in li.sort_values(["date", "hour"]).iterrows():
        print(f"  {r['date']} {int(r['hour']):02d}:00  "
              f"imps={r['ad_server_impressions']}  "
              f"clicks={r['ad_server_clicks']}")
except Exception as e:
    print("PIXEL HIT CHECK failed:", repr(e)[:400])
