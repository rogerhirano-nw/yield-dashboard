#!/usr/bin/env python3
"""Build the Comscore Campaign Ratings (CCR) setup form for a GAM order.

Comscore (Kristie Chesebro, Technical Customer Success) needs a CCR setup form
for every Direct campaign carrying Comscore tags, and flags any campaign ID that
starts showing tag activity before its form arrives. Four did in Aug–Sep 2026
(4057788230, 4159204943, 4171515326, 4183464375). This script removes the manual
step: give it one or more GAM order ids and it fills `templates/
comscore_ccr_template.xlsx` from GAM, ready to attach.

What it fills (read-only against GAM; nothing is written there):
  Study Details  campaign name (order name, ≤150 chars), flight dates,
                 advertiser/brand/product/category (from the Newsweek naming
                 convention, overridable), KPIs, an "End of campaign report"
                 custom period (split into ≤92-day chunks, Comscore's max)
  Media Details  flight start/end, the order id(s), ad server, and a Newsweek
                 partner row with estimated Desktop / Mobile / CTV impressions

Impression estimate per line item = its impression goal (lifetime units, or
daily units × flight days). The device split uses the line's own delivered
device mix when it has delivered, else the order's, else the network's last 28
days. Sponsorship (percentage-goal) lines have no impression count and are left
out of the estimate with a warning.

Line items Comscore doesn't measure are excluded (Kael, 2026-08-28): Apple News
and newsletter lines. Canceled and archived lines are excluded too.

The template's hidden "Q4 2024 - $128k" sheet (another client's pricing, left
over from an old media plan) was removed when the template was made; the script
refuses to write a form if a hidden sheet other than "Data Validation" ever
reappears.

Usage:
  python scripts/build_ccr_form.py --order 4183464375 [--order ...]
      [--out ccr.xlsx] [--advertiser "Apple TV"] [--brand ...]
      [--product ...] [--category ...] [--kpis "CTR, Viewability"]
      [--campaign-name ...]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "comscore_ccr_template.xlsx"
V = "v202605"

NAME_MAX = 150       # Comscore UI/API limit on the campaign name
PERIOD_MAX_DAYS = 92  # Comscore limit on one custom reporting period
PERIOD_ROWS = range(10, 15)  # Study Details rows 10-14 hold custom periods
PARTNER_ROW = 9       # Media Details: first Digital/CTV partner row
ALLOWED_HIDDEN = {"Data Validation"}

_PERIOD_CODE = re.compile(r"(Q[1-4]\d{0,4}|FY\d{2,4}(-?Q[1-4])?|H[12]\d{0,4})", re.I)

_CATEGORY_ALIASES = {"tech": "Technology", "auto": "Automotive"}

# GAM DEVICE_CATEGORY_NAME -> Media Details column bucket.
_DEVICE_BUCKET = {
    "desktop": "desktop",
    "smartphone": "mobile",
    "tablet": "mobile",
    "feature phone": "mobile",
    "connected tv": "ctv",
    "set top box": "ctv",
}

# Formats Comscore does not measure for us (Kael Rabelo, 2026-08-28:
# "an applenews format we're not tracking Comscore" / "a newsletter also a
# format we're not tracking Comscore").
_EXCLUDE_PATTERNS = [
    (re.compile(r"apple[-_ ]?news", re.I), "Apple News (not Comscore-tagged)"),
    (re.compile(r"newsletter", re.I), "newsletter (not Comscore-tagged)"),
]


# --------------------------------------------------------------------------
# Pure logic (tested in tests/test_build_ccr_form.py)
# --------------------------------------------------------------------------

def _pretty(token: str) -> str:
    return re.sub(r"\s+", " ", token.replace("-", " ")).strip()


def parse_order_name(name: str) -> dict:
    """Advertiser/brand/product/category from the Newsweek naming convention.

    Direct: Newsweek_Direct_<vertical>_NA_NA_<holding>_<agency>_<advertiser>_<campaign>_…
    PG/PD:  Newsweek_PG_<vertical>_<exchange>_<dsp>_<holding>_<agency>_<advertiser>_<campaign>_…
    Both put the vertical at token 2, advertiser at 7, campaign at 8. A
    non-convention name returns empty strings (the caller falls back to the
    GAM advertiser company, or an override).
    """
    parts = (name or "").split("_")
    if len(parts) < 9 or parts[0] != "Newsweek":
        return {"category": "", "advertiser": "", "brand": "", "product": ""}
    vertical = parts[2].strip()
    category = _CATEGORY_ALIASES.get(vertical.lower(), _pretty(vertical))
    if vertical.upper() in {"NA", "N/A"}:
        category = ""
    advertiser, product = _pretty(parts[7]), _pretty(parts[8])
    # PG/PD names often pack advertiser + title into token 7 and put a
    # quarter/FY code at 8 (…_AppleTv-Slow-Horses-S6_Q426_…): the title is
    # the product and the first dash-word of token 7 is the advertiser.
    if _PERIOD_CODE.fullmatch(parts[8].strip()):
        product = advertiser
        advertiser = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", parts[7].split("-")[0]).strip()
    return {
        "category": category,
        "advertiser": advertiser,
        "brand": advertiser,
        "product": product,
    }


def exclusion_reason(li: dict) -> str | None:
    """Why a line item is left off the form, or None to include it."""
    if li.get("is_archived"):
        return "archived"
    if (li.get("status") or "").upper() == "CANCELED":
        return "canceled"
    for pat, why in _EXCLUDE_PATTERNS:
        if pat.search(li.get("name") or ""):
            return why
    return None


def impression_estimate(li: dict) -> int | None:
    """Impressions the line is booked for, or None when the goal isn't an
    impression count (sponsorship %, clicks, none)."""
    units = li.get("goal_units")
    if not units or units <= 0:
        return None
    if (li.get("unit_type") or "IMPRESSIONS").upper() != "IMPRESSIONS":
        return None
    goal_type = (li.get("goal_type") or "").upper()
    if goal_type == "LIFETIME":
        return int(units)
    if goal_type == "DAILY":
        if (li.get("line_item_type") or "").upper() == "SPONSORSHIP":
            return None  # daily % of traffic, not an impression count
        s, e = li.get("start"), li.get("end")
        if s and e:
            return int(units) * ((e - s).days + 1)
    return None


def normalize_mix(rows: dict[str, float]) -> dict[str, float] | None:
    """{GAM device category: impressions} -> {bucket: share}; None if empty."""
    buckets = {"desktop": 0.0, "mobile": 0.0, "ctv": 0.0}
    for dev, n in rows.items():
        b = _DEVICE_BUCKET.get((dev or "").strip().lower())
        if b and n:
            buckets[b] += float(n)
    total = sum(buckets.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in buckets.items()}


def split_impressions(total: int, mix: dict[str, float]) -> dict[str, int]:
    """Split total across buckets by mix, rounding so the parts sum to total."""
    raw = {k: total * mix.get(k, 0.0) for k in ("desktop", "mobile", "ctv")}
    out = {k: int(v) for k, v in raw.items()}
    short = total - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:short]:
        out[k] += 1
    return out


def reporting_periods(start: date, end: date) -> list[tuple[str, date, date]]:
    """'End of campaign report' covering the flight, chunked to ≤92 days."""
    chunks = []
    s = start
    while s <= end:
        e = min(end, s + timedelta(days=PERIOD_MAX_DAYS - 1))
        chunks.append((s, e))
        s = e + timedelta(days=1)
    if len(chunks) == 1:
        return [("End of campaign report", start, end)]
    return [(f"End of campaign report (part {i})", s, e)
            for i, (s, e) in enumerate(chunks, 1)]


def flight_text(start: date, end: date) -> str:
    return f"{start:%B} {start.day}, {start.year} to {end:%B} {end.day}, {end.year}"


@dataclass
class Facts:
    order_ids: list[str]
    campaign_name: str
    start: date
    end: date
    advertiser: str
    brand: str
    product: str
    category: str
    kpis: str
    impressions: dict[str, int]
    included: list[dict] = field(default_factory=list)
    excluded: list[tuple[dict, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_facts(orders: list[dict], line_items: list[dict],
                li_mix: dict[str, dict[str, float]],
                order_mix: dict[str, dict[str, float]],
                network_mix: dict[str, float] | None,
                overrides: dict | None = None) -> Facts:
    """Everything the form needs, from pulled GAM data.

    `li_mix` / `order_mix` are normalized device mixes keyed by line item id /
    order id (see normalize_mix); `network_mix` is the last-resort fallback.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v}
    warnings: list[str] = []
    included, excluded = [], []
    for li in line_items:
        why = exclusion_reason(li)
        (excluded.append((li, why)) if why else included.append(li))
    if not included:
        raise ValueError("no Comscore-measured line items on the order(s)")

    starts = [li["start"] for li in included if li.get("start")]
    ends = [li["end"] for li in included if li.get("end")]
    start = min(starts) if starts else min(o["start"] for o in orders)
    end = max(ends) if ends else max(o["end"] for o in orders)

    totals = {"desktop": 0, "mobile": 0, "ctv": 0}
    for li in included:
        if (li.get("status") or "").upper() == "DRAFT":
            warnings.append(f"LI {li['id']} is still DRAFT — included, confirm it will run")
        est = impression_estimate(li)
        if est is None:
            warnings.append(
                f"LI {li['id']} has no impression goal "
                f"({li.get('line_item_type')}, {li.get('goal_type')} "
                f"{li.get('goal_units')} {li.get('unit_type')}) — left out of the estimate")
            continue
        mix = li_mix.get(li["id"]) or order_mix.get(li["order_id"]) or network_mix
        if mix is None:
            warnings.append(f"LI {li['id']}: no device mix available — counted as desktop")
            mix = {"desktop": 1.0}
        for k, v in split_impressions(est, mix).items():
            totals[k] += v

    first = orders[0]
    parsed = parse_order_name(first["name"])
    advertiser = parsed["advertiser"] or re.sub(r"^\[nw\]\s*", "", first.get("advertiser") or "")
    is_video = any((li.get("environment") or "").upper() == "VIDEO_PLAYER"
                   or re.search(r"video|pre-?roll", li.get("name") or "", re.I)
                   for li in included)
    name = overrides.get("campaign_name") or first["name"]
    if len(name) > NAME_MAX:
        warnings.append(f"campaign name is {len(name)} chars — truncated to {NAME_MAX}")
        name = name[:NAME_MAX]

    return Facts(
        order_ids=[o["id"] for o in orders],
        campaign_name=name,
        start=start, end=end,
        advertiser=overrides.get("advertiser") or advertiser,
        brand=overrides.get("brand") or parsed["brand"] or advertiser,
        product=overrides.get("product") or parsed["product"],
        category=overrides.get("category") or parsed["category"],
        kpis=overrides.get("kpis") or ("VCR, Viewability" if is_video else "CTR, Viewability"),
        impressions=totals,
        included=included, excluded=excluded, warnings=warnings,
    )


