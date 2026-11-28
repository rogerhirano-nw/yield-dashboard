#!/usr/bin/env python3
"""Leave exactly one creative eligible to serve a given size on an order.

The Prebid orders associate eleven 1x1 third-party catch-alls with every line
item, each carrying a `LineItemCreativeAssociation.sizes` override that lists
the sizes it may serve into — 970x250 among them. Once a purpose-built 970x250
creative exists, those eleven should stop competing for that slot.

The size override is the only lever that does this precisely. The line item's
`creativePlaceholders` gate the size for EVERY creative on the line, so
removing 970x250 there would take the new sized creative down too;
deactivating the eleven associations would remove them from all twelve sizes,
not one. So this edits `sizes` per association, and nothing else.

Three things it refuses to do, each of which would be silent damage:
  - empty an override (an association whose `sizes` becomes [] falls back to
    the creative's native size — a 1x1 catch-all would start serving at 1x1)
  - leave a line item with NO creative able to serve the size at all
  - trust that the write landed: `sizes` may well be server-side read-only, so
    an apply run edits ONE association first, re-reads it, and aborts if the
    value did not actually change

Reversible: `--mode add` puts the size back on the same associations.

Usage:
    python3 scripts/restrict_size_to_creative.py                    # dry run
    python3 scripts/restrict_size_to_creative.py --apply
    python3 scripts/restrict_size_to_creative.py --mode add --apply  # roll back
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

_envp = Path(__file__).resolve().parent.parent / ".env"
if _envp.exists():
    for _line in _envp.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient  # noqa: E402
from googleads import ad_manager  # noqa: E402

V = "v202605"

DEFAULT_ORDERS = [3670858241, 3671186123]
DEFAULT_SIZE = "970x250"
DEFAULT_KEEP = "Newsweek_Prebid_Display_970x250_UC"


def _g(obj, *names):
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _size_str(sz) -> str | None:
    if sz is None:
        return None
    return f"{getattr(sz, 'width', '?')}x{getattr(sz, 'height', '?')}"


def _size_obj(s: str) -> dict:
    w, h = s.lower().split("x")
    return {"width": int(w), "height": int(h), "isAspectRatio": False}


def _override(la) -> list[str]:
    v = _g(la, "sizes")
    if not isinstance(v, (list, tuple)):
        return []
    return [_size_str(s) for s in v]


def _page(svc, method, where, version=V, limit=200, **binds):
    sb = ad_manager.StatementBuilder(version=version).Where(where).Limit(limit)
    for k, v in binds.items():
        sb = sb.WithBindVariable(k, v)
    out = []
    while True:
        resp = getattr(svc, method)(sb.ToStatement())
        results = list(getattr(resp, "results", []) or [])
        out.extend(results)
        if not results:
            break
        sb.offset += sb.limit
        if sb.offset >= getattr(resp, "totalResultSetSize", 0):
            break
    return out


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write to GAM (default is a dry run)")
    ap.add_argument("--mode", choices=("remove", "add"), default="remove",
                    help="remove the size from the other creatives' overrides, "
                         "or add it back (the rollback)")
    ap.add_argument("--orders", default=",".join(str(o) for o in DEFAULT_ORDERS))
    ap.add_argument("--size", default=DEFAULT_SIZE)
    ap.add_argument("--keep", default=DEFAULT_KEEP,
                    help="creative name or id left eligible for the size; "
                         "its associations are never touched")
    ap.add_argument("--batch", type=int, default=100,
                    help="associations per update call")
    args = ap.parse_args()

    size = args.size
    gc = GAMClient()
    client = gc._get_soap_client()
    li_svc = client.GetService("LineItemService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)

    mode = "APPLY" if args.apply else "DRY RUN"
    print("=" * 74)
    print(f"{args.mode.upper()} {size} on every creative except {args.keep!r}"
          f"   ({mode})")
    print("=" * 74)

    # ---------------- gather ----------------
    lica_by_li: dict[int, list] = defaultdict(list)
    creatives: dict[int, object] = {}
    for oid in (int(o) for o in args.orders.split(",") if o.strip()):
        lis = _page(li_svc, "getLineItemsByStatement",
                    "orderId = :o AND isArchived = false", o=oid)
        ids = [int(_g(li, "id")) for li in lis]
        licas = []
        for chunk in _chunks(ids, 300):
            licas.extend(_page(
                lica_svc, "getLineItemCreativeAssociationsByStatement",
                f"lineItemId IN ({', '.join(str(i) for i in chunk)})"))
        for la in licas:
            lica_by_li[int(_g(la, "lineItemId"))].append(la)
        cids = sorted({int(_g(la, "creativeId")) for la in licas
                       if _g(la, "creativeId") is not None})
        for chunk in _chunks(cids, 200):
            for c in _page(cr_svc, "getCreativesByStatement",
                           f"id IN ({', '.join(str(i) for i in chunk)})"):
                creatives[int(_g(c, "id"))] = c
        print(f"\nOrder {oid}: {len(lis):,} line items, {len(licas):,} associations")

    keep_ids = {cid for cid, c in creatives.items()
                if str(cid) == args.keep or _g(c, "name") == args.keep}
    if not keep_ids:
        raise SystemExit(f"--keep {args.keep!r} matches no creative on these "
                         f"orders; refusing to strip every creative off {size}")
    print(f"\nKeeping eligible: " + ", ".join(
        f"{cid} {_g(creatives[cid], 'name')!r} "
        f"[{_size_str(_g(creatives[cid], 'size'))}]" for cid in sorted(keep_ids)))

    # ---------------- plan ----------------
    edits: list[tuple[object, list[str]]] = []
    per_creative = Counter()
    refused_empty: list[tuple[int, int]] = []
    lines_left_uncovered: list[int] = []

    for lid, licas in lica_by_li.items():
        # Who can serve `size` on this line once the edit lands? A line left
        # with nobody would stop filling the size entirely.
        survivors = 0
        for la in licas:
            cid = int(_g(la, "creativeId"))
            c = creatives.get(cid)
            csize = _size_str(_g(c, "size")) if c else None
            ov = _override(la)
            if cid in keep_ids:
                if csize == size or size in ov or not ov:
                    survivors += 1
                continue
            if csize == size:
                survivors += 1  # exact-size creatives are not override-driven

        for la in licas:
            cid = int(_g(la, "creativeId"))
            if cid in keep_ids:
                continue
            ov = _override(la)
            if args.mode == "remove":
                if size not in ov:
                    continue
                new = [s for s in ov if s != size]
                if not new:
                    refused_empty.append((lid, cid))
                    continue
            else:
                if size in ov or not ov:
                    continue
                new = ov + [size]
            edits.append((la, new))
            per_creative[cid] += 1

        if args.mode == "remove" and survivors == 0:
            lines_left_uncovered.append(lid)

    print(f"\nAssociations to {args.mode}:")
    for cid, n in per_creative.most_common():
        c = creatives.get(cid)
        print(f"  {cid}  {_g(c, 'name') if c else '?'} "
              f"[{_size_str(_g(c, 'size')) if c else '?'}]: {n:,}")
    print(f"  TOTAL: {len(edits):,}")

    if edits:
        la, new = edits[0]
        print(f"\nSample — line item {_g(la, 'lineItemId')}, creative "
              f"{_g(la, 'creativeId')}:")
        print(f"  before: {_override(la)}")
        print(f"  after : {new}")

    if refused_empty:
        print(f"\n  !! {len(refused_empty):,} association(s) list {size} and "
              f"nothing else — SKIPPED, since emptying the override drops them "
              f"to the creative's native size:")
        for lid, cid in refused_empty[:5]:
            print(f"     line item {lid}, creative {cid}")

    if lines_left_uncovered:
        print(f"\nREFUSING: {len(lines_left_uncovered):,} line item(s) would be "
              f"left with NO creative able to serve {size}, which would stop "
              f"the size filling on them entirely:")
        for lid in lines_left_uncovered[:5]:
            print(f"     {lid}")
        print("Associate the keeper with those line items first.")
        return 1

    if not args.apply:
        print(f"\nDRY RUN — nothing was written. Re-run with --apply.")
        return 0
    if not edits:
        print("\nNothing to do.")
        return 0

    # ---------------- apply ----------------
    # `sizes` may be server-side read-only. Prove one write lands before
    # sending the other seven thousand.
    canary_la, canary_new = edits[0]
    canary_la.sizes = [_size_obj(s) for s in canary_new]
    lica_svc.updateLineItemCreativeAssociations([canary_la])
    check = _page(lica_svc, "getLineItemCreativeAssociationsByStatement",
                  "lineItemId = :l AND creativeId = :c",
                  l=int(_g(canary_la, "lineItemId")),
                  c=int(_g(canary_la, "creativeId")))
    got = sorted(_override(check[0])) if check else []
    if got != sorted(canary_new):
        print(f"\nCANARY FAILED — the update was accepted but the value did "
              f"not change.\n  wanted: {sorted(canary_new)}\n  got   : {got}")
        print("`sizes` is not writable this way. Nothing else was sent; the "
              "one edited association is unchanged in GAM.")
        return 1
    print(f"\ncanary ok — line item {_g(canary_la, 'lineItemId')}, creative "
          f"{_g(canary_la, 'creativeId')} now {got}")

    done = 1
    failed = 0
    for batch in _chunks(edits[1:], args.batch):
        objs = []
        for la, new in batch:
            la.sizes = [_size_obj(s) for s in new]
            objs.append(la)
        try:
            lica_svc.updateLineItemCreativeAssociations(objs)
            done += len(objs)
        except Exception as exc:
            print(f"  batch of {len(objs)} failed ({exc}) — retrying singly")
            for la in objs:
                try:
                    lica_svc.updateLineItemCreativeAssociations([la])
                    done += 1
                except Exception as exc2:
                    failed += 1
                    print(f"    LI {_g(la, 'lineItemId')} / creative "
                          f"{_g(la, 'creativeId')}: {exc2}")
    print(f"\n{done:,} association(s) updated"
          + (f", {failed:,} FAILED" if failed else ""))
    print("GAM takes ~10 minutes to pick this up. Roll back with --mode add.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
