"""One-off GAM pull: Cognizant AI Impact Summit — CTR optimization diagnostics.

Read-only. Pulls per-line-item / per-creative / per-size / per-device / weekly
delivery + clicks for every order whose name contains the needle (default
"cognizant"), so we can see *where* the under-benchmark CTR is coming from:
which creatives drag the average, whether rotation is EVEN vs OPTIMIZED,
which sizes/devices are weakest, and how CTR trends week over week.

Dispatched by .github/workflows/cognizant_media_perf.yml (GAM creds from repo
secrets — this cloud session has none locally, same pattern as
pull_index_ob_requests.yml). Prints markdown to stdout; the workflow posts it
as a PR comment.

Env overrides: ORDER_NEEDLE, PULL_START (YYYY-MM-DD), PULL_END (YYYY-MM-DD).
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gam_client import GAMClient  # noqa: E402

_ET = ZoneInfo("America/New_York")

NEEDLE = os.environ.get("ORDER_NEEDLE", "cognizant").lower()
# Branded-article promo flights began 4/28; brand media 7/1. Cover both.
START = date.fromisoformat(os.environ.get("PULL_START", "2026-04-28"))
END = (
    date.fromisoformat(os.environ["PULL_END"])
    if os.environ.get("PULL_END")
    else datetime.now(_ET).date() - timedelta(days=1)  # yesterday ET — same-day data lags
)


def _pct(n: float, d: float) -> str:
    return f"{n / d:.3%}" if d else "—"


def _num(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def try_report(client: GAMClient, title: str, dims: list[str], mets: list[str]) -> pd.DataFrame | None:
    """Run one report; on REPORT_ERROR_CONSTRAINTS_INCOMPATIBILITY (or any API
    error) print the failure and keep going — each section degrades independently."""
    try:
        return client._run_report(dims, mets, START, END)
    except Exception as exc:  # noqa: BLE001 — one-off diagnostic, report and continue
        print(f"\n> ⚠️ `{title}` report failed: `{type(exc).__name__}: {exc}`\n")
        return None


def _filter(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty or "order_name" not in df.columns:
        return df if df is not None else None
    return df[df["order_name"].str.lower().str.contains(NEEDLE, na=False)].copy()


def _table(df: pd.DataFrame, cols: list[str], headers: list[str], max_rows: int = 60) -> None:
    print("| " + " | ".join(headers) + " |")
    print("|" + "---|" * len(headers))
    shown = df.head(max_rows)
    for _, r in shown.iterrows():
        print("| " + " | ".join(str(r[c]) for c in cols) + " |")
    dropped = len(df) - len(shown)
    if dropped > 0:
        print(f"\n_…{dropped} more rows omitted (sorted by impressions; the tail is low-volume)._")


def main() -> None:
    client = GAMClient()
    print(f"# Cognizant GAM diagnostics — {START} → {END}")
    print(f"\nOrder filter: name contains `{NEEDLE}` (case-insensitive).\n")

    # ---- 1. Line-item summary --------------------------------------------
    li = _filter(
        try_report(
            client,
            "line-item summary",
            ["LINE_ITEM_ID", "LINE_ITEM_NAME", "ORDER_ID", "ORDER_NAME"],
            [
                "AD_SERVER_IMPRESSIONS",
                "AD_SERVER_CLICKS",
                "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS_RATE",
                "VIDEO_VIEWERSHIP_STARTS",
                "VIDEO_VIEWERSHIP_COMPLETES",
            ],
        )
    )
    if li is None or li.empty:
        # Wrong needle? Surface what orders DO exist so the needle can be fixed.
        allo = try_report(client, "order inventory", ["ORDER_ID", "ORDER_NAME"], ["AD_SERVER_IMPRESSIONS"])
        print("## ⚠️ No line items matched the order filter\n")
        if allo is not None and not allo.empty:
            allo = allo.sort_values("ad_server_impressions", ascending=False).head(40)
            print("Top orders by impressions in the window (adjust `ORDER_NEEDLE`):\n")
            _table(allo, ["order_name", "ad_server_impressions"], ["Order", "Impr"], 40)
        return

    li["impr"] = li["ad_server_impressions"].map(_num)
    li["clicks"] = li["ad_server_clicks"].map(_num)
    li["ctr"] = li.apply(lambda r: _pct(r["clicks"], r["impr"]), axis=1)
    li["starts"] = li["video_viewership_starts"].map(_num)
    li["completes"] = li["video_viewership_completes"].map(_num)
    li["vcr"] = li.apply(lambda r: _pct(r["completes"], r["starts"]), axis=1)
    def _rate(v) -> str:
        if v is None or v == "" or pd.isna(v):
            return "—"
        f = float(v)
        if f > 1.5:  # some builds return 0-100 instead of 0-1
            f /= 100.0
        return f"{f:.1%}"

    li["viewable"] = li["ad_server_active_view_viewable_impressions_rate"].map(_rate)
    li = li.sort_values(["order_name", "impr"], ascending=[True, False])
    print(f"## 1. Line items ({len(li)}) — flight-to-date\n")
    _table(
        li,
        ["order_name", "line_item_name", "line_item_id", "impr", "clicks", "ctr", "viewable", "vcr"],
        ["Order", "Line item", "LI id", "Impr", "Clicks", "CTR", "Viewable", "VCR"],
    )

    # ---- 2. Creative rotation type per LI --------------------------------
    rot = _filter(
        try_report(
            client,
            "creative rotation",
            ["LINE_ITEM_ID", "LINE_ITEM_NAME", "ORDER_NAME", "LINE_ITEM_CREATIVE_ROTATION_TYPE_NAME"],
            ["AD_SERVER_IMPRESSIONS"],
        )
    )
    if rot is not None and not rot.empty:
        rot = rot.sort_values(["order_name", "line_item_name"])
        print("\n## 2. Creative rotation per line item\n")
        print("(EVEN rotation on a multi-creative LI leaves CTR on the table — OPTIMIZED lets GAM shift serves to the clickier creative.)\n")
        _table(
            rot,
            ["line_item_name", "line_item_creative_rotation_type_name"],
            ["Line item", "Rotation"],
        )

    # ---- 3. Per-creative -------------------------------------------------
    cr = _filter(
        try_report(
            client,
            "per-creative",
            ["ORDER_NAME", "LINE_ITEM_NAME", "CREATIVE_ID", "CREATIVE_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS"],
        )
    )
    if cr is not None and not cr.empty:
        cr["impr"] = cr["ad_server_impressions"].map(_num)
        cr["clicks"] = cr["ad_server_clicks"].map(_num)
        cr["ctr"] = cr.apply(lambda r: _pct(r["clicks"], r["impr"]), axis=1)
        li_tot = cr.groupby("line_item_name")["impr"].transform("sum")
        cr["share"] = (cr["impr"] / li_tot.where(li_tot > 0, 1)).map(lambda v: f"{v:.0%}")
        cr = cr.sort_values(["order_name", "line_item_name", "impr"], ascending=[True, True, False])
        print(f"\n## 3. Per-creative ({len(cr)} rows) — impressions share within LI + CTR\n")
        _table(
            cr,
            ["line_item_name", "creative_name", "creative_id", "impr", "share", "clicks", "ctr"],
            ["Line item", "Creative", "Creative id", "Impr", "LI share", "Clicks", "CTR"],
            max_rows=80,
        )

    # ---- 4. Per rendered size --------------------------------------------
    sz = _filter(
        try_report(
            client,
            "per-size",
            ["ORDER_NAME", "LINE_ITEM_NAME", "RENDERED_CREATIVE_SIZE"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS"],
        )
    )
    if sz is not None and not sz.empty:
        sz["impr"] = sz["ad_server_impressions"].map(_num)
        sz["clicks"] = sz["ad_server_clicks"].map(_num)
        sz["ctr"] = sz.apply(lambda r: _pct(r["clicks"], r["impr"]), axis=1)
        sz = sz.sort_values(["order_name", "line_item_name", "impr"], ascending=[True, True, False])
        print("\n## 4. Per rendered creative size\n")
        _table(
            sz,
            ["line_item_name", "rendered_creative_size", "impr", "clicks", "ctr"],
            ["Line item", "Size", "Impr", "Clicks", "CTR"],
        )

    # ---- 5. Per device ----------------------------------------------------
    dev = _filter(
        try_report(
            client,
            "per-device",
            ["ORDER_NAME", "LINE_ITEM_NAME", "DEVICE_CATEGORY_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS"],
        )
    )
    if dev is not None and not dev.empty:
        dev["impr"] = dev["ad_server_impressions"].map(_num)
        dev["clicks"] = dev["ad_server_clicks"].map(_num)
        dev["ctr"] = dev.apply(lambda r: _pct(r["clicks"], r["impr"]), axis=1)
        dev = dev.sort_values(["order_name", "line_item_name", "impr"], ascending=[True, True, False])
        print("\n## 5. Per device category\n")
        _table(
            dev,
            ["line_item_name", "device_category_name", "impr", "clicks", "ctr"],
            ["Line item", "Device", "Impr", "Clicks", "CTR"],
        )

    # ---- 6. Weekly CTR trend ----------------------------------------------
    daily = _filter(
        try_report(
            client,
            "daily trend",
            ["DATE", "ORDER_NAME", "LINE_ITEM_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS"],
        )
    )
    if daily is not None and not daily.empty:
        daily["impr"] = daily["ad_server_impressions"].map(_num)
        daily["clicks"] = daily["ad_server_clicks"].map(_num)
        daily["week"] = pd.to_datetime(daily["date"]).dt.to_period("W").dt.start_time.dt.strftime("%m/%d")
        wk = daily.groupby(["line_item_name", "week"])[["impr", "clicks"]].sum().reset_index()
        wk["ctr"] = wk.apply(lambda r: _pct(r["clicks"], r["impr"]), axis=1)
        pivot = wk.pivot(index="line_item_name", columns="week", values="ctr").fillna("—")
        print("\n## 6. Weekly CTR trend (week starting)\n")
        cols = list(pivot.columns)
        print("| Line item | " + " | ".join(cols) + " |")
        print("|" + "---|" * (len(cols) + 1))
        for name, row in pivot.iterrows():
            print(f"| {name} | " + " | ".join(str(row[c]) for c in cols) + " |")

    print("\n---\n_Read-only pull; no GAM entities were modified._")


if __name__ == "__main__":
    main()
