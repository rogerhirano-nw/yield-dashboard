"""Why do some Prebid bidders read far below the site's Active View baseline?

Prompted by the 2026-08-14→09-03 "PreBid Display and Video" GAM report, which
on impression-weighted numbers shows (banner baseline 75.8%, video 86.0%):

    smilewanted  40.4%   4.6M banner imps   (client-side)
    ogury        54.4%   2.8M banner imps   (s2s)
    oms          56.4%    38k banner imps   (s2s)
    onetag       47.7%   138k in-stream video imps  (s2s)

"Low" on its own doesn't say what to do about it, because two very different
things produce it:

  * the bidder wins on slots/devices that are inherently less viewable
    (deep in-article positions, desktop rails, refreshed slots) — a MIX
    story, i.e. a yield conversation, or
  * on the same slot and device as everyone else it still measures worse —
    a RENDER story, the class of problem docs/mobkoi_viewability.md solved
    with the iframe mirror.

This script pulls the GAM numbers at a grain fine enough to tell them apart
— hb_bidder × ad unit × device × rendered creative size, with the full
Active View eligible/measurable/viewable split — and runs
`dashboard_logic.viewability_mix_adjusted` over it, which re-weights each
bidder's cells to its peers' rates and reports:

    mix_gap_pp     what the bidder's placement mix costs it
    render_gap_pp  what's left over, i.e. the bidder's own effect

A big negative render_gap on real volume is what justifies the DOM
forensics in scripts/prebid_render_forensics.py; a big negative mix_gap
with render_gap near zero means nothing is broken.

Also reported, because it separates the failure modes further:
  * measurable rate — Active View measurable / eligible. A creative that
    renders somewhere AV can't instrument shows up here, not in viewable%.
  * per-day series — is this a regression with a start date, or structural?

Env: DAYS (default 21), PREBID_ADVERTISER_ID (default 5724335726, the
advertiser the source report filtered on), BIDDERS (extra bidders to detail),
OUT_DIR. Requires GAM_SERVICE_ACCOUNT_JSON + GAM_NETWORK_ID, so it runs in
Actions (see .github/workflows/prebid_viewability_audit.yml) or locally with
.env — the companion workflow posts the output as a PR comment.
"""

from __future__ import annotations

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

import dashboard_logic as dl  # noqa: E402
from gam_client import GAMClient  # noqa: E402

DAYS = int(os.environ.get("DAYS") or "21")
# The Prebid demand advertiser — same filter the source GAM report used.
ADVERTISER_ID = int(os.environ.get("PREBID_ADVERTISER_ID") or "5724335726")
FOCUS = [b.strip().lower() for b in (
    os.environ.get("BIDDERS") or "smilewanted,ogury,oms,onetag"
).split(",") if b.strip()]
OUT_DIR = Path(os.environ.get("OUT_DIR") or "/tmp/prebid-viewability-audit")

METRICS = [
    "AD_SERVER_IMPRESSIONS",
    "ACTIVE_VIEW_ELIGIBLE_IMPRESSIONS",
    "ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
    "ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
]
# hb_bidder arrives as the custom-targeting dimension "{key}={value}";
# CONTAINS keeps the row count to that one key instead of every KV the page
# sends. Pairing it with the advertiser filter keeps the report small enough
# to fetch in one pass.
_HB_PREFIX = "hb_bidder="


def _bidder(kv: object) -> str | None:
    s = str(kv or "")
    return s.split("=", 1)[1].strip().lower() if s.startswith(_HB_PREFIX) else None


def _pull(gam: GAMClient, dims: list[str], start: date, end: date) -> pd.DataFrame:
    df = gam._run_report(
        dimensions=dims,
        metrics=METRICS,
        start_date=start,
        end_date=end,
        filters=[
            ("ADVERTISER_ID", "IN", [ADVERTISER_ID]),
            ("KEY_VALUES_NAME", "CONTAINS", [_HB_PREFIX]),
        ],
    )
    df = df.rename(columns={
        "ad_server_impressions": "impressions",
        "active_view_eligible_impressions": "eligible",
        "active_view_measurable_impressions": "measurable",
        "active_view_viewable_impressions": "viewable_impressions",
    })
    df["bidder"] = df["key_values_name"].map(_bidder)
    return df[df["bidder"].notna()]


def _rate(num, den) -> float:
    den = float(den or 0)
    return float(num or 0) / den * 100.0 if den else float("nan")


