#!/usr/bin/env python3
"""Read-only pull of everything needed to build a Screenshots Document.

Works for ANY GAM order — nothing here is campaign-specific. Given an order id
(and optionally a line item id to highlight), dumps:
  - order: name, advertiser, trafficker, flight dates, status
  - each line item: name, status, type, flight, sizes, goal, targeted ad units
  - each creative on those LIs: name, type, size, destination URL, preview URL,
    and the image asset URL when GAM hosts the asset

Writes JSON to --out and prints a human-readable summary. With --markdown it
also writes the Screenshots Document body — lead, campaign table, per-size
capture checklist, and whatever is actually blocking capture — derived from
what the pull found, ready to paste into the doc. No writes to GAM.

Order/line item come from --order/--line-item or, for the Actions workflow,
SCREENSHOTS_ORDER_ID / SCREENSHOTS_LINE_ITEM_ID.

Usage:
  python scripts/pull_screenshots_source.py --order 4198147401 \
      [--line-item 7432006947] [--markdown doc.md]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

# --- load .env into os.environ when running locally (no-op in Actions) ---
envp = Path(__file__).resolve().parent.parent / ".env"
if envp.exists():
    for line in envp.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gam_client import GAMClient  # noqa: E402
from googleads import ad_manager  # noqa: E402

V = "v202605"


def _g(obj, *names):
    """First non-None attribute among names."""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _date(d):
    if d is None:
        return None
    dd = getattr(d, "date", d)
    y, m, day = getattr(dd, "year", None), getattr(dd, "month", None), getattr(dd, "day", None)
    if y is None:
        return str(d)
    s = f"{y:04d}-{m:02d}-{day:02d}"
    hh, mm = getattr(d, "hour", None), getattr(d, "minute", None)
    if hh is not None:
        s += f" {hh:02d}:{mm or 0:02d}"
    return s


def _size(sz):
    if sz is None:
        return None
    return f"{getattr(sz, 'width', '?')}x{getattr(sz, 'height', '?')}"


def _page(svc, method, where, version=V, limit=200):
    """Run a paged PQL query, returning all results."""
    sb = ad_manager.StatementBuilder(version=version).Where(where).Limit(limit)
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


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one}" if n == 1 else f"{n:,} {many or one + 's'}"


def _pretty_date(s: str | None) -> str:
    """'2026-09-22 00:00' -> '22 Sep 2026'."""
    if not s:
        return "—"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return s
    y, mo, d = (int(g) for g in m.groups())
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{d} {months[mo - 1]} {y}"


def _iso(s: str | None) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s or "")
    return m.group(1) if m else None


def _shot_rows(sizes: list[str]) -> list[tuple[str, str, str]]:
    """(size, device, shot) rows — one in-context shot per size on desktop;
    sizes narrow enough to run on a phone get a mobile one too.

    In-context only. The close crop was dropped from the deliverable (Roger,
    22 Sep 2026): the proof is the ad sitting in the page, and a crop of the
    creative is a picture of the asset the client already has. The capture
    script still writes a `_crop` file per shot — useful for checking the
    creative rendered legibly — it just does not earn a slide."""
    rows: list[tuple[str, str, str]] = []
    for sz in dict.fromkeys(sizes):  # de-dupe, keep order
        try:
            width = int(sz.split("x")[0])
        except (ValueError, IndexError):
            width = 0
        rows.append((sz, "Desktop", "Full page, ad in context"))
        if 0 < width <= 400:
            rows.append((sz, "Mobile", "Full page, ad in context"))
    return rows


def _seller(order_name: str | None) -> str:
    """The AE who sold it: the last token of the Newsweek naming convention
    (`..._Team-INTL_AShah`), resolved through settings.json's `ae_names` so the
    deck shows "Amit Shah" rather than "AShah". Case variants live in that map
    (AShah / Ashah), so the lookup tries the token as-is first."""
    parts = (order_name or "").split("_")
    if len(parts) < 2 or parts[0] != "Newsweek":
        return "—"
    token = parts[-1].strip()
    if not token:
        return "—"
    try:
        import json
        names = json.loads(
            (REPO_ROOT / "settings.json").read_text()).get("ae_names") or {}
    except Exception:
        names = {}
    return (names.get(token)
            or names.get(token.title())
            or names.get(token.capitalize())
            or token)


# Vertical token (index 2 of the Newsweek naming convention) -> the article
# category slug to shoot against. `cat`/`sitecat` on a newsweek.com page is
# "nwus-" + the primary category slug, hyphens as underscores.
_VERTICAL_SLUGS = {
    "health": ["health"],
    "finance": ["personal_finance", "business"],
    "auto": ["autos"],
    "automotive": ["autos"],
    "tech": ["technology"],
    "technology": ["technology"],
    "retail": ["business"],
    "travel": ["travel"],
    "sports": ["sports"],
    "entertainment": ["culture", "entertainment"],
    "politics": ["politics"],
    "education": ["education"],
}


def _vertical(order_name: str | None) -> tuple[str | None, list[str]]:
    """(vertical, candidate category slugs) from the order name's token 2.
    Non-convention names give (None, [])."""
    parts = (order_name or "").split("_")
    if len(parts) < 3 or parts[0] != "Newsweek":
        return None, []
    v = parts[2].strip()
    if not v or v.upper() in {"NA", "N/A"}:
        return None, []
    return v, _VERTICAL_SLUGS.get(v.lower().replace("-", ""), [])


def build_markdown(payload: dict, today: date) -> str:
    """The Screenshots Document body, derived from what the pull found."""
    o = payload["order"]
    lis = payload["line_items"]
    creatives = payload["creatives_by_line_item"]
    # The highlighted line, else the first one.
    li = next((x for x in lis if x.get("is_highlight")), lis[0] if lis else None)
    if li is None:
        return f"# Screenshots — {o['name']}\n\nOrder {o['id']} has no line items.\n"

    n_creatives = sum(len(v) for v in creatives.values())
    start_iso, end_iso = _iso(li["start"]), _iso(li["end"])
    not_started = bool(start_iso and start_iso > today.isoformat())
    ended = bool(end_iso and end_iso < today.isoformat())
    sizes = li["sizes"]
    units = ", ".join(
        f"`{u['ad_unit']}` ({u['ad_unit_id']})"
        + (" descendants included" if u["include_descendants"] else "")
        for u in li["targeted_ad_units"]
    ) or "—"
    ros = any(u["include_descendants"] for u in li["targeted_ad_units"])
    vertical, cat_slugs = _vertical(o.get("name"))

    blockers = []
    if n_creatives == 0:
        blockers.append(
            "- [ ] **Attach creatives.** The line has zero creative "
            "associations, which is why GAM shows it "
            f"{li['status']} rather than Ready. "
            f"{'Both sizes are' if len(sizes) == 2 else 'All sizes are'} "
            f"needed: {', '.join(sizes)}."
        )
    if not_started:
        blockers.append(
            f"- [ ] **Wait for the flight to open.** Nothing serves before "
            f"{_pretty_date(li['start'])}. First delivery data lands the "
            f"morning after."
        )

    if blockers:
        lead = (
            f"No screenshots can be taken yet: "
            + " and ".join(
                filter(None, [
                    f"the flight starts {_pretty_date(li['start'])}" if not_started else "",
                    f"line item {li['id']} is {li['status']} with zero creatives attached"
                    if n_creatives == 0 else "",
                ])
            )
            + ". Everything below is verified against GAM and is ready to fill "
              "the moment the line delivers."
        )
        shots_body = (
            "Empty until the line delivers. The shots above drop in here, each "
            "captioned with URL, date, time and size."
        )
    else:
        lead = (
            f"Line item {li['id']} is {li['status']} and "
            f"{'ran' if ended else 'is running'} "
            f"{_pretty_date(li['start'])} → {_pretty_date(li['end'])}. "
            f"{_plural(n_creatives, 'creative')} attached."
        )
        shots_body = "Captured shots below, each captioned with URL, date, time and size."

    rows = _shot_rows(sizes)
    shot_tbl = "\n".join(
        f"| {i} | {sz} | Article page, `inarticle` slot | {dev} | {shot} |"
        for i, (sz, dev, shot) in enumerate(rows, 1)
    )
    if ros:
        shot_tbl += (
            f"\n| {len(rows) + 1} | Either | Homepage or section page | Desktop "
            "| Only if the line serves there |"
        )

    nw_note = ""
    if "[nw]" in (o.get("advertiser") or ""):
        nw_note = (
            f"\n\nOne thing worth knowing separately: the advertiser is named "
            f"`{o['advertiser']}`, and the yield dashboard's Direct table excludes "
            "any line item whose advertiser contains `[nw]`. This campaign will "
            "not appear there while it runs unless the advertiser is renamed or "
            "the exclusion is narrowed."
        )

    if vertical:
        topic = (
            f"the order's vertical is **{vertical}**, so pick a "
            f"{vertical.lower()} story"
        )
        slug_hint = (
            " Those pages carry `cat` = "
            + " or ".join(f"`nwus-{s}`" for s in cat_slugs) + "."
        ) if cat_slugs else ""
    else:
        topic = (
            "the order name carries no vertical token, so take the category "
            "from the client's own business"
        )
        slug_hint = ""

    page_choice = (
        f"### Picking the page\n\n"
        f"The article has to clear two tests, both of them before the shot is "
        f"taken.\n\n"
        f"**1. In the client's industry.** {topic[0].upper()}{topic[1:]}, not "
        f"whatever article happens to be open. An ad shot beside unrelated "
        f"content reads as careless, and the client notices.{slug_hint} Verify "
        f"on the page: the `cat` / `sitecat` GPT key-value is `nwus-` plus the "
        f"primary category slug, hyphens as underscores. Do not trust the section "
        f"listing: Newsweek cross-posts editorially, so plenty of articles under "
        f"/health carry a different `cat` (a real one seen 21 Sep 2026 read "
        f"`nwus-family_parenting`).\n\n"
        f"**2. Brand safe.** Industry-relevant is not enough \u2014 a malpractice "
        f"suit, an outbreak, a lawsuit or a death story is on-topic and still "
        f"the wrong page to hand a client. Newsweek classifies this itself, so "
        f"read it off the page rather than judging by the headline: the `brandsafe` "
        f"GPT key-value must be `y` and `adexclusion` must carry no "
        f"`brand_safety` label. A failing page reads `brandsafe: n` with "
        f"`adexclusion: generic_brand_safety` \u2014 pick another page, do not "
        f"shoot it and crop around the headline. Other `adexclusion` labels "
        f"(`nopassfq` was sitewide on 22 Sep 2026) are inventory hygiene, not "
        f"a verdict on the content, and do not disqualify the page. "
        f"(`ABS` / `CBS` / `BSC` and Proximic `vnd_prx_segments` are opaque "
        f"segment-id lists, not a pass/fail \u2014 `brandsafe` is the flag.)"
    )
    if ros:
        page_choice += (
            "\n\nThis line is run-of-site with no contextual targeting, so the "
            "page is a presentation choice for this document, not something "
            "the trafficking guarantees. Say so if anyone reads the shot as "
            "proof of contextual placement."
        )

    goal = (
        f"{li['goal_units']:,} impressions, "
        f"{(li['goal_type'] or '').lower()}, {li['cost_type']}"
        if li["goal_units"] else "—"
    )

    return f"""# Screenshots — {o['name']}

