"""One bidder's Active View day by day, graded against peers, to read a change.

Follow-up to docs/prebid_viewability.md: on 2026-10-20 we asked SmileWanted to
pause all in-banner video (IBV) demand, the leading cause of its uniform
~38pp render gap. The pause is the experiment, so the question is a
before/after on the same grain the audit used: hb_bidder (custom dimension) ×
ad unit, now × DATE.

Per day it prints the bidder's impressions, revenue, viewable% and the peer
rate re-weighted to that day's ad-unit mix (so a shift in where the bidder
fills can't pass for a render fix), plus the gap. A pause that took shows as
a step in the gap — and usually in volume, since IBV was part of the fill.

The split date is PAUSE_DATE when set (first day *after* the pause). Without
it the script picks the day that best separates the gap series (largest
before/after mean difference, ≥5 days each side) and says so — a suggestion
to confirm against when SmileWanted actually switched it off, not a fact.
Then it prints before vs after per ad unit.

Env: DAYS (default 60, ending yesterday), BIDDER (default smilewanted),
PAUSE_DATE (YYYY-MM-DD, optional), PREBID_ADVERTISER_IDS (default
5724335726,5713671547), OUT_DIR. Needs GAM_SERVICE_ACCOUNT_JSON +
GAM_NETWORK_ID, so it runs in Actions
(.github/workflows/prebid_bidder_daily_viewability.yml).
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
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pandas as pd  # noqa: E402

from gam_client import GAMClient  # noqa: E402
from prebid_source_viewability import (  # noqa: E402
    ADVERTISER_IDS, METRICS, _RENAME, _find_keys, _key_id, _pct, _rate,
    _CUSTOM_DIM,
)

DAYS = int(os.environ.get("DAYS") or "60")
BIDDER = (os.environ.get("BIDDER") or "smilewanted").strip().lower()
PAUSE_DATE = (os.environ.get("PAUSE_DATE") or "").strip()
OUT_DIR = Path(os.environ.get("OUT_DIR") or "/tmp/prebid-bidder-daily")
MIN_SIDE = 5


def _peer_rate(mine: pd.DataFrame, peers: pd.DataFrame) -> float:
    """Peer viewable% re-weighted to `mine`'s ad-unit impression mix."""
    m_u = mine.groupby("ad_unit")["impressions"].sum()
    p_u = peers.groupby("ad_unit")[["viewable", "impressions"]].sum()
    p_rate = (p_u["viewable"] / p_u["impressions"]).reindex(m_u.index)
    w = m_u[p_rate.notna()]
    return float((p_rate.dropna() * w).sum() / w.sum() * 100) if w.sum() else float("nan")