def _bidder_table(df: pd.DataFrame, title: str) -> None:
    g = df.groupby("bidder", dropna=False)[
        ["impressions", "eligible", "measurable", "viewable_impressions"]].sum()
    g = g[g["impressions"] > 0].sort_values("impressions", ascending=False)
    site_vw = _rate(g["viewable_impressions"].sum(), g["measurable"].sum())
    print(f"\n-- {title} (site viewable/measurable {site_vw:.1f}%) --")
    print(f"{'bidder':<18}{'imps':>12}{'measurable%':>13}{'viewable%':>11}{'vw/measurable%':>16}")
    for b, r in g.iterrows():
        # viewable% is quoted against impressions (what the GAM report shows);
        # vw/measurable isolates "of the ones AV could actually see".
        print(f"{b:<18}{int(r.impressions):>12,}"
              f"{_rate(r.measurable, r.eligible):>12.1f}%"
              f"{_rate(r.viewable_impressions, r.impressions):>10.1f}%"
              f"{_rate(r.viewable_impressions, r.measurable):>15.1f}%")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=DAYS - 1)
    gam = GAMClient()

    print("=" * 78)
    print(f"PREBID VIEWABILITY AUDIT  {start} → {end}  (advertiser {ADVERTISER_ID})")
    print("=" * 78)

    # Cell grain: ad unit + device + rendered size is what makes two bidders
    # comparable. INVENTORY_FORMAT_NAME keeps banner and video apart, since
    # their baselines differ by ~10pp and mixing them would fake a mix effect.
    cell = _pull(gam, ["KEY_VALUES_NAME", "AD_UNIT_NAME", "DEVICE_CATEGORY_NAME",
                       "RENDERED_CREATIVE_SIZE", "INVENTORY_FORMAT_NAME"], start, end)
    cell.to_csv(OUT_DIR / "by_cell.csv", index=False)
    print(f"\n{len(cell):,} bidder×unit×device×size×format rows, "
          f"{int(cell['impressions'].sum()):,} impressions")

    for fmt, sub in cell.groupby("inventory_format_name", dropna=False):
        _bidder_table(sub, f"{fmt}: Active View by bidder")

    # ── mix vs render ────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("MIX vs RENDER  (baselines are leave-one-out: each bidder is graded")
    print("against its PEERS in the same unit/device/size/format cells)")
    print("=" * 78)
    for fmt, sub in cell.groupby("inventory_format_name", dropna=False):
        if sub["impressions"].sum() < 1000:
            continue
        adj = dl.viewability_mix_adjusted(
            sub, "bidder",
            ["ad_unit_name", "device_category_name", "rendered_creative_size"])
        adj = adj[adj["impressions"] >= 1000]
        print(f"\n-- {fmt} --")
        print(f"{'bidder':<18}{'imps':>12}{'actual%':>9}{'expected%':>11}"
              f"{'MIX pp':>9}{'RENDER pp':>11}{'uncovered':>11}")
        for _, r in adj.iterrows():
            print(f"{r.bidder:<18}{int(r.impressions):>12,}{r.actual_pct:>8.1f}%"
                  f"{r.expected_pct:>10.1f}%{r.mix_gap_pp:>+9.1f}{r.render_gap_pp:>+11.1f}"
                  f"{int(r.uncovered_impressions):>11,}")
        adj.to_csv(OUT_DIR / f"mix_vs_render_{str(fmt).replace(' ', '_')}.csv", index=False)

    # ── where the focus bidders actually buy ─────────────────────────────
    print("\n" + "=" * 78)
    print("FOCUS BIDDERS — top cells, with the peer rate in the same cell")
    print("=" * 78)
    for b in FOCUS:
        sub = cell[cell["bidder"] == b]
        if sub.empty:
            print(f"\n-- {b}: no impressions in the window --")
            continue
        keys = ["ad_unit_name", "device_category_name", "rendered_creative_size"]
        mine = sub.groupby(keys)[["impressions", "viewable_impressions"]].sum()
        allc = cell.groupby(keys)[["impressions", "viewable_impressions"]].sum()
        peer_i = allc["impressions"].reindex(mine.index).fillna(0) - mine["impressions"]
        peer_v = allc["viewable_impressions"].reindex(mine.index).fillna(0) - mine["viewable_impressions"]
        print(f"\n-- {b} ({int(sub['impressions'].sum()):,} imps) --")
        print(f"{'unit / device / size':<52}{'imps':>10}{'this%':>8}{'peers%':>8}")
        for k, r in mine.sort_values("impressions", ascending=False).head(12).iterrows():
            label = " / ".join(str(x) for x in k)[:50]
            print(f"{label:<52}{int(r.impressions):>10,}"
                  f"{_rate(r.viewable_impressions, r.impressions):>7.1f}%"
                  f"{_rate(peer_v.loc[k], peer_i.loc[k]):>7.1f}%")

    # ── regression check: structural, or did it start on a date? ─────────
    daily = _pull(gam, ["DATE", "KEY_VALUES_NAME", "INVENTORY_FORMAT_NAME"], start, end)
    daily.to_csv(OUT_DIR / "by_day.csv", index=False)
    print("\n" + "=" * 78)
    print("DAILY viewable% — a step change dates a regression; a flat line is structural")
    print("=" * 78)
    for b in FOCUS:
        sub = daily[daily["bidder"] == b]
        if sub.empty:
            continue
        s = sub.groupby("date")[["impressions", "viewable_impressions"]].sum()
        series = " ".join(f"{str(d)[5:]}:{_rate(r.viewable_impressions, r.impressions):.0f}%"
                          for d, r in s.iterrows())
        print(f"\n{b}: {series}")

    print(f"\nCSVs: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
