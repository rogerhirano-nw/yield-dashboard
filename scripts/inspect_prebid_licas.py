#!/usr/bin/env python3
"""Read-only dump of the line-item/creative associations on an order.

Written to answer one question the Screenshots-style pull could not: when a
1x1 third-party creative is associated with a line item that lists twelve
display sizes, WHICH sizes does it actually serve into, and is that recorded
per association?

GAM records it on the association, not the creative:
`LineItemCreativeAssociation.sizes` is the creative-size override, and it is
the only place a 1x1 catch-all's eligible sizes are expressed. Removing a
size from one creative's rotation therefore means editing that list — not the
line item's `creativePlaceholders`, which gate the size for EVERY creative on
the line, the new sized one included.

Dumps every field of every association on a sample of line items so the shape
is visible rather than assumed. Writes nothing.

Usage:
    python3 scripts/inspect_prebid_licas.py --orders 3670858241,3671186123
    python3 scripts/inspect_prebid_licas.py --line-items 6874687800
"""
from __future__ import annotations

import argparse
import os
import sys
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


def _sizes(v) -> str:
    """The `sizes` override, however zeep hands it back."""
    if v is None:
        return "None"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_size_str(s) or "?" for s in v) + "]"
    return str(v)


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", default="3670858241,3671186123")
    ap.add_argument("--line-items", default="",
                    help="specific line item ids; overrides --orders sampling")
    ap.add_argument("--sample", type=int, default=2,
                    help="line items to dump per order")
    ap.add_argument("--size", default="970x250",
                    help="size to tally eligibility for")
    ap.add_argument("--census", action="store_true",
                    help="tally the size override across EVERY line item on "
                         "the orders, not just the sampled ones")
    args = ap.parse_args()

    gc = GAMClient()
    client = gc._get_soap_client()
    li_svc = client.GetService("LineItemService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)

    if args.line_items:
        li_ids = [int(x) for x in args.line_items.split(",") if x.strip()]
    else:
        li_ids = []
        for oid in (int(o) for o in args.orders.split(",") if o.strip()):
            lis = _page(li_svc, "getLineItemsByStatement",
                        "orderId = :o AND isArchived = false", o=oid)
            li_ids.extend(int(_g(li, "id")) for li in lis[:args.sample])

    for lid in li_ids:
        li = (_page(li_svc, "getLineItemsByStatement", "id = :i", i=lid)
              or [None])[0]
        print()
        print("=" * 74)
        print(f"LINE ITEM {lid}  {_g(li, 'name') if li else '?'}")
        placeholders = [_size_str(getattr(ph, "size", None))
                        for ph in (_g(li, "creativePlaceholders") or [])]
        print(f"  creativePlaceholders: {sorted(set(placeholders))}")
        print("=" * 74)

        licas = _page(lica_svc, "getLineItemCreativeAssociationsByStatement",
                      "lineItemId = :l", l=lid)
        cids = sorted({int(_g(la, "creativeId")) for la in licas
                       if _g(la, "creativeId") is not None})
        creatives = {}
        if cids:
            for c in _page(cr_svc, "getCreativesByStatement",
                           f"id IN ({', '.join(str(i) for i in cids)})"):
                creatives[int(_g(c, "id"))] = c

        for la in sorted(licas, key=lambda x: int(_g(x, "creativeId") or 0)):
            cid = int(_g(la, "creativeId"))
            c = creatives.get(cid)
            print(f"\n  creative {cid}  {_g(c, 'name') if c else '?'}")
            print(f"    creative size : {_size_str(_g(c, 'size')) if c else '?'}")
            print(f"    lica.sizes    : {_sizes(_g(la, 'sizes'))}")
            print(f"    lica.status   : {_g(la, 'status')}")
            for fld in ("manualCreativeRotationWeight", "targetingName",
                        "startDateTime", "endDateTime", "destinationUrl"):
                val = getattr(la, fld, None)
                if val is not None:
                    print(f"    {fld:14s}: {val}")

        # The tally that decides the job: how many creatives can serve --size
        # on this line, and by which route.
        eligible = []
        for la in licas:
            cid = int(_g(la, "creativeId"))
            c = creatives.get(cid)
            csize = _size_str(_g(c, "size")) if c else None
            override = _g(la, "sizes")
            override_sizes = ([_size_str(s) for s in override]
                              if isinstance(override, (list, tuple)) else [])
            if csize == args.size:
                eligible.append((cid, "exact size"))
            elif args.size in override_sizes:
                eligible.append((cid, "sizes override"))
            elif csize == "1x1" and not override_sizes:
                eligible.append((cid, "1x1 catch-all, no override"))
        print(f"\n  --> creatives able to serve {args.size}: {len(eligible)}")
        for cid, why in eligible:
            nm = _g(creatives.get(cid), "name") if creatives.get(cid) else "?"
            print(f"       {cid}  {nm}  ({why})")

    if args.census:
        print()
        print("=" * 74)
        print(f"CENSUS — every non-archived line item, override containing "
              f"{args.size}")
        print("=" * 74)
        import collections
        carries = collections.Counter()      # creative -> LICAs listing --size
        would_empty = collections.defaultdict(list)  # creative -> [line item]
        native = collections.Counter()       # creative -> LICAs at exact size
        total_lis = 0
        cre_names: dict[int, str] = {}
        for oid in (int(o) for o in args.orders.split(",") if o.strip()):
            lis = _page(li_svc, "getLineItemsByStatement",
                        "orderId = :o AND isArchived = false", o=oid)
            total_lis += len(lis)
            ids = [int(_g(li, "id")) for li in lis]
            # One paged LICA query per order beats 669 per-line-item queries.
            licas = []
            for chunk in [ids[i:i + 300] for i in range(0, len(ids), 300)]:
                licas.extend(_page(
                    lica_svc, "getLineItemCreativeAssociationsByStatement",
                    f"lineItemId IN ({', '.join(str(i) for i in chunk)})"))
            cids = sorted({int(_g(la, "creativeId")) for la in licas
                           if _g(la, "creativeId") is not None})
            for chunk in [cids[i:i + 200] for i in range(0, len(cids), 200)]:
                for c in _page(cr_svc, "getCreativesByStatement",
                               f"id IN ({', '.join(str(i) for i in chunk)})"):
                    cre_names[int(_g(c, "id"))] = (
                        f"{_g(c, 'name')} [{_size_str(_g(c, 'size'))}]")
            for la in licas:
                cid = int(_g(la, "creativeId"))
                ov = _g(la, "sizes")
                ov_sizes = ([_size_str(s_) for s_ in ov]
                            if isinstance(ov, (list, tuple)) else [])
                if args.size in ov_sizes:
                    carries[cid] += 1
                    if len(ov_sizes) == 1:
                        would_empty[cid].append(int(_g(la, "lineItemId")))
                elif not ov_sizes and cre_names.get(cid, "").endswith(
                        f"[{args.size}]"):
                    native[cid] += 1
        print(f"  line items scanned: {total_lis:,}")
        print(f"\n  associations whose override LISTS {args.size} "
              f"(these are what a removal would edit):")
        for cid, n in carries.most_common():
            print(f"    {cid}  {cre_names.get(cid, '?')}: {n:,}")
        print(f"    TOTAL: {sum(carries.values()):,}")
        if native:
            print(f"\n  associations serving {args.size} natively "
                  f"(no override — untouched by a removal):")
            for cid, n in native.most_common():
                print(f"    {cid}  {cre_names.get(cid, '?')}: {n:,}")
        if would_empty:
            print(f"\n  !! {sum(len(v) for v in would_empty.values()):,} "
                  f"association(s) list {args.size} and NOTHING ELSE — removing "
                  f"it would empty the override and drop them back to the "
                  f"creative's native size. These need deactivating, not "
                  f"editing:")
            for cid, lids in would_empty.items():
                print(f"    {cid}  {cre_names.get(cid, '?')}: {len(lids):,} "
                      f"(e.g. line item {lids[0]})")
        else:
            print(f"\n  no association lists {args.size} alone — a removal "
                  f"never empties an override.")

    print("\nRead-only — nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
