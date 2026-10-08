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
        # Attention is a standing part of every final report (Roger,
        # 2026-10-08) — fail rather than ship a report without it.
        raise SystemExit("!! DATABASE_URL is not set — DV Attention is required "
                         "in a final report (run via the workflow, or pass --no-dv)")
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
            overall = pd.read_sql(text(
                "SELECT AVG(attention_index) AS attention_index, COUNT(*) AS rows "
                "FROM dv_attention WHERE line_item_id = ANY(:ids) "
                "AND attention_index IS NOT NULL"),
                c, params={"ids": ids})
            daily = pd.read_sql(text(
                "SELECT date, AVG(attention_index) AS attention_index "
                "FROM dv_attention WHERE line_item_id = ANY(:ids) "
                "AND attention_index IS NOT NULL GROUP BY date ORDER BY date"),
                c, params={"ids": ids})
            out["attention_daily"] = {str(d)[:10]: float(a) for d, a in
                                      zip(daily["date"], daily["attention_index"])}
            v = overall["attention_index"].iloc[0] if not overall.empty else None
            out["attention_overall"] = None if v is None or pd.isna(v) else float(v)
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
    _attention_from_inbox(out, ids, start, end)
    return out


def _attention_from_inbox(out: dict, ids: list[str], start, end) -> None:
    """Full-flight Attention straight from DV's report emails.

    The `dv_attention` cache is a rolling window, not history: each refresh
    `_safe_replace`s it with the 2 newest DV emails (each a rolling 7 days),
    so a flight longer than ~a week has its early days overwritten (seen
    2026-10-08: Elevance, 23 Sep-7 Oct flight, cache held only 30 Sep-6 Oct).
    The emails themselves stay in the inbox, so read back far enough to cover
    the flight. Newest email wins per (date, line item), as in the cache.
    Overrides the cache figures only when the inbox yields rows."""
    key, inbox = os.environ.get("AGENTMAIL_API_KEY"), os.environ.get("AGENTMAIL_INBOX_ID")
    if not key or not inbox:
        out.setdefault("notes", []).append("AGENTMAIL creds not set — attention from cache only")
        return
    from dv_attention_client import pull_dv_attention
    days = (end - start).days + 1
    try:
        df = pull_dv_attention(key, inbox, limit=min(60, days + 10))
    except Exception as e:
        out.setdefault("notes", []).append(f"inbox pull failed: {type(e).__name__}: {e}")
        return
    if df.empty or "line_item_id" not in df.columns:
        return
    df = df[df["line_item_id"].isin(set(ids)) & df["attention_index"].notna()].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    if df.empty:
        return
    # Newest email = the one whose rolling window reaches furthest; for each
    # (date, LI) keep only that email's rows (no assumption on inbox order).
    msg_end = df.groupby("_email_message_id")["date"].transform("max")
    df = df.assign(_msg_end=msg_end)
    best = df.groupby(["date", "line_item_id"])["_msg_end"].transform("max")
    df = df[df["_msg_end"] == best]
    df = df.drop_duplicates(subset=[c for c in df.columns if c not in ("_email_message_id",)])
    out["attention"] = [
        {"line_item_id": li, "attention_index": float(g["attention_index"].mean()),
         "rows": int(len(g)), "first_date": str(g["date"].min()), "last_date": str(g["date"].max())}
        for li, g in df.groupby("line_item_id")]
    out["attention_daily"] = {str(d): float(g["attention_index"].mean())
                              for d, g in df.groupby("date")}
    out["attention_overall"] = float(df["attention_index"].mean())
    out["attention_source"] = "DV report emails (inbox)"


def _fmt_int(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{int(round(v)):,}"


def _fmt_att(v) -> str:
    """DV Attention index — 100 = DV's baseline. A gap reads as "no DV data",
    never as a number."""
    return "no DV data" if v is None or pd.isna(v) else f"{v:.0f}"


def _fmt_pct(v, dp=1) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:.{dp}f}%"


