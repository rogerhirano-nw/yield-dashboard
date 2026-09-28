#!/usr/bin/env python3
"""Why is this line item's CTR low? Read-only.

Cuts one LI's delivery (impressions / clicks / CTR / Active View) by creative,
rendered size, device, ad unit and day, then puts it next to peer baselines:
the rest of its order, and every STANDARD/SPONSORSHIP line on the network at
the same rendered size x device. A low CTR usually turns out to be one of:
  - a mix effect (the line is heavy on a size/device that is low-CTR everywhere),
  - one creative dragging the average (broken click-through, no clickTag),
  - low viewability (people can't click what they don't see),
  - a step change on a date (a creative swap or targeting edit).
The peer cut is what separates the first from the rest.

Usage:
    python3 scripts/diagnose_li_ctr.py 7440622442 [--days 45]
"""
from __future__ import annotations

import argparse
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

METRICS = [
    "AD_SERVER_IMPRESSIONS",
    "AD_SERVER_CLICKS",
    "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
    "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
]
pd.set_option("display.width", 250)
pd.set_option("display.max_rows", 400)


def _shape(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for m in METRICS:
        c = m.lower()
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    df = df.rename(columns={
        "ad_server_impressions": "impr",
        "ad_server_clicks": "clicks",
        "ad_server_active_view_measurable_impressions": "av_meas",
        "ad_server_active_view_viewable_impressions": "av_view",
    })
    return df


def _rates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    def _div(n, d, k):
        return (k * n.astype(float) / d.astype(float).where(d > 0)).round(3)
    df["ctr_%"] = _div(df["clicks"], df["impr"], 100)
    df["viewable_%"] = _div(df["av_view"], df["av_meas"], 100).round(1)
    df["clicks_per_1k_viewable"] = _div(df["clicks"], df["av_view"], 1000).round(2)
    return df


def _section(title: str) -> None:
    print()
    print("=" * 90)
    print(title)
    print("=" * 90)


def _report(gam, dims, start, end, filters, label, sort=None, top=None):
    _section(f"{label}  ({start} -> {end})")
    try:
        df = gam._run_report(dims, METRICS, start, end, filters=filters)
    except Exception as e:  # dim/metric incompatibility etc.
        print(f"  REPORT FAILED: {e}")
        return None
    if df.empty:
        print("  (no rows)")
        return None
    df = _rates(_shape(df))
    if "(14d, >=1k impr)" in label:
        df = df[df["impr"] >= 1000].sort_values(["line_item_id", "date"])
    if sort:
        df = df.sort_values(sort, ascending=False)
    out = df.head(top) if top else df
    print(out.drop(columns=["av_meas", "av_view"]).to_string(index=False, max_colwidth=70))
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("line_item_id", type=int)
    ap.add_argument("--days", type=int, default=45)
    a = ap.parse_args()
    li_id = a.line_item_id

    gam = GAMClient()
    client = gam._get_soap_client()
    V = gam._SOAP_API_VERSION
    from googleads import ad_manager  # type: ignore

    li_svc = client.GetService("LineItemService", version=V)
    stmt = ad_manager.StatementBuilder(version=V).Where(f"id = {li_id}").Limit(1)
    lis = li_svc.getLineItemsByStatement(stmt.ToStatement()).results or []
    if not lis:
        print(f"!! LI {li_id} not found")
        return 1
    li = lis[0]
    sd = li.startDateTime.date
    li_start = date(sd.year, sd.month, sd.day)
    order_id = int(li.orderId)
    yesterday = date.today() - timedelta(days=1)
    start = min(max(li_start, yesterday - timedelta(days=a.days)), yesterday)
    print(f"LI {li_id}  {li.name}")
    print(f"order {order_id}  type {li.lineItemType} p{li.priority}  status {li.status}"
          f"  flight start {li_start}  window {start} -> {yesterday}")
    print("placeholders: " + ", ".join(
        f"{p.size.width}x{p.size.height}" for p in (li.creativePlaceholders or [])))

    f_li = [("LINE_ITEM_ID", "IN", [li_id])]
    tot = _report(gam, ["LINE_ITEM_ID", "LINE_ITEM_NAME"], start, yesterday, f_li,
                  "LI TOTAL")
    _report(gam, ["CREATIVE_ID", "CREATIVE_NAME", "RENDERED_CREATIVE_SIZE"], start,
            yesterday, f_li, "BY CREATIVE", sort="impr")
    li_sd = _report(gam, ["RENDERED_CREATIVE_SIZE", "DEVICE_CATEGORY_NAME"], start,
                    yesterday, f_li, "BY RENDERED SIZE x DEVICE", sort="impr")
    _report(gam, ["AD_UNIT_NAME"], start, yesterday, f_li, "BY AD UNIT (leaf)",
            sort="impr", top=40)
    _report(gam, ["DATE"], start, yesterday, f_li, "BY DAY")
    _report(gam, ["DEVICE_CATEGORY_NAME", "BROWSER_NAME"], start, yesterday, f_li,
            "BY DEVICE x BROWSER", sort="impr", top=25)
    _report(gam, ["CREATIVE_ID", "DATE"], start, yesterday, f_li,
            "BY CREATIVE x DAY (last 14 rows per creative)")

    # ── Peers ────────────────────────────────────────────────────────────
    # Peers use the full lookback even when the LI itself is days old.
    pstart = yesterday - timedelta(days=a.days)
    unit_df = _report(gam, ["LINE_ITEM_ID", "AD_UNIT_NAME"], start, yesterday, f_li,
                      "LI AD UNITS (for the peer cut)")
    units = sorted(set(unit_df["ad_unit_name"])) if unit_df is not None else []
    if units:
        _report(gam, ["ORDER_NAME", "LINE_ITEM_ID", "LINE_ITEM_NAME"], pstart, yesterday,
                [("AD_UNIT_NAME", "IN", units)],
                f"PEERS: EVERY LI ON AD UNIT(S) {units} (top 40 by impr)",
                sort="impr", top=40)
        # Did the whole unit move on a date (site change), or just this LI?
        _report(gam, ["DATE"], yesterday - timedelta(days=21), yesterday,
                [("AD_UNIT_NAME", "IN", units)], f"PEERS: AD UNIT(S) {units} BY DAY (21d)")
        _report(gam, ["DATE", "LINE_ITEM_ID"], yesterday - timedelta(days=14), yesterday,
                [("AD_UNIT_NAME", "IN", units)],
                f"PEERS: AD UNIT(S) {units} BY DAY x LI (14d, >=1k impr)", sort=None)
        _report(gam, ["DEVICE_CATEGORY_NAME"], pstart, yesterday,
                [("AD_UNIT_NAME", "IN", units)], f"PEERS: AD UNIT(S) {units} BY DEVICE")
    _report(gam, ["LINE_ITEM_ID", "LINE_ITEM_NAME"], pstart, yesterday,
            [("LINE_ITEM_NAME", "CONTAINS", ["Interstitial"])],
            "PEERS: EVERY LI NAMED *Interstitial* (top 40 by impr)", sort="impr", top=40)
    _report(gam, ["LINE_ITEM_ID", "LINE_ITEM_NAME"], pstart, yesterday,
            [("ORDER_ID", "IN", [order_id])], "PEERS: OTHER LIs IN THIS ORDER",
            sort="impr")

    peer = _report(
        gam, ["RENDERED_CREATIVE_SIZE", "DEVICE_CATEGORY_NAME"], pstart, yesterday,
        [("LINE_ITEM_TYPE", "IN", ["STANDARD", "SPONSORSHIP"])],
        "PEERS: NETWORK STANDARD+SPONSORSHIP BY SIZE x DEVICE (top 30)",
        sort="impr", top=30,
    )

    # Mix-adjusted expectation: this LI's size x device mix at peer CTRs.
    if li_sd is not None and peer is not None and tot is not None:
        _section("MIX-ADJUSTED EXPECTATION")
        k = ["rendered_creative_size", "device_category_name"]
        m = li_sd[k + ["impr", "clicks"]].merge(
            peer[k + ["ctr_%", "viewable_%"]].rename(
                columns={"ctr_%": "peer_ctr_%", "viewable_%": "peer_viewable_%"}),
            on=k, how="left")
        m["expected_clicks"] = m["impr"] * m["peer_ctr_%"].fillna(0) / 100
        print(m.to_string(index=False))
        actual = int(tot["clicks"].sum())
        exp = float(m["expected_clicks"].sum())
        imps = int(tot["impr"].sum())
        print(f"\nactual CTR   {100 * actual / max(imps, 1):.3f}%  ({actual:,} clicks / {imps:,} impr)")
        print(f"expected CTR {100 * exp / max(imps, 1):.3f}%  (same mix at network peer CTRs)")
        print(f"ratio actual/expected = {actual / exp:.2f}" if exp else "ratio n/a")

    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
