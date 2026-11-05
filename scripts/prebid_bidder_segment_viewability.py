"""One bidder on one ad unit, cut by device / OS / browser, against peers.

Follow-up to docs/prebid_viewability.md: Ogury reads 35.5% viewable on
`sticky` against 92.9% for every other bidder on the same unit (Oct 9-19),
while its in-article units are at parity. On-page forensics can't catch an
Ogury sticky win at a useful rate (58 cloud loads + 15 residential loads, 0
sticky wins), so this asks GAM instead: is the deficit everywhere, or
concentrated in one environment? A gap that lives in one OS or browser (say
iOS Safari at 10% while Chrome Android matches peers) names the cause without
a single captured render; a gap that is uniform says the creative itself
fails to paint.

For each segment dimension it runs hb_bidder (custom dimension) × AD_UNIT_NAME
× <segment>, filtered to the one ad unit, and prints per segment value the
bidder's impressions, share of its volume, measurable%, viewable%, the peer
viewable% in the same segment, and the gap. A dimension GAM refuses to cross
with the custom dimension is reported and skipped, never fatal.

Env: BIDDER (default ogury), AD_UNIT (default sticky), DAYS (default 11,
ending yesterday), SEGMENTS (comma list of GAM dimensions), TOP (rows per
segment, default 15), PREBID_ADVERTISER_IDS, OUT_DIR. Needs
GAM_SERVICE_ACCOUNT_JSON + GAM_NETWORK_ID, so it runs in Actions
(.github/workflows/prebid_bidder_segment_viewability.yml).
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

BIDDER = (os.environ.get("BIDDER") or "ogury").strip().lower()
AD_UNIT = (os.environ.get("AD_UNIT") or "sticky").strip()
DAYS = int(os.environ.get("DAYS") or "11")
SEGMENTS = [s.strip() for s in (
    os.environ.get("SEGMENTS")
    or "DEVICE_CATEGORY_NAME,OPERATING_SYSTEM_VERSION_NAME,BROWSER_NAME,DEVICE_MODEL_NAME"
).split(",") if s.strip()]
TOP = int(os.environ.get("TOP") or "15")
OUT_DIR = Path(os.environ.get("OUT_DIR") or "/tmp/prebid-bidder-segments")


def _cut(df: pd.DataFrame, seg: str) -> pd.DataFrame:
    """Per segment value: bidder vs peers on the same unit and segment."""
    cols = ["impressions", "eligible", "measurable", "viewable"]
    mine = df[df["bidder"] == BIDDER].groupby(seg)[cols].sum()
    peers = df[df["bidder"] != BIDDER].groupby(seg)[cols].sum()
    out = pd.DataFrame({
        "imps": mine["impressions"],
        "share": mine["impressions"] / mine["impressions"].sum() * 100,
        "meas": [_rate(m, e) for m, e in zip(mine["measurable"], mine["eligible"])],
        "vw": [_rate(v, i) for v, i in zip(mine["viewable"], mine["impressions"])],
    })
    p = peers.reindex(out.index)
    out["peer_imps"] = p["impressions"].fillna(0).astype(int)
    out["peer_vw"] = [_rate(v, i) for v, i in zip(p["viewable"], p["impressions"])]
    out["gap"] = out["vw"] - out["peer_vw"]
    # Viewable impressions the bidder is short of peers in this segment —
    # ranks where the loss actually is, not just where the rate is worst.
    out["short"] = ((out["peer_vw"] - out["vw"]) / 100 * out["imps"]).fillna(0)
    return out.sort_values("imps", ascending=False)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=DAYS - 1)
    gam = GAMClient()

    print("=" * 78)
    print(f"{BIDDER} ON '{AD_UNIT}' BY DEVICE / OS / BROWSER vs PEERS")
    print(f"{start} → {end}   advertisers {ADVERTISER_IDS}")
    print("=" * 78)

    bidder_key = _find_keys({"hb_bidder"}).get("hb_bidder")
    if bidder_key is None or bidder_key.reportable_type != _CUSTOM_DIM:
        raise SystemExit("hb_bidder is not a reportable custom dimension.")
    filters = [("ADVERTISER_ID", "IN", ADVERTISER_IDS), ("AD_UNIT_NAME", "IN", [AD_UNIT])]

    for seg in SEGMENTS:
        col = seg.lower()
        try:
            df = gam._run_report(
                dimensions=["CUSTOM_DIMENSION_0_VALUE", "AD_UNIT_NAME", seg],
                metrics=METRICS, start_date=start, end_date=end, filters=filters,
                custom_dimension_key_ids=[_key_id(bidder_key)],
            )
        except Exception as exc:  # incompatible dimension pairs are expected
            print(f"\n-- {seg}: GAM refused this cut ({str(exc)[:160]}) --")
            continue
        df = df.rename(columns={"custom_dimension_0_value": "bidder"}).rename(columns=_RENAME)
        df["bidder"] = df["bidder"].astype(str).str.strip().str.lower()
        df = df[df["ad_unit_name"].astype(str) == AD_UNIT]
        df[col] = df[col].astype(str).replace({"": "(none)"})
        df.to_csv(OUT_DIR / f"{BIDDER}_{AD_UNIT}_{col}.csv", index=False)
        if df[df["bidder"] == BIDDER].empty:
            print(f"\n-- {seg}: no {BIDDER} impressions on {AD_UNIT} --")
            continue
        t = _cut(df, col)
        tot_m = df[df["bidder"] == BIDDER][["impressions", "viewable"]].sum()
        tot_p = df[df["bidder"] != BIDDER][["impressions", "viewable"]].sum()
        print(f"\n-- by {seg}  ({BIDDER} {int(tot_m.impressions):,} imps "
              f"{_pct(_rate(tot_m.viewable, tot_m.impressions))} vs peers "
              f"{_pct(_rate(tot_p.viewable, tot_p.impressions))}) --")
        print(f"{'segment':<34}{'imps':>10}{'share':>7}{'meas%':>8}{'vw%':>8}"
              f"{'peer imps':>11}{'peers%':>8}{'gap':>7}{'vw short':>10}")
        for k, r in t.head(TOP).iterrows():
            gap = "    —" if pd.isna(r.gap) else f"{r.gap:+7.1f}"
            print(f"{str(k)[:33]:<34}{int(r.imps):>10,}{r.share:>6.1f}%{_pct(r.meas):>8}"
                  f"{_pct(r.vw):>8}{int(r.peer_imps):>11,}{_pct(r.peer_vw):>8}{gap}"
                  f"{int(r.short):>10,}")
        if len(t) > TOP:
            rest = t.iloc[TOP:]
            print(f"{'(' + str(len(rest)) + ' more)':<34}{int(rest.imps.sum()):>10,}"
                  f"{rest.share.sum():>6.1f}%")
        # Where the loss concentrates: share of the total viewable shortfall
        # carried by the top segments.
        short = t["short"].clip(lower=0)
        if short.sum() > 0:
            top3 = short.sort_values(ascending=False).head(3)
            print("  shortfall concentration: " + ", ".join(
                f"{k} {v / short.sum() * 100:.0f}%" for k, v in top3.items()))

    print(f"\nCSVs: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
