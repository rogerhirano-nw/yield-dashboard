"""
Retail-investor audience avails (GAM forecast): first-party contextual
targeting, third-party audience segments, and the two combined.

Written for Mobkoi's ETF-brand RFP (2026-09, Ramy Yared): "can you target
retail investors, what's the definition, audience size, CPM". Read-only
against GAM, so it runs from Actions with the repo's secrets
(`.github/workflows/gam_retail_investor_avails.yml`).

What it does
------------
1. **Third-party segments.** Searches the AudienceSegmentService catalog
   for investor-intent names (invest, trader, brokerage, ETF, stock market,
   wealth, …), keeps ACTIVE ones that aren't disapproved, and prints each
   one's provider, size (users) and data CPM. It auto-picks the top
   `--pick` segments, ranked by how directly the name says *retail investor*
   and then by size. Pass `--segment-ids` to forecast a hand-picked set
   instead.
2. **First-party contextual.** Resolves the `cat` / `sitecat` page key-values
   (`nwus-<category>`) whose category is finance-ish (personal_finance,
   business, markets, economy, money, …). It also reports first-party GAM
   audience segments with finance or investor names, if any exist.
3. **Forecast.** Asks ForecastService (`getAvailabilityForecast`) for the
   forecast month, US only, for each cut, on Display (`newsweek`, the union
   of 300x250/320x50/970x250/728x90) and Video (`vid.newsweek`), on all
   devices and on smartphone only (Mobkoi's formats are mobile):
     - baseline: all US inventory, for scale
     - 1P contextual: cat/sitecat IN finance values
     - 1P contextual + brandsafe=y (if the key exists)
     - 3P: each picked segment on its own
     - 3P union: any of the picked segments
     - 1P ∧ 3P: finance context AND any picked segment

`available` is the unreserved inventory you can sell, so quote that one.
`matched` is the gross pool. Segment "size" is users. The forecast figures
are impressions, so the two are different units.

Gotchas: the AudienceSegmentCriteria and CustomCriteria go in the same
customTargeting tree. An AudienceSegmentCriteria with several ids is an OR
across them. A failed forecast call is reported as missing, never as zero
(see gam_avails_forecast.py).

Usage:
    python scripts/gam_retail_investor_avails.py                  # next month
    python scripts/gam_retail_investor_avails.py --month 2026-10 --pick 12
    python scripts/gam_retail_investor_avails.py --segment-ids 123,456
"""

from __future__ import annotations

import argparse
import calendar
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from gam_client import GAMClient  # noqa: E402

V = "v202605"
TZ = "America/New_York"
DISPLAY_UNIT = "newsweek"
VIDEO_UNIT = "vid.newsweek"
DISPLAY_SIZES = [(300, 250), (320, 50), (970, 250), (728, 90)]
VIDEO_SIZE = (640, 360)
VIDEO_MAX_DURATION_MS = 30_000
US_GEO_ID = 2840

# Catalog search terms (PQL LIKE, case-insensitive in GAM).
SEARCH_TERMS = ["invest", "trader", "trading", "brokerage", "broker", "ETF",
                "stock", "equities", "mutual fund", "wealth", "portfolio",
                "retirement", "401k", "IRA", "self-directed", "fintech",
                "robo", "Robinhood", "Schwab", "Fidelity", "E*Trade", "Vanguard"]

# Ranking: a higher tier means the name reads more directly as a retail investor.
TIERS = [
    (5, r"retail invest|self[- ]?directed|diy invest|active (investor|trader)|online (trading|brokerage)|day trad"),
    (4, r"\betf|exchange[- ]traded|brokerage|robinhood|schwab|fidelity|e\*?trade|vanguard|webull|td ameritrade|interactive brokers"),
    (3, r"stock|equit|invest(or|ing|ment)s?\b|trader|trading"),
    (2, r"mutual fund|portfolio|wealth|401k|\bira\b|retirement plan"),
    (1, r"financ|money|fintech"),
]
# Names that match the search terms but aren't retail investors.
NOISE = re.compile(
    r"real estate invest|property invest|investigat|b2b|business decision|"
    r"it decision|commercial|job title|occupation|employee|industry|"
    r"company|firmographic|crypto mining|sports trading card|trading card|"
    r"trade ?show|tradesm|forex broker job|"
    # Negated / suppression segments ("Not Active Investors", "Unlikely to Be
    # Active Investors") match the positive patterns — the first run auto-
    # picked one. Also non-US and political/TV-only taxonomies.
    r"\bnot\b|\bnon[- ]|unlikely|\b(CA|IN|UK|AU|DE|FR)\s*:|global|political|voters|for tv|optimizedfortv", re.I)
