"""
Magnite Open Bidding video ad-request volume, to cross-check the SSP's own
"Seller Integration Type" report against GAM's view of the same callouts.

Context (2026-09-18): Magnite's video report for 2026-08-18 → 2026-09-16 shows
Open Bidding at 265,819,907 "Ad Requests" — 2.07x Prebid Server (RP Hosted).
RESULT (run 35376751276): GAM records 52,036,623 video callouts — Magnite
reports 5.11x that. BOTH ARE CORRECT. Ad Manager applies *bid flattening* to
video: one callout is split into several OpenRTB bid requests (by format, video
duration, and pod position) before it reaches the exchange. YIELD_GROUP_CALLOUTS
counts the callout, PRE-split; Magnite counts the requests, POST-split. So a
callout is an OPPORTUNITY count, not a requests-received count, and the two
columns are simply in different units.

Confirmed by Google Partner Solutions 2026-09-22 and publicly documented:
https://support.google.com/authorizedbuyers/answer/9198190

An earlier version of this script and doc concluded the gap was Magnite's
reporting error. That was WRONG and is withdrawn. The tell we already had:
Magnite reported 115,402,553 ad responses, which is 2.22x the 52,036,623
requests the old reading said they received — a bidder cannot respond more
often than it is asked. See docs/ob_vs_prebid_video_requests.md.

GAM-side notes (see CLAUDE.md "GAM facts"):
- HEADER_BIDDER_INTEGRATION_TYPE_NAME is incompatible with every YIELD_GROUP_*
  metric, so we filter by YIELD_GROUP_NAME instead. Both yield groups at
  Newsweek are 100% Open Bidding (every ad source is OPEN_BIDDING), so a
  callout count filtered to `video` is OB-only by construction.
- YIELD_GROUP_CALLOUTS is the GAM UI's "Ad requests" column for a yield partner.
  Despite the label it is an OPPORTUNITY count, not requests-received (see above).
- Funnel: CALLOUTS -> (requests split ~5x on video) -> BIDS -> AUCTIONS_WON
  -> IMPRESSIONS. Only CALLOUTS is measured per callout; everything after it is
  measured per individual bid, which is why BIDS can exceed CALLOUTS on video.
- AUCTIONS_WON is counted per winning BID at ad-selection time, before render,
  so AUCTIONS_WON/IMPRESSIONS is not a render rate.
"""

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from gam_client import GAMClient  # noqa: E402

# The exact window of the Magnite report we are reconciling against.
START = date(2026, 8, 18)
END = date(2026, 9, 16)

# What the Magnite "Seller Integration Type" report claims for this window.
MAGNITE_CLAIMS = {
    "ad_requests": 265_819_907,
    "auctions": 99_800_117,
    "ad_responses": 115_402_553,
    "paid_impressions": 4_658_480,
}

METRICS = [
    "YIELD_GROUP_CALLOUTS",
    "YIELD_GROUP_BIDS",
    "YIELD_GROUP_AUCTIONS_WON",
    "YIELD_GROUP_IMPRESSIONS",
]
METRIC_COLS = [m.lower() for m in METRICS]


