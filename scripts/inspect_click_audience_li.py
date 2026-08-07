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
pg = getattr(li, "primaryGoal", None)
print("goal:", getattr(pg, "units", None), getattr(pg, "unitType", None),
      f"({getattr(pg, 'goalType', None)})",
      " deliveryRate:", getattr(li, "deliveryRateType", None))

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

    for sid in (9443004817, 9265049836, 9265053919, 9443596281):
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

    print("more metric names:", sorted(
        m for m in mets if any(k in m for k in
        ("UNFILLED", "UNMATCHED", "SERVED", "CODE_SERVED")))[:10])
except Exception as e:
    print("enum introspection failed:", repr(e)[:200])

# LI delivery by hour in the v3w era (proves clicking still healthy; same
# dims/metrics refresh_gam_hourly runs daily in prod, known-fast).
try:
    cdf = gc._run_report(["DATE", "HOUR", "LINE_ITEM_ID"],
                         ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS"],
                         date(2026, 8, 6), END)
    li = cdf[cdf["line_item_id"].astype(str) == str(LI_ID)]
    print(f"\nLI {LI_ID} delivery by hour (ET; v3w wrapper live 8/6 18:07 ET):")
    for _, r in li.sort_values(["date", "hour"]).iterrows():
        print(f"  {r['date']} {int(r['hour']):02d}:00  "
              f"imps={r['ad_server_impressions']}  "
              f"clicks={r['ad_server_clicks']}")
except Exception as e:
    print("LI hourly report failed:", repr(e)[:300])

# Pixel-unit requests: narrowest possible shape — one dimension, SINGLE-day.
# (DATE+HOUR+AD_UNIT_ID 400'd; DATE+AD_UNIT_ID over 3 days hung past the job
# timeout.) Per-day since 8/5: 8/5 = pre-fix control (known 0), 8/6 = v3w
# cutover day (wrapper updated 22:07Z + canary fires ~22:00Z), 8/7+ = full
# correct-form days.
day_reqs = {}
for d in [date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]:
    if d > END:
        continue
    try:
        df = gc._run_report(["AD_UNIT_ID"], ["AD_REQUESTS"], d, d)
        hit = df[df["ad_unit_id"].astype(str) == PIXEL_UNIT]
        tot = hit["ad_requests"].astype(float).sum() if len(hit) else 0
        day_reqs[str(d)] = tot
        print(f"\nDFPAudiencePixel AD_REQUESTS {d}: {tot:.0f}")
    except Exception as e:
        print(f"pixel-unit report {d} failed:", repr(e)[:200])

# Interstitial UNIT demand-vs-supply: did the slot stop being requested
# (site-side) or did requests hold while LI 7384069597 stopped winning
# (buyer/demand-side)? Same single-dimension single-day shape as the pixel
# unit checks. 8/5 = healthy control; collapse started ~8/6 11:00 ET.
INTERSTITIAL_UNIT = "23295929518"
for d in [date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]:
    if d > END:
        continue
    try:
        df = gc._run_report(["AD_UNIT_ID"],
                            ["AD_REQUESTS", "AD_SERVER_IMPRESSIONS",
                             "UNFILLED_IMPRESSIONS"], d, d)
        hit = df[df["ad_unit_id"].astype(str) == INTERSTITIAL_UNIT]
        if len(hit):
            row = hit.iloc[0]
            print(f"interstitial unit {d}: requests={row['ad_requests']} "
                  f"served={row['ad_server_impressions']} "
                  f"unfilled={row['unfilled_impressions']}")
        else:
            print(f"interstitial unit {d}: NO ROW (zero activity)")
    except Exception as e:
        print(f"interstitial unit report {d} failed:", repr(e)[:200])

# Deal-level bid funnel for the Apple deals: GAM->DV360 solicitation
# (deals_bid_requests) vs DV360 responses (deals_bids). Discriminates
# "line stopped matching requests" (requests collapse too) from "buyer
# stopped bidding" (requests hold, bids collapse). Same shape as the
# daily sweep's refresh_gam_deal_bids — known-fast.
try:
    bdf = gc.run_deal_bid_report(date(2026, 8, 4), END)
    # The PG deals don't carry "Apple" in DEAL_NAME — filter to deals that
    # actually BID in the window instead (only delivering deals do).
    live = bdf.groupby("programmatic_deal_name")["deals_bids"].sum()
    live = set(live[live > 0].index)
    sub = bdf[bdf["programmatic_deal_name"].isin(live)]
    print(f"\nDeals with bids>0 in 8/4-{END} ({len(live)} deals):")
    for _, r in sub.sort_values(["programmatic_deal_name", "date"]).iterrows():
        print(f"  {r['date']}  {r['programmatic_deal_name'][:70]}  "
              f"bid_reqs={r['deals_bid_requests']}  bids={r['deals_bids']}  "
              f"wins={r['deals_winning_bids']}")
except Exception as e:
    print("deal bid report failed:", repr(e)[:300])

# If the v3w era still reads 0, AD_REQUESTS itself may not count activity
# hits — probe alternate request-shaped metrics on the cutover day.
post = [v for k, v in day_reqs.items() if k >= "2026-08-06"]
if post and max(post) == 0:
    try:
        alt_mets = {m.name for m in gcmod._M}
    except Exception:
        alt_mets = set()
    for alt in ("TOTAL_CODE_SERVED_COUNT", "UNMATCHED_AD_REQUESTS",
                "TOTAL_IMPRESSIONS", "UNFILLED_IMPRESSIONS"):
        if alt_mets and alt not in alt_mets:
            print(f"alt metric {alt}: not in enum, skipped")
            continue
        try:
            df = gc._run_report(["AD_UNIT_ID"], [alt],
                                date(2026, 8, 6), date(2026, 8, 6))
            hit = df[df["ad_unit_id"].astype(str) == PIXEL_UNIT]
            tot = hit[alt.lower()].astype(float).sum() if len(hit) else 0
            print(f"DFPAudiencePixel {alt} 2026-08-06: {tot:.0f}")
        except Exception as e:
            print(f"alt metric {alt} failed:", repr(e)[:160])
