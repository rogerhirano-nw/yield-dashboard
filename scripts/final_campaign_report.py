#!/usr/bin/env python3
"""Read-only end-of-campaign (final) delivery report for any GAM order.

Given an order id, pulls the order + its line items (SOAP, for goal, flight,
CPM and status) and the order's delivery over its whole flight (REST
Historical report, filtered server-side to ORDER_ID), then writes:

  - totals per line item: goal, delivered, % of goal, clicks, CTR, revenue,
    Active View measurable % / viewable %
  - delivery by day, by creative (with rendered size), by device, by country

The report window runs from the earliest line-item start through TODAY in the
network timezone (America/New_York) — not yesterday — because the final report
is usually requested right after the last impressions serve (e.g. a short
extension that finishes the goal the same day). Same-day GAM numbers can lag a
few hours, so the report says when the window includes today.

Writes markdown to --markdown and raw JSON to --out, and prints the markdown.
No writes to GAM.

Usage:
  python scripts/final_campaign_report.py --order 4194246183 \
      [--end 2026-11-02] [--markdown report.md] [--out report.json]
Env (Actions): FINAL_REPORT_ORDER_ID, FINAL_REPORT_END_DATE.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

_ET = ZoneInfo("America/New_York")

METRICS = [
    "AD_SERVER_IMPRESSIONS",
    "AD_SERVER_CLICKS",
    "AD_SERVER_REVENUE",
    "AD_SERVER_ACTIVE_VIEW_ELIGIBLE_IMPRESSIONS",
    "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
    "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
]
_SHORT = {
    "ad_server_impressions": "impressions",
    "ad_server_clicks": "clicks",
    "ad_server_revenue": "revenue",
    "ad_server_active_view_eligible_impressions": "av_eligible",
    "ad_server_active_view_measurable_impressions": "av_measurable",
    "ad_server_active_view_viewable_impressions": "av_viewable",
}


def _ser(obj) -> dict:
    from zeep.helpers import serialize_object
    return json.loads(json.dumps(serialize_object(obj), default=str))


def _dt_to_et_date(dt: dict | None) -> date | None:
    """SOAP DateTime dict -> calendar date in the network tz."""
    if not dt or not dt.get("date"):
        return None
    d = dt["date"]
    try:
        tz = ZoneInfo(dt.get("timeZoneId") or "America/New_York")
        naive = datetime(int(d["year"]), int(d["month"]), int(d["day"]),
                         int(dt.get("hour") or 0), int(dt.get("minute") or 0))
        return naive.replace(tzinfo=tz).astimezone(_ET).date()
    except (KeyError, TypeError, ValueError):
        return date(int(d["year"]), int(d["month"]), int(d["day"]))


def _dt_str(dt: dict | None) -> str:
    if not dt or not dt.get("date"):
        return "—"
    d = dt["date"]
    return (f"{int(d['year']):04d}-{int(d['month']):02d}-{int(d['day']):02d} "
            f"{int(dt.get('hour') or 0):02d}:{int(dt.get('minute') or 0):02d}")


def _money(m: dict | None) -> float | None:
    if not m or m.get("microAmount") is None:
        return None
    return int(m["microAmount"]) / 1_000_000


def _num(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=_SHORT)
    for c in _SHORT.values():
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    for c in ("impressions", "clicks", "av_eligible", "av_measurable", "av_viewable"):
        if c in df.columns:
            df[c] = df[c].astype("int64")
    return df


def _rates(df: pd.DataFrame) -> pd.DataFrame:
    imp = df["impressions"].replace(0, pd.NA)
    df["ctr_%"] = (100 * df["clicks"] / imp).astype(float).round(2)
    df["measurable_%"] = (100 * df["av_measurable"]
                          / df["av_eligible"].replace(0, pd.NA)).astype(float).round(1)
    df["viewable_%"] = (100 * df["av_viewable"]
                        / df["av_measurable"].replace(0, pd.NA)).astype(float).round(1)
    return df


def _fmt_int(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{int(v):,}"


def _fmt_pct(v, dp=1) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:.{dp}f}%"


def _fmt_usd(v) -> str:
    return "—" if v is None or pd.isna(v) else f"${v:,.2f}"


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", default=os.environ.get("FINAL_REPORT_ORDER_ID", ""))
    ap.add_argument("--end", default=os.environ.get("FINAL_REPORT_END_DATE", ""),
                    help="report end date YYYY-MM-DD (default: today, ET)")
    ap.add_argument("--markdown", default="final_report.md")
    ap.add_argument("--out", default="final_report.json")
    args = ap.parse_args()
    if not args.order.strip().isdigit():
        print("need --order <numeric GAM order id>", file=sys.stderr)
        return 2
    order_id = int(args.order.strip())
    today = datetime.now(_ET).date()
    end = date.fromisoformat(args.end) if args.end.strip() else today

    gam = GAMClient()
    client = gam._get_soap_client()
    V = gam._SOAP_API_VERSION
    from googleads import ad_manager  # type: ignore

    o_svc = client.GetService("OrderService", version=V)
    li_svc = client.GetService("LineItemService", version=V)
    c_svc = client.GetService("CompanyService", version=V)

    sb = ad_manager.StatementBuilder(version=V).Where(f"id = {order_id}").Limit(1)
    orders = o_svc.getOrdersByStatement(sb.ToStatement()).results or []
    if not orders:
        print(f"order {order_id} not found", file=sys.stderr)
        return 1
    order = _ser(orders[0])

    adv_name = None
    if order.get("advertiserId"):
        sb = ad_manager.StatementBuilder(version=V).Where(
            f"id = {order['advertiserId']}").Limit(1)
        cs = c_svc.getCompaniesByStatement(sb.ToStatement()).results or []
        adv_name = _ser(cs[0]).get("name") if cs else None

    sb = ad_manager.StatementBuilder(version=V).Where(f"orderId = {order_id}").Limit(500)
    lis = [_ser(li) for li in (li_svc.getLineItemsByStatement(sb.ToStatement()).results or [])]
    if not lis:
        print(f"order {order_id} has no line items", file=sys.stderr)
        return 1

    starts = [d for d in (_dt_to_et_date(li.get("startDateTime")) for li in lis) if d]
    start = min(starts) if starts else end
    if start > end:
        print(f"flight starts {start}, after report end {end}", file=sys.stderr)
        return 1

    flt = [("ORDER_ID", "IN", [order_id])]

    def rep(dims: list[str]) -> pd.DataFrame:
        df = gam._run_report(dims, METRICS, start, end, filters=flt)
        return _num(df) if not df.empty else df

    by_li = rep(["LINE_ITEM_ID", "LINE_ITEM_NAME"])
    by_day = rep(["DATE"])
    by_cr = rep(["LINE_ITEM_ID", "CREATIVE_ID", "CREATIVE_NAME", "RENDERED_CREATIVE_SIZE"])
    by_dev = rep(["DEVICE_CATEGORY_NAME"])
    by_geo = rep(["COUNTRY_NAME"])

    # ── DoubleVerify Attention (optional — needs DATABASE_URL) ────────────
    # Same aggregation as the dashboard (dl.attention_current_and_prior):
    # mean per date first, then the mean of those daily means.
    att_daily: dict[str, float] = {}
    att_overall = None
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        try:
            import sqlalchemy
            if db_url.startswith("postgres://"):
                db_url = "postgresql://" + db_url[len("postgres://"):]
            eng = sqlalchemy.create_engine(db_url)
            ids = [str(li.get("id")) for li in lis]
            q = sqlalchemy.text(
                "SELECT CAST(date AS TEXT) AS date, attention_index FROM dv_attention "
                "WHERE line_item_id IN :ids AND attention_index IS NOT NULL"
            ).bindparams(sqlalchemy.bindparam("ids", expanding=True))
            with eng.connect() as conn:
                dv = pd.read_sql(q, conn, params={"ids": ids})
            if not dv.empty:
                dv["date"] = dv["date"].str[:10]
                d = dv.groupby("date")["attention_index"].mean().sort_index()
                att_daily = {k: round(float(v), 1) for k, v in d.items()}
                att_overall = round(float(d.mean()), 1)
            print(f"DV attention: {len(dv)} rows, {len(att_daily)} days, "
                  f"index {att_overall}", file=sys.stderr)
        except Exception as e:  # DV is a nice-to-have; never fail the report
            print(f"DV attention unavailable: {e}", file=sys.stderr)

    # ── assemble ──────────────────────────────────────────────────────────
    li_rows = []
    deliv = {}
    if not by_li.empty:
        by_li["line_item_id"] = by_li["line_item_id"].astype(str)
        deliv = {r["line_item_id"]: r for _, r in _rates(by_li.copy()).iterrows()}
    tot = {k: 0 for k in ("goal", "impressions", "clicks", "revenue",
                          "av_eligible", "av_measurable", "av_viewable")}
    for li in sorted(lis, key=lambda x: str(x.get("id"))):
        lid = str(li.get("id"))
        d = deliv.get(lid)
        goal = (li.get("primaryGoal") or {})
        goal_units = goal.get("units")
        goal_units = int(goal_units) if goal_units not in (None, -1, "-1") else None
        imp = int(d["impressions"]) if d is not None else 0
        row = {
            "id": lid,
            "name": li.get("name"),
            "status": li.get("status"),
            "type": li.get("lineItemType"),
            "cost_type": li.get("costType"),
            "rate": _money(li.get("costPerUnit")),
            "start": _dt_str(li.get("startDateTime")),
            "end": _dt_str(li.get("endDateTime")),
            "goal_type": goal.get("goalType"),
            "goal_unit": goal.get("unitType"),
            "goal": goal_units,
            "impressions": imp,
            "clicks": int(d["clicks"]) if d is not None else 0,
            "revenue": float(d["revenue"]) if d is not None else 0.0,
            "av_eligible": int(d["av_eligible"]) if d is not None else 0,
            "av_measurable": int(d["av_measurable"]) if d is not None else 0,
            "av_viewable": int(d["av_viewable"]) if d is not None else 0,
        }
        li_rows.append(row)
        if goal_units and (goal.get("unitType") == "IMPRESSIONS"):
            tot["goal"] += goal_units
        for k in ("impressions", "clicks", "revenue",
                  "av_eligible", "av_measurable", "av_viewable"):
            tot[k] += row[k]

    def pct(a, b):
        return (100 * a / b) if b else None

    # ── markdown ──────────────────────────────────────────────────────────
    name = order.get("name") or str(order_id)
    md = [f"# Final campaign report — {name}", ""]
    md.append(f"Order **{order_id}** · advertiser {adv_name or '—'} "
              f"({order.get('advertiserId')}) · order status {order.get('status')}")
    if order.get("poNumber"):
        md.append(f"PO {order['poNumber']}")
    md.append("")
    md.append(f"Report window: **{start} → {end}** (ET), pulled "
              f"{datetime.now(_ET):%Y-%m-%d %H:%M} ET from GAM ad server.")
    if end >= today:
        md.append("")
        md.append("> The window includes today. GAM's same-day numbers can lag a "
                  "few hours, so today's delivery may still rise slightly; re-run "
                  "tomorrow for the settled figure.")
    md.append("")
    md.append("## Summary")
    md.append("")
    md.append(_md_table(
        ["Goal (impr)", "Delivered", "% of goal", "Clicks", "CTR",
         "Revenue", "AV measurable", "AV viewable"],
        [[_fmt_int(tot["goal"] or None), _fmt_int(tot["impressions"]),
          _fmt_pct(pct(tot["impressions"], tot["goal"])),
          _fmt_int(tot["clicks"]),
          _fmt_pct(pct(tot["clicks"], tot["impressions"]), 2),
          _fmt_usd(tot["revenue"]),
          _fmt_pct(pct(tot["av_measurable"], tot["av_eligible"])),
          _fmt_pct(pct(tot["av_viewable"], tot["av_measurable"]))]]))
    md.append("")
    if att_overall is not None:
        md.append(f"DoubleVerify Attention index: **{att_overall:.0f}** "
                  f"(100 = DV baseline; mean of {len(att_daily)} daily readings, "
                  f"{min(att_daily)} → {max(att_daily)}).")
    else:
        md.append("DoubleVerify Attention: not available for this report.")
    md.append("")
    md.append("## By line item")
    md.append("")
    md.append(_md_table(
        ["Line item", "Status", "Flight (ET)", "Rate", "Goal", "Delivered",
         "% of goal", "Clicks", "CTR", "Viewable"],
        [[f"{r['id']} — {r['name']}", str(r["status"]),
          f"{r['start']} → {r['end']}",
          (f"{_fmt_usd(r['rate'])} {r['cost_type']}" if r["rate"] is not None else "—"),
          (f"{_fmt_int(r['goal'])} {r['goal_unit'] or ''}".strip()
           if r["goal"] else str(r["goal_type"] or "—")),
          _fmt_int(r["impressions"]),
          _fmt_pct(pct(r["impressions"], r["goal"])
                   if r["goal"] and r["goal_unit"] == "IMPRESSIONS" else None),
          _fmt_int(r["clicks"]),
          _fmt_pct(pct(r["clicks"], r["impressions"]), 2),
          _fmt_pct(pct(r["av_viewable"], r["av_measurable"]))]
         for r in li_rows]))

    def section(title: str, df: pd.DataFrame, key_cols: list[str], top: int | None = None):
        md.append("")
        md.append(f"## {title}")
        md.append("")
        if df.empty:
            md.append("_(no delivery rows)_")
            return
        df = _rates(df.copy())
        if top:
            df = df.sort_values("impressions", ascending=False)
            rest = df.iloc[top:]
            df = df.iloc[:top]
        rows = [[*(str(r[c]) for c in key_cols), _fmt_int(r["impressions"]),
                 _fmt_int(r["clicks"]), _fmt_pct(r["ctr_%"], 2),
                 _fmt_pct(r["viewable_%"])] for _, r in df.iterrows()]
        if top and not rest.empty:
            rows.append([f"Other ({len(rest)})", *([""] * (len(key_cols) - 1)),
                         _fmt_int(rest["impressions"].sum()),
                         _fmt_int(rest["clicks"].sum()), "", ""])
        md.append(_md_table([*key_cols, "Impressions", "Clicks", "CTR", "Viewable"], rows))

    if not by_day.empty:
        by_day = by_day.sort_values("date")
    section("By day", by_day, ["date"])
    if not by_cr.empty:
        by_cr = by_cr.sort_values(["line_item_id", "impressions"], ascending=[True, False])
    section("By creative", by_cr,
            ["creative_id", "creative_name", "rendered_creative_size"])
    if not by_dev.empty:
        by_dev = by_dev.sort_values("impressions", ascending=False)
    section("By device", by_dev, ["device_category_name"])
    section("By country (top 15)", by_geo, ["country_name"], top=15)

    md.append("")
    md.append("_Viewable = Active View viewable / measurable. CTR = clicks / "
              "impressions. Ad-server impressions (GAM's billing count)._")
    text = "\n".join(md) + "\n"

    Path(args.markdown).write_text(text)
    payload = {
        "order": {"id": order_id, "name": name, "status": order.get("status"),
                  "advertiser_id": order.get("advertiserId"), "advertiser": adv_name,
                  "po": order.get("poNumber")},
        "window": {"start": str(start), "end": str(end), "includes_today": end >= today},
        "totals": tot,
        "dv_attention": {"overall": att_overall, "daily": att_daily},
        "line_items": li_rows,
        "by_day": by_day.to_dict("records") if not by_day.empty else [],
        "by_creative": by_cr.to_dict("records") if not by_cr.empty else [],
        "by_device": by_dev.to_dict("records") if not by_dev.empty else [],
        "by_country": by_geo.to_dict("records") if not by_geo.empty else [],
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
