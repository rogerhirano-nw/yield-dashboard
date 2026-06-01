"""Betting CPA rebalance (IO1109 / order 4068491190), 2026-06-01.

Data-driven pivot after 4 days of test-LI delivery + DV quality pull:
  - Traffic quality is pristine (DV attention 170, presence 199, 98% viewable,
    0.5% IVT) -> the 0.08% FTP rate is a FORMAT-INTENT + advertiser-funnel
    problem, not a media-quality problem.
  - 320x50 = 86% of clicks, 0 FTPs. Every conversion came from 728x90/300x250.
  - Among test segments, OnlineCasino (7319884497) is the only one delivering
    real volume (17.8K imps) with a CTR edge (0.157% vs control 0.094%).
    Basketball + SBEnthusiast are starved (~1.1-1.3K imps in 4 days).

Actions:
  1. PAUSE Basketball (7319885244) + SBEnthusiast (7322268934) — starved, no
     measurable signal possible this flight.
  2. OnlineCasino (7319884497) -> LARGE FORMATS ONLY: deactivate its 320x50
     LICA (creative 138560237489). Best audience x converting formats.
  3. Restore control (7306352098) goal 1,230,000 -> 1,660,000 to recover the
     ~60% total-volume drop the original split caused. (1.66M control + 215K
     OnlineCasino = 1.875M = the $15K / 1.875M contract. Paused LIs = 0.)

Default: dry-run. --apply to execute. Logs to /tmp/rebalance_betting_log.json.
"""
import os, json, sys, warnings, tempfile, datetime
warnings.filterwarnings("ignore")

DRY_RUN = "--apply" not in sys.argv

PAUSE_LIS = [7319885244, 7322268934]            # Basketball, SBEnthusiast
ONLINECASINO_LI = 7319884497
ONLINECASINO_320x50_CID = 138560237489
CONTROL_LI = 7306352098
CONTROL_NEW_GOAL = 1_660_000

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
lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)

log = {"ts": None, "actions": []}
mode = "DRY RUN" if DRY_RUN else "APPLY"
print("=" * 70)
print(f"BETTING REBALANCE  ({mode})")
print("=" * 70)

# ---- current state ----
sb = ad_manager.StatementBuilder(version=V)
sb.Where(f"id IN ({CONTROL_LI},{ONLINECASINO_LI},{PAUSE_LIS[0]},{PAUSE_LIS[1]})")
cur = {li.id: li for li in li_svc.getLineItemsByStatement(sb.ToStatement()).results}
print("\nCurrent state:")
for lid, li in cur.items():
    print(f"  {lid}  {li.status:<10}  goal={li.primaryGoal.units:,}")

# ---- 1. Halt starved test LIs via LICA deactivation ----
# PauseLineItems is NOT_ALLOWED on reserved LIs via API on this network, so we
# deactivate every active LICA on the two LIs instead — no active creative =>
# no delivery. Same proven path used for the macro-test creative cleanup.
print(f"\n[1] Halt {PAUSE_LIS} (Basketball, SBEnthusiast) via LICA deactivation")
sbl1 = ad_manager.StatementBuilder(version=V)
sbl1.Where(f"lineItemId IN ({PAUSE_LIS[0]},{PAUSE_LIS[1]}) AND status = 'ACTIVE'")
licas = lica_svc.getLineItemCreativeAssociationsByStatement(sbl1.ToStatement()).results or []
print(f"    {len(licas)} active LICA(s) across the two LIs")
if not DRY_RUN:
    sbd = ad_manager.StatementBuilder(version=V)
    sbd.Where(f"lineItemId IN ({PAUSE_LIS[0]},{PAUSE_LIS[1]}) AND status = 'ACTIVE'")
    res = lica_svc.performLineItemCreativeAssociationAction(
        {"xsi_type": "DeactivateLineItemCreativeAssociations"}, sbd.ToStatement())
    n = getattr(res, "numChanges", 0) or 0
    print(f"    ✓ Deactivated {n} LICA(s) — both LIs now have no serving creatives")
    log["actions"].append({"action": "halt_via_lica", "lis": PAUSE_LIS, "num_changes": n})