{lead}

## Campaign

{_plural(len(lis), 'line item')}\
{', run of site' if ros else ''}, {_plural(len(sizes), 'size')}. \
Pulled from GAM on {_pretty_date(today.isoformat())}.

| Field | Value |
| --- | --- |
| Advertiser | {o['advertiser']} ({o['advertiser_id']}) |
| Order | {o['id']} — status {o['status']} |
| Line item | {li['id']} — status **{li['status']}**, type {li['line_item_type']} |
| Flight | {_pretty_date(li['start'])} → {_pretty_date(li['end'])} ET |
| Goal | {goal} |
| Sizes | {', '.join(sizes) or '—'} |
| Targeting | {units}{' — run of site' if ros else ''} |
| PO / IO | {o['po_number'] or '—'} |
| Seller | {_seller(o.get('name'))} |

GAM end times are network-tz instants: the line ends at 23:59 ET on its last \
day, which reads as the next day in UTC.

## What gets captured

{_plural(len(rows) + (1 if ros else 0), 'shot')}: each size in context on \
desktop{', and on mobile where the size fits a phone slot' if any(d == 'Mobile' for _, d, _ in rows) else ''}.\
{' Run-of-site targeting means the line can serve anywhere under the targeted unit, so an article page is the shot to lead with.' if ros else ''}