def fill_template(facts: Facts, out: Path, template: Path = TEMPLATE) -> Path:
    """Write the filled CCR form. Refuses to emit unexpected hidden sheets."""
    try:
        import PIL  # noqa: F401 — without Pillow openpyxl silently drops the logos
    except ImportError as e:
        raise RuntimeError("Pillow is required to keep the template's images") from e
    import openpyxl

    wb = openpyxl.load_workbook(template)
    hidden = [ws.title for ws in wb if ws.sheet_state != "visible"
              and ws.title not in ALLOWED_HIDDEN]
    if hidden:
        raise RuntimeError(f"template has unexpected hidden sheet(s) {hidden} — "
                           "remove them before anything is sent to Comscore")

    sd = wb["Study Details"]
    sd["C3"] = facts.campaign_name
    sd["C4"] = flight_text(facts.start, facts.end)
    sd["C5"] = "National Advertiser"
    sd["C6"] = "National Study"
    for row in PERIOD_ROWS:
        for col in "CEG":
            sd[f"{col}{row}"] = None
    periods = reporting_periods(facts.start, facts.end)
    if len(periods) > len(PERIOD_ROWS):
        raise ValueError(f"flight needs {len(periods)} reporting periods; "
                         f"the form holds {len(PERIOD_ROWS)}")
    for row, (label, s, e) in zip(PERIOD_ROWS, periods):
        sd[f"C{row}"], sd[f"E{row}"], sd[f"G{row}"] = label, s, e
        sd[f"E{row}"].number_format = sd[f"G{row}"].number_format = "d-mmm-yy"
    sd["C17"] = facts.advertiser
    sd["C18"] = facts.brand
    sd["C19"] = facts.product
    sd["C20"] = facts.category
    sd["C23"] = facts.kpis

    md = wb["Media Details"]
    md["B4"], md["B5"] = facts.start, facts.end
    ids = [int(i) for i in facts.order_ids]
    md["B6"] = ids[0] if len(ids) == 1 else ", ".join(str(i) for i in ids)
    md["B7"] = "GAM"
    r = PARTNER_ROW
    md[f"A{r}"] = "Newsweek (newsweek.com)"
    md[f"B{r}"] = facts.impressions["desktop"]
    md[f"C{r}"] = facts.impressions["mobile"]
    md[f"D{r}"] = 0
    md[f"E{r}"] = facts.impressions["ctv"]
    for c in "BCDE":
        md[f"{c}{r}"].number_format = "#,##0"

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def default_filename(facts: Facts) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", facts.product or facts.advertiser).strip("_")
    return f"CCR_Setup_{slug or 'campaign'}_{'_'.join(facts.order_ids)}.xlsx"