else:
    print("    would deactivate all active LICAs on both LIs")

# ---- 2. OnlineCasino -> large formats only (deactivate 320x50 LICA) ----
print(f"\n[2] OnlineCasino {ONLINECASINO_LI} -> drop 320x50 (LICA creative {ONLINECASINO_320x50_CID})")
if not DRY_RUN:
    sbl = ad_manager.StatementBuilder(version=V)
    sbl.Where(f"lineItemId = {ONLINECASINO_LI} AND creativeId = {ONLINECASINO_320x50_CID}")
    res = lica_svc.performLineItemCreativeAssociationAction(
        {"xsi_type": "DeactivateLineItemCreativeAssociations"}, sbl.ToStatement())
    n = getattr(res, "numChanges", 0) or 0
    print(f"    ✓ DeactivateLICA — {n} change(s)  (728x90/300x250/970x250 remain)")
    log["actions"].append({"action": "deactivate_lica", "li": ONLINECASINO_LI,
                           "creative": ONLINECASINO_320x50_CID, "num_changes": n})
else:
    print("    would deactivate the 320x50 LICA; large formats remain active")

# ---- 3. Restore control goal ----
print(f"\n[3] Control {CONTROL_LI} goal {cur[CONTROL_LI].primaryGoal.units:,} -> {CONTROL_NEW_GOAL:,}")

DEAD_IDS = {8636918122, 8637757077, 8731515695, 8731516835}  # already removed earlier; belt+braces
def rebuild(node):
    asids = getattr(node, "audienceSegmentIds", None)
    if asids is not None:
        live = [int(i) for i in asids if int(i) not in DEAD_IDS]
        return {"xsi_type": "AudienceSegmentCriteria", "operator": str(node.operator),
                "audienceSegmentIds": live}
    if getattr(node, "keyId", None) is not None:
        return {"xsi_type": "CustomCriteria", "keyId": int(node.keyId),
                "valueIds": [int(v) for v in node.valueIds], "operator": str(node.operator)}
    if getattr(node, "logicalOperator", None) is not None:
        return {"xsi_type": "CustomCriteriaSet", "logicalOperator": str(node.logicalOperator),
                "children": [rebuild(c) for c in node.children]}
    raise ValueError(f"unknown node: {node}")

if not DRY_RUN:
    li = cur[CONTROL_LI]
    if getattr(li.targeting, "customTargeting", None) is not None:
        li.targeting.customTargeting = rebuild(li.targeting.customTargeting)
    li.primaryGoal.units = CONTROL_NEW_GOAL
    li.allowOverbook = True
    updated = li_svc.updateLineItems([li])
    print(f"    ✓ control goal now {updated[0].primaryGoal.units:,}")
    log["actions"].append({"action": "control_goal", "li": CONTROL_LI,
                           "new_goal": CONTROL_NEW_GOAL})
else:
    print(f"    would set goal to {CONTROL_NEW_GOAL:,} (control keeps all sizes = volume arm)")

if DRY_RUN:
    print("\nDRY RUN — no writes. Re-run with --apply.\n")
    sys.exit(0)

log["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
with open("/tmp/rebalance_betting_log.json", "w") as f:
    json.dump(log, f, indent=2)
print(f"\nLogged: /tmp/rebalance_betting_log.json")

# ---- verify ----
print("\nPost-change state:")
sb2 = ad_manager.StatementBuilder(version=V)
sb2.Where(f"id IN ({CONTROL_LI},{ONLINECASINO_LI},{PAUSE_LIS[0]},{PAUSE_LIS[1]})")
for li in li_svc.getLineItemsByStatement(sb2.ToStatement()).results:
    print(f"  {li.id}  {li.status:<10}  goal={li.primaryGoal.units:,}")