| # | Size | Where | Device | Shot |
| --- | --- | --- | --- | --- |
{shot_tbl}

Each shot carries the URL, the date and time, and the size. Article content \
slots on newsweek.com are lazily defined, so the page has to be scrolled to \
the slot before the ad exists in GPT — a screenshot taken at page load will \
show an empty well.

{page_choice}

## Before capture

{chr(10).join(blockers) if blockers else 'Nothing blocking — the line is live with creatives attached.'}{nw_note}

## Screenshots

{shots_body}

Capture runs off the `capture_screenshots.yml` workflow — SOAP `getPreviewUrl` \
for each trafficked creative, then headless Chromium on a live newsweek.com \
article page at desktop and mobile, scrolling the lazy slot into view before \
the shot. It gates the page on both tests above before shooting anything, and \
skips any creative too wide for the viewport. Images come back as workflow \
artifacts.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", default=os.environ.get("SCREENSHOTS_ORDER_ID"))
    ap.add_argument("--line-item",
                    default=os.environ.get("SCREENSHOTS_LINE_ITEM_ID") or None)
    ap.add_argument("--out", default="/tmp/screenshots_source.json")
    ap.add_argument("--markdown", default=None,
                    help="also write the Screenshots Document body here")
    args = ap.parse_args()
    if not args.order and not args.line_item:
        ap.error("pass --order or --line-item "
                 "(or set SCREENSHOTS_ORDER_ID / SCREENSHOTS_LINE_ITEM_ID)")

    gc = GAMClient()
    client = gc._get_soap_client()

    # A line item id alone is the common case — it is what a GAM deep link
    # carries. Resolve its order rather than making the caller look it up.
    if not args.order:
        li_svc = client.GetService("LineItemService", version=V)
        found = _page(li_svc, "getLineItemsByStatement",
                      f"id = {int(args.line_item)}")
        if not found:
            print(f"!! line item {args.line_item} not found", file=sys.stderr)
            return 1
        args.order = str(_g(found[0], "orderId"))
        print(f"line item {args.line_item} -> order {args.order}")

    # ---------------- order ----------------
    o_svc = client.GetService("OrderService", version=V)
    orders = _page(o_svc, "getOrdersByStatement", f"id = {int(args.order)}")
    if not orders:
        print(f"!! order {args.order} not found", file=sys.stderr)
        return 1
    o = orders[0]

    company_id = _g(o, "advertiserId")
    advertiser = None
    if company_id:
        c_svc = client.GetService("CompanyService", version=V)
        cs = _page(c_svc, "getCompaniesByStatement", f"id = {int(company_id)}")
        if cs:
            advertiser = getattr(cs[0], "name", None)

    order = {
        "id": str(_g(o, "id")),
        "name": _g(o, "name"),
        "status": str(_g(o, "status")),
        "advertiser_id": str(company_id) if company_id else None,
        "advertiser": advertiser,
        "start": _date(_g(o, "startDateTime")),
        "end": _date(_g(o, "endDateTime")),
        "is_archived": _g(o, "isArchived"),
        "notes": _g(o, "notes"),
        "po_number": _g(o, "poNumber"),
    }

    # ---------------- line items ----------------
    li_svc = client.GetService("LineItemService", version=V)
    lis = _page(li_svc, "getLineItemsByStatement", f"orderId = {int(args.order)}")

    # ad unit id -> name, resolved once for every ad unit we touch
    au_ids: set[int] = set()
    for li in lis:
        inv = _g(_g(li, "targeting"), "inventoryTargeting")
        for au in (getattr(inv, "targetedAdUnits", None) or []):
            aid = getattr(au, "adUnitId", None)
            if aid:
                au_ids.add(int(aid))
    au_names: dict[int, str] = {}
    if au_ids:
        au_svc = client.GetService("InventoryService", version=V)
        ids_str = ", ".join(str(i) for i in sorted(au_ids))
        for au in _page(au_svc, "getAdUnitsByStatement", f"id IN ({ids_str})"):
            aid = getattr(au, "id", None)
            if aid:
                au_names[int(aid)] = getattr(au, "adUnitCode", None) or getattr(au, "name", "")

    line_items = []
    for li in lis:
        inv = _g(_g(li, "targeting"), "inventoryTargeting")
        units = []
        for au in (getattr(inv, "targetedAdUnits", None) or []):
            aid = getattr(au, "adUnitId", None)
            units.append({
                "ad_unit_id": str(aid) if aid else None,
                "ad_unit": au_names.get(int(aid)) if aid else None,
                "include_descendants": getattr(au, "includeDescendants", None),
            })
        goal = _g(li, "primaryGoal")
        line_items.append({
            "id": str(_g(li, "id")),
            "name": _g(li, "name"),
            "status": str(_g(li, "status")),
            "line_item_type": str(_g(li, "lineItemType")),
            "start": _date(_g(li, "startDateTime")),
            "end": _date(_g(li, "endDateTime")),
            "sizes": [
                _size(getattr(ph, "size", None))
                for ph in (_g(li, "creativePlaceholders") or [])
                if getattr(ph, "size", None) is not None
            ],
            "goal_units": _g(goal, "units") if goal is not None else None,
            "goal_type": str(_g(goal, "goalType")) if goal is not None else None,
            "cost_type": str(_g(li, "costType")),
            "targeted_ad_units": units,
            "is_highlight": str(_g(li, "id")) == str(args.line_item),
        })

    li_ids = [li["id"] for li in line_items]

    # ---------------- LICAs + creatives ----------------
    creatives_by_li: dict[str, list] = {lid: [] for lid in li_ids}
    creative_ids: set[int] = set()
    lica_pairs = []
    if li_ids:
        lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
        ids_str = ", ".join(str(int(i)) for i in li_ids)
        for la in _page(lica_svc, "getLineItemCreativeAssociationsByStatement",
                        f"lineItemId IN ({ids_str})"):
            lid, cid = _g(la, "lineItemId"), _g(la, "creativeId")
            if lid is None or cid is None:
                continue
            creative_ids.add(int(cid))
            lica_pairs.append({
                "line_item_id": str(lid),
                "creative_id": str(cid),
                "status": str(_g(la, "status")),
                "destination_url": _g(la, "destinationUrl"),
            })

    creatives: dict[str, dict] = {}
    if creative_ids:
        cr_svc = client.GetService("CreativeService", version=V)
        ids_str = ", ".join(str(i) for i in sorted(creative_ids))
        for c in _page(cr_svc, "getCreativesByStatement", f"id IN ({ids_str})"):
            cid = str(_g(c, "id"))
            asset = _g(c, "primaryImageAsset", "imageAsset", "asset")
            creatives[cid] = {
                "id": cid,
                "name": _g(c, "name"),
                "type": type(c).__name__,
                "size": _size(_g(c, "size")),
                "preview_url": _g(c, "previewUrl"),
                "destination_url": _g(c, "destinationUrl"),
                "asset_url": _g(asset, "assetUrl") if asset is not None else None,
                "asset_size": _size(_g(asset, "size")) if asset is not None else None,
                "third_party_snippet": bool(_g(c, "snippet") or _g(c, "htmlSnippet")),
                "vast_redirect_url": _g(c, "vastXmlUrl"),
            }

    for p in lica_pairs:
        creatives_by_li.setdefault(p["line_item_id"], []).append({
            **p, **creatives.get(p["creative_id"], {"id": p["creative_id"]}),
        })

    payload = {
        "order": order,
        "line_items": line_items,
        "creatives_by_line_item": creatives_by_li,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str))
    if args.markdown:
        Path(args.markdown).write_text(build_markdown(payload, date.today()))

    # ---------------- summary ----------------
    print(f"ORDER {order['id']}  {order['name']}")
    print(f"  advertiser : {order['advertiser']}")
    print(f"  status     : {order['status']}   flight: {order['start']} → {order['end']}")
    print(f"  line items : {len(line_items)}")
    for li in line_items:
        mark = " <<< requested" if li["is_highlight"] else ""
        print(f"\n  LI {li['id']}  {li['name']}{mark}")
        print(f"     status={li['status']}  type={li['line_item_type']}  "
              f"flight={li['start']} → {li['end']}")
        print(f"     sizes={li['sizes']}  goal={li['goal_units']} {li['goal_type']} ({li['cost_type']})")
        for u in li["targeted_ad_units"]:
            print(f"     ad unit: {u['ad_unit']} ({u['ad_unit_id']}) "
                  f"descendants={u['include_descendants']}")
        for cr in creatives_by_li.get(li["id"], []):
            print(f"     creative {cr['id']}  {cr.get('name')}")
            print(f"        type={cr.get('type')} size={cr.get('size')} "
                  f"lica_status={cr.get('status')}")
            print(f"        click  : {cr.get('destination_url')}")
            print(f"        asset  : {cr.get('asset_url')}")
            print(f"        preview: {cr.get('preview_url')}")
    print(f"\nJSON → {args.out}")
    if args.markdown:
        print(f"Doc markdown → {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
