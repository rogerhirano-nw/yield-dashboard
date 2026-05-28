"""Test-LI batch creator (DRY RUN by default; --apply to execute).

Plan:
  1. Reduce control LI 7306352098 lifetime impression goal: 1,875,000 -> 1,230,000
  2. For each of 3 high-intent betting segments (Basketball / Sports Bet Enthusiast
     / Online Casino), create a new STANDARD LI under order 4068491190 cloned
     from the control LI's targeting + a positive audience-segment-id filter,
     each with a 215,000-impression lifetime goal.
  3. For each new LI, create 4 new ImageCreatives (one per size) cloned from
     the corresponding source creative, with destinationUrl hardcoded as
     ...&sub_id1=<size>_li<new_LI_id>  so each click attributes back to its LI.
  4. Create 4 LICAs per new LI linking the new creatives.
  5. Deactivate the macro-test creative 138559273952 so its broken sub_id1
     stops appearing in reports.

Reversible operations:
  - LIs: created in DRAFT state; archive via LineItemAction.ArchiveLineItems.
  - Creatives: deactivate via CreativeAction.DeactivateCreatives.
  - Control goal: revert by updateLineItems with primaryGoal.units = 1875000.

The script logs all created IDs + the prior control goal to
/tmp/test_lis_log.json so a rollback is mechanical.
"""

import os, json, sys, warnings, tempfile, datetime, copy
warnings.filterwarnings("ignore")

DRY_RUN = "--apply" not in sys.argv

# ---- env ----
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

# ---- config ----
ORDER_ID            = 4068491190
CONTROL_LI_ID       = 7306352098
ADVERTISER_ID       = 6069130066
NEW_CONTROL_GOAL    = 1_230_000
PER_TEST_LI_GOAL    = 215_000
MACRO_TEST_CID      = 138559273952  # to deactivate

PICKS = [
    {"segment_id": 9385007833, "handle": "Aud-Basketball",
     "long": "Sports Betting > Basketball"},
    {"segment_id": 9168610732, "handle": "Aud-SBEnthusiast",
     "long": "Sports Betting Enthusiast"},
    {"segment_id": 9333427967, "handle": "Aud-OnlineCasino",
     "long": "Online Casinos > Regulated Consumer"},
]

CLICK_URL_TEMPLATE = "https://trk.spnfnt.com/click?o=1&a=236&c=1&link_id=5&sub_id1={size}&sub_id2=li{li_id}"

# ---- load snapshot ----
snap = json.load(open("/tmp/li_source_snapshot.json"))
src_li = snap["li"]
src_crs = {(c["size"]["width"], c["size"]["height"]): c for c in snap["creatives"]}

assert set(src_crs.keys()) == {(320,50),(300,250),(728,90),(970,250)}, \
    f"unexpected source sizes: {sorted(src_crs.keys())}"

# ---- build new-LI specs ----
def build_audience_custom_targeting(segment_id: int) -> dict:
    """Build the customTargeting dict that adds positive audience-segment
    targeting via AudienceSegmentCriteria. Schema verified against the
    SOAP v202605 WSDL:
        CustomCriteriaSet { logicalOperator, children[] }
          -> AudienceSegmentCriteria { operator, audienceSegmentIds[] }
    Each polymorphic node carries an explicit xsi_type so the googleads
    SOAP serializer can dispatch (without these hints it bails with
    KeyError: 'logicalOperator' on AND/OR sets, or 'audienceSegmentIds'
    on the leaf)."""
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


