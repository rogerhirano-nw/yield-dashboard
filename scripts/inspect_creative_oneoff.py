"""One-off: identify a GAM creative and explain its Active View posture.

Given creative ID(s) (env CREATIVE_IDS, comma-separated):
  1. SOAP CreativeService: xsi type, name, size, SafeFrame flag, the tag
     snippet itself (what does it render, and where?)
  2. SOAP LICAs WHERE creativeId IN (...): which line items carry it, and
     whether the association is still active
  3. SOAP LineItem + AdUnit: order, type, priority, status, environment,
     placeholders, targeted ad units
  4. REST report: impressions / clicks / Active View eligible-measurable-
     viewable for those LIs by creative (siblings included for contrast),
     and by day for the asked creative(s)

Output is plain text; the companion workflow posts it as a PR comment.

Born to answer "why are we not measuring viewability for creative
138562096700?" — but generic to any creative id.
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

CREATIVE_IDS = [
    s.strip() for s in (
        os.environ.get("CREATIVE_IDS") or "138562096700"
    ).split(",") if s.strip()
]
SNIPPET_CHARS = 4000    # per-creative tag excerpt cap (PR comment budget)
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

    cids = ", ".join(CREATIVE_IDS)

    # ── 1. Creatives ─────────────────────────────────────────────────────
    print("=" * 72)
    print(f"CREATIVES (SOAP CreativeService): {cids}")
    print("=" * 72)
    cr_svc = client.GetService("CreativeService", version=V)
    stmt = ad_manager.StatementBuilder(version=V).Where(f"id IN ({cids})").Limit(50)
    creatives = cr_svc.getCreativesByStatement(stmt.ToStatement()).results or []
    if not creatives:
        print("  NOT FOUND — no creative with these ids in this network")
    for cr in creatives:
        d = _ser(cr)
        size = d.get("size") or {}
        print(f"\n--- Creative {d.get('id')}  [{type(cr).__name__}]  {d.get('name')}")
        print(f"  advertiserId: {d.get('advertiserId')}")
        print(f"  size: {size.get('width')}x{size.get('height')}"
              f"  isAspectRatio={size.get('isAspectRatio')}")
        print(f"  lastModified: {d.get('lastModifiedDateTime')}")
        for k in ("isSafeFrameCompatible", "sslScanResult", "sslManualOverride",
                  "destinationUrl", "isInterstitial", "lockedOrientation",
                  "isNativeEligible", "creativeTemplateId"):
            if d.get(k) is not None:
                print(f"  {k}: {d[k]}")
        snippet = (d.get("snippet") or d.get("htmlSnippet")
                   or d.get("codeSnippet") or d.get("expandedSnippet") or "")
        if snippet:
            print(f"  --- tag snippet ({len(snippet)} chars) ---")
            print(_truncate(snippet, SNIPPET_CHARS))
        if d.get("creativeTemplateVariableValues"):
            print(f"  templateVars: "
                  f"{_truncate(json.dumps(d['creativeTemplateVariableValues']), 2000)}")

    # ── 2. LICAs: which line items carry these creatives ────────────────
    print()
    print("=" * 72)
    print("LINE ITEM ASSOCIATIONS (SOAP LICA, by creativeId)")
    print("=" * 72)
    lica_svc = client.GetService(
        "LineItemCreativeAssociationService", version=V
    )
    stmt = ad_manager.StatementBuilder(version=V).Where(
        f"creativeId IN ({cids})"
    ).Limit(200)
    licas = lica_svc.getLineItemCreativeAssociationsByStatement(
        stmt.ToStatement()
    ).results or []
    li_ids: list[str] = []
    if not licas:
        print("  (no LICAs — creative is not associated with any line item)")
    for lica in licas:
        d = _ser(lica)
        li_id = str(d.get("lineItemId"))
        if li_id not in li_ids:
            li_ids.append(li_id)
        print(f"  LI {li_id}  <- creative {d.get('creativeId')}"
              f"  status={d.get('status')}"
              f"  startDateTimeType={d.get('startDateTimeType')}")

    # ── 3. Line items + their targeted ad units ─────────────────────────
    earliest_start = date.today()
    ad_unit_ids: set[str] = set()
    if li_ids:
        print()
        print("=" * 72)
        print("LINE ITEMS (SOAP)")
        print("=" * 72)
        li_svc = client.GetService("LineItemService", version=V)
        ids = ", ".join(li_ids)
        stmt = ad_manager.StatementBuilder(version=V).Where(f"id IN ({ids})").Limit(50)
        for li in li_svc.getLineItemsByStatement(stmt.ToStatement()).results or []:
            d = _ser(li)
            sd = (d.get("startDateTime") or {}).get("date") or {}
            if sd:
                earliest_start = min(
                    earliest_start,
                    date(int(sd["year"]), int(sd["month"]), int(sd["day"])),
                )
            print(f"\n--- LI {d.get('id')}  {d.get('name')}")
            for k in ("orderId", "orderName", "lineItemType", "priority", "status",
                      "costType", "environmentType", "roadblockingType",
                      "creativeRotationType"):
                if d.get(k) is not None:
                    print(f"  {k}: {d[k]}")
            print(f"  flight: {(d.get('startDateTime') or {}).get('date')} -> "
                  f"{(d.get('endDateTime') or {}).get('date')}")
            goal = d.get("primaryGoal") or {}
            print(f"  goal: {goal.get('goalType')} {goal.get('units')} "
                  f"{goal.get('unitType')}")
            for ph in d.get("creativePlaceholders") or []:
                size = ph.get("size") or {}
                print(f"  placeholder: {size.get('width')}x{size.get('height')}"
                      f"  sizeType={ph.get('creativeSizeType')}")
            tgt = d.get("targeting") or {}
            inv = tgt.get("inventoryTargeting") or {}
            for au in inv.get("targetedAdUnits") or []:
                ad_unit_ids.add(str(au.get("adUnitId")))
                print(f"  targeted adUnit: {au.get('adUnitId')} "
                      f"(includeDescendants={au.get('includeDescendants')})")
            ct = tgt.get("customTargeting")
            if ct:
                print(f"  customTargeting: {_truncate(json.dumps(ct), 1200)}")

    if ad_unit_ids:
        print()
        print("=" * 72)
        print("TARGETED AD UNITS (SOAP InventoryService)")
        print("=" * 72)
        inv_svc = client.GetService("InventoryService", version=V)
        au_ids = ", ".join(sorted(ad_unit_ids))
        stmt = ad_manager.StatementBuilder(version=V).Where(
            f"id IN ({au_ids})"
        ).Limit(200)
        for au in inv_svc.getAdUnitsByStatement(stmt.ToStatement()).results or []:
            d = _ser(au)
            path = "/".join(p.get("name", "") for p in d.get("parentPath") or [])
            print(f"  {d.get('id')}  {path}/{d.get('name')}  "
                  f"code={d.get('adUnitCode')}  status={d.get('status')}")

    # ── 4. Delivery + Active View reports (REST) ─────────────────────────
    if not li_ids:
        print("\n(no line items -> no delivery report to pull)\ndone.")
        return 0

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

    def _report(dims: list[str], label: str,
                creative_only: bool) -> pd.DataFrame | None:
        print()
        print("=" * 72)
        print(f"{label}  ({start} -> {yesterday})")
        print("=" * 72)
        try:
            df = gam._run_report(dims, av_metrics, start, yesterday)
        except Exception as e:  # report dim/metric incompatibilities etc.
            print(f"  REPORT FAILED: {e}")
            return None
        if "line_item_id" in df.columns:
            df = df[df["line_item_id"].astype(str).isin(li_ids)].copy()
        if creative_only and "creative_id" in df.columns:
            df = df[df["creative_id"].astype(str).isin(CREATIVE_IDS)].copy()
        if df.empty:
            print("  (no rows)")
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

    _report(["LINE_ITEM_ID", "CREATIVE_ID", "CREATIVE_NAME"],
            "ALL CREATIVES ON THE CARRYING LINE ITEMS (contrast)",
            creative_only=False)
    _report(["DATE", "LINE_ITEM_ID", "CREATIVE_ID"],
            "ASKED CREATIVE(S) BY DAY",
            creative_only=True)

    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
