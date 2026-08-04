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
