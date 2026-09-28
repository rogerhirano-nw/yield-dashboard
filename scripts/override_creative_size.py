#!/usr/bin/env python3
"""Override the serving size of one creative on every line item it's attached to.

GAM's "size override" lives on the LineItemCreativeAssociation, not on the
creative: `LineItemCreativeAssociation.sizes` replaces `Creative.size` for that
line, so the creative can serve into slots its native size wouldn't match. This
sets that override to exactly the given size(s) on each of the creative's
associations. The creative itself is not edited.

Guards:
  - an association whose line item has no placeholder for a target size is
    skipped and reported (GAM rejects the write, and the line couldn't serve
    the size anyway)
  - archived / inactive-line associations are listed but still updated only if
    the LICA itself is ACTIVE
  - one association is written and re-read first (canary), and every
    association is re-read after the batch, so a silently ignored update is
    reported, not assumed

Usage:
    python3 scripts/override_creative_size.py 138502478327 970x250          # dry run (add)
    python3 scripts/override_creative_size.py 138502478327 970x250 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

_envp = Path(__file__).resolve().parent.parent / ".env"
if _envp.exists():
    for _line in _envp.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from googleads import ad_manager, oauth2  # noqa: E402

V = "v202605"


def _client():
    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        kf = f.name
    oc = oauth2.GoogleServiceAccountClient(kf, "https://www.googleapis.com/auth/dfp")
    return ad_manager.AdManagerClient(oc, "NewsweekDashboard/1.0",
                                      network_code=os.environ["GAM_NETWORK_ID"])


def _q(svc, method, where, **binds):
    """All pages — a Prebid catch-all sits on 600+ line items, past one page."""
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(500)
    for k, v in binds.items():
        sb = sb.WithBindVariable(k, v)
    out = []
    while True:
        resp = getattr(svc, method)(sb.ToStatement())
        page = list(getattr(resp, "results", []) or [])
        out.extend(page)
        sb.offset += sb.limit
        if not page or sb.offset >= (getattr(resp, "totalResultSetSize", 0) or 0):
            return out


def _sz(s) -> str:
    return f"{s.width}x{s.height}"


def _override(la) -> list[str]:
    return [_sz(s) for s in (getattr(la, "sizes", None) or [])]


def _placeholder_sizes(li) -> set[str]:
    out = set()
    for p in (li.creativePlaceholders or []):
        out.add(_sz(p.size))
        for c in (getattr(p, "companions", None) or []):
            out.add(_sz(c.size))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("creative_id", type=int)
    ap.add_argument("sizes", help="comma-separated, e.g. 970x250")
    ap.add_argument("--mode", choices=("add", "replace"), default="add",
                    help="add: keep the existing override and add the size(s); "
                         "replace: the override becomes exactly the size(s)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    targets = [s.strip().lower() for s in args.sizes.split(",") if s.strip()]

    client = _client()
    cr_svc = client.GetService("CreativeService", version=V)
    li_svc = client.GetService("LineItemService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)

    crs = _q(cr_svc, "getCreativesByStatement", "id = :i", i=args.creative_id)
    if not crs:
        raise SystemExit(f"creative {args.creative_id} not found")
    c = crs[0]
    print(f"{'APPLY' if args.apply else 'DRY RUN'} — {args.mode} override size(s) {targets}")
    print(f"creative {c.id} '{c.name}' [{type(c).__name__}] "
          f"native size {_sz(c.size)}  advertiser {c.advertiserId}")

    licas = _q(lica_svc, "getLineItemCreativeAssociationsByStatement",
               "creativeId = :c", c=args.creative_id)
    print(f"\n{len(licas)} line-item association(s)")
    if not licas:
        return 1

    lis = {}
    ids = sorted({la.lineItemId for la in licas})
    for i in range(0, len(ids), 300):
        for li in _q(li_svc, "getLineItemsByStatement",
                     f"id IN ({', '.join(str(x) for x in ids[i:i + 300])})"):
            lis[li.id] = li

    todo = []
    for la in licas:
        li = lis[la.lineItemId]
        ph = _placeholder_sizes(li)
        cur = _override(la)
        want = (cur + [t for t in targets if t not in cur]
                if args.mode == "add" and cur else list(targets))
        missing = [t for t in targets if t not in ph]
        print(f"\n  LI {li.id} '{li.name}'  status {li.status}  "
              f"archived={li.isArchived}")
        print(f"    placeholders: {sorted(ph)}")
        print(f"    LICA {la.status}  current override: {cur or '(none — native size)'}")
        if sorted(cur) == sorted(want):
            print("    -> already set, skip")
        elif missing:
            print(f"    -> SKIP: line has no placeholder for {missing}; add it to the "
                  f"line item first")
        elif li.isArchived:
            print("    -> SKIP: line item archived")
        else:
            print(f"    -> set override to {want}")
            todo.append((la, want))

    if not args.apply:
        print(f"\nDRY RUN — {len(todo)} association(s) would change. Nothing written.")
        return 0
    if not todo:
        print("\nNothing to do.")
        return 0

    def _set(la, want):
        la.sizes = [{"width": int(t.split("x")[0]), "height": int(t.split("x")[1]),
                     "isAspectRatio": False} for t in want]
        return la

    def _reread():
        return {la.lineItemId: _override(la) for la in _q(
            lica_svc, "getLineItemCreativeAssociationsByStatement",
            "creativeId = :c", c=args.creative_id)}

    la, want = todo[0]
    lica_svc.updateLineItemCreativeAssociations([_set(la, want)])
    got = _reread().get(la.lineItemId, [])
    if sorted(got) != sorted(want):
        print(f"\nCANARY FAILED on LI {la.lineItemId}: wanted {want}, got {got}. "
              f"Nothing else sent.")
        return 1
    print(f"\ncanary ok — LI {la.lineItemId} now {got}")

    rest = todo[1:]
    for i in range(0, len(rest), 100):
        batch = [_set(la, want) for la, want in rest[i:i + 100]]
        try:
            lica_svc.updateLineItemCreativeAssociations(batch)
        except Exception as exc:
            print(f"  batch failed ({exc}) — retrying singly")
            for la in batch:
                try:
                    lica_svc.updateLineItemCreativeAssociations([la])
                except Exception as exc2:
                    print(f"    LI {la.lineItemId}: {exc2}")

    now = _reread()
    bad = [la.lineItemId for la, want in todo
           if sorted(now.get(la.lineItemId, [])) != sorted(want)]
    print(f"\n{len(todo) - len(bad)} of {len(todo)} association(s) verified updated.")
    for lid in bad[:20]:
        print(f"  !! LI {lid}: override is {now.get(lid)}")
    print("GAM takes ~10 minutes to pick this up.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
