#!/usr/bin/env python3
"""Debug: what are the creatives CreativeService didn't return? (read-only)

Usage: python scripts/probe_creatives.py CREATIVE_ID ... -- LINE_ITEM_ID ...
"""
import json, os, sys, tempfile
from googleads import ad_manager, oauth2
from zeep.helpers import serialize_object

V = "v202605"
args = sys.argv[1:]
cids = args[:args.index("--")]; lis = args[args.index("--") + 1:]
sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump(sa, f); kf = f.name
c = ad_manager.AdManagerClient(oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp"),
                               "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"])
cr = c.GetService("CreativeService", version=V)
lica = c.GetService("LineItemCreativeAssociationService", version=V)

def q(svc, m, where):
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(500)
    return list(getattr(getattr(svc, m)(sb.ToStatement()), "results", []) or [])

for i in cids:
    try:
        r = q(cr, "getCreativesByStatement", f"id = {i}")
        print(f"creative {i}: {len(r)} result(s)", [type(x).__name__ for x in r])
    except Exception as e:
        print(f"creative {i}: ERROR {e}")
for li in lis:
    print(f"\nLI {li} LICAs:")
    for la in q(lica, "getLineItemCreativeAssociationsByStatement", f"lineItemId = {li}"):
        d = serialize_object(la)
        print("  LICA", {k: d.get(k) for k in ("creativeId", "creativeSetId", "status", "manualCreativeRotationWeight")})
        try:
            for x in q(cr, "getCreativesByStatement", f"id = {la.creativeId}"):
                blob = json.dumps(serialize_object(x), default=str)
                t = x._xsd_type.name if hasattr(x, "_xsd_type") else type(x).__name__
                print(f"    creative {x.id} [{t}] {x.name}")
                print("    pixel in GAM def:", "scorecardresearch" in blob.lower() or "6972086" in blob)
                print("    fields:", json.dumps({k: v for k, v in serialize_object(x).items()
                      if k in ("vastXmlUrl", "vastRedirectType", "trackingUrls", "thirdPartyImpressionTrackingUrls",
                               "snippet", "expandedSnippet", "htmlSnippet", "sslScanResult", "size")}, default=str)[:1500])
        except Exception as e:
            print("    ERROR", e)
