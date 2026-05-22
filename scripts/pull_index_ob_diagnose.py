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

resp = svc.getYieldGroupsByStatement(sb.ToStatement())
results = getattr(resp, "results", None) or []
print(f"Returned {len(results)} yield groups, totalResultSetSize={getattr(resp, 'totalResultSetSize', '?')}\n")

# Introspect the first object so we can see what fields the v202605 SOAP
# schema actually uses. zeep `dir()` includes inherited methods; we want
# data attrs. Try serializing to dict via zeep.helpers.
from zeep import helpers as zeep_helpers  # noqa: E402

for i, yg in enumerate(results):
    print(f"=== yield_group[{i}] — raw repr ===")
    print(repr(yg))
    print(f"\n=== yield_group[{i}] — zeep serialize ===")
    try:
        as_dict = zeep_helpers.serialize_object(yg, target_cls=dict)
        # Pretty-print, but truncate long children
        import json
        def _trunc(o, depth=0):
            if isinstance(o, dict):
                return {k: _trunc(v, depth+1) for k, v in o.items()}
            if isinstance(o, list):
                return [_trunc(x, depth+1) for x in o[:5]] + (["…"] if len(o) > 5 else [])
            if isinstance(o, str) and len(o) > 200:
                return o[:200] + "…"
            return o
        print(json.dumps(_trunc(as_dict), indent=2, default=str))
    except Exception as e:
        print(f"  serialize failed: {e}")
    print()
