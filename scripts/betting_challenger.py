"""Stand up Challenger-1 by repurposing the halted Basketball LI (7319885244).

Sequential audience-exploration screen: rotate one fresh audience at a time
through a single challenger slot, measure CTR + DV Attention vs the no-audience
control (format-matched), promote whatever beats control's ~0.094% CTR.

Challenger-1 audience: 9363363991 — Facteus Purchase History > FANDUEL Frequent
(3.39M). Transactional/purchase signal (frequent FanDuel spenders) — highest-
intent type for a betting CPA goal; sportsbook complement to the casino LI.

Why repurpose Basketball's LI instead of creating new:
  - Basketball (7319885244) is halted (LICAs deactivated) but its 4 creatives
    + LICAs still exist. Reusing it = no new creatives/LICAs to build.
  - sub_id2=li7319885244 still uniquely identifies the line; we just remap
    "7319885244 = Challenger-1 (FANDUEL Frequent)" in the runbook.

Runs ALL 4 sizes (not large-format-only): the screen measures AUDIENCE quality,
so we want max volume + a clean format-matched CTR comparison against the
all-sizes control. Format optimization (large-only) applies when a winning
audience graduates to a conversion line.

Actions:
  1. Update LI 7319885244: audience -> AudienceSegmentCriteria(9363363991),
     rename Aud-Basketball -> Aud-FanduelFreq, allowOverbook=True.
  2. Reactivate all 4 LICAs on 7319885244.

Default: dry-run. --apply to execute.
"""
import os, json, sys, warnings, tempfile, datetime
warnings.filterwarnings("ignore")

DRY_RUN = "--apply" not in sys.argv
LI_ID = 7319885244
NEW_SEGMENT = 9363363991
NEW_HANDLE = "Aud-FanduelFreq"

with open("/Users/roger/code/yield-dashboard/.env") as f:
    for line in f:
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); v=v.strip()
        if v.startswith('"') and v.endswith('"'): v=v[1:-1]
        os.environ.setdefault(k.strip(),v)

from googleads import ad_manager, oauth2
sa=json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
with tempfile.NamedTemporaryFile(mode="w",suffix=".json",delete=False) as f:
    json.dump(sa,f); kf=f.name
oc=oauth2.GoogleServiceAccountClient(kf,"https://www.googleapis.com/auth/dfp")
client=ad_manager.AdManagerClient(oc,"NewsweekDashboard/1.0",network_code=os.environ["GAM_NETWORK_ID"])
V="v202605"
li_svc=client.GetService("LineItemService",version=V)
lica_svc=client.GetService("LineItemCreativeAssociationService",version=V)

def audience_ct(seg):
    return {"xsi_type":"CustomCriteriaSet","logicalOperator":"AND",
            "children":[{"xsi_type":"AudienceSegmentCriteria","operator":"IS",
                         "audienceSegmentIds":[seg]}]}

sb=ad_manager.StatementBuilder(version=V); sb.Where(f"id = {LI_ID}")
li=li_svc.getLineItemsByStatement(sb.ToStatement()).results[0]
old_name=li.name
new_name="_".join(old_name.split("_")[:-2]+[NEW_HANDLE, old_name.split("_")[-1]])

print("="*70)
print(f"CHALLENGER-1 SETUP  ({'DRY RUN' if DRY_RUN else 'APPLY'})")
print("="*70)
print(f"\nLI {LI_ID}  status={li.status}")
print(f"  rename: ...{old_name[-26:]}")
print(f"      ->  ...{new_name[-26:]}")
print(f"  audience -> {NEW_SEGMENT} (FANDUEL Frequent, 3.39M)")

# LICA state
sbl=ad_manager.StatementBuilder(version=V); sbl.Where(f"lineItemId = {LI_ID}")
licas=lica_svc.getLineItemCreativeAssociationsByStatement(sbl.ToStatement()).results or []
print(f"  LICAs: {[(l.creativeId, l.status) for l in licas]}")

if DRY_RUN:
    print("\nDRY RUN — re-run with --apply.\n"); sys.exit(0)

# 1. Update LI
li.name=new_name
li.targeting.customTargeting=audience_ct(NEW_SEGMENT)
li.allowOverbook=True
li_svc.updateLineItems([li])
print(f"\n  ✓ LI updated (name + audience)")

# 2. Reactivate all LICAs
sba=ad_manager.StatementBuilder(version=V); sba.Where(f"lineItemId = {LI_ID} AND status = 'INACTIVE'")
res=lica_svc.performLineItemCreativeAssociationAction(
    {"xsi_type":"ActivateLineItemCreativeAssociations"}, sba.ToStatement())
n=getattr(res,"numChanges",0) or 0
print(f"  ✓ reactivated {n} LICA(s) — challenger now serving all sizes")

log={"ts":datetime.datetime.now(datetime.timezone.utc).isoformat(),
     "li":str(LI_ID),"role":"Challenger-1","segment":NEW_SEGMENT,
     "segment_name":"Facteus FANDUEL Frequent","new_name":new_name,"licas_activated":n}
with open("/tmp/challenger1_log.json","w") as f: json.dump(log,f,indent=2)
print(f"\nLogged: /tmp/challenger1_log.json")

# Verify
li2=li_svc.getLineItemsByStatement(sb.ToStatement()).results[0]
act=[l.creativeId for l in lica_svc.getLineItemCreativeAssociationsByStatement(sbl.ToStatement()).results if l.status=="ACTIVE"]
print(f"Post: name ...{li2.name[-26:]}  active LICAs={len(act)}")
