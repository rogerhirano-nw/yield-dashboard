#!/usr/bin/env python3
"""Create the "clicked the Apple at Work interstitial" first-party audience
segment (LI 7384069597) — the pixel-population shape cloned from the existing
Subscription segments (recon 2026-08-04, PR #351):

  RuleBasedFirstPartyAudienceSegment
    pageViews=1, recencyDays=1, membershipExpirationDays=<arg, default 90>
    rule.inventoryRule    -> DFPAudiencePixel ad unit (includeDescendants)
    rule.customCriteriaRule -> dc_seg IS '<own segment id>'

Because the dc_seg value's *name* must equal the new segment's id, creation is
three steps: create segment (inventory-only rule) -> create the dc_seg custom
targeting value named with the id -> update the segment rule to require it.
The seconds-long window with an inventory-only rule is harmless: membership
processing lags 30min-48h.

Idempotent: if a segment with the target name already exists, it just prints
the segment + pixel tags and exits. Dry-run by default; --apply to write.
"""
import argparse
import os
import sys
from pathlib import Path

envp = Path(__file__).resolve().parent.parent / ".env"
if envp.exists():
    for line in envp.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient
from googleads import ad_manager

V = "v202605"
DEFAULT_NAME = "[nw] Apple at Work Q426 - ad clickers (LI 7384069597)"
DEFAULT_DESC = (
    "Users who clicked the Apple at Work PG interstitial "
    "(LI 7384069597, order 4144759148). Populated via DFPAudiencePixel "
    "activity tag fired on click (Innovid click tracker). Created via API "
    "2026-08-04; see yield-dashboard PR #351."
)


def stmt(where, limit=10):
    return (ad_manager.StatementBuilder(version=V)
            .Where(where).Limit(limit).ToStatement())


def pixel_tags(network_code, seg_id):
    act = (f"https://pubads.g.doubleclick.net/activity;"
           f"dc_iu=/{network_code}/DFPAudiencePixel;"
           f"ord=[timestamp];dc_seg={seg_id}")
    script = ('<script async id="google-pcd-tag" '
              'src="https://pagead2.googlesyndication.com/pagead/js/pcd.js" '
              f'data-audience-pixel="dc_iu=/{network_code}/DFPAudiencePixel;'
              f'dc_seg={seg_id}"></script>')
    return act, script


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--description", default=DEFAULT_DESC)
    ap.add_argument("--expiration-days", type=int, default=90)
    ap.add_argument("--apply", action="store_true",
                    help="actually create (default: dry-run)")
    args = ap.parse_args()

    gc = GAMClient()
    client = gc._get_soap_client()
    as_svc = client.GetService("AudienceSegmentService", version=V)
    ct_svc = client.GetService("CustomTargetingService", version=V)
    inv_svc = client.GetService("InventoryService", version=V)
    net = client.GetService("NetworkService", version=V).getCurrentNetwork()

    # Resolve the pixel plumbing dynamically (assert against recon'd ids).
    units = inv_svc.getAdUnitsByStatement(
        stmt("adUnitCode = 'DFPAudiencePixel'", 1)).results
    if not units:
        sys.exit("FATAL: DFPAudiencePixel ad unit not found")
    pixel_unit_id = int(units[0].id)
    keys = ct_svc.getCustomTargetingKeysByStatement(
        stmt("name = 'dc_seg'", 1)).results
    if not keys:
        sys.exit("FATAL: dc_seg custom targeting key not found")
    dc_seg_key_id = int(keys[0].id)
    print(f"pixel unit: {pixel_unit_id}  dc_seg key: {dc_seg_key_id}")

    # Idempotency: name lookup first.
    sb = (ad_manager.StatementBuilder(version=V)
          .Where("name = :n").WithBindVariable("n", args.name).Limit(1))
    existing = as_svc.getAudienceSegmentsByStatement(sb.ToStatement())
    if getattr(existing, "results", None):
        s = existing.results[0]
        print(f"\nSegment already exists — nothing to create.")
        report(net.networkCode, s)
        return

    print(f"\nplan: create RuleBasedFirstPartyAudienceSegment")
    print(f"  name: {args.name!r}")
    print(f"  pageViews=1 recencyDays=1 "
          f"membershipExpirationDays={args.expiration_days}")
    print(f"  rule: DFPAudiencePixel unit {pixel_unit_id} (+descendants) "
          f"AND dc_seg = <new segment id>")
    if not args.apply:
        print("\nDRY RUN — pass --apply to create.")
        return

    seg = {
        "xsi_type": "RuleBasedFirstPartyAudienceSegment",
        "name": args.name,
        "description": args.description,
        "pageViews": 1,
        "recencyDays": 1,
        "membershipExpirationDays": args.expiration_days,
        "rule": {
            "inventoryRule": {
                "targetedAdUnits": [{
                    "adUnitId": pixel_unit_id,
                    "includeDescendants": True,
                }],
            },
        },
    }
    created = as_svc.createAudienceSegments([seg])[0]
    sid = int(created.id)
    print(f"\ncreated segment id={sid} status={created.status}")

    # dc_seg value named with the segment id (reuse if it already exists).
    vres = ct_svc.getCustomTargetingValuesByStatement(
        stmt(f"customTargetingKeyId = {dc_seg_key_id} "
             f"AND name = '{sid}'", 1)).results
    if vres:
        vid = int(vres[0].id)
        print(f"dc_seg value '{sid}' already exists (id {vid})")
    else:
        val = ct_svc.createCustomTargetingValues([{
            "customTargetingKeyId": dc_seg_key_id,
            "name": str(sid),
            "displayName": str(sid),
            "matchType": "EXACT",
        }])[0]
        vid = int(val.id)
        print(f"created dc_seg value '{sid}' (id {vid})")

    created.rule.customCriteriaRule = {
        "xsi_type": "CustomCriteriaSet",
        "logicalOperator": "OR",
        "children": [{
            "xsi_type": "CustomCriteriaSet",
            "logicalOperator": "AND",
            "children": [{
                "xsi_type": "CustomCriteria",
                "operator": "IS",
                "keyId": dc_seg_key_id,
                "valueIds": [vid],
            }],
        }],
    }
    updated = as_svc.updateAudienceSegments([created])[0]
    print(f"rule updated — segment {updated.id} now requires dc_seg={sid}")
    report(net.networkCode, updated)


def report(network_code, s):
    act, script = pixel_tags(network_code, s.id)
    print(f"\n=== SEGMENT ===")
    print(f"id: {s.id}")
    print(f"name: {s.name!r}")
    print(f"status: {getattr(s, 'status', None)}  "
          f"expirationDays: {getattr(s, 'membershipExpirationDays', None)}")
    print(f"\nactivity pixel (image/click-tracker form — give this to "
          f"Innovid/OMD as a click tracker; their macro replaces [timestamp]):")
    print(f"  {act}")
    print(f"\nscript tag form (for any page we control):")
    print(f"  {script}")
    print(f"\nGAM UI: Signals > Audience > segment id {s.id}")


if __name__ == "__main__":
    main()
