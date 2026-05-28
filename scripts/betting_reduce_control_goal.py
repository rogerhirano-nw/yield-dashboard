"""Reduce the control LI (7306352098) lifetime impression goal from
1,875,000 to 1,230,000.

The update needs to send back the LI's full customTargeting, which has 4
dead audience-segment IDs that cause CommonError.NOT_FOUND. We rebuild
the tree with proper xsi_type hints and filter out the dead IDs from the
single AudienceSegmentCriteria node that holds them.

What changes:
  1. primaryGoal.units: 1,875,000 -> 1,230,000
  2. customTargeting: dead audience IDs removed (10 of 14 retained):
       REMOVE: 8636918122, 8637757077, 8731515695, 8731516835
       KEEP:   the other 10 (live segments)

What stays the same:
  - All 4 CustomCriteria content-category filters (3 IS_NOT + 1 IS)
  - All other LI fields (priority, cost, dates, creatives, etc.)
  - The logical structure (OR -> AND)

Default: dry-run. --apply to execute.
"""
import os, json, sys, warnings, tempfile, datetime
warnings.filterwarnings("ignore")

DRY_RUN = "--apply" not in sys.argv
LI_ID = 7306352098
NEW_GOAL = 1_230_000
DEAD_IDS = {8636918122, 8637757077, 8731515695, 8731516835}

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
svc = client.GetService("LineItemService", version=V)

# Fetch the LI
sb = ad_manager.StatementBuilder(version=V); sb.Where(f"id = {LI_ID}")
li = svc.getLineItemsByStatement(sb.ToStatement()).results[0]
print(f"LI {li.id}  current goal_units={li.primaryGoal.units:,}  status={li.status}")

# Rebuild customTargeting with xsi_type hints + dead-ID filter
def rebuild(node):
    """Walk the existing customTargeting tree (zeep sudsobject) and produce
    a dict with explicit xsi_type hints. Filter dead audience segment IDs
    along the way."""
    # AudienceSegmentCriteria node — has audienceSegmentIds attr
    asids = getattr(node, "audienceSegmentIds", None)
    if asids is not None:
        live = [int(i) for i in asids if int(i) not in DEAD_IDS]
        removed = [int(i) for i in asids if int(i) in DEAD_IDS]
        if removed:
            print(f"  removing dead audience IDs from AudienceSegmentCriteria: {removed}")
        return {
            "xsi_type": "AudienceSegmentCriteria",
            "operator": str(node.operator),
            "audienceSegmentIds": live,
        }
    # CustomCriteria node — has keyId/valueIds/operator
    if getattr(node, "keyId", None) is not None:
        return {
            "xsi_type": "CustomCriteria",
            "keyId": int(node.keyId),
            "valueIds": [int(v) for v in node.valueIds],
            "operator": str(node.operator),
        }
    # CustomCriteriaSet node — has logicalOperator/children
    if getattr(node, "logicalOperator", None) is not None:
        return {
            "xsi_type": "CustomCriteriaSet",
            "logicalOperator": str(node.logicalOperator),
            "children": [rebuild(c) for c in node.children],
        }
    raise ValueError(f"Unknown customTargeting node shape: {node}")

new_ct = rebuild(li.targeting.customTargeting)
print(f"\nNew customTargeting (sanitized):")
print(json.dumps(new_ct, indent=2))

if DRY_RUN:
    print(f"\nDRY RUN — would set goal_units to {NEW_GOAL:,} and replace customTargeting as above.")
    sys.exit(0)

# Mutate the LI
li.primaryGoal.units = NEW_GOAL
li.targeting.customTargeting = new_ct

try:
    updated = svc.updateLineItems([li])
    print(f"\n✓ updated. new goal_units = {updated[0].primaryGoal.units:,}")
    log = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "li_id": str(LI_ID),
        "old_goal_units": 1875000,
        "new_goal_units": NEW_GOAL,
        "removed_dead_audience_ids": sorted(DEAD_IDS),
    }
    with open("/tmp/control_goal_reduction_log.json", "w") as f:
        json.dump(log, f, indent=2)
    print(f"Logged: /tmp/control_goal_reduction_log.json")
except Exception as e:
    print(f"✗ FAILED: {type(e).__name__}: {str(e)[:500]}")
    sys.exit(1)