def _summary(mine: pd.DataFrame, peers: pd.DataFrame) -> dict:
    t = mine[["impressions", "eligible", "measurable", "viewable", "revenue"]].sum()
    vw = _rate(t.viewable, t.impressions)
    pr = _peer_rate(mine, peers)
    return {"impressions": int(t.impressions), "revenue": float(t.revenue),
            "measurable_pct": _rate(t.measurable, t.eligible),
            "viewable_pct": vw, "peer_pct": pr, "gap_pp": vw - pr}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=DAYS - 1)
    gam = GAMClient()

    print("=" * 78)
    print(f"{BIDDER} DAILY VIEWABILITY vs PEERS (mix-adjusted by ad unit)")
    print(f"{start} → {end}   advertisers {ADVERTISER_IDS}")
    print("=" * 78)

    bidder_key = _find_keys({"hb_bidder"}).get("hb_bidder")
    if bidder_key is None or bidder_key.reportable_type != _CUSTOM_DIM:
        raise SystemExit("hb_bidder is not a reportable custom dimension.")

    df = gam._run_report(
        dimensions=["DATE", "CUSTOM_DIMENSION_0_VALUE", "AD_UNIT_NAME"],
        metrics=METRICS, start_date=start, end_date=end,
        filters=[("ADVERTISER_ID", "IN", ADVERTISER_IDS)],
        custom_dimension_key_ids=[_key_id(bidder_key)],
    ).rename(columns={"custom_dimension_0_value": "bidder"}).rename(columns=_RENAME)
    df["bidder"] = df["bidder"].astype(str).str.strip().str.lower()
    df["ad_unit"] = df["ad_unit_name"].astype(str)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df.to_csv(OUT_DIR / "bidder_adunit_daily.csv", index=False)
    print(f"{len(df):,} rows, {int(df['impressions'].sum()):,} impressions")

    mine_all = df[df["bidder"] == BIDDER]
    peers_all = df[df["bidder"] != BIDDER]
    if mine_all.empty:
        print(f"\n{BIDDER}: no impressions in the window")
        return 0

    # ── day by day ──────────────────────────────────────────────────────
    days = []
    for d in sorted(df["date"].unique()):
        m = mine_all[mine_all["date"] == d]
        p = peers_all[peers_all["date"] == d]
        days.append({"date": d, **(_summary(m, p) if not m.empty else
                     {"impressions": 0, "revenue": 0.0, "measurable_pct": float("nan"),
                      "viewable_pct": float("nan"), "peer_pct": float("nan"),
                      "gap_pp": float("nan")})})
    daily = pd.DataFrame(days)
    daily.to_csv(OUT_DIR / f"{BIDDER}_daily.csv", index=False)
    print(f"\n-- {BIDDER} by day (peers re-weighted to its unit mix that day) --")
    print(f"{'date':<12}{'imps':>11}{'revenue':>10}{'eCPM':>7}{'meas%':>8}"
          f"{'viewable%':>11}{'peers%':>8}{'gap pp':>8}")
    for r in daily.itertuples():
        ecpm = r.revenue / r.impressions * 1000 if r.impressions else 0
        gap = "    —" if pd.isna(r.gap_pp) else f"{r.gap_pp:+8.1f}"
        print(f"{str(r.date):<12}{r.impressions:>11,}{r.revenue:>10,.0f}{ecpm:>7.2f}"
              f"{_pct(r.measurable_pct):>8}{_pct(r.viewable_pct):>11}"
              f"{_pct(r.peer_pct):>8}{gap:>8}")

    # ── split date: given, or the best-separating day ───────────────────
    valid = daily.dropna(subset=["gap_pp"]).reset_index(drop=True)
    if PAUSE_DATE:
        split = date.fromisoformat(PAUSE_DATE)
        how = "PAUSE_DATE"
    elif len(valid) >= 2 * MIN_SIDE:
        best, split = -1.0, None
        for i in range(MIN_SIDE, len(valid) - MIN_SIDE + 1):
            diff = abs(valid["gap_pp"].iloc[i:].mean() - valid["gap_pp"].iloc[:i].mean())
            if diff > best:
                best, split = diff, valid["date"].iloc[i]
        how = (f"auto: best-separating day (|Δ mean gap| {best:.1f}pp) — "
               "confirm against when the pause actually took effect")
    else:
        print(f"\nfewer than {2 * MIN_SIDE} days with {BIDDER} delivery — no split")
        return 0

    before_m, after_m = mine_all[mine_all["date"] < split], mine_all[mine_all["date"] >= split]
    before_p, after_p = peers_all[peers_all["date"] < split], peers_all[peers_all["date"] >= split]
    n_before = before_m["date"].nunique()
    n_after = after_m["date"].nunique()
    print("\n" + "=" * 78)
    print(f"BEFORE vs AFTER {split}  ({how})")
    print(f"before: {n_before} days with delivery   after: {n_after} days")
    print("=" * 78)
    if after_m.empty or before_m.empty:
        print(f"{BIDDER} has no delivery on one side of {split} — nothing to compare "
              "(no delivery after it means the bidder stopped filling entirely)")
        return 0
    b, a = _summary(before_m, before_p), _summary(after_m, after_p)
    print(f"{'':<8}{'imps/day':>11}{'rev/day':>9}{'eCPM':>7}{'viewable%':>11}"
          f"{'peers%':>8}{'gap pp':>8}")
    for lab, s, n in (("before", b, n_before), ("after", a, n_after)):
        print(f"{lab:<8}{s['impressions'] / n:>11,.0f}{s['revenue'] / n:>9,.0f}"
              f"{s['revenue'] / s['impressions'] * 1000:>7.2f}{_pct(s['viewable_pct']):>11}"
              f"{_pct(s['peer_pct']):>8}{s['gap_pp']:+8.1f}")
    print(f"gap change: {a['gap_pp'] - b['gap_pp']:+.1f}pp   "
          f"volume/day: {(a['impressions'] / n_after) / (b['impressions'] / n_before) - 1:+.0%}")
    if n_after < 7:
        print(f"note: only {n_after} day(s) after the split — read as early, not settled")

    print(f"\n-- {BIDDER} by ad unit, before vs after --")
    print(f"{'ad unit':<22}{'imps bef':>10}{'vw bef':>8}{'peers':>7}"
          f"{'imps aft':>10}{'vw aft':>8}{'peers':>7}{'Δgap':>7}")
    rows = []
    units = mine_all.groupby("ad_unit")["impressions"].sum().sort_values(ascending=False)
    for unit in units.index:
        sb = _summary(before_m[before_m["ad_unit"] == unit], before_p[before_p["ad_unit"] == unit])
        sa = _summary(after_m[after_m["ad_unit"] == unit], after_p[after_p["ad_unit"] == unit])
        rows.append({"ad_unit": unit, **{f"before_{k}": v for k, v in sb.items()},
                     **{f"after_{k}": v for k, v in sa.items()}})
        dg = sa["gap_pp"] - sb["gap_pp"]
        print(f"{unit[:21]:<22}{sb['impressions']:>10,}{_pct(sb['viewable_pct']):>8}"
              f"{_pct(sb['peer_pct']):>7}{sa['impressions']:>10,}{_pct(sa['viewable_pct']):>8}"
              f"{_pct(sa['peer_pct']):>7}{('   —' if pd.isna(dg) else f'{dg:+7.1f}')}")
    pd.DataFrame(rows).to_csv(OUT_DIR / f"{BIDDER}_before_after_by_adunit.csv", index=False)
    print(f"\nCSVs: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
