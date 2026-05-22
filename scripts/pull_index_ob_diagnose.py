"""
Diagnostic: determine whether the Index Exchange yield groups at Newsweek
are Open Bidding (EXCHANGE_BIDDING) or Mediation.

The GAM REST reporting API rejects HEADER_BIDDER_INTEGRATION_TYPE_NAME
alongside every YIELD_GROUP_* metric we tried, so we fall back to the
legacy SOAP YieldGroupService (already used in gam_client.py for
creatives/LICA) and inspect the `type` field on each yield group
directly.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from gam_client import GAMClient  # noqa: E402

client = GAMClient()
soap = client._get_soap_client()
from googleads import ad_manager  # noqa: E402

svc = soap.GetService("YieldGroupService", version=client._SOAP_API_VERSION)
sb = ad_manager.StatementBuilder(version=client._SOAP_API_VERSION)
sb.Limit(500)

groups = []
while True:
    resp = svc.getYieldGroupsByStatement(sb.ToStatement())
    results = getattr(resp, "results", None) or []
    if not results:
        break
    for yg in results:
        groups.append({
            "id":          getattr(yg, "id", None),
            "name":        getattr(yg, "name", None),
            "type":        getattr(yg, "type", None),     # EXCHANGE_BIDDING | MEDIATION
            "status":      getattr(yg, "status", None),
            "ad_format":   getattr(yg, "adFormat", None),
        })
    sb.offset += sb.limit
    if sb.offset >= getattr(resp, "totalResultSetSize", 0):
        break

print(f"Total yield groups: {len(groups)}\n")
print("By type:")
by_type = {}
for g in groups:
    by_type[g["type"]] = by_type.get(g["type"], 0) + 1
for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]):
    print(f"  {k}: {v}")

print("\nAll yield groups:")
print(f"{'id':<14} {'type':<18} {'status':<10} {'ad_format':<12} name")
print("-" * 80)
for g in sorted(groups, key=lambda x: (str(x["type"]), str(x["name"]))):
    print(f"{str(g['id']):<14} {str(g['type']):<18} {str(g['status']):<10} {str(g['ad_format']):<12} {g['name']}")

# Now drill into the yield-group partner assignments to find which groups
# include Index Exchange as a partner. SOAP YieldGroup objects carry a
# `yieldPartners` list, but it may not be populated on the list response —
# fetch each group individually if needed.
print("\nYield groups containing Index Exchange as a partner:")
hits = 0
for g in groups:
    yg_id = g["id"]
    try:
        sb2 = ad_manager.StatementBuilder(version=client._SOAP_API_VERSION)
        sb2.Where("id = :id").WithBindVariable("id", yg_id).Limit(1)
        full = svc.getYieldGroupsByStatement(sb2.ToStatement())
        items = getattr(full, "results", None) or []
        if not items:
            continue
        yg = items[0]
        partners = getattr(yg, "yieldPartners", None) or []
        for p in partners:
            tag = getattr(p, "thirdPartyCompanyId", None) or getattr(p, "yieldPartnerName", None) or str(p)
            name = str(tag)
            # The "Index Exchange" company shows up either by company id
            # or by display name within the yieldPartner record.
            if "index" in name.lower():
                hits += 1
                print(f"  yield_group={g['name']!r} type={g['type']} partner_tag={name}")
    except Exception as e:
        print(f"  (failed to inspect yield group {yg_id}: {e})")

if hits == 0:
    print("  (no partner records mentioned 'index' by name; partner lookup may need a")
    print("   separate CompanyService call to resolve thirdPartyCompanyId → company name)")