def main() -> None:
    client = GAMClient()
    df = client._run_report(
        dimensions=["DATE", "YIELD_GROUP_NAME", "YIELD_GROUP_BUYER_NAME"],
        metrics=METRICS,
        start_date=START,
        end_date=END,
    )

    for c in METRIC_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")

    print(f"GAM Open Bidding callouts, {START} -> {END}\n")

    # 1. Every buyer, so the exact spelling of Magnite's name is visible and we
    #    can see whether it is split across several ad sources.
    by_buyer = (
        df.groupby(["yield_group_name", "yield_group_buyer_name"], as_index=False)[METRIC_COLS]
        .sum()
        .sort_values(["yield_group_name", "yield_group_callouts"], ascending=[True, False])
    )
    print("=== All OB buyers by yield group (window totals) ===")
    print(by_buyer.to_string(index=False))

    # 2. Magnite only. Match loosely: the ad source may be named Magnite,
    #    Rubicon, or carry a DV+/Demand Manager suffix.
    mask = df["yield_group_buyer_name"].str.contains(
        "magnite|rubicon", case=False, na=False
    )
    mag = df[mask]
    if mag.empty:
        print("\n!! No buyer matched magnite|rubicon — check the names listed above.")
        return

    print(f"\nMatched buyer names: {sorted(mag['yield_group_buyer_name'].unique())}")

    mag_by_group = mag.groupby("yield_group_name", as_index=False)[METRIC_COLS].sum()
    print("\n=== Magnite by yield group ===")
    print(mag_by_group.to_string(index=False))

    video = mag[mag["yield_group_name"].str.contains("video", case=False, na=False)]
    if video.empty:
        print("\n!! No `video` yield group rows for Magnite.")
        return

    tot = video[METRIC_COLS].sum()
    callouts = int(tot["yield_group_callouts"])
    claim = MAGNITE_CLAIMS["ad_requests"]

    print("\n=== VIDEO yield group — Magnite, window totals ===")
    print(f"  GAM callouts (ad requests) : {callouts:,}")
    print(f"  GAM bids                   : {int(tot['yield_group_bids']):,}")
    print(f"  GAM auctions won           : {int(tot['yield_group_auctions_won']):,}")
    print(f"  GAM impressions            : {int(tot['yield_group_impressions']):,}")

    print("\n=== Reconciliation against Magnite's own report ===")
    print(f"  {'metric':<26}{'Magnite':>14}{'GAM':>14}{'ratio':>9}{'delta':>10}")
    pairs = [
        ("Ad requests / callouts", MAGNITE_CLAIMS["ad_requests"], callouts),
        ("Auctions / auctions won", MAGNITE_CLAIMS["auctions"], int(tot["yield_group_auctions_won"])),
        ("Ad responses / bids", MAGNITE_CLAIMS["ad_responses"], int(tot["yield_group_bids"])),
        ("Paid impr / impressions", MAGNITE_CLAIMS["paid_impressions"], int(tot["yield_group_impressions"])),
    ]
    for lbl, m, g in pairs:
        print(f"  {lbl:<26}{m:>14,}{g:>14,}{m / g:>8.2f}x{(m - g) / g:>+10.1%}")

    print(
        "\n  Read: CALLOUTS is an OPPORTUNITY count, measured BEFORE Ad Manager "
        "splits a video\n  callout into several OpenRTB bid requests (bid flattening: "
        "format, duration, pods).\n  The exchange counts the split requests, so a ratio "
        "of ~5x on the request row is\n  EXPECTED on video and is not an error on "
        "either side. Responses and impressions\n  near 1.00x is the real "
        "reconciliation — those are counted in the same units.\n  Do NOT compare this "
        "callout count against an exchange's request column, and do NOT\n  compare a "
        "flattened OB request count against an unflattened Prebid Server one."
    )

    # 3. Cross-partner bids-per-callout, computed here rather than transcribed.
    #    This is the table that shows the anomaly is Magnite x video: every
    #    other OB partner sits well below 1.0 bids per callout on video, and
    #    only Magnite's video bid count matches its display bid count.
    print("\n=== Bids per callout, EVERY OB partner, by yield group ===")
    piv = (
        df.groupby(["yield_group_buyer_name", "yield_group_name"], as_index=False)[METRIC_COLS]
        .sum()
    )
    by_buyer: dict[str, dict[str, tuple[int, int]]] = {}
    for r in piv.itertuples(index=False):
        by_buyer.setdefault(r.yield_group_buyer_name, {})[r.yield_group_name] = (
            int(r.yield_group_callouts), int(r.yield_group_bids)
        )

    def _ratio(pair: tuple[int, int] | None) -> float | None:
        if not pair or pair[0] == 0:
            return None
        return pair[1] / pair[0]

    def _fmt(x: float | None, spec: str = ">9.3f") -> str:
        return format(x, spec) if x is not None else " " * int(spec.split(".")[0][1:])

    hdr = (f"  {'partner':<30}{'disp b/c':>10}{'video b/c':>11}"
           f"{'video bids':>15}{'display bids':>15}{'vid/disp':>10}")
    print(hdr)
    rows = []
    for buyer, groups in by_buyer.items():
        vid, disp = groups.get("video"), groups.get("display")
        rows.append((buyer, _ratio(disp), _ratio(vid),
                     vid[1] if vid else 0, disp[1] if disp else 0))
    for buyer, dr, vr, vb, db in sorted(rows, key=lambda x: -(x[2] or 0)):
        vd = (vb / db) if db else None
        flag = "   <== bids EXCEED callouts" if (vr or 0) > 1 else ""
        print(f"  {buyer:<30}{_fmt(dr)}{_fmt(vr, '>11.3f')}{vb:>15,}{db:>15,}"
              f"{_fmt(vd, '>10.3f')}{flag}")
    over = [r[0] for r in rows if (r[2] or 0) > 1]
    print(f"\n  partners whose VIDEO bids exceed their video callouts: "
          f"{over if over else 'none'}")
    print("  (>1.0 is EXPECTED on video: bids are counted after the ~5x flattening\n"
          "   split, callouts before it, so any partner bidding above ~1/split shows\n"
          "   a ratio over 1. Divide by the split factor for the true bid rate —\n"
          "   Magnite 2.29/5.11 = 45%, the same as its display rate. The others bid\n"
          "   2-5%, which is why the split stays invisible on their rows.)")

    # 4. The decisive test for the bids anomaly: are Magnite's DAILY display
    #    bids and DAILY video bids the same number? At window level they match
    #    to 0.17%, which is either one total attributed to both yield groups or
    #    a remarkable coincidence. A day-by-day match settles it; a day-by-day
    #    divergence means the two figures really are independently measured and
    #    the window-level match is chance.
    print("\n=== Magnite: daily DISPLAY bids vs daily VIDEO bids ===")
    mag_daily = (
        mag.groupby(["date", "yield_group_name"], as_index=False)[METRIC_COLS].sum()
    )
    piv_d = mag_daily.pivot(index="date", columns="yield_group_name",
                            values="yield_group_bids").fillna(0).astype("int64")
    piv_c = mag_daily.pivot(index="date", columns="yield_group_name",
                            values="yield_group_callouts").fillna(0).astype("int64")
    if "display" in piv_d.columns and "video" in piv_d.columns:
        print(f"  {'date':<12}{'display bids':>15}{'video bids':>15}{'vid/disp':>10}"
              f"{'video callouts':>16}{'vid b/c':>9}")
        exact = near = 0
        for d in piv_d.index:
            db, vb = int(piv_d.loc[d, "display"]), int(piv_d.loc[d, "video"])
            vc = int(piv_c.loc[d, "video"]) if "video" in piv_c.columns else 0
            r = vb / db if db else float("nan")
            exact += (db == vb)
            near += (db and abs(vb - db) / db < 0.01)
            print(f"  {str(d)[:10]:<12}{db:>15,}{vb:>15,}{r:>10.3f}{vc:>16,}"
                  f"{(vb / vc if vc else float('nan')):>9.2f}")
        n = len(piv_d.index)
        print(f"\n  days where display bids == video bids exactly: {exact}/{n}")
        print(f"  days where they agree within 1%:               {near}/{n}")
        if near >= n * 0.9:
            print("  -> VERDICT: the same bid total is being reported under both yield\n"
                  "     groups. GAM's per-yield-group bid split is not trustworthy for\n"
                  "     this partner, and the video figure should not be used.")
        else:
            print("  -> VERDICT: the daily figures diverge, so the two are independently\n"
                  "     measured and the window-level match is coincidence. The video\n"
                  "     bids > callouts ratio is then explained by bid flattening:\n"
                  "     bids are counted post-split, callouts pre-split.")
    else:
        print("  (need both yield groups in the window to run this test)")

    # 5. A shareable CSV of the raw rows behind the claim — this is what goes
    #    to Google Support / the SSP, so it is written straight from the API
    #    response with no reshaping beyond column ordering.
    out_csv = os.environ.get("CSV_OUT")
    if out_csv:
        cols = ["date", "yield_group_name", "yield_group_buyer_name"] + METRIC_COLS
        mag.sort_values(["yield_group_name", "date"])[cols].to_csv(out_csv, index=False)
        print(f"\n[csv] wrote {out_csv} ({len(mag)} rows: "
              f"{mag['yield_group_name'].nunique()} yield groups x "
              f"{mag['date'].nunique()} days)")

    # 6. Daily series, so a partial-day or gap at either end is visible rather
    #    than silently skewing the window total.
    daily = (
        video.groupby("date", as_index=False)[METRIC_COLS]
        .sum()
        .sort_values("date")
    )
    print("\n=== VIDEO yield group — Magnite, by day ===")
    print(daily.to_string(index=False))


if __name__ == "__main__":
    main()