# --------------------------------------------------------------------------
# GAM pull
# --------------------------------------------------------------------------

def _g(obj, *names):
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _soap_date(dt) -> date | None:
    """SOAP DateTime -> date. Read y/m/d directly: the DateTime is already in
    the network timezone (see CLAUDE.md, GAM facts)."""
    d = getattr(dt, "date", None) if dt is not None else None
    if d is None:
        return None
    return date(d.year, d.month, d.day)


def _page(svc, method, where, limit=200):
    from googleads import ad_manager  # type: ignore
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(limit)
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


def pull(order_ids: list[str]):
    sys.path.insert(0, str(ROOT))
    from gam_client import GAMClient

    gc = GAMClient()
    client = gc._get_soap_client()
    o_svc = client.GetService("OrderService", version=V)
    c_svc = client.GetService("CompanyService", version=V)
    li_svc = client.GetService("LineItemService", version=V)

    orders, line_items = [], []
    for oid in order_ids:
        found = _page(o_svc, "getOrdersByStatement", f"id = {int(oid)}")
        if not found:
            raise SystemExit(f"!! order {oid} not found")
        o = found[0]
        adv = None
        if _g(o, "advertiserId"):
            cs = _page(c_svc, "getCompaniesByStatement", f"id = {int(o.advertiserId)}")
            adv = getattr(cs[0], "name", None) if cs else None
        orders.append({
            "id": str(o.id), "name": o.name, "advertiser": adv,
            "start": _soap_date(_g(o, "startDateTime")),
            "end": _soap_date(_g(o, "endDateTime")),
        })
        for li in _page(li_svc, "getLineItemsByStatement", f"orderId = {int(oid)}"):
            goal = _g(li, "primaryGoal")
            line_items.append({
                "id": str(li.id), "order_id": str(oid), "name": li.name,
                "status": str(_g(li, "status")),
                "is_archived": bool(_g(li, "isArchived")),
                "line_item_type": str(_g(li, "lineItemType")),
                "environment": str(_g(li, "environmentType")),
                "start": _soap_date(_g(li, "startDateTime")),
                "end": _soap_date(_g(li, "endDateTime")),
                "goal_type": str(_g(goal, "goalType")) if goal is not None else None,
                "unit_type": str(_g(goal, "unitType")) if goal is not None else None,
                "goal_units": _g(goal, "units") if goal is not None else None,
            })

    # Device mixes: per LI and per order over delivery to date, network fallback.
    yesterday = date.today() - timedelta(days=1)
    li_mix: dict[str, dict] = {}
    order_mix: dict[str, dict] = {}
    starts = [o["start"] for o in orders if o["start"]]
    if starts and min(starts) <= yesterday:
        df = gc._run_report(
            ["ORDER_ID", "LINE_ITEM_ID", "DEVICE_CATEGORY_NAME"],
            ["AD_SERVER_IMPRESSIONS"], min(starts), yesterday,
            filters=[("ORDER_ID", "IN", [int(i) for i in order_ids])],
        )
        if not df.empty:
            df["order_id"] = df["order_id"].astype(str)
            df["line_item_id"] = df["line_item_id"].astype(str)
            for key, grp in df.groupby("line_item_id"):
                m = normalize_mix(grp.groupby("device_category_name")["ad_server_impressions"].sum().to_dict())
                if m:
                    li_mix[key] = m
            for key, grp in df.groupby("order_id"):
                m = normalize_mix(grp.groupby("device_category_name")["ad_server_impressions"].sum().to_dict())
                if m:
                    order_mix[key] = m
    net = gc._run_report(["DEVICE_CATEGORY_NAME"], ["AD_SERVER_IMPRESSIONS"],
                         yesterday - timedelta(days=27), yesterday)
    network_mix = normalize_mix(dict(zip(net["device_category_name"],
                                         net["ad_server_impressions"])))
    return orders, line_items, li_mix, order_mix, network_mix


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--order", action="append", default=[],
                    help="GAM order id; repeat or comma-separate for several")
    ap.add_argument("--out", type=Path, default=None)
    for f in ("advertiser", "brand", "product", "category", "kpis", "campaign-name"):
        ap.add_argument(f"--{f}", default=os.environ.get(f"CCR_{f.upper().replace('-', '_')}") or None)
    args = ap.parse_args()

    raw = args.order or [os.environ.get("CCR_ORDER_IDS", "")]
    order_ids = [x.strip() for s in raw for x in s.split(",") if x.strip()]
    if not order_ids:
        ap.error("pass --order (or set CCR_ORDER_IDS)")
    bad = [x for x in order_ids if not x.isdigit()]
    if bad:
        ap.error(f"order ids must be numeric: {bad}")

    orders, lis, li_mix, order_mix, network_mix = pull(order_ids)
    facts = build_facts(orders, lis, li_mix, order_mix, network_mix, overrides={
        "advertiser": args.advertiser, "brand": args.brand, "product": args.product,
        "category": args.category, "kpis": args.kpis, "campaign_name": args.campaign_name,
    })
    out = args.out or Path(default_filename(facts))
    fill_template(facts, out)

    imp = facts.impressions
    print(f"CCR form written: {out}")
    print(f"  campaign name : {facts.campaign_name}")
    print(f"  flight        : {flight_text(facts.start, facts.end)}")
    print(f"  campaign id(s): {', '.join(facts.order_ids)}")
    print(f"  advertiser    : {facts.advertiser} | brand {facts.brand} | "
          f"product {facts.product} | category {facts.category}")
    print(f"  KPIs          : {facts.kpis}")
    print(f"  impressions   : desktop {imp['desktop']:,} | mobile {imp['mobile']:,} | "
          f"CTV {imp['ctv']:,} | total {sum(imp.values()):,}")
    print(f"  line items    : {len(facts.included)} included")
    for li in facts.included:
        print(f"    + {li['id']}  {li['status']:<11} {li['name']}")
    for li, why in facts.excluded:
        print(f"    - {li['id']}  excluded: {why}  {li['name']}")
    for w in facts.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
