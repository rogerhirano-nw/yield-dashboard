"""
Apple News CTR by ad size — trailing N days (default 30) ending yesterday ET.

Scope: top-level ad unit `applenews.newsweek` (AD_UNIT_NAME_TOP_LEVEL — the
leaf AD_UNIT_NAME would only match the bare parent; see CLAUDE.md GAM facts).

Pulls IMPRESSIONS / CLICKS / REVENUE by RENDERED_CREATIVE_SIZE x
LINE_ITEM_TYPE_NAME x AD_UNIT_NAME (leaf) in one report, then prints:
  1. CTR by size (all demand), ranked
  2. CTR by size x line-item type (direct vs programmatic vs house mix
     can move a size's CTR more than the size itself)
  3. CTR by leaf ad unit x size (placement position effect)
Sizes under --min-impr impressions are flagged as too thin to rank.

Usage:
    python scripts/apple_news_ctr_by_size.py [--days 30] [--min-impr 10000] [--csv-dir out]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gam_client import GAMClient  # noqa: E402

UNIT = "applenews.newsweek"
METRICS = ["IMPRESSIONS", "CLICKS", "REVENUE"]
DIMS = ["RENDERED_CREATIVE_SIZE", "LINE_ITEM_TYPE_NAME", "AD_UNIT_NAME"]


def _ctr_ci(clicks: float, impr: float) -> tuple[float, float]:
    """95% Wilson interval on CTR, in percent."""
    if impr <= 0:
        return float("nan"), float("nan")
    z, p = 1.96, clicks / impr
    d = 1 + z * z / impr
    c = (p + z * z / (2 * impr)) / d
    h = z * math.sqrt(p * (1 - p) / impr + z * z / (4 * impr * impr)) / d
    return (c - h) * 100, (c + h) * 100


def _roll(df: pd.DataFrame, keys: list[str], min_impr: int) -> pd.DataFrame:
    g = df.groupby(keys, as_index=False)[["impressions", "clicks", "revenue"]].sum()
    g = g[g.impressions > 0]
    g["ctr_pct"] = g.clicks / g.impressions * 100
    ci = g.apply(lambda r: _ctr_ci(r.clicks, r.impressions), axis=1, result_type="expand")
    g["ctr_lo"], g["ctr_hi"] = ci[0], ci[1]
    g["ecpm"] = g.revenue / g.impressions * 1000
    g["share_impr_pct"] = g.impressions / g.impressions.sum() * 100
    g["thin"] = g.impressions < min_impr
    return g.sort_values(["thin", "ctr_pct"], ascending=[True, False])


def _fmt(g: pd.DataFrame) -> str:
    out = g.copy()
    out["impressions"] = out.impressions.map("{:,.0f}".format)
    out["clicks"] = out.clicks.map("{:,.0f}".format)
    out["revenue"] = out.revenue.map("${:,.0f}".format)
    out["ecpm"] = out.ecpm.map("${:,.2f}".format)
    out["share_impr_pct"] = out.share_impr_pct.map("{:.1f}%".format)
    out["ctr"] = [f"{c:.3f}%  [{lo:.3f}–{hi:.3f}]" for c, lo, hi in zip(out.ctr_pct, out.ctr_lo, out.ctr_hi)]
    out["thin"] = out.thin.map({True: "THIN", False: ""})
    cols = [c for c in out.columns if c not in ("ctr_pct", "ctr_lo", "ctr_hi")]
    cols = [c for c in cols if c not in ("ctr", "thin")] + ["ctr", "thin"]
    return out[cols].to_string(index=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-impr", type=int, default=10000)
    ap.add_argument("--csv-dir")
    a = ap.parse_args()

    end = datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    start = end - timedelta(days=a.days - 1)

    raw = GAMClient()._run_report(
        dimensions=DIMS, metrics=METRICS, start_date=start, end_date=end,
        filters=[("AD_UNIT_NAME_TOP_LEVEL", "IN", [UNIT])],
    )
    if raw.empty:
        raise SystemExit(f"no rows for {UNIT} {start}..{end}")
    for c in ("impressions", "clicks", "revenue"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce").fillna(0)
    raw = raw.rename(columns={"rendered_creative_size": "size",
                              "line_item_type_name": "li_type",
                              "ad_unit_name": "ad_unit"})
    raw["size"] = raw["size"].replace("", "(unknown)")

    tot_i, tot_c = raw.impressions.sum(), raw.clicks.sum()
    print(f"Apple News ({UNIT}) — {start} .. {end} ({a.days} days, ET)")
    print(f"Total: {tot_i:,.0f} impressions, {tot_c:,.0f} clicks, CTR {tot_c / tot_i * 100:.3f}%, "
          f"revenue ${raw.revenue.sum():,.0f}")
    print(f"CTR shown with 95% Wilson interval; THIN = under {a.min_impr:,} impressions (don't rank).\n")

    by_size = _roll(raw, ["size"], a.min_impr)
    by_type = _roll(raw, ["size", "li_type"], a.min_impr)
    by_unit = _roll(raw, ["ad_unit", "size"], a.min_impr)

    print("== 1. CTR by size (all demand) ==")
    print(_fmt(by_size), "\n")
    print("== 2. CTR by size x line-item type ==")
    print(_fmt(by_type.sort_values(["size", "impressions"], ascending=[True, False])), "\n")
    print("== 3. CTR by leaf ad unit x size ==")
    print(_fmt(by_unit.sort_values(["ad_unit", "impressions"], ascending=[True, False])))

    if a.csv_dir:
        d = Path(a.csv_dir)
        d.mkdir(parents=True, exist_ok=True)
        raw.to_csv(d / "apple_news_raw.csv", index=False)
        by_size.to_csv(d / "apple_news_ctr_by_size.csv", index=False)
        by_type.to_csv(d / "apple_news_ctr_by_size_li_type.csv", index=False)
        by_unit.to_csv(d / "apple_news_ctr_by_unit_size.csv", index=False)


if __name__ == "__main__":
    main()
