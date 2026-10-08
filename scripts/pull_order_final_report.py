#!/usr/bin/env python3
"""Read-only end-of-flight ("final") delivery report for any GAM order.

Given an order id, pulls:
  - order + line items (SOAP): name, status, type, flight, goal, cost type, rate
  - delivery (REST v1 historical report, flight start -> min(end, yesterday)):
      by line item, by line item x day, by rendered creative size, by device —
      impressions, clicks, CTR, Active View viewable/measurable, revenue
  - DoubleVerify Attention + IVT from the cache DB, when DATABASE_URL is set

Writes JSON to --out and the report body as markdown to --markdown. No writes
to GAM or the DB.

Usage:
  python scripts/pull_order_final_report.py --order 4202666637 \
      --out final.json --markdown final.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from gam_client import GAMClient  # noqa: E402
from pull_screenshots_source import V, _date, _g, _page, _seller, _size  # noqa: E402

METRICS = [
    "AD_SERVER_IMPRESSIONS",
    "AD_SERVER_CLICKS",
    "AD_SERVER_REVENUE",
    "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
    "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
]


def _micro(m) -> float | None:
    if m is None:
        return None
    v = getattr(m, "microAmount", None)
    return None if v is None else int(v) / 1e6


def _num(df: pd.DataFrame) -> pd.DataFrame:
    for m in METRICS:
        col = m.lower()
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def _rates(df: pd.DataFrame) -> pd.DataFrame:
    imp = df["ad_server_impressions"]
    meas = df["ad_server_active_view_measurable_impressions"]
    df["ctr_pct"] = (df["ad_server_clicks"] / imp.where(imp > 0) * 100).round(3)
    df["viewability_pct"] = (
        df["ad_server_active_view_viewable_impressions"] / meas.where(meas > 0) * 100
    ).round(1)
    df["measurable_pct"] = (meas / imp.where(imp > 0) * 100).round(1)
    return df


def _report(gc: GAMClient, dims, li_ids, start, end) -> pd.DataFrame:
    df = gc._run_report(
        dimensions=dims,
        metrics=METRICS,
        start_date=start,
        end_date=end,
        filters=[("LINE_ITEM_ID", "IN", [int(i) for i in li_ids])],
    )
    if df.empty:
        return df
    if "line_item_id" in df.columns:
        df["line_item_id"] = df["line_item_id"].astype(str)
        df = df[df["line_item_id"].isin({str(i) for i in li_ids})]
    return _rates(_num(df.copy()))


def _dv(li_ids, start, end) -> dict:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"note": "DATABASE_URL not set — DV skipped"}
    from sqlalchemy import create_engine, text
    out: dict = {}
    eng = create_engine(url)
    ids = [str(i) for i in li_ids]
    try:
        with eng.connect() as c:
            att = pd.read_sql(text(
                "SELECT line_item_id, AVG(attention_index) AS attention_index, "
                "COUNT(*) AS rows, MIN(date) AS first_date, MAX(date) AS last_date "
                "FROM dv_attention WHERE line_item_id = ANY(:ids) "
                "AND attention_index IS NOT NULL GROUP BY line_item_id"),
                c, params={"ids": ids})
            out["attention"] = att.to_dict(orient="records")
            ivt = pd.read_sql(text(
                "SELECT line_item_id, traffic_validity, SUM(monitored_ads) AS monitored_ads "
                "FROM dv_ivt WHERE line_item_id = ANY(:ids) "
                "GROUP BY line_item_id, traffic_validity"),
                c, params={"ids": ids})
            out["ivt_rows"] = ivt.to_dict(orient="records")
            if not ivt.empty:
                ivt["monitored_ads"] = pd.to_numeric(ivt["monitored_ads"], errors="coerce").fillna(0)
                tot = ivt["monitored_ads"].sum()
                by = ivt.groupby("traffic_validity")["monitored_ads"].sum()
                out["ivt_totals"] = {
                    "monitored_ads": float(tot),
                    **{k: round(float(v) / tot * 100, 3) if tot else None
                       for k, v in by.items()},
                }
    except Exception as e:  # report what's there; DV is supplementary
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _fmt_int(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{int(round(v)):,}"


def _fmt_pct(v, dp=1) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:.{dp}f}%"


def build_markdown(p: dict) -> str:
    o, lis = p["order"], p["line_items"]
    by_li = {r["line_item_id"]: r for r in p["by_line_item"]}
    t = p["totals"]
    L = [f"# Final report — {o['name']}", ""]
    L += [f"- **Order:** {o['id']} · {o['status']}",
          f"- **Advertiser:** {o.get('advertiser') or '—'}",
          f"- **Seller:** {o.get('seller') or '—'}",
          f"- **Flight:** {o['start']} → {o['end']}",
          f"- **Reporting window:** {p['window']['start']} → {p['window']['end']}"
          + (" (flight not yet ended — partial)" if p["window"]["partial"] else ""),
          ""]
    L += ["## Totals", "",
          "| Impressions | Goal | % of goal | Clicks | CTR | Viewability | Measurable | Revenue |",
          "|---:|---:|---:|---:|---:|---:|---:|---:|",
          f"| {_fmt_int(t['impressions'])} | {_fmt_int(t['goal'])} | {_fmt_pct(t['pct_of_goal'])} "
          f"| {_fmt_int(t['clicks'])} | {_fmt_pct(t['ctr_pct'], 2)} | {_fmt_pct(t['viewability_pct'])} "
          f"| {_fmt_pct(t['measurable_pct'])} | ${t['revenue']:,.2f} |", ""]
    L += ["## By line item", "",
          "| Line item | ID | Type | Flight | Goal | Delivered | % goal | Clicks | CTR | Viewability | CPM | Revenue |",
          "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for li in lis:
        r = by_li.get(li["id"], {})
        imp = r.get("ad_server_impressions", 0) or 0
        goal = li.get("goal_units")
        pg = imp / goal * 100 if goal and goal > 0 else None
        L.append(
            f"| {li['name']} | {li['id']} | {li['line_item_type']} | {li['start'][:10] if li['start'] else '—'} → "
            f"{li['end'][:10] if li['end'] else '—'} | {_fmt_int(goal) if goal and goal > 0 else '—'} "
            f"| {_fmt_int(imp)} | {_fmt_pct(pg)} | {_fmt_int(r.get('ad_server_clicks'))} "
            f"| {_fmt_pct(r.get('ctr_pct'), 2)} | {_fmt_pct(r.get('viewability_pct'))} "
            f"| {'$%.2f' % li['rate'] if li.get('rate') is not None else '—'} "
            f"| ${(r.get('ad_server_revenue') or 0):,.2f} |")
    L.append("")
    for key, title, col in (("by_size", "By creative size", "rendered_creative_size"),
                            ("by_device", "By device", "device_category_name")):
        rows = p.get(key) or []
        if not rows:
            continue
        L += [f"## {title}", "", "| | Impressions | Clicks | CTR | Viewability |", "|---|---:|---:|---:|---:|"]
        for r in rows:
            L.append(f"| {r[col]} | {_fmt_int(r['ad_server_impressions'])} | {_fmt_int(r['ad_server_clicks'])} "
                     f"| {_fmt_pct(r['ctr_pct'], 2)} | {_fmt_pct(r['viewability_pct'])} |")
        L.append("")
    daily = p.get("by_day") or []
    if daily:
        L += ["## By day", "", "| Date | Impressions | Clicks | CTR | Viewability |", "|---|---:|---:|---:|---:|"]
        for r in daily:
            L.append(f"| {r['date']} | {_fmt_int(r['ad_server_impressions'])} | {_fmt_int(r['ad_server_clicks'])} "
                     f"| {_fmt_pct(r['ctr_pct'], 2)} | {_fmt_pct(r['viewability_pct'])} |")
        L.append("")
    dv = p.get("dv") or {}
    L += ["## DoubleVerify", ""]
    if dv.get("attention"):
        for a in dv["attention"]:
            L.append(f"- Attention index LI {a['line_item_id']}: {a['attention_index']:.0f} "
                     f"(100 = DV baseline; {a['first_date']} → {a['last_date']})")
    if dv.get("ivt_totals"):
        L.append(f"- IVT (impression-weighted over {_fmt_int(dv['ivt_totals']['monitored_ads'])} monitored ads): "
                 + ", ".join(f"{k} {v:.2f}%" for k, v in dv["ivt_totals"].items()
                             if k != "monitored_ads" and v is not None))
    if not dv.get("attention") and not dv.get("ivt_totals"):
        L.append(f"- No DV rows for these line items. {dv.get('note') or dv.get('error') or ''}")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", default=os.environ.get("FINAL_REPORT_ORDER_ID"))
    ap.add_argument("--out", default="final_report.json")
    ap.add_argument("--markdown", default="final_report.md")
    args = ap.parse_args()
    if not args.order:
        ap.error("pass --order or set FINAL_REPORT_ORDER_ID")

    gc = GAMClient()
    client = gc._get_soap_client()

    orders = _page(client.GetService("OrderService", version=V),
                   "getOrdersByStatement", f"id = {int(args.order)}")
    if not orders:
        print(f"!! order {args.order} not found", file=sys.stderr)
        return 1
    o = orders[0]
    adv = None
    if _g(o, "advertiserId"):
        cs = _page(client.GetService("CompanyService", version=V),
                   "getCompaniesByStatement", f"id = {int(_g(o, 'advertiserId'))}")
        adv = getattr(cs[0], "name", None) if cs else None
    order = {
        "id": str(_g(o, "id")), "name": _g(o, "name"), "status": str(_g(o, "status")),
        "advertiser": adv, "seller": _seller(_g(o, "name")),
        "start": _date(_g(o, "startDateTime")), "end": _date(_g(o, "endDateTime")),
        "po_number": _g(o, "poNumber"),
    }

    lis = _page(client.GetService("LineItemService", version=V),
                "getLineItemsByStatement", f"orderId = {int(args.order)}")
    line_items = []
    for li in lis:
        goal = _g(li, "primaryGoal")
        line_items.append({
            "id": str(_g(li, "id")), "name": _g(li, "name"), "status": str(_g(li, "status")),
            "line_item_type": str(_g(li, "lineItemType")),
            "start": _date(_g(li, "startDateTime")), "end": _date(_g(li, "endDateTime")),
            "goal_units": _g(goal, "units") if goal is not None else None,
            "goal_type": str(_g(goal, "goalType")) if goal is not None else None,
            "unit_type": str(_g(goal, "unitType")) if goal is not None else None,
            "cost_type": str(_g(li, "costType")), "rate": _micro(_g(li, "costPerUnit")),
            "sizes": [_size(getattr(ph, "size", None)) for ph in (_g(li, "creativePlaceholders") or [])],
            "is_archived": _g(li, "isArchived"),
        })
    li_ids = [li["id"] for li in line_items]
    print(f"order {order['id']} {order['name']}: {len(li_ids)} line items")

    starts = [li["start"] for li in line_items if li["start"]] or [order["start"]]
    ends = [li["end"] for li in line_items if li["end"]] or [order["end"]]
    start = datetime.strptime(min(starts)[:10], "%Y-%m-%d").date()
    flight_end = datetime.strptime(max(ends)[:10], "%Y-%m-%d").date()
    yesterday = date.today() - timedelta(days=1)
    end = min(flight_end, yesterday)

    by_li = _report(gc, ["LINE_ITEM_ID", "LINE_ITEM_NAME"], li_ids, start, end)
    by_day = _report(gc, ["DATE"], li_ids, start, end)
    by_size = _report(gc, ["RENDERED_CREATIVE_SIZE"], li_ids, start, end)
    by_device = _report(gc, ["DEVICE_CATEGORY_NAME"], li_ids, start, end)
    if not by_day.empty:
        by_day = by_day.sort_values("date")
    for df in (by_size, by_device):
        if not df.empty:
            df.sort_values("ad_server_impressions", ascending=False, inplace=True)

    def _sum(c):
        return float(by_li[c].sum()) if not by_li.empty else 0.0
    imp, clk = _sum("ad_server_impressions"), _sum("ad_server_clicks")
    view, meas = (_sum("ad_server_active_view_viewable_impressions"),
                  _sum("ad_server_active_view_measurable_impressions"))
    goal = sum(li["goal_units"] for li in line_items
               if li["goal_units"] and li["goal_units"] > 0 and li["unit_type"] == "IMPRESSIONS")
    totals = {
        "impressions": imp, "clicks": clk, "revenue": _sum("ad_server_revenue"),
        "goal": goal or None, "pct_of_goal": imp / goal * 100 if goal else None,
        "ctr_pct": clk / imp * 100 if imp else None,
        "viewability_pct": view / meas * 100 if meas else None,
        "measurable_pct": meas / imp * 100 if imp else None,
    }

    payload = {
        "order": order, "line_items": line_items,
        "window": {"start": str(start), "end": str(end), "partial": end < flight_end},
        "totals": totals,
        "by_line_item": by_li.to_dict(orient="records"),
        "by_day": by_day.to_dict(orient="records"),
        "by_size": by_size.to_dict(orient="records"),
        "by_device": by_device.to_dict(orient="records"),
        "dv": _dv(li_ids, start, end),
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str))
    md = build_markdown(payload)
    Path(args.markdown).write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