# Category slugs (the part after `nwus-`) that count as finance context.
FINANCE_CATS = re.compile(
    r"personal_finance|business|market|econom|money|financ|invest|stock|"
    r"wealth|retire|banking|tax|crypto", re.I)
CONTEXT_KEYS = ["cat", "sitecat"]

_throttle = threading.Semaphore(3)


def _g(obj, name, default=None):
    """Attribute read that tolerates zeep's polymorphic segment types."""
    try:
        v = obj[name]
    except (KeyError, AttributeError, TypeError, IndexError):
        v = getattr(obj, name, default)
    return default if v is None else v


def _q_all(svc, method, where, **binds):
    from googleads import ad_manager
    out, offset = [], 0
    while True:
        sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(500).Offset(offset)
        for k, v in binds.items():
            sb = sb.WithBindVariable(k, v)
        res = getattr(svc, method)(sb.ToStatement())
        rows = list(getattr(res, "results", None) or [])
        out.extend(rows)
        if len(rows) < 500:
            return out
        offset += 500


def _month_arg(s):
    if s:
        y, m = (int(x) for x in s.split("-"))
        return y, m
    t = date.today()
    nxt = date(t.year, t.month, 28) + timedelta(days=7)
    return nxt.year, nxt.month


# ---------------------------------------------------------------- segments
def _seg_row(s) -> dict:
    cost = _g(s, "cost")
    micro = _g(cost, "microAmount") if cost is not None else None
    prov = _g(s, "dataProvider")
    return {
        "id": int(s.id),
        "name": str(_g(s, "name", "")),
        "type": str(_g(s, "type", "")),
        "provider": str(_g(prov, "name", "")) if prov is not None else "",
        "status": str(_g(s, "status", "")),
        "approval": str(_g(s, "approvalStatus", "")),
        "license": str(_g(s, "licenseType", "")),
        "size": int(_g(s, "size", 0) or 0),
        "mobile_web_size": int(_g(s, "mobileWebSize", 0) or 0),
        "data_cpm": (int(micro) / 1e6) if micro else None,
        "currency": str(_g(cost, "currencyCode", "")) if cost is not None else "",
    }


def _tier(name: str) -> int:
    if NOISE.search(name):
        return 0
    for t, pat in TIERS:
        if re.search(pat, name, re.I):
            return t
    return 0


def find_segments(soap) -> pd.DataFrame:
    svc = soap.GetService("AudienceSegmentService", version=V)
    seen: dict[int, dict] = {}
    for term in SEARCH_TERMS:
        try:
            rows = _q_all(svc, "getAudienceSegmentsByStatement",
                          "name LIKE :n AND status = 'ACTIVE'", n=f"%{term}%")
        except Exception as ex:  # noqa: BLE001
            print(f"  search '{term}' failed: {str(ex)[:120]}")
            continue
        for s in rows:
            seen.setdefault(int(s.id), _seg_row(s))
        print(f"  '{term}': {len(rows)} active segments")
    df = pd.DataFrame(seen.values())
    if df.empty:
        return df
    df["tier"] = df["name"].map(_tier)
    df = df[df["approval"] != "DISAPPROVED"]
    return df.sort_values(["tier", "size"], ascending=[False, False]).reset_index(drop=True)