def build_markdown(p: dict) -> str:
    o, lis = p["order"], p["line_items"]
    by_li = {r["line_item_id"]: r for r in p["by_line_item"]}
    t = p["totals"]
    dv = p.get("dv") or {}
    att_all = dv.get("attention_overall")
    att_by_li = {str(a["line_item_id"]): a.get("attention_index") for a in dv.get("attention") or []}
    L = [f"# Final report — {o['name']}", ""]
    L += [f"- **Order:** {o['id']} · {o['status']}",
          f"- **Advertiser:** {o.get('advertiser') or '—'}",
          f"- **Seller:** {o.get('seller') or '—'}",
          f"- **Flight:** {o['start']} → {o['end']}",
          f"- **Reporting window:** {p['window']['start']} → {p['window']['end']}"
          + (" (flight not yet ended — partial)" if p["window"]["partial"] else ""),
          ""]
    L += ["## Totals", "",
          "| Impressions | Goal | % of goal | Clicks | CTR | Viewability | Measurable | Attention | Revenue |",
          "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
          f"| {_fmt_int(t['impressions'])} | {_fmt_int(t['goal'])} | {_fmt_pct(t['pct_of_goal'])} "
          f"| {_fmt_int(t['clicks'])} | {_fmt_pct(t['ctr_pct'], 2)} | {_fmt_pct(t['viewability_pct'])} "
          f"| {_fmt_pct(t['measurable_pct'])} | {_fmt_att(att_all)} | ${t['revenue']:,.2f} |", ""]
    L += ["## By line item", "",
          "| Line item | ID | Type | Flight | Goal | Delivered | % goal | Clicks | CTR | Viewability | Attention | CPM | Revenue |",
          "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
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
            f"| {_fmt_att(att_by_li.get(li['id']))} "
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
        att_day = dv.get("attention_daily") or {}
        L += ["## Daily delivery", "",
              "| Date | Impressions | Clicks | CTR | Viewability | Attention |",
              "|---|---:|---:|---:|---:|---:|"]
        for r in daily:
            L.append(f"| {r['date']} | {_fmt_int(r['ad_server_impressions'])} | {_fmt_int(r['ad_server_clicks'])} "
                     f"| {_fmt_pct(r['ctr_pct'], 2)} | {_fmt_pct(r['viewability_pct'])} "
                     f"| {_fmt_att(att_day.get(str(r['date'])[:10])) if str(r['date'])[:10] in att_day or not att_day or str(r['date'])[:10] < max(att_day) else 'pending (DV lag)'} |")
        L.append("")
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


def _client_logo(src: str | None, xlsx_path: str) -> str | None:
    """Local PNG path for the client logo: downloads a URL and rasterises an
    SVG (via cairosvg) next to the workbook. None when no logo was given."""
    if not src:
        return None
    import urllib.request
    data = src
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=30).read()
    else:
        data = Path(src).read_bytes()
    out = Path(xlsx_path).with_name("client_logo.png")
    if src.lower().split("?")[0].endswith(".svg") or data.lstrip()[:5] in (b"<?xml", b"<svg "):
        import cairosvg
        cairosvg.svg2png(bytestring=data, write_to=str(out), output_height=176)
    else:
        out.write_bytes(data)
    return str(out)


def _pretty(tok: str) -> str:
    return tok.replace("-", " ").strip()


def _decorate(p: dict) -> dict:
    """Client-facing labels from the Newsweek naming convention: advertiser =
    token 7, campaign = token 8 with the advertiser prefix it repeats dropped
    (the same rule as dashboard_logic.line_item_display_name)."""
    o = p["order"]
    o["seller"] = _seller(o.get("name"))
    parts = (o.get("name") or "").split("_")
    if len(parts) > 8 and parts[0] == "Newsweek":
        adv, camp = parts[7], parts[8]
        if camp.lower().startswith(adv.lower() + "-"):
            camp = camp[len(adv) + 1:]
        o["display_advertiser"], o["display_campaign"] = _pretty(adv), _pretty(camp)
    for li in p["line_items"]:
        lp = (li.get("name") or "").split("_")
        fmt = lp[10] if len(lp) > 10 else ""
        sizes = ", ".join(s for s in dict.fromkeys(li.get("sizes") or []) if s)
        li["display_name"] = " · ".join(x for x in (_pretty(fmt), sizes) if x) or li["name"]
    w = p["window"]
    def _d(s):
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
        return f"{dt.day} {dt.strftime('%b %Y')}"
    p["flight_label"] = f"{_d(w['start'])} – {_d(w['end'])}"
    att_day = (p.get("dv") or {}).get("attention_daily") or {}
    if att_day and not p["dv"].get("attention_window"):
        ks = sorted(att_day)
        p["dv"]["attention_window"] = f"{_d(ks[0])} – {_d(ks[-1])}"
    p.setdefault("pulled", w["end"])
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", default=os.environ.get("FINAL_REPORT_ORDER_ID"))
    ap.add_argument("--out", default="final_report.json")
    ap.add_argument("--markdown", default="final_report.md")
    ap.add_argument("--xlsx", default=None,
                    help="also write a Newsweek-styled Excel report here")
    ap.add_argument("--client-logo", default=os.environ.get("FINAL_REPORT_CLIENT_LOGO") or None,
                    help="client logo for the workbook masthead: a PNG/JPG path or URL "
                         "(SVG needs cairosvg)")
    ap.add_argument("--from-json", default=None,
                    help="re-render from a saved payload instead of pulling")
    ap.add_argument("--no-dv", action="store_true",
                    help="skip DV Attention/IVT (local runs without DATABASE_URL)")
    args = ap.parse_args()
    if not args.order and not args.from_json:
        ap.error("pass --order or set FINAL_REPORT_ORDER_ID")

    if args.from_json:
        payload = _decorate(json.loads(Path(args.from_json).read_text()))
        Path(args.markdown).write_text(build_markdown(payload))
        if args.xlsx:
            from final_report_xlsx import build_xlsx
            build_xlsx(payload, args.xlsx, _client_logo(args.client_logo, args.xlsx))
        return 0

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
        "dv": {"note": "skipped (--no-dv)"} if args.no_dv else _dv(li_ids, start, end),
    }
    payload["pulled"] = str(date.today())
    payload = _decorate(payload)
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str))
    if args.xlsx:
        from final_report_xlsx import build_xlsx
        build_xlsx(payload, args.xlsx, _client_logo(args.client_logo, args.xlsx))
    md = build_markdown(payload)
    Path(args.markdown).write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
