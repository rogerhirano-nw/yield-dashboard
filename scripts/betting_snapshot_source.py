"""Snapshot the control LI and its 4 source creatives to JSON for the
test-LI batch script to clone."""
import os, json, warnings, tempfile
warnings.filterwarnings("ignore")

with open("/Users/roger/code/yield-dashboard/.env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1); v = v.strip()
        if v.startswith('"') and v.endswith('"'): v = v[1:-1]
        os.environ.setdefault(k.strip(), v)

from googleads import ad_manager, oauth2
from zeep.helpers import serialize_object

sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump(sa, f); kf = f.name
oc = oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp")
client = ad_manager.AdManagerClient(oc, "NewsweekDashboard/1.0",
                                    network_code=os.environ["GAM_NETWORK_ID"])
V = "v202605"

li_svc = client.GetService("LineItemService", version=V)
sb = ad_manager.StatementBuilder(version=V); sb.Where("id = 7306352098")
li = li_svc.getLineItemsByStatement(sb.ToStatement()).results[0]

# Exclude the macro-test creative from the source set
MACRO_TEST_CID = 138559273952
lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
sb2 = ad_manager.StatementBuilder(version=V)
sb2.Where("lineItemId = 7306352098 AND status = 'ACTIVE'")
licas = lica_svc.getLineItemCreativeAssociationsByStatement(sb2.ToStatement()).results or []
cids = [l.creativeId for l in licas if l.creativeId != MACRO_TEST_CID]

cr_svc = client.GetService("CreativeService", version=V)
sb3 = ad_manager.StatementBuilder(version=V)
sb3.Where(f"id IN ({','.join(str(c) for c in cids)})")
crs = cr_svc.getCreativesByStatement(sb3.ToStatement()).results

def safe(o):
    from datetime import date, datetime as dt
    if isinstance(o, (date, dt)): return o.isoformat()
    return str(o)

out = {
    "li": serialize_object(li, dict),
    "creatives": [serialize_object(c, dict) for c in crs],
    "macro_test_creative_to_deactivate": MACRO_TEST_CID,
}
with open("/tmp/li_source_snapshot.json", "w") as f:
    json.dump(out, f, default=safe, indent=2)

print(f"Snapshot written: /tmp/li_source_snapshot.json")
print(f"  LI fields: {len(out['li'])}")
print(f"  Source creatives: {len(out['creatives'])}")
for c in out["creatives"]:
    sz = c.get("size") or {}
    a = (c.get("primaryImageAsset") or {})
    print(f"    id={c['id']:>14}  {sz.get('width'):>4}x{sz.get('height'):<4}  "
          f"asset={a.get('assetId')}  name={c.get('name')}")
geo = (out["li"].get("targeting") or {}).get("geoTargeting") or {}
ct = (out["li"].get("targeting") or {}).get("customTargeting") or {}
print(f"  geo: {len(geo.get('targetedLocations',[]))} included, "
      f"{len(geo.get('excludedLocations',[]))} excluded")
print(f"  customTargeting root: {ct.get('logicalOperator')} with "
      f"{len(ct.get('children',[]))} children")
