"""One-off: why do the Mobkoi interscroller/uniscroller LIs report ~1%
Active View viewability in GAM?

Pulls, for the three in-flight Mobkoi LIs (Invesco IO1117 + Cartier IO1118):
  1. SOAP LineItem: type, placeholders, environment, targeting (ad units,
     custom targeting, device categories)
  2. SOAP LICAs + Creative objects: xsi type, size, SafeFrame flag, and the
     tag snippet itself (what does Mobkoi's tag render, and where?)
  3. SOAP AdUnit names for everything the LIs target
  4. REST report: impressions / clicks / Active View eligible-measurable-
     viewable split by LI, by creative, by ad unit, and by day

Output is plain text; the companion workflow posts it as a PR comment.

Context: cache shows AV measurable 100% but viewable 0.4-0.6%, while CTR is
0.3-0.8% on clean traffic (DV IVT ~0.1%) — more clicks than "viewable"
impressions, so users see ads Active View says are invisible. Hypothesis:
the Mobkoi tag renders its full-screen unit outside the element AV measures.
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_env = REPO_ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import pandas as pd  # noqa: E402

from gam_client import GAMClient  # noqa: E402

# Override via env (workflow_dispatch input) to diagnose/verify other LIs,
# e.g. a [TEST] LI carrying Mobkoi's AV-measurable tag build.
LINE_ITEM_IDS = [
    s.strip() for s in (
        os.environ.get("MOBKOI_LI_IDS") or "7310815861,7313011338,7316916920"
    ).split(",") if s.strip()
]
KNOWN_CREATIVE_IDS = ["138557481462", "138558555303"]  # from the GAM UI links
SNIPPET_CHARS = 2500    # per-creative tag excerpt cap (PR comment budget)
LOOKBACK_MAX_DAYS = int(os.environ.get("LOOKBACK_MAX_DAYS") or "45")


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n… [truncated, {len(s)} chars total]"


def _ser(obj) -> dict:
    """zeep object -> plain dict (json-able)."""
    from zeep.helpers import serialize_object
    return json.loads(json.dumps(serialize_object(obj), default=str))


def main() -> int:
    gam = GAMClient()
    client = gam._get_soap_client()
    V = gam._SOAP_API_VERSION
    from googleads import ad_manager  # type: ignore

    li_svc = client.GetService("LineItemService", version=V)
    inv_svc = client.GetService("InventoryService", version=V)

    ids = ", ".join(LINE_ITEM_IDS)

    # ── 1. Line items ────────────────────────────────────────────────────
    print("=" * 72)
    print("LINE ITEMS (SOAP)")
    print("=" * 72)
    stmt = ad_manager.StatementBuilder(version=V).Where(f"id IN ({ids})").Limit(50)
    lis = li_svc.getLineItemsByStatement(stmt.ToStatement()).results or []
    ad_unit_ids: set[str] = set()
    earliest_start = date.today()
    for li in lis:
        d = _ser(li)
        sd = (d.get("startDateTime") or {}).get("date") or {}
        if sd:
            earliest_start = min(
                earliest_start, date(int(sd["year"]), int(sd["month"]), int(sd["day"]))
            )
        print(f"\n--- LI {d.get('id')}  {d.get('name')}")
        for k in ("orderId", "orderName", "lineItemType", "priority", "status",
                  "costType", "environmentType", "roadblockingType",
                  "deliveryRateType", "skipInventoryCheck", "webPropertyCode"):
            if d.get(k) is not None:
                print(f"  {k}: {d[k]}")
        print(f"  flight: {(d.get('startDateTime') or {}).get('date')} -> "
              f"{(d.get('endDateTime') or {}).get('date')}")
        goal = d.get("primaryGoal") or {}
        print(f"  goal: {goal.get('goalType')} {goal.get('units')} {goal.get('unitType')}")
        for ph in d.get("creativePlaceholders") or []:
            size = ph.get("size") or {}
            print(f"  placeholder: {size.get('width')}x{size.get('height')}"
                  f"  sizeType={ph.get('creativeSizeType')}"
                  f"  isAmpOnly={ph.get('isAmpOnly')}")
        tgt = d.get("targeting") or {}
        inv = tgt.get("inventoryTargeting") or {}
        for au in inv.get("targetedAdUnits") or []:
            ad_unit_ids.add(str(au.get("adUnitId")))
            print(f"  targeted adUnit: {au.get('adUnitId')} "
                  f"(includeDescendants={au.get('includeDescendants')})")
        for au in inv.get("excludedAdUnits") or []:
            print(f"  EXCLUDED adUnit: {au.get('adUnitId')}")
        if inv.get("targetedPlacementIds"):
            print(f"  targeted placements: {inv['targetedPlacementIds']}")
        geo = tgt.get("geoTargeting") or {}
        if geo.get("targetedLocations"):
            locs = [loc.get("displayName") for loc in geo["targetedLocations"]]
            print(f"  geo: {locs}")
        tech = tgt.get("technologyTargeting") or {}
        if tech:
            dc = (tech.get("deviceCategoryTargeting") or {}).get("targetedDeviceCategories")
            if dc:
                print(f"  deviceCategories: {[c.get('id') for c in dc]}")
        ct = tgt.get("customTargeting")
        if ct:
            print(f"  customTargeting: {_truncate(json.dumps(ct), 1200)}")

    # ── 2. Targeted ad units ─────────────────────────────────────────────
    print()
    print("=" * 72)
    print("TARGETED AD UNITS (SOAP InventoryService)")
    print("=" * 72)
    if ad_unit_ids:
        au_ids = ", ".join(sorted(ad_unit_ids))
        stmt = ad_manager.StatementBuilder(version=V).Where(f"id IN ({au_ids})").Limit(200)
        for au in inv_svc.getAdUnitsByStatement(stmt.ToStatement()).results or []:
            d = _ser(au)
            path = "/".join(p.get("name", "") for p in d.get("parentPath") or [])
            print(f"  {d.get('id')}  {path}/{d.get('name')}  "
                  f"code={d.get('adUnitCode')}  status={d.get('status')}  "
                  f"target={d.get('targetWindow')}")
    else:
        print("  (run-of-network: no explicit ad-unit targeting)")

    # ── 3. LICAs + creatives ─────────────────────────────────────────────
    print()
    print("=" * 72)
    print("CREATIVES ON THESE LINE ITEMS (SOAP LICA + CreativeService)")
    print("=" * 72)
    licas = gam.list_line_item_creative_associations(LINE_ITEM_IDS)
    by_li: dict[str, list[str]] = {}
    for _, r in licas.iterrows():
        by_li.setdefault(r["line_item_id"], []).append(r["creative_id"])
    for li_id in LINE_ITEM_IDS:
        print(f"  LI {li_id}: creatives {by_li.get(li_id, [])}")

    cr_svc = client.GetService("CreativeService", version=V)
    all_creative_ids = sorted(
        {c for v in by_li.values() for c in v} | set(KNOWN_CREATIVE_IDS)
    )
    if all_creative_ids:
        cids = ", ".join(all_creative_ids)
        stmt = ad_manager.StatementBuilder(version=V).Where(f"id IN ({cids})").Limit(100)
        for cr in cr_svc.getCreativesByStatement(stmt.ToStatement()).results or []:
            d = _ser(cr)
            size = d.get("size") or {}
            print(f"\n--- Creative {d.get('id')}  [{type(cr).__name__}]  {d.get('name')}")
            print(f"  size: {size.get('width')}x{size.get('height')}"
                  f"  isAspectRatio={size.get('isAspectRatio')}")
            for k in ("isSafeFrameCompatible", "sslScanResult", "sslManualOverride",
                      "destinationUrl", "isInterstitial", "lockedOrientation"):
                if d.get(k) is not None:
                    print(f"  {k}: {d[k]}")
            snippet = (d.get("snippet") or d.get("htmlSnippet")
                       or d.get("codeSnippet") or "")
            if snippet:
                print(f"  --- tag snippet ({len(snippet)} chars) ---")
                print(_truncate(snippet, SNIPPET_CHARS))
            if d.get("creativeTemplateVariableValues"):
                print(f"  templateVars: "
                      f"{_truncate(json.dumps(d['creativeTemplateVariableValues']), 1500)}")

    # ── 4. Delivery + Active View reports (REST) ─────────────────────────
    yesterday = date.today() - timedelta(days=1)
    start = max(earliest_start, yesterday - timedelta(days=LOOKBACK_MAX_DAYS))
    start = min(start, yesterday)
    av_metrics = [
        "AD_SERVER_IMPRESSIONS",
        "AD_SERVER_CLICKS",
        "AD_SERVER_ACTIVE_VIEW_ELIGIBLE_IMPRESSIONS",
        "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
        "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
    ]

    def _report(dims: list[str], label: str) -> pd.DataFrame | None:
        print()
        print("=" * 72)
        print(f"{label}  ({start} -> {yesterday})")
        print("=" * 72)
        try:
            df = gam._run_report(dims, av_metrics, start, yesterday)
        except Exception as e:  # report dim/metric incompatibilities etc.
            print(f"  REPORT FAILED: {e}")
            return None
        df = df[df["line_item_id"].astype(str).isin(LINE_ITEM_IDS)].copy()
        if df.empty:
            print("  (no rows for these LIs)")
            return None
        for c in [m.lower() for m in av_metrics]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
        df["viewable_%"] = (
            100 * df["ad_server_active_view_viewable_impressions"]
            / df["ad_server_active_view_measurable_impressions"].replace(0, pd.NA)
        ).astype(float).round(2)
        df["measurable_%"] = (
            100 * df["ad_server_active_view_measurable_impressions"]
            / df["ad_server_active_view_eligible_impressions"].replace(0, pd.NA)
        ).astype(float).round(2)
        df.columns = [c.replace("ad_server_", "").replace("active_view_", "av_")
                      for c in df.columns]
        print(df.to_string(index=False, max_colwidth=60))
        return df

    _report(["LINE_ITEM_ID", "LINE_ITEM_NAME"], "TOTALS BY LINE ITEM")
    _report(["LINE_ITEM_ID", "CREATIVE_ID", "CREATIVE_NAME"], "BY CREATIVE")

    from gam_client import _D  # noqa: E402
    members = set(_D.__members__)
    au_dim = next((d for d in ("AD_UNIT_NAME", "AD_UNIT_ID", "AD_UNIT_NAME_LEVEL_1")
                   if d in members), None)
    if au_dim:
        _report(["LINE_ITEM_ID", au_dim], f"BY AD UNIT ({au_dim})")
    else:
        print(f"\n  (no AD_UNIT dimension in this client; available: "
              f"{sorted(m for m in members if 'AD_UNIT' in m)[:10]})")

    _report(["DATE", "LINE_ITEM_ID"], "BY DAY")

    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
