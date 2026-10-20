"""
GAM half of the TAG IVT monitoring workbook (`DV-GAM_discrepancy_report.xlsx`).

Queries each workbook column straight from its GAM dimensions and metric, and
prints one row per month for each sheet, in workbook column order, ready to
paste. The definitions are recorded in docs/tag_ivt_monitoring_report.md.

  Column            Metric                                        Extra filter
  B Ad Exchange     AD_EXCHANGE_IMPRESSIONS                       (split by INVENTORY_FORMAT_NAME)
  C Open Bidding    IMPRESSIONS                                   DEMAND_CHANNEL_NAME = Open Bidding
  D PMP - PA        IMPRESSIONS                                   DEMAND_SUBCHANNEL_NAME = Private Auction
  E Ad server       AD_SERVER_UNFILTERED_DOWNLOADED_IMPRESSIONS   -
  F PMP - PD+PG     IMPRESSIONS                                   DEMAND_SUBCHANNEL_NAME in Programmatic
                                                                  Guaranteed, Preferred Deals
  G GAM total       display: CODE_SERVED_COUNT; video: workbook formula =B+D+E

The workbook is the reference: each column follows the convention the 2026
workbook already uses, so a new month lines up with the old ones. Columns C-F
and display G are split by MONTH_YEAR x AD_UNIT_NAME_TOP_LEVEL and filtered to
`newsweek` (display) / `vid.newsweek` (video). Column B is split by
MONTH_YEAR x INVENTORY_FORMAT_NAME with no ad-unit filter: the Banner row goes
to display, the In-stream video row to video. That leaves out the small
"In-stream video or audio" format, the same way the workbook does.

Usage:
    python scripts/tag_ivt_gam_pull.py                       # Jan..last closed month
    python scripts/tag_ivt_gam_pull.py --start 2026-07 --end 2026-09
    python scripts/tag_ivt_gam_pull.py --csv out.csv
"""

from __future__ import annotations

import argparse
import calendar
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gam_client import GAMClient

AD_UNITS = {"display": "newsweek", "video": "vid.newsweek"}

# Column B: INVENTORY_FORMAT_NAME value -> sheet.
B_FORMATS = {"Banner": "newsweek", "In-stream video": "vid.newsweek"}

# (column, metric, extra filters) — all also split/filtered by top-level ad unit.
COLUMNS = [
    ("C_open_bidding", "IMPRESSIONS",
     [("DEMAND_CHANNEL_NAME", "IN", ["Open Bidding"])]),
    ("D_pmp_pa", "IMPRESSIONS",
     [("DEMAND_SUBCHANNEL_NAME", "IN", ["Private Auction"])]),
    ("E_ad_server", "AD_SERVER_UNFILTERED_DOWNLOADED_IMPRESSIONS", []),
    ("F_pmp_pd_pg", "IMPRESSIONS",
     [("DEMAND_SUBCHANNEL_NAME", "IN", ["Programmatic Guaranteed", "Preferred Deals"])]),
    ("G_code_served", "CODE_SERVED_COUNT", []),
]


def _month_code_to_str(code: int) -> str:
    # MONTH_YEAR comes back as (year - 1900) * 12 + (month - 1).
    return f"{1900 + code // 12}-{code % 12 + 1:02d}"


def _parse_month(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


def pull(start: date, end: date) -> pd.DataFrame:
    gam = GAMClient()
    frames = []
    for col, metric, extra in COLUMNS:
        df = gam._run_report(
            dimensions=["AD_UNIT_NAME_TOP_LEVEL", "MONTH_YEAR"],
            metrics=[metric],
            start_date=start,
            end_date=end,
            filters=[("AD_UNIT_NAME_TOP_LEVEL", "IN", list(AD_UNITS.values()))] + extra,
        )
        df.columns = ["ad_unit", "month", col]
        frames.append(df.set_index(["ad_unit", "month"]))
    b = gam._run_report(
        dimensions=["INVENTORY_FORMAT_NAME", "MONTH_YEAR"],
        metrics=["AD_EXCHANGE_IMPRESSIONS"],
        start_date=start,
        end_date=end,
    )
    b.columns = ["format", "month", "B_ad_exchange"]
    b = b[b["format"].isin(B_FORMATS)].assign(ad_unit=lambda d: d["format"].map(B_FORMATS))
    frames.insert(0, b.drop(columns="format").set_index(["ad_unit", "month"]))
    out = pd.concat(frames, axis=1).fillna(0).astype("int64").reset_index()
    out["month"] = out["month"].map(_month_code_to_str)
    months = set(out["month"])
    want = {f"{y}-{m:02d}" for y, m in _months_between(start, end)}
    assert months <= want, f"months outside the requested window: {sorted(months - want)}"
    return out.sort_values(["ad_unit", "month"])


def _months_between(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main() -> None:
    today = datetime.now(ZoneInfo("America/New_York")).date()  # GAM network tz
    last_closed = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=f"{last_closed[0]}-01", help="YYYY-MM (default: January)")
    ap.add_argument("--end", default=f"{last_closed[0]}-{last_closed[1]:02d}",
                    help="YYYY-MM (default: last closed month)")
    ap.add_argument("--csv", help="also write the table to this CSV path")
    args = ap.parse_args()

    sy, sm = _parse_month(args.start)
    ey, em = _parse_month(args.end)
    start = date(sy, sm, 1)
    end = date(ey, em, calendar.monthrange(ey, em)[1])

    df = pull(start, end)
    for sheet, unit in AD_UNITS.items():
        part = df[df["ad_unit"] == unit].drop(columns="ad_unit")
        if sheet == "video":
            part = part.assign(G_code_served=part["B_ad_exchange"] + part["D_pmp_pa"] + part["E_ad_server"])
        part = part.rename(columns={"G_code_served": "G_gam_total"})
        print(f"\n## {sheet} sheet (ad unit {unit})")
        print(part.to_string(index=False))
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nwrote {args.csv}")


if __name__ == "__main__":
    main()