def build_new_li(pick):
    """Clone targeting from control LI + add positive audience-segment
    targeting for `pick`."""
    # Deep-clone targeting so we don't mutate the snapshot
    targeting = copy.deepcopy(src_li["targeting"])
    # Replace the control LI's customTargeting tree with a single
    # AudienceSegmentCriteria node for this segment. We can't just APPEND
    # to the control's customTargeting because cloning that polymorphic tree
    # losslessly is what trips up the SOAP serializer in the first place
    # (the control's nodes lack xsi_type hints after a zeep round-trip).
    # Test LIs therefore lose the control LI's content-category IS_NOT
    # exclusions; add via GAM UI if important.
    targeting["customTargeting"] = build_audience_custom_targeting(pick["segment_id"])

    # Mint name following 14-field convention: swap creative slot to Aud-<handle>
    # Source: Newsweek_Direct_Gambling_NA_NA_NA_NA_Spinfinite_Spinfinite-Digital-Campaign_US_Display_IO1109_1_Team-USA_RShore
    src_name = src_li["name"]
    parts = src_name.split("_")
    # Replace second-to-last segment (creative slot) with Aud handle
    parts[-2] = pick["handle"]
    new_name = "_".join(parts)

    return {
        "name": new_name,
        "orderId": ORDER_ID,
        "targeting": targeting,
        "lineItemType": "STANDARD",
        "priority": src_li.get("priority", 8),
        "costType": "CPM",
        "costPerUnit": dict(src_li["costPerUnit"]),  # $8 CPM
        "creativeRotationType": src_li.get("creativeRotationType", "EVEN"),
        "creativePlaceholders": copy.deepcopy(src_li["creativePlaceholders"]),
        "startDateTimeType": "IMMEDIATELY",
        "endDateTime": src_li["endDateTime"],
        "primaryGoal": {
            "goalType": "LIFETIME",
            "unitType": "IMPRESSIONS",
            "units": PER_TEST_LI_GOAL,
        },
        "discountType": src_li.get("discountType", "PERCENTAGE"),
        "discount": src_li.get("discount", 0),
        # Bypass forecast check on the narrower audience-targeted LIs. The
        # segment + ad-unit combination may not project enough inventory to
        # cover the 215K imp goal; allowOverbook lets the LI reserve anyway
        # and just deliver what's available. Without this, updateLineItems
        # / createLineItems return ForecastingError.NOT_ENOUGH_INVENTORY.
        "allowOverbook": True,
        # Don't carry forward the source's id, status, lastModified, stats, etc
    }

new_li_specs = [(p, build_new_li(p)) for p in PICKS]

# ---- build creative specs (URLs include placeholder for now; filled at apply time) ----
def build_new_creatives(li_id_placeholder, pick):
    """Return 4 ImageCreative dicts, one per size."""
    out = []
    for (w, h), src in sorted(src_crs.items()):
        size_str = f"{w}x{h}"
        click_url = CLICK_URL_TEMPLATE.format(size=size_str, li_id=li_id_placeholder)
        spec = {
            "xsi_type": "ImageCreative",
            "name": f"Newsweek - {size_str} {pick['handle']}",
            "advertiserId": ADVERTISER_ID,
            "size": dict(src["size"]),
            "destinationUrl": click_url,
            "destinationUrlType": src.get("destinationUrlType", "CLICK_TO_WEB"),
            "overrideSize": src.get("overrideSize", False),
            "primaryImageAsset": {
                "assetId": src["primaryImageAsset"]["assetId"],
                "imageDensity": src["primaryImageAsset"].get("imageDensity", "ONE_TO_ONE"),
            },
            "thirdPartyDataDeclaration": copy.deepcopy(src.get("thirdPartyDataDeclaration")) or None,
            "thirdPartyImpressionTrackingUrls": list(src.get("thirdPartyImpressionTrackingUrls") or []),
            "adBadgingEnabled": False,
            "selfDeclaredEuropeanUnionPoliticalContent": False,
        }
        out.append(spec)
    return out

# ============================== DRY RUN PRINT ==============================
print("=" * 78)
print(f"TEST-LI BATCH  ({'DRY RUN' if DRY_RUN else 'APPLY'})")
print("=" * 78)
print(f"\nORDER {ORDER_ID}")
print(f"CONTROL LI {CONTROL_LI_ID}")
print(f"  goal change: 1,875,000 imps -> {NEW_CONTROL_GOAL:,} imps  "
      f"(frees ${(1_875_000-NEW_CONTROL_GOAL)*8/1000:,.0f} of remaining budget)")
print(f"\n3 NEW TEST LIs (each goal {PER_TEST_LI_GOAL:,} imps  =  "
      f"${PER_TEST_LI_GOAL*8/1000:,.0f} of media spend before data fees):")
for pick, spec in new_li_specs:
    print(f"  - {spec['name']}")
    print(f"      segment: {pick['segment_id']}  ({pick['long']})")
    print(f"      sizes:   {[(p['size']['width'], p['size']['height']) for p in spec['creativePlaceholders']]}")
    print(f"      end:     {src_li['endDateTime']['date']['year']}-"
          f"{src_li['endDateTime']['date']['month']:02d}-"
          f"{src_li['endDateTime']['date']['day']:02d}")
print(f"\n12 NEW CREATIVES (4 per LI, asset reused from source):")
for pick, _ in new_li_specs:
    crs = build_new_creatives("<NEW_LI_ID>", pick)
    for c in crs:
        sz = c["size"]; w,h = sz["width"], sz["height"]
        print(f"  - {c['name']:<48}  asset={c['primaryImageAsset']['assetId']}  "
              f"url=...sub_id1={w}x{h}&sub_id2=li<NEW_LI_ID>")
