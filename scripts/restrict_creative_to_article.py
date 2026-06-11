"""Pin one creative on a line item to a single newsweek.com article.

The site sets the GPT key-value `article_id=<entityId>` on every ad request
(entityId = the trailing number in the article URL), so per-article scoping
is a custom-targeting criterion on that key — the same mechanism the
Infiniti Newsmakers logo LI 7336465381 uses (docs/article_sponsor_logo.md).

This script does it at the CREATIVE level, so the line item keeps serving
its other creatives everywhere it already targets, while the chosen
creative serves only on the one article:

  1. get-or-create the `article_id` value for the article
  2. append a CreativeTargeting (name + customTargeting criterion) to
     LineItem.creativeTargetings
  3. set targetingName on the creative's LICA to point at it

Idempotent: re-runs reuse the existing value/CreativeTargeting/LICA state.
Revert by clearing targetingName on the LICA in the GAM UI (or API).

Usage:
    python scripts/restrict_creative_to_article.py \
        --line-item-id 7309466805 --creative-id 138557893457 \
        --article-id 12010430              # dry run (default)
    ... --apply                            # write to GAM
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# .env (same loader as the other GAM scripts; no-op when env vars are set,
# e.g. in Actions)
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    with open(_env) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

V = "v202605"


def get_client():
    sa_json = os.environ.get("GAM_SERVICE_ACCOUNT_JSON")
    network_id = os.environ.get("GAM_NETWORK_ID")
    if not sa_json or not network_id:
        sys.exit("GAM_SERVICE_ACCOUNT_JSON / GAM_NETWORK_ID env vars not set")
    from googleads import ad_manager, oauth2  # type: ignore

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(json.loads(sa_json), f)
        key_file = f.name
    oc = oauth2.GoogleServiceAccountClient(key_file, "https://www.googleapis.com/auth/dfp")
    client = ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=network_id
    )
    return client, ad_manager


def _one(resp):
    return (getattr(resp, "results", None) or [None])[0]


def _all(resp):
    return list(getattr(resp, "results", None) or [])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--line-item-id", type=int, required=True)
    p.add_argument("--creative-id", type=int, required=True)
    p.add_argument("--article-id", required=True,
                   help="newsweek entityId — the trailing number in the article URL")
    p.add_argument("--key", default="article_id", help="custom targeting key name")
    p.add_argument("--targeting-name", default=None,
                   help="CreativeTargeting name (default: article-<article_id>-only)")
    p.add_argument("--apply", action="store_true", help="write to GAM (default: dry run)")
    args = p.parse_args()
    tname = args.targeting_name or f"article-{args.article_id}-only"

    client, ad_manager = get_client()
    li_svc = client.GetService("LineItemService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    ct_svc = client.GetService("CustomTargetingService", version=V)

    def stmt(where, **binds):
        sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(200)
        for k, v in binds.items():
            sb.WithBindVariable(k, v)
        return sb.ToStatement()

    print("=" * 72)
    print(f"RESTRICT CREATIVE TO ARTICLE  ({'APPLY' if args.apply else 'DRY RUN'})")
    print("=" * 72)

    # ── line item ─────────────────────────────────────────────────────────
    li = _one(li_svc.getLineItemsByStatement(stmt("id = :id", id=args.line_item_id)))
    if li is None:
        sys.exit(f"Line item {args.line_item_id} not found")
    print(f"Line item {li['id']}: {li['name']}")
    print(f"  type={li['lineItemType']}  status={li['status']}  "
          f"roadblocking={li['roadblockingType']}  rotation={li['creativeRotationType']}")
    sizes = [f"{ph['size']['width']}x{ph['size']['height']}"
             for ph in (li['creativePlaceholders'] or [])]
    print(f"  placeholders: {', '.join(sizes)}")
    existing_cts = list(li['creativeTargetings'] or [])
    print(f"  creativeTargetings: {[ct['name'] for ct in existing_cts] or 'none'}")

    # ── LICAs + creatives on the LI ───────────────────────────────────────
    licas = _all(lica_svc.getLineItemCreativeAssociationsByStatement(
        stmt("lineItemId = :li", li=args.line_item_id)))
    if not licas:
        sys.exit(f"No creatives associated with line item {args.line_item_id}")
    target_lica = None
    print(f"  creatives on this LI ({len(licas)}):")
    for lica in licas:
        cid = lica['creativeId']
        cr = _one(cr_svc.getCreativesByStatement(stmt("id = :id", id=cid)))
        size = f"{cr['size']['width']}x{cr['size']['height']}" if cr is not None else "?"
        mark = "★" if cid == args.creative_id else " "
        print(f"   {mark} {cid}  {size:>9}  status={lica['status']}  "
              f"targetingName={lica['targetingName'] or '—'}  "
              f"{cr['name'] if cr is not None else '(creative not readable)'}")
        if cid == args.creative_id:
            target_lica = lica
    if target_lica is None:
        sys.exit(f"Creative {args.creative_id} is not associated with "
                 f"line item {args.line_item_id}")

    # ── article_id key + value ────────────────────────────────────────────
    keys = _all(ct_svc.getCustomTargetingKeysByStatement(
        stmt("name = :n AND status = 'ACTIVE'", n=args.key)))
    if len(keys) > 1:
        print(f"  WARNING: {len(keys)} active keys named {args.key!r} — using first")
    key = keys[0] if keys else None
    if key is not None:
        print(f"Key {args.key!r}: id={key['id']}  type={key['type']}")
    else:
        print(f"Key {args.key!r}: MISSING — will create (type=FREEFORM)")

    value = None
    if key is not None:
        value = _one(ct_svc.getCustomTargetingValuesByStatement(stmt(
            "customTargetingKeyId = :k AND name = :v AND status = 'ACTIVE'",
            k=key['id'], v=str(args.article_id))))
    print(f"Value {args.article_id!r}: "
          + (f"id={value['id']}" if value is not None else "MISSING — will create"))

    # ── plan ──────────────────────────────────────────────────────────────
    ct_match = next((ct for ct in existing_cts if ct['name'] == tname), None)
    print(f"\nPlan:")
    print(f"  1. CreativeTargeting {tname!r} on LI: "
          + ("already present — reuse" if ct_match is not None else "append"))
    cur = target_lica['targetingName']
    print(f"  2. LICA targetingName: {cur or '—'} → {tname}"
          + ("  (no-op)" if cur == tname else ""))

    if not args.apply:
        print("\nDry run only — re-run with --apply to write to GAM.")
        return

    # ── apply ─────────────────────────────────────────────────────────────
    print()
    if key is None:
        key = ct_svc.createCustomTargetingKeys([{
            "name": args.key, "displayName": args.key, "type": "FREEFORM",
        }])[0]
        print(f"Created key {args.key!r} id={key['id']}")
    if value is None:
        value = ct_svc.createCustomTargetingValues([{
            "customTargetingKeyId": key['id'],
            "name": str(args.article_id),
            "displayName": str(args.article_id),
            "matchType": "EXACT",
        }])[0]
        print(f"Created value {args.article_id!r} id={value['id']}")

    criterion = {
        "xsi_type": "CustomCriteriaSet",
        "logicalOperator": "OR",
        "children": [{
            "xsi_type": "CustomCriteriaSet",
            "logicalOperator": "AND",
            "children": [{
                "xsi_type": "CustomCriteria",
                "keyId": key['id'],
                "valueIds": [value['id']],
                "operator": "IS",
            }],
        }],
    }

    if ct_match is None:
        li['creativeTargetings'] = existing_cts + [
            {"name": tname, "targeting": {"customTargeting": criterion}}
        ]
        # updateLineItems re-runs the forecast; skip it or a delivering
        # sponsorship can throw NOT_ENOUGH_INVENTORY (docs/article_sponsor_logo.md)
        li['skipInventoryCheck'] = True
        li['allowOverbook'] = True
        li = li_svc.updateLineItems([li])[0]
        print(f"LI updated — creativeTargetings: "
              f"{[ct['name'] for ct in li['creativeTargetings']]}")
    else:
        print(f"CreativeTargeting {tname!r} already on LI — left as-is "
              f"(verify it targets {args.key}={args.article_id} if it predates this run)")

    if target_lica['targetingName'] != tname:
        target_lica['targetingName'] = tname
        target_lica = lica_svc.updateLineItemCreativeAssociations([target_lica])[0]
    print(f"LICA cr={target_lica['creativeId']} targetingName="
          f"{target_lica['targetingName']}  status={target_lica['status']}")
    print(f"\nDone. Creative {args.creative_id} now serves only where "
          f"{args.key}={args.article_id}.")
    print(f"Verify: https://admanager.google.com/{os.environ['GAM_NETWORK_ID']}"
          f"#delivery/line_item/detail/line_item_id={args.line_item_id}")


if __name__ == "__main__":
    main()
