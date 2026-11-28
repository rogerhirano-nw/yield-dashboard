"""List every GAM custom targeting key (ACTIVE and INACTIVE), read-only.

Used to reconcile a key-value cleanup register against what actually exists
in the network. Prints one tab-separated row per key — id, name, display
name, type, status, reportable type, value count — and writes the same data
as JSON to --out.

    python scripts/list_custom_targeting_keys.py --out /tmp/kv_keys.json
"""

import argparse
import json
import os
import sys
import tempfile

API_VERSION = "v202605"
PAGE = 500


def get_soap_client():
    sa_json = os.environ.get("GAM_SERVICE_ACCOUNT_JSON")
    network_id = os.environ.get("GAM_NETWORK_ID")
    if not sa_json:
        sys.exit("GAM_SERVICE_ACCOUNT_JSON env var not set")
    if not network_id:
        sys.exit("GAM_NETWORK_ID env var not set")
    from googleads import ad_manager, oauth2  # type: ignore

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(json.loads(sa_json), f)
        key_file = f.name
    oauth2_client = oauth2.GoogleServiceAccountClient(
        key_file, "https://www.googleapis.com/auth/dfp"
    )
    client = ad_manager.AdManagerClient(
        oauth2_client, "NewsweekDashboard/1.0", network_code=network_id
    )
    return client, ad_manager


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="kv_keys.json")
    args = ap.parse_args()

    client, ad_manager = get_soap_client()
    svc = client.GetService("CustomTargetingService", version=API_VERSION)

    keys = []
    offset = 0
    while True:
        sb = ad_manager.StatementBuilder(version=API_VERSION)
        sb.OrderBy("id", ascending=True)
        sb.Limit(PAGE)
        sb.Offset(offset)
        resp = svc.getCustomTargetingKeysByStatement(sb.ToStatement())
        results = getattr(resp, "results", None) or []
        for k in results:
            keys.append({
                "id": int(k.id),
                "name": k.name,
                "displayName": getattr(k, "displayName", None),
                "type": getattr(k, "type", None),
                "status": getattr(k, "status", None),
                "reportableType": getattr(k, "reportableType", None),
            })
        if len(results) < PAGE:
            break
        offset += PAGE

    # Value count per key (all statuses) — one cheap limit-1 query each.
    for k in keys:
        sb = ad_manager.StatementBuilder(version=API_VERSION)
        sb.Where("customTargetingKeyId = :kid")
        sb.WithBindVariable("kid", k["id"])
        sb.Limit(1)
        try:
            resp = svc.getCustomTargetingValuesByStatement(sb.ToStatement())
            k["values"] = int(getattr(resp, "totalResultSetSize", 0) or 0)
        except Exception as e:  # noqa: BLE001 — a count is best-effort
            k["values"] = None
            print(f"WARN value count failed for {k['name']}: {e}", file=sys.stderr)

    print("id\tname\tdisplayName\ttype\tstatus\treportableType\tvalues")
    for k in keys:
        print("\t".join(str(k[c]) for c in
                        ("id", "name", "displayName", "type", "status",
                         "reportableType", "values")))
    print(f"TOTAL {len(keys)} keys", file=sys.stderr)
    with open(args.out, "w") as f:
        json.dump(keys, f, indent=1)


if __name__ == "__main__":
    main()