def pick_segments(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Top tiers first, one segment per identical name (providers resell the
    same taxonomy node), THIRD_PARTY only, and at least 100K users so a
    forecast isn't noise."""
    c = df[(df["type"].str.contains("THIRD_PARTY")) & (df["tier"] >= 3) & (df["size"] >= 100_000)]
    c = c.drop_duplicates(subset=["name"])
    return c.head(n)


# ---------------------------------------------------------------- context
def find_context_values(soap) -> dict[str, dict]:
    """key name -> {"key_id", "values": [(id, name)], "all": n}."""
    ct = soap.GetService("CustomTargetingService", version=V)
    out = {}
    for key in CONTEXT_KEYS + ["brandsafe"]:
        ks = _q_all(ct, "getCustomTargetingKeysByStatement", "name = :n", n=key)
        if not ks:
            print(f"  key '{key}': not found")
            continue
        k = ks[0]
        vals = _q_all(ct, "getCustomTargetingValuesByStatement",
                      "customTargetingKeyId = :k AND status = 'ACTIVE'", k=int(k.id))
        if key == "brandsafe":
            keep = [(int(v.id), v.name) for v in vals if str(v.name).lower() == "y"]
        else:
            keep = [(int(v.id), v.name) for v in vals if FINANCE_CATS.search(str(v.name))]
        out[key] = {"key_id": int(k.id), "type": str(_g(k, "type", "")),
                    "values": keep, "all": len(vals)}
        print(f"  key '{key}' ({_g(k, 'type', '')}, id {k.id}): {len(vals)} active values, "
              f"{len(keep)} kept")
        for vid, vn in keep:
            print(f"      {vid}  {vn}")
    return out


# ---------------------------------------------------------------- forecast
def _resolve_units(soap) -> dict[str, int]:
    net = soap.GetService("NetworkService", version=V).getCurrentNetwork()
    inv = soap.GetService("InventoryService", version=V)
    units = {u.name: int(u.id) for u in _q_all(inv, "getAdUnitsByStatement", "parentId = :p",
                                               p=int(net["effectiveRootAdUnitId"]))}
    missing = {DISPLAY_UNIT, VIDEO_UNIT} - units.keys()
    if missing:
        raise SystemExit(f"top-level ad unit(s) not found: {sorted(missing)}")
    return units


def _smartphone_id(soap) -> int | None:
    from googleads import ad_manager
    pql = soap.GetService("PublisherQueryLanguageService", version=V)
    q = ad_manager.StatementBuilder(version=V).Select("Id, DeviceCategoryName") \
        .From("Device_Category").Limit(50)
    for r in (getattr(pql.select(q.ToStatement()), "rows", None) or []):
        gid, name = (v.value for v in r.values)
        if "smartphone" in str(name).lower():
            return int(gid)
    return None


def _custom_tree(ctx_crit: list[dict] | None, seg_ids: list[int] | None):
    """All given criteria ANDed together (inside the required OR>AND wrapper)."""
    kids = list(ctx_crit or [])
    if seg_ids:
        kids.append({"xsi_type": "AudienceSegmentCriteria", "operator": "IS",
                     "audienceSegmentIds": [int(i) for i in seg_ids]})
    if not kids:
        return None
    return {"xsi_type": "CustomCriteriaSet", "logicalOperator": "OR",
            "children": [{"xsi_type": "CustomCriteriaSet", "logicalOperator": "AND",
                          "children": kids}]}


def _line_item(y, m, unit_id, video, device_id, custom):
    last = calendar.monthrange(y, m)[1]
    sizes = [VIDEO_SIZE] if video else DISPLAY_SIZES
    li = {
        "name": "retail investor avails probe",
        "startDateTime": {"date": {"year": y, "month": m, "day": 1},
                          "hour": 0, "minute": 0, "second": 0, "timeZoneId": TZ},
        "endDateTime": {"date": {"year": y, "month": m, "day": last},
                        "hour": 23, "minute": 59, "second": 0, "timeZoneId": TZ},
        "lineItemType": "STANDARD",
        "costType": "CPM",
        "primaryGoal": {"goalType": "LIFETIME", "unitType": "IMPRESSIONS", "units": 1},
        "creativePlaceholders": [{"size": {"width": w, "height": h, "isAspectRatio": False}}
                                 for w, h in sizes],
        "targeting": {
            "inventoryTargeting": {"targetedAdUnits": [{"adUnitId": str(unit_id),
                                                        "includeDescendants": True}]},
            "geoTargeting": {"targetedLocations": [{"id": str(US_GEO_ID)}]},
        },
    }
    if custom:
        li["targeting"]["customTargeting"] = custom
    if device_id:
        li["targeting"]["technologyTargeting"] = {
            "deviceCategoryTargeting": {"targetedDeviceCategories": [{"id": str(device_id)}]}}
    if video:
        # All three together or the call fails — see gam_avails_forecast.py.
        li["environmentType"] = "VIDEO_PLAYER"
        li["targeting"]["requestPlatformTargeting"] = {"targetedRequestPlatforms": ["VIDEO_PLAYER"]}
        li["videoMaxDuration"] = VIDEO_MAX_DURATION_MS
    return li


def _forecast(fc, li, attempts=6):
    for i in range(attempts):
        try:
            with _throttle:
                r = fc.getAvailabilityForecast(
                    {"lineItem": li},
                    {"includeTargetingCriteriaBreakdown": False,
                     "includeContendingLineItems": False})
            return int(r.matchedUnits), int(r.availableUnits)
        except Exception as ex:  # noqa: BLE001
            msg = str(ex)
            transient = any(k in msg for k in ("EXCEEDED_QUOTA", "RATE_EXCEEDED", "SERVER_ERROR",
                                               "Timeout", "timed out", "INTERNAL"))
            if not transient or i == attempts - 1:
                raise
            time.sleep(min(2 ** i * 5, 90))
    raise RuntimeError("unreachable")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="YYYY-MM to forecast (default: next month)")
    ap.add_argument("--pick", type=int, default=10, help="auto-picked 3P segments (default 10)")
    ap.add_argument("--segment-ids", help="comma-separated segment ids (overrides the auto-pick)")
    ap.add_argument("--csv-dir")
    ap.add_argument("--xlsx")
    args = ap.parse_args()

    y, m = _month_arg(args.month)
    label = f"{y:04d}-{m:02d}"
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 90)
    pd.set_option("display.max_rows", 200)
    print(f"# RETAIL-INVESTOR AVAILS — US, forecast {label} (ForecastService {V})\n")

    client = GAMClient()
    soap = client._get_soap_client()

    # ---- 1. segment catalog
    print("## Third-party / first-party audience segment catalog search")
    segs = find_segments(soap)
    if segs.empty:
        print("!! no matching segments")
    else:
        cols = ["id", "tier", "type", "provider", "name", "size", "mobile_web_size",
                "data_cpm", "approval", "license"]
        rel = segs[segs["tier"] > 0]
        print(f"\n{len(segs)} active matches, {len(rel)} investor-relevant (tier>0). "
              f"Top 60 by tier then size:\n")
        print(rel[cols].head(60).to_string(index=False))
        fp = segs[~segs["type"].str.contains("THIRD_PARTY")]
        print(f"\nFirst-party / non-3P segments matching: {len(fp)}")
        if not fp.empty:
            print(fp[cols].head(30).to_string(index=False))

    if args.segment_ids:
        ids = [int(x) for x in args.segment_ids.split(",") if x.strip()]
        picked = segs[segs["id"].isin(ids)] if not segs.empty else pd.DataFrame()
        missing = set(ids) - set(picked["id"] if not picked.empty else [])
        if missing:
            svc = soap.GetService("AudienceSegmentService", version=V)
            extra = [_seg_row(s) for s in _q_all(svc, "getAudienceSegmentsByStatement",
                                                  f"id IN ({','.join(map(str, missing))})")]
            picked = pd.concat([picked, pd.DataFrame(extra)], ignore_index=True)
    else:
        picked = pick_segments(segs, args.pick) if not segs.empty else pd.DataFrame()
    print(f"\n## Picked 3P segments ({len(picked)})\n")
    if not picked.empty:
        print(picked[["id", "provider", "name", "size", "data_cpm"]].to_string(index=False))

    # ---- 2. contextual
    print("\n## First-party contextual key-values")
    ctx = find_context_values(soap)
    ctx_key = next((k for k in CONTEXT_KEYS if ctx.get(k, {}).get("values")), None)
    ctx_crit = None
    if ctx_key:
        ctx_crit = [{"xsi_type": "CustomCriteria", "keyId": ctx[ctx_key]["key_id"],
                     "operator": "IS", "valueIds": [v for v, _ in ctx[ctx_key]["values"]]}]
        print(f"\n1P contextual cut uses `{ctx_key}` IN "
              f"{[n for _, n in ctx[ctx_key]['values']]}")
    else:
        print("!! no finance values on cat/sitecat — 1P contextual cuts skipped")
    bs_crit = None
    if ctx.get("brandsafe", {}).get("values"):
        bs_crit = [{"xsi_type": "CustomCriteria", "keyId": ctx["brandsafe"]["key_id"],
                    "operator": "IS", "valueIds": [ctx["brandsafe"]["values"][0][0]]}]

    # ---- 3. forecast
    units = _resolve_units(soap)
    phone = _smartphone_id(soap)
    print(f"\nsmartphone device category id: {phone}")
    seg_ids = [int(i) for i in picked["id"]] if not picked.empty else []

    cuts = [("0 Baseline: all US", None, None)]
    if ctx_crit:
        cuts.append(("1 1P contextual: finance", ctx_crit, None))
        if bs_crit:
            cuts.append(("1b 1P contextual: finance + brandsafe=y", ctx_crit + bs_crit, None))
    for r in picked.itertuples():
        cuts.append((f"2 3P: {r.name[:70]} [{r.id}]", None, [r.id]))
    if len(seg_ids) > 1:
        cuts.append(("3 3P union: any picked segment", None, seg_ids))
    if ctx_crit and seg_ids:
        cuts.append(("4 1P finance AND 3P union", ctx_crit, seg_ids))

    fc = soap.GetService("ForecastService", version=V)
    jobs = []
    for name, cc, sids in cuts:
        custom = _custom_tree(cc, sids)
        for fmt, unit, video in (("Display", DISPLAY_UNIT, False), ("Video", VIDEO_UNIT, True)):
            for dev, did in (("All devices", None), ("Smartphone", phone)):
                if dev == "Smartphone" and not did:
                    continue
                jobs.append((name, fmt, dev, _line_item(y, m, units[unit], video, did, custom)))

    print(f"\nrunning {len(jobs)} forecast calls …")
    rows, errors = [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_forecast, fc, li): (n, f, d) for n, f, d, li in jobs}
        for fut in as_completed(futs):
            n, f, d = futs[fut]
            try:
                mt, av = fut.result()
                rows.append({"cut": n, "format": f, "device": d, "matched": mt, "available": av})
            except Exception as exc:  # noqa: BLE001
                errors.append((n, f, d, str(exc)[:200]))
    print(f"done in {time.time()-t0:.0f}s — {len(rows)} ok, {len(errors)} failed")
    for e in errors:
        print("  FAILED:", e)

    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no forecasts returned")
    wide = df.pivot_table(index="cut", columns=["format", "device"],
                          values="available", aggfunc="sum").sort_index()
    print(f"\n## {label} US AVAILABLE impressions (unreserved, sellable). Blank = call failed, "
          f"NOT zero\n")
    print(wide.to_string(float_format=lambda v: f"{v:,.0f}"))
    wide_m = df.pivot_table(index="cut", columns=["format", "device"],
                            values="matched", aggfunc="sum").sort_index()
    print(f"\n## {label} US MATCHED impressions (gross pool before reservations)\n")
    print(wide_m.to_string(float_format=lambda v: f"{v:,.0f}"))
    print("\nNOTE: segment size = users (as the data provider reports it); forecasts = "
          "impressions for the month. Display is the de-duplicated union of 4 sizes.")

    if args.csv_dir:
        out = Path(args.csv_dir)
        out.mkdir(parents=True, exist_ok=True)
        df.to_csv(out / f"retail_investor_forecast_{label}.csv", index=False)
        if not segs.empty:
            segs.to_csv(out / "retail_investor_segment_catalog.csv", index=False)
    if args.xlsx:
        p = Path(args.xlsx)
        p.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(p, engine="openpyxl") as xl:
            pd.DataFrame({f"Newsweek retail-investor avails, US, forecast {label}": [
                f"Source: GAM ForecastService {V}, run {date.today()}.",
                "Available = unreserved impressions for the month (quote this). "
                "Matched = gross pool.",
                "Display = `newsweek` ad unit, union of 300x250/320x50/970x250/728x90. "
                "Video = `vid.newsweek`.",
                f"1P contextual = `{ctx_key}` IN finance categories. 3P = third-party "
                "audience segments (data CPM on the Segments sheet, charged on top of "
                "media).",
                "Segment size is users. Forecast figures are impressions.",
            ]}).to_excel(xl, sheet_name="Read me", index=False)
            flat = wide.copy()
            flat.columns = [f"{f} · {d}" for f, d in flat.columns]
            flat.reset_index().to_excel(xl, sheet_name="Available", index=False)
            if not picked.empty:
                picked.to_excel(xl, sheet_name="Picked segments", index=False)
            if not segs.empty:
                segs.to_excel(xl, sheet_name="Segment catalog", index=False)
        print(f"wrote {p}")
    return 1 if errors and not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
