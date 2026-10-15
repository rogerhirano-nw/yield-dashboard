#!/usr/bin/env python3
"""Daily social-campaign report: delivery, spend, IO billing and pacing.

Reads one IO spec (scripts/social/<IO>.json — the media plan's lines, CPMs,
goals and flight) and a CSV of platform delivery rows, and writes an .xlsx:

  Summary  one row per IO line: goal, delivered, pace vs straight line,
           projected finish, impressions/day needed, billable, spend, margin
  Daily    date x platform: impressions, spend, billable at the IO CPM,
           margin, actual CPM, link clicks, CTR, landing-page views, $/LPV
  Off-plan delivery on platforms the IO didn't sell (Meta's automatic
           placements spill into Threads / WhatsApp / Audience Network).
           Its spend is counted; its impressions are NOT billed.

The delivery CSV needs columns: date, platform, campaign, spend, impressions
and optionally link_clicks, landing_page_views, video_thruplays. Rows are
kept only when `campaign` contains one of the spec's `campaign_name_match`
tokens (the IO number — Newsweek's social campaigns are named on the GAM
convention, so the IO number is already in every campaign name).

Today the CSV comes from a Supermetrics pull (Meta: publisher_platform x
day). A direct platform client will write the same columns later.

    python scripts/social_report.py --spec scripts/social/SO01195.json \
        --social meta_rows.csv --out SO01195_social.xlsx [--as-of 2026-10-17]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dashboard_logic as dl  # noqa: E402

_ET = ZoneInfo("America/New_York")
_OPTIONAL = ("link_clicks", "landing_page_views", "video_thruplays")


def load_rows(path: str, spec: dict) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = {"date", "platform", "campaign", "spend", "impressions"} - set(df.columns)
    if missing:
        sys.exit(f"{path}: missing column(s) {sorted(missing)}")
    for c in _OPTIONAL:
        if c not in df.columns:
            df[c] = 0
    tokens = [t.lower() for t in spec["campaign_name_match"]]
    keep = df["campaign"].astype(str).str.lower().apply(
        lambda n: any(t in n for t in tokens))
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["platform"] = df["platform"].astype(str).str.lower()
    for c in ("spend", "impressions", *_OPTIONAL):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def build(spec: dict, rows: pd.DataFrame, as_of) -> dict[str, pd.DataFrame]:
    rows = rows[rows["date"] <= as_of]
    summary, daily_parts, offplan_parts = [], [], []
    for line in spec["lines"]:
        goal = line.get("impressions") if line["cost_method"] == "CPM" else None
        base = {"line": line["name"], "source": line["source"],
                "cost_method": line["cost_method"], "cpm": line.get("cpm"),
                "goal": line.get("impressions") or line.get("page_views"),
                "io_amount": line.get("amount")}
        if line["source"] != "social" or line["cost_method"] != "CPM":
            summary.append({**base, "status": "no data source wired yet"})
            continue
        plats = set(line["platforms"])
        on = rows[rows["platform"].isin(plats)]
        off = rows[~rows["platform"].isin(plats)]
        offplan_parts.append(off)
        billed = dl.social_daily_billing(on.sort_values("date"), line["cpm"], goal)
        daily_parts.append(billed.assign(line=line["name"]))
        delivered = float(on["impressions"].sum())
        recent = on[on["date"] > as_of - timedelta(days=3)]
        rate = recent.groupby("date")["impressions"].sum().mean() if len(recent) else None
        p = dl.io_line_pacing(goal, line["start"], line["end"], delivered, as_of, rate) or {}
        # Off-plan delivery isn't billed, but its spend is still our cost.
        spend = float(on["spend"].sum()) + float(off["spend"].sum())
        bill = float(billed["billable"].sum())
        summary.append({
            **base, "status": "ok" if len(on) else "no delivery yet",
            "delivered": delivered, "expected_to_date": p.get("expected"),
            "pace_pct": p.get("pace_pct"),
            "projected_pct": p.get("projected_pct"),
            "needed_per_day": p.get("needed_per_day"),
            "days_left": p.get("days_left"),
            "billable": bill, "spend": spend,
            "offplan_spend": float(off["spend"].sum()), "margin": bill - spend,
            "margin_pct": (bill - spend) / bill * 100.0 if bill else None,
        })

    daily = pd.concat(daily_parts) if daily_parts else pd.DataFrame()
    if not daily.empty:
        daily = (daily.groupby(["date", "platform"], as_index=False)
                 [["impressions", "spend", "billable", "margin", *_OPTIONAL]].sum())
        daily["actual_cpm"] = daily["spend"] / daily["impressions"].where(daily["impressions"] > 0) * 1000
        daily["ctr_pct"] = daily["link_clicks"] / daily["impressions"].where(daily["impressions"] > 0) * 100
        daily["cost_per_lpv"] = daily["spend"] / daily["landing_page_views"].where(daily["landing_page_views"] > 0)
        daily = daily.sort_values(["date", "platform"])
    offplan = pd.concat(offplan_parts) if offplan_parts else pd.DataFrame()
    if not offplan.empty:
        offplan = offplan.groupby(["platform", "campaign"], as_index=False)[["impressions", "spend"]].sum()
    return {"Summary": pd.DataFrame(summary), "Daily": daily, "Off-plan": offplan}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--social", required=True, help="platform delivery CSV")
    ap.add_argument("--out", required=True, help=".xlsx to write")
    ap.add_argument("--as-of", help="last day with data (default: yesterday ET)")
    a = ap.parse_args()

    spec = json.loads(Path(a.spec).read_text())
    as_of = (pd.Timestamp(a.as_of).date() if a.as_of
             else datetime.now(_ET).date() - timedelta(days=1))
    sheets = build(spec, load_rows(a.social, spec), as_of)
    with pd.ExcelWriter(a.out, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(
                    12, min(60, max(len(str(c.value or "")) for c in col) + 2))
    print(f"{spec['io_number']} through {as_of} -> {a.out}")
    print(sheets["Summary"].to_string(index=False))


if __name__ == "__main__":
    main()
