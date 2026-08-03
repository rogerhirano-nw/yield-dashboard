"""One-off: dump line items + creatives for GAM order 4144745465 via SOAP.

Runs in GitHub Actions (see pull_fito_order.yml) where
GAM_SERVICE_ACCOUNT_JSON / GAM_NETWORK_ID secrets are available.
Prints line item settings relevant to the FITO takeover build:
type/priority, environment, companion delivery, roadblocking, sizes,
custom targeting, plus each creative's type and companion IDs.
"""
import json
import os
import tempfile

from googleads import ad_manager, oauth2

VERSION = "v202605"
ORDER_ID = 4144745465

key_data = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump(key_data, f)
    key_file = f.name
oauth2_client = oauth2.GoogleServiceAccountClient(
    key_file, "https://www.googleapis.com/auth/dfp"
)
client = ad_manager.AdManagerClient(
    oauth2_client, "FitoOrderPull/1.0", network_code=os.environ["GAM_NETWORK_ID"]
)

li_svc = client.GetService("LineItemService", version=VERSION)
sb = ad_manager.StatementBuilder(version=VERSION)
sb.Where("orderId = :oid").WithBindVariable("oid", ORDER_ID).Limit(200)
resp = li_svc.getLineItemsByStatement(sb.ToStatement())
items = getattr(resp, "results", None) or []
print(f"=== ORDER {ORDER_ID}: {len(items)} line item(s) ===")

li_ids = []
for li in items:
    li_ids.append(li.id)
    sizes = []
    for ph in getattr(li, "creativePlaceholders", None) or []:
        s = getattr(ph, "size", None)
        if s is not None:
            sizes.append(f"{s.width}x{s.height}")
    tgt = getattr(li, "targeting", None)
    print(json.dumps({
        "id": li.id,
        "name": li.name,
        "type": str(getattr(li, "lineItemType", "")),
        "priority": getattr(li, "priority", None),
        "status": str(getattr(li, "status", "")),
        "env": str(getattr(li, "environmentType", "")),
        "companion_delivery": str(getattr(li, "companionDeliveryOption", "")),
        "roadblocking": str(getattr(li, "roadblockingType", "")),
        "cost_type": str(getattr(li, "costType", "")),
        "goal": str(getattr(li, "primaryGoal", ""))[:160],
        "sizes": sizes,
        "custom_targeting": str(getattr(tgt, "customTargeting", None))[:400] if tgt else None,
    }, default=str))

if li_ids:
    lica_svc = client.GetService(
        "LineItemCreativeAssociationService", version=VERSION
    )
    ids_str = ", ".join(str(i) for i in li_ids)
    sb2 = ad_manager.StatementBuilder(version=VERSION)
    sb2.Where(f"lineItemId IN ({ids_str})").Limit(500)
    resp2 = lica_svc.getLineItemCreativeAssociationsByStatement(sb2.ToStatement())
    licas = getattr(resp2, "results", None) or []
    cids = sorted({lica.creativeId for lica in licas})
    print(f"\n=== {len(licas)} creative association(s) ===")
    for lica in licas:
        print(f"LI {lica.lineItemId} -> creative {lica.creativeId}")

    if cids:
        cr_svc = client.GetService("CreativeService", version=VERSION)
        sb3 = ad_manager.StatementBuilder(version=VERSION)
        sb3.Where(f"id IN ({', '.join(str(c) for c in cids)})").Limit(500)
        resp3 = cr_svc.getCreativesByStatement(sb3.ToStatement())
        print("\n=== CREATIVES ===")
        for c in getattr(resp3, "results", None) or []:
            s = getattr(c, "size", None)
            comps = getattr(c, "companionCreativeIds", None)
            snippet = (getattr(c, "snippet", None) or getattr(c, "htmlSnippet", None) or "")
            print(json.dumps({
                "id": c.id,
                "name": getattr(c, "name", None),
                "type": type(c).__name__,
                "size": f"{s.width}x{s.height}" if s is not None else None,
                "companion_creative_ids": list(comps) if comps else None,
                "vast_url": getattr(c, "vastXmlUrl", None),
                "snippet_head": snippet[:200] if snippet else None,
            }, default=str))
