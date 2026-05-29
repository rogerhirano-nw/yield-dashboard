"""Add audience-segment targeting to test LIs via updateLineItems.

Uses the verified schema:
  customTargeting = CustomCriteriaSet
    children = [AudienceSegmentCriteria(operator=IS, audienceSegmentIds=[<id>])]

Default: dry-run (just prints intent). --apply does the write. --all to do
all 3 test LIs at once; otherwise targets just LI 7319885244 (Basketball)
as the single-LI smoke test.
"""
import os, json, sys, warnings, tempfile, datetime
warnings.filterwarnings("ignore")

DRY_RUN = "--apply" not in sys.argv
DO_ALL  = "--all"   in sys.argv

PICKS_BY_LI = {
    "7319885244": (9385007833, "Aud-Basketball"),
    "7322268934": (9168610732, "Aud-SBEnthusiast"),
    "7319884497": (9333427967, "Aud-OnlineCasino"),
}

# Default: just Basketball
TARGETS = PICKS_BY_LI if DO_ALL else {"7319885244": PICKS_BY_LI["7319885244"]}

with open("/Users/roger/code/yield-dashboard/.env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1); v = v.strip()
        if v.startswith('"') and v.endswith('"'): v = v[1:-1]
        os.environ.setdefault(k.strip(), v)

from googleads import ad_manager, oauth2
sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump(sa, f); kf = f.name
oc = oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp")
client = ad_manager.AdManagerClient(oc, "NewsweekDashboard/1.0",
                                    network_code=os.environ["GAM_NETWORK_ID"])
V = "v202605"
li_svc = client.GetService("LineItemService", version=V)

def build_audience_custom_targeting(segment_id: int) -> dict:
    """Construct the customTargeting dict with explicit xsi_type hints on
    every polymorphic node so the googleads SOAP serializer can dispatch.

    Shape (verified from WSDL introspection):
        CustomCriteriaSet { logicalOperator, children[] }
          -> AudienceSegmentCriteria { operator, audienceSegmentIds[] }
    """
    return {
        "xsi_type": "CustomCriteriaSet",
        "logicalOperator": "AND",
        "children": [
            {
                "xsi_type": "AudienceSegmentCriteria",
                "operator": "IS",
                "audienceSegmentIds": [segment_id],
            }
        ],
    }

print("=" * 70)
print(f"ADD AUDIENCE TARGETING  ({'DRY RUN' if DRY_RUN else 'APPLY'})")
print("=" * 70)
print()

log_entries = []
for li_id_s, (segment_id, handle) in TARGETS.items():
    li_id = int(li_id_s)
    print(f"LI {li_id_s} ({handle}) → segment {segment_id}")

    sb = ad_manager.StatementBuilder(version=V); sb.Where(f"id = {li_id}")
    li = li_svc.getLineItemsByStatement(sb.ToStatement()).results[0]

    # Check current customTargeting state
    existing_ct = li.targeting.get("customTargeting") if hasattr(li.targeting, "get") else getattr(li.targeting, "customTargeting", None)
    print(f"  current status: {li.status}  current customTargeting: {existing_ct}")

    if DRY_RUN:
        print(f"  would set customTargeting = AudienceSegmentCriteria(IS, [{segment_id}])\n")
        continue

    # Mutate the targeting
    li.targeting.customTargeting = build_audience_custom_targeting(segment_id)
    # Bypass forecast check — segment may not have enough projected inventory
    # for full 215K goal. LI will deliver what's available; better than not
    # delivering at all.
    li.allowOverbook = True

    try:
        updated = li_svc.updateLineItems([li])
        # Verify what came back
        new_ct = updated[0].targeting.customTargeting
        print(f"  ✓ updated. new customTargeting: {new_ct}")
        log_entries.append({"li_id": li_id_s, "segment_id": segment_id, "ok": True})
    except Exception as e:
        print(f"  ✗ FAILED: {type(e).__name__}: {str(e)[:300]}")
        log_entries.append({"li_id": li_id_s, "segment_id": segment_id, "ok": False, "error": str(e)[:500]})
    print()

if not DRY_RUN:
    log = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "results": log_entries,
    }
    with open("/tmp/audience_patch_log.json", "w") as f:
        json.dump(log, f, indent=2)
    print(f"Logged: /tmp/audience_patch_log.json")
