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
  - every write is re-read, so a silently ignored update is reported, not
    assumed

Usage:
    python3 scripts/override_creative_size.py 138502478327 970x250          # dry run
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
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(500)
    for k, v in binds.items():
        sb = sb.WithBindVariable(k, v)
    return list(getattr(getattr(svc, method)(sb.ToStatement()), "results", []) or [])


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
    print(f"{'APPLY' if args.apply else 'DRY RUN'} — override size -> {targets}")
    print(f"creative {c.id} '{c.name}' [{type(c).__name__}] "
          f"native size {_sz(c.size)}  advertiser {c.advertiserId}")

    licas = _q(lica_svc, "getLineItemCreativeAssociationsByStatement",
               "creativeId = :c", c=args.creative_id)
    print(f"\n{len(licas)} line-item association(s)")
    if not licas:
        return 1

    todo = []
    for la in licas:
        li = _q(li_svc, "getLineItemsByStatement", "id = :i", i=la.lineItemId)[0]
        ph = _placeholder_sizes(li)
        cur = _override(la)
        missing = [t for t in targets if t not in ph]
        print(f"\n  LI {li.id} '{li.name}'  status {li.status}  "
              f"archived={li.isArchived}")
        print(f"    placeholders: {sorted(ph)}")
        print(f"    LICA {la.status}  current override: {cur or '(none — native size)'}")
        if sorted(cur) == sorted(targets):
            print("    -> already set, skip")
        elif missing:
            print(f"    -> SKIP: line has no placeholder for {missing}; add it to the "
                  f"line item first")
        elif li.isArchived:
            print("    -> SKIP: line item archived")
        else:
            print(f"    -> set override to {targets}")
            todo.append(la)

    if not args.apply:
        print(f"\nDRY RUN — {len(todo)} association(s) would change. Nothing written.")
        return 0
    if not todo:
        print("\nNothing to do.")
        return 0

    bad = 0
    for la in todo:
        la.sizes = [{"width": int(t.split("x")[0]), "height": int(t.split("x")[1]),
                     "isAspectRatio": False} for t in targets]
        lica_svc.updateLineItemCreativeAssociations([la])
        chk = _q(lica_svc, "getLineItemCreativeAssociationsByStatement",
                 "lineItemId = :l AND creativeId = :c",
                 l=la.lineItemId, c=la.creativeId)
        got = _override(chk[0]) if chk else []
        ok = sorted(got) == sorted(targets)
        bad += not ok
        print(f"  LI {la.lineItemId}: override now {got} {'ok' if ok else '!! NOT APPLIED'}")
    print("\nDone." + (f" {bad} failed." if bad else "") +
          " GAM takes ~10 minutes to pick this up.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