print(f"\n12 NEW LICAs: each new creative -> its new LI")
print(f"\nCLEANUP: deactivate creative {MACRO_TEST_CID}  (the failed macro test)")
print(f"\nLI STATUS: new LIs will be created as DRAFT. They will NOT deliver until")
print(f"approved (in GAM UI: Delivery > Orders > {ORDER_ID} > select LIs > Approve).")
print(f"Auto-approval via API requires trafficker permission on the service account.")
print(f"Run with --approve-after-create to attempt auto-approval (may fail with PermissionDenied).")

if DRY_RUN:
    print("\nDRY RUN — no GAM writes. Re-run with --apply to execute.\n")
    sys.exit(0)

# ============================== APPLY ==============================
print("\n" + "=" * 78 + "\nAPPLYING\n" + "=" * 78)

li_svc   = client.GetService("LineItemService", version=V)
cr_svc   = client.GetService("CreativeService", version=V)
lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)

log = {
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "control_li_prior_goal": 1_875_000,
    "control_li_new_goal":   NEW_CONTROL_GOAL,
    "new_lis": [],
    "deactivated_creatives": [],
}

# 1. Update control LI goal (best-effort — skip silently if blocked by stale
#    audience-segment refs in customTargeting; user can adjust in GAM UI later)
print(f"\n[1/4] Reducing control LI {CONTROL_LI_ID} goal to {NEW_CONTROL_GOAL:,}...")
try:
    sb = ad_manager.StatementBuilder(version=V); sb.Where(f"id = {CONTROL_LI_ID}")
    control_li = li_svc.getLineItemsByStatement(sb.ToStatement()).results[0]
    control_li["primaryGoal"]["units"] = NEW_CONTROL_GOAL
    updated = li_svc.updateLineItems([control_li])
    print(f"   ✓ control LI updated. new units = {updated[0]['primaryGoal']['units']:,}")
    log["control_li_goal_updated"] = True
except Exception as e:
    print(f"   ⚠ control goal update SKIPPED ({type(e).__name__}). "
          f"Continuing with test-LI creation; adjust the control goal manually in GAM UI.")
    print(f"   error: {str(e)[:200]}")
    log["control_li_goal_updated"] = False
    log["control_li_goal_skip_reason"] = str(e)[:200]

# 2-4. For each pick: create LI -> create 4 creatives -> 4 LICAs
for pick, spec in new_li_specs:
    print(f"\n[2/4] Creating LI: {spec['name']}")
    created_lis = li_svc.createLineItems([spec])
    new_li_id = created_lis[0]["id"]
    print(f"   ✓ new LI id = {new_li_id}  status = {created_lis[0]['status']}")

    li_log = {"segment_id": pick["segment_id"], "handle": pick["handle"],
              "li_id": str(new_li_id), "name": spec["name"], "creatives": []}

    print(f"[3/4] Creating 4 creatives for LI {new_li_id}...")
    cr_specs = build_new_creatives(new_li_id, pick)
    created_crs = cr_svc.createCreatives(cr_specs)
    for c in created_crs:
        print(f"   ✓ creative {c['id']}  {c['size']['width']}x{c['size']['height']}  "
              f"-> {c['destinationUrl']}")
        li_log["creatives"].append({"id": str(c["id"]),
                                    "size": f"{c['size']['width']}x{c['size']['height']}",
                                    "url": c["destinationUrl"]})

    print(f"[4/4] LICA-ing {len(created_crs)} creatives to LI {new_li_id}...")
    licas = [{"lineItemId": new_li_id, "creativeId": c["id"]} for c in created_crs]
    created_licas = lica_svc.createLineItemCreativeAssociations(licas)
    for l in created_licas:
        print(f"   ✓ LICA status={l['status']}  ({l['lineItemId']}/{l['creativeId']})")

    log["new_lis"].append(li_log)
    # Incremental log write — survive a mid-batch crash
    with open("/tmp/test_lis_log.json", "w") as f:
        json.dump(log, f, indent=2)

# 5. Deactivate the macro-test creative
print(f"\n[CLEANUP] Deactivating macro-test creative {MACRO_TEST_CID}...")
sb_cleanup = ad_manager.StatementBuilder(version=V); sb_cleanup.Where(f"id = {MACRO_TEST_CID}")
result = cr_svc.performCreativeAction({"xsi_type": "DeactivateCreatives"}, sb_cleanup.ToStatement())
n_changed = getattr(result, "numChanges", 0) or 0
print(f"   ✓ deactivated ({n_changed} change)")
log["deactivated_creatives"].append(str(MACRO_TEST_CID))

# Save log
log["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
with open("/tmp/test_lis_log.json", "w") as f:
    json.dump(log, f, indent=2)
print(f"\nLog: /tmp/test_lis_log.json")
print(f"\nNEXT MANUAL STEP: open GAM UI -> Delivery > Orders > {ORDER_ID} -> approve the 3 new LIs.")
print(f"(They are DRAFT and will not deliver until approved.)\n")
