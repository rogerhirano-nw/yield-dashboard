#!/usr/bin/env python3
"""Add one sized Prebid creative to every line item of the Prebid orders.

The two `Newsweek_OpenExchange_Prebid_Display_*` orders carry ~669 PRICE_PRIORITY
line items between them. Each line item lists all twelve display sizes as
creative placeholders, but the creatives associated with them are the eleven
shared 1x1 `Newsweek_Prebib_Display_*` third-party tags — GAM treats a 1x1
third-party creative as the size-agnostic catch-all the Prebid wrapper needs,
and eleven copies is how many concurrent Prebid wins one page can render.

This script creates a creative at an explicit size (default 970x250) carrying
the Prebid Universal Creative snippet, and associates it with every line item
on the given orders. Nothing about the line items changes — the target size is
already in their placeholders, which the script verifies per line item and
refuses to associate where it is missing (GAM would reject the LICA anyway).

Lookup-first and idempotent: an existing creative of the same name is reused
rather than duplicated, and line items already associated with it are skipped,
so a re-run after a partial failure only fills the gaps.

Settings the new creative can't invent — advertiser, SafeFrame compatibility,
SSL override — are copied from a reference creative already serving on these
orders, so the new tag sits in the same configuration as its eleven siblings.

Usage:
    python3 scripts/add_prebid_size_creative.py            # dry run
    python3 scripts/add_prebid_size_creative.py --apply    # create in GAM

    # a different size, or several copies for pages with N such slots
    python3 scripts/add_prebid_size_creative.py --size 728x90 --copies 2
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# --- load .env into os.environ when running locally (no-op in Actions) ---
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
DEFAULT_SNIPPET = "docs/snippets/prebid_universal_creative.html"


def _g(obj, *names):
    """First non-None attribute among names."""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _size_str(sz) -> str | None:
    if sz is None:
        return None
    return f"{getattr(sz, 'width', '?')}x{getattr(sz, 'height', '?')}"


def _page(svc, method, where, version=V, limit=200, **binds):
    """Run a paged PQL query, returning all results."""
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


def _parse_size(s: str) -> tuple[int, int]:
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        raise SystemExit(f"--size must look like 970x250, got {s!r}")


def _norm(s: str) -> str:
    """Whitespace-insensitive form, for comparing a tag GAM echoes back
    against the file — GAM reflows the snippet it stores."""
    return " ".join((s or "").split())


def _creative_names(base: str, copies: int) -> list[str]:
    """One copy keeps the bare name; several get a _1.._N suffix, matching how
    the existing eleven siblings are numbered."""
    if copies == 1:
        return [base]
    return [f"{base}_{i}" for i in range(1, copies + 1)]


def _report_current_use(licas, li_svc, o_svc, cap: int = 200) -> None:
    """Say where a creative is already associated. A creative that is live on
    other orders is not ours to repurpose — the name collision is then a
    naming problem, not a reason to overwrite someone else's tag."""
    li_ids = sorted({int(_g(la, "lineItemId")) for la in licas
                     if _g(la, "lineItemId") is not None})
    if not li_ids:
        print("       currently associated with NO line items")
        return
    print(f"       currently associated with {len(li_ids):,} line item(s)")
    sample = li_ids[:cap]
    lis = _page(li_svc, "getLineItemsByStatement",
                f"id IN ({', '.join(str(i) for i in sample)})")
    order_ids = sorted({int(_g(li, "orderId")) for li in lis
                        if _g(li, "orderId") is not None})
    if not order_ids:
        return
    orders = _page(o_svc, "getOrdersByStatement",
                   f"id IN ({', '.join(str(i) for i in order_ids)})")
    names = {int(_g(o, "id")): _g(o, "name") for o in orders}
    label = "orders" if len(li_ids) <= cap else f"orders (first {cap} LIs)"
    for oid in order_ids:
        print(f"       {label}: {oid}  {names.get(oid, '?')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="create in GAM (default is a dry run)")
    ap.add_argument("--orders", default=",".join(str(o) for o in DEFAULT_ORDERS),
                    help="comma-separated GAM order ids")
    ap.add_argument("--size", default=DEFAULT_SIZE, help="creative size, e.g. 970x250")
    ap.add_argument("--name", default=None,
                    help="creative name (default Newsweek_Prebid_Display_<size>)")
    ap.add_argument("--copies", type=int, default=1,
                    help="how many identical creatives to create and associate "
                         "(one per concurrent slot of this size on a page)")
    ap.add_argument("--snippet", default=DEFAULT_SNIPPET,
                    help="file holding the third-party tag")
    ap.add_argument("--batch", type=int, default=100,
                    help="LICAs per createLineItemCreativeAssociations call")
    ap.add_argument("--allow-snippet-mismatch", action="store_true",
                    help="associate a reused creative whose tag differs from "
                         "the snippet file (default is to refuse)")
    args = ap.parse_args()

    order_ids = [int(o) for o in args.orders.split(",") if o.strip()]
    width, height = _parse_size(args.size)
    size_key = f"{width}x{height}"
    base_name = args.name or f"Newsweek_Prebid_Display_{size_key}"
    if args.copies < 1:
        raise SystemExit("--copies must be at least 1")
    names = _creative_names(base_name, args.copies)

    snippet_path = Path(args.snippet)
    if not snippet_path.is_absolute():
        snippet_path = Path(__file__).resolve().parent.parent / snippet_path
    snippet = snippet_path.read_text().strip()

    gc = GAMClient()
    client = gc._get_soap_client()
    li_svc = client.GetService("LineItemService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
    o_svc = client.GetService("OrderService", version=V)

    mode = "APPLY" if args.apply else "DRY RUN"
    print("=" * 74)
    print(f"ADD {size_key} PREBID CREATIVE   ({mode})")
    print("=" * 74)

    # ---------------- line items ----------------
    targets: list[dict] = []        # line items that can take the creative
    no_placeholder: list[dict] = []  # line items whose placeholders lack the size
    for oid in order_ids:
        order = (_page(o_svc, "getOrdersByStatement", "id = :o", o=oid)
                 or [None])[0]
        if order is None:
            raise SystemExit(f"order {oid} not found")
        lis = _page(li_svc, "getLineItemsByStatement", "orderId = :o", o=oid)
        sized = []
        for li in lis:
            sizes = {
                _size_str(getattr(ph, "size", None))
                for ph in (_g(li, "creativePlaceholders") or [])
            }
            row = {
                "id": int(_g(li, "id")),
                "name": _g(li, "name"),
                "status": str(_g(li, "status")),
                "order_id": oid,
            }
            (sized if size_key in sizes else no_placeholder).append(row)
        targets.extend(sized)
        print(f"\nOrder {oid}  {_g(order, 'name')}")
        print(f"  line items          : {len(lis):,}")
        print(f"  with {size_key} placeholder: {len(sized):,}")

    if no_placeholder:
        print(f"\n  !! {len(no_placeholder):,} line item(s) have no {size_key} "
              f"placeholder and will be SKIPPED — GAM rejects the association:")
        for row in no_placeholder[:10]:
            print(f"       {row['id']}  {row['name']}  ({row['status']})")
        if len(no_placeholder) > 10:
            print(f"       … and {len(no_placeholder) - 10:,} more")
    if not targets:
        print("\nNothing to do — no line item takes this size.")
        return 0

    # ---------------- reference creative ----------------
    # Copy advertiser / SafeFrame / SSL settings off a tag already serving on
    # these line items rather than guessing them.
    ref = None
    first_licas = _page(lica_svc, "getLineItemCreativeAssociationsByStatement",
                        "lineItemId = :l", limit=50, l=targets[0]["id"])
    ref_ids = sorted({int(_g(la, "creativeId")) for la in first_licas
                      if _g(la, "creativeId") is not None})
    if ref_ids:
        ref_cands = _page(cr_svc, "getCreativesByStatement",
                          f"id IN ({', '.join(str(i) for i in ref_ids)})")
        # Prefer a third-party tag — that is what we are cloning the config of.
        ref = next((c for c in ref_cands
                    if type(c).__name__ == "ThirdPartyCreative"), None) \
            or (ref_cands[0] if ref_cands else None)
    if ref is None:
        raise SystemExit("no reference creative found on the first line item — "
                         "cannot copy advertiser / SafeFrame settings")

    advertiser_id = int(_g(ref, "advertiserId"))
    safeframe = bool(_g(ref, "isSafeFrameCompatible"))
    ssl_override = _g(ref, "sslManualOverride")
    print(f"\nReference creative {_g(ref, 'id')}  {_g(ref, 'name')!r}")
    print(f"  type={type(ref).__name__}  size={_size_str(_g(ref, 'size'))}")
    print(f"  advertiserId={advertiser_id}  isSafeFrameCompatible={safeframe}  "
          f"sslManualOverride={ssl_override}")

    # ---------------- creatives to create / reuse ----------------
    existing = {}
    mismatched: list[str] = []
    licas_by_name: dict[str, list] = {}
    for nm in names:
        found = _page(cr_svc, "getCreativesByStatement", "name = :n",
                      limit=5, n=nm)
        if found:
            existing[nm] = found[0]
            licas_by_name[nm] = _page(
                lica_svc, "getLineItemCreativeAssociationsByStatement",
                "creativeId = :c", c=int(_g(found[0], "id")))

    print(f"\nCreative{'s' if len(names) > 1 else ''} ({size_key}, snippet "
          f"{snippet_path.name}, {len(snippet):,} bytes):")
    for nm in names:
        c = existing.get(nm)
        if c is None:
            print(f"  {nm!r}  [will create]")
        else:
            print(f"  {nm!r}  [exists: id={_g(c, 'id')}, "
                  f"size={_size_str(_g(c, 'size'))}]")
            if _size_str(_g(c, "size")) != size_key:
                raise SystemExit(
                    f"existing creative {_g(c, 'id')} named {nm!r} is "
                    f"{_size_str(_g(c, 'size'))}, not {size_key} — refusing to "
                    f"associate it. Pass a different --name."
                )
            # A name match is not a tag match. Reusing a creative whose
            # snippet has drifted from the file would push a stale tag onto
            # every line item, silently.
            live = _norm(_g(c, "snippet", "htmlSnippet") or "")
            if live == _norm(snippet):
                print("    snippet matches the file")
            else:
                mismatched.append(nm)
                print(f"    !! SNIPPET DIFFERS from {snippet_path.name} "
                      f"(GAM {len(live):,} chars vs file {len(_norm(snippet)):,})")
                for label, text in (("GAM ", live), ("file", _norm(snippet))):
                    head = text[:160] + ("…" if len(text) > 160 else "")
                    print(f"       {label}: {head}")
                _report_current_use(licas_by_name.get(nm, []), li_svc, o_svc)

    # ---------------- associations already in place ----------------
    target_ids = {row["id"] for row in targets}
    already: dict[str, set[int]] = {}
    for nm in existing:
        already[nm] = {int(_g(la, "lineItemId"))
                       for la in licas_by_name.get(nm, [])
                       if _g(la, "lineItemId") is not None} & target_ids

    to_link = {nm: sorted(target_ids - already.get(nm, set())) for nm in names}
    total_links = sum(len(v) for v in to_link.values())
    print(f"\nAssociations across {len(target_ids):,} line items:")
    for nm in names:
        done = len(already.get(nm, set()))
        print(f"  {nm!r}: {len(to_link[nm]):,} to create"
              + (f", {done:,} already associated" if done else ""))
    print(f"  total new LICAs: {total_links:,}")

    blocked = bool(mismatched) and not args.allow_snippet_mismatch
    if blocked:
        print(f"\nBLOCKER: {len(mismatched)} reused creative(s) carry a tag "
              f"that is not {snippet_path.name}: {', '.join(mismatched)}.")
        print("Associating one would push a stale tag onto every line item. "
              "Reconcile the creative in GAM, point --snippet at the tag that "
              "is actually live, or pass --allow-snippet-mismatch if the "
              "difference is known and intended.")

    if not args.apply:
        # A dry run reports; it does not fail. The blocker above is the
        # finding, not an error in the run that found it.
        print("\nDRY RUN — nothing was written."
              + ("" if blocked else " Re-run with --apply."))
        return 0
    if blocked:
        print("\nRefusing to write. Nothing was changed.")
        return 1
    if total_links == 0 and all(nm in existing for nm in names):
        print("\nNothing to do — creatives exist and every line item is linked.")
        return 0

    # ---------------- apply ----------------
    for nm in names:
        c = existing.get(nm)
        if c is None:
            c = cr_svc.createCreatives([{
                "xsi_type": "ThirdPartyCreative",
                "name": nm,
                "advertiserId": advertiser_id,
                "size": {"width": width, "height": height, "isAspectRatio": False},
                "snippet": snippet,
                "isSafeFrameCompatible": safeframe,
                **({"sslManualOverride": ssl_override} if ssl_override else {}),
            }])[0]
            existing[nm] = c
            print(f"\ncreated creative {_g(c, 'id')}  {nm!r}")
        cid = int(_g(c, "id"))

        made = failed = 0
        for batch in _chunks(to_link[nm], args.batch):
            specs = [{"lineItemId": lid, "creativeId": cid} for lid in batch]
            try:
                made += len(lica_svc.createLineItemCreativeAssociations(specs) or [])
            except Exception as exc:  # one bad row fails the whole batch
                print(f"  batch of {len(specs)} failed ({exc}) — retrying singly")
                for spec in specs:
                    try:
                        lica_svc.createLineItemCreativeAssociations([spec])
                        made += 1
                    except Exception as exc2:
                        if "ALREADY_EXISTS" in str(exc2):
                            continue
                        failed += 1
                        print(f"    LI {spec['lineItemId']}: {exc2}")
        print(f"  {nm!r}: {made:,} associations created"
              + (f", {failed:,} FAILED" if failed else ""))

    print("\nDone. GAM takes ~10 minutes to pick up new associations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
