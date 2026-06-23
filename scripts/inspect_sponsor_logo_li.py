#!/usr/bin/env python3
"""Read-only: dump the sponsor-logo LI's serving config (targeting, roadblock,
creative placeholders, type/cost/priority) so we can scope a viewable-tracker.
"""
import os, sys
from pathlib import Path
envp = Path(__file__).resolve().parent.parent / ".env"
for line in envp.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient
from googleads import ad_manager

LI = sys.argv[1] if len(sys.argv) > 1 else "7336465381"
V = "v202605"
gc = GAMClient()
client = gc._get_soap_client()
svc = client.GetService("LineItemService", version=V)
net = client.GetService("NetworkService", version=V)
sb = ad_manager.StatementBuilder(version=V).Where(f"id = {int(LI)}").Limit(1)
li = svc.getLineItemsByStatement(sb.ToStatement()).results[0]

print("name:", li.name)
print("type/cost/priority:", li.lineItemType, getattr(li, "costType", None), li.priority)
print("roadblockingType:", getattr(li, "roadblockingType", None))
print("status:", li.status, " reservationStatus:", getattr(li, "reservationStatus", None))
print("skipInventoryCheck:", getattr(li, "skipInventoryCheck", None),
      " allowOverbook:", getattr(li, "allowOverbook", None))
print("\ncreativePlaceholders:")
for cp in (li.creativePlaceholders or []):
    sz = getattr(cp, "size", None)
    print(f"   size={getattr(sz,'width',None)}x{getattr(sz,'height',None)} "
          f"sizeType={getattr(cp,'creativeSizeType',None)} "
          f"expected={getattr(cp,'expectedCreativeCount',None)}")

# Resolve targeted ad unit ids -> names
inv = getattr(li.targeting, "inventoryTargeting", None)
unit_ids = []
print("\ninventoryTargeting.targetedAdUnits:")
for au in (getattr(inv, "targetedAdUnits", None) or []):
    uid = getattr(au, "adUnitId", None)
    unit_ids.append(uid)
    print(f"   adUnitId={uid} includeDescendants={getattr(au,'includeDescendants',None)}")
if unit_ids:
    iu = client.GetService("InventoryService", version=V)
    sb2 = ad_manager.StatementBuilder(version=V).Where(
        f"id IN ({', '.join(str(int(i)) for i in unit_ids)})").Limit(50)
    for u in iu.getAdUnitsByStatement(sb2.ToStatement()).results:
        print(f"      -> {u.id}  name='{u.name}'  adUnitCode='{getattr(u,'adUnitCode',None)}'")

# custom targeting (article_id / nwdemocr) presence, lightly
ct = getattr(li.targeting, "customTargeting", None)
print("\ncustomTargeting present:", bool(ct))
