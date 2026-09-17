"""Why is SmileWanted the only wrapper bidder with this deficit — and is it the creative?

The display audit (scripts/prebid_viewability_audit.py, SCOPE=display) established
WHAT is wrong: smilewanted measures ~35pp below its peers on every ad unit, mix
+0.5 / render -35.1, with measurable at 100%. Two questions it cannot answer:

  1. WHY only them? Every remaining "it's their inventory, not their creative"
     explanation is a mix story the per-unit cut does not control for — device,
     geography, browser. If the deficit survives INSIDE each of those cuts too,
     mix is dead and it is the render.

  2. Is there a creative red flag? Active View cannot show the markup, but it can
     show CLICKS, and the ratio of clicks to viewable impressions is the tell the
     Mobkoi work turned on (docs/mobkoi_viewability.md): an ad that users click
     at a normal rate while Active View scores it non-viewable is being SEEN and
     mis-measured — that is the breakout signature. An ad whose clicks fall in
     proportion to its viewability is genuinely NOT being seen, which is a real
     delivery defect and a different conversation with the SSP.

Each cut is its own report because GAM refuses most dimension pairs alongside
KEY_VALUES_NAME (CONSTRAINTS_INCOMPATIBILITY — the audit's ladder learned that
AD_UNIT_NAME is the only companion that survives). Every cut here is therefore
attempted and skipped cleanly if refused, and every comparison is leave-one-out
within the cell, so no bidder is graded against its own impressions.

Env: DAYS (default 21), BIDDERS (default smilewanted plus the other flagged
three), EXCLUDE_UNITS (default vid.newsweek — display only, same scope as the
audit), PREBID_ADVERTISER_ID, OUT_DIR.
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

from gam_client import GAMClient, _D, _M  # noqa: E402

DAYS = int(os.environ.get("DAYS") or "21")
ADVERTISER_ID = int(os.environ.get("PREBID_ADVERTISER_ID") or "5724335726")
FOCUS = [b.strip().lower() for b in (
    os.environ.get("BIDDERS") or "smilewanted,ogury,oms,onetag,kargo"
).split(",") if b.strip()]
EXCLUDE_UNITS = {u.strip() for u in (
    os.environ.get("EXCLUDE_UNITS") or "vid.newsweek").split(",") if u.strip()}
OUT_DIR = Path(os.environ.get("OUT_DIR") or "/tmp/prebid-smilewanted-forensics")

_HB_PREFIX = "hb_bidder="

# Clicks are the point of this script; the rest mirror the audit. Names are
# proto enum members, so an unknown one raises KeyError rather than failing at
# the API — probe first and report what this API version does not carry.
_WANT_METRICS = [
    "AD_SERVER_IMPRESSIONS",
    "ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
    "ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
    "AD_SERVER_CLICKS",
    # Average viewable time would date the failure precisely (a creative that
    # paints late leaves short viewable times); it is not in every API version.
    "ACTIVE_VIEW_AVERAGE_VIEWABLE_TIME",
    "AD_SERVER_ACTIVE_VIEW_AVERAGE_VIEWABLE_TIME",
]


def _known(names: list[str], enum=_M, label: str = "metrics") -> list[str]:
    """Keep the names this API version actually carries.

    These are proto enum members, so an unknown one is a KeyError at call time,
    not an API error — OPERATING_SYSTEM_NAME crashed the first run that way.
    """
    out = []
    for n in names:
        try:
            enum[n]
            out.append(n)
        except (KeyError, ValueError):
            print(f"[{label}] not in this API version, skipping: {n}")
    return out


METRICS = _known(_WANT_METRICS)
# The cuts below were refused even with hb_bidder demoted to a filter, which
# points at the METRIC set rather than the dimensions: GAM's compatibility
# rules cover metrics too, and the audit's leaner set survives cuts this one
# does not. So the cuts retry with the minimum that still answers the question.
_MIN_METRICS = [m for m in ("AD_SERVER_IMPRESSIONS",
                            "ACTIVE_VIEW_VIEWABLE_IMPRESSIONS") if m in METRICS]

_REN = {
    "ad_server_impressions": "impressions",
    "active_view_measurable_impressions": "measurable",
    "active_view_viewable_impressions": "viewable_impressions",
    "ad_server_clicks": "clicks",
    "active_view_average_viewable_time": "avg_viewable_time",
}


def _bidder(kv: object) -> str | None:
    s = str(kv or "")
    return s.split("=", 1)[1].strip().lower() if s.startswith(_HB_PREFIX) else None


def _pull(gam: GAMClient, dims: list[str], start: date, end: date,
          kv_value: str | None = None, metrics: list[str] | None = None):
    """Return the cut as a frame, or None if GAM refuses this dimension set.

    `kv_value` narrows the hb_bidder filter to one bidder, which is how the
    cuts that cannot carry KEY_VALUES_NAME as a dimension still get a per-
    bidder answer.
    """
    dims = _known(dims, _D, "dimensions")
    if not dims:
        return None
    needle = _HB_PREFIX + (kv_value or "")
    try:
        df = gam._run_report(
            dimensions=dims, metrics=metrics or METRICS, start_date=start, end_date=end,
            filters=[("ADVERTISER_ID", "IN", [ADVERTISER_ID]),
                     ("KEY_VALUES_NAME", "CONTAINS", [needle])],
        )
    except Exception as exc:  # noqa: BLE001
        if "CONSTRAINTS_INCOMPATIBILITY" in str(exc):
            print(f"  [refused] {', '.join(dims)}")
            return None
        raise
    df = df.rename(columns=_REN)
    if "key_values_name" in df.columns:
        df["bidder"] = df["key_values_name"].map(_bidder)
        df = df[df["bidder"].notna()]
    if "ad_unit_name" in df.columns and EXCLUDE_UNITS:
        df = df[~df["ad_unit_name"].isin(EXCLUDE_UNITS)]
    return df


def _rate(n, d) -> float:
    d = float(d or 0)
    return float(n or 0) / d * 100.0 if d else float("nan")


def _dwell(df: pd.DataFrame) -> None:
    """Average time in view, for the impressions that DID become viewable.

    This is the sharpest thing GAM can say about a creative it cannot show us.
    A creative that renders late, paints slowly, or is heavy would be viewable
    for LESS time than its peers in the same slot — the user has already been
    on the page a while when it finally appears. A creative whose dwell matches
    its peers exactly, while far fewer of its impressions ever become viewable,
    is failing in a BINARY way: fully seen, or never seen. Those are different
    conversations with an SSP, and the number below decides which one.
    """
    if "avg_viewable_time" not in df.columns:
        print("(average viewable time unavailable in this API version)")
        return
    # GAM averages the time over each row's viewable impressions, so
    # re-aggregating means weighting by viewable impressions.
    df = df.assign(_t=df["avg_viewable_time"] * df["viewable_impressions"])
    g = df.groupby("bidder").agg(imps=("impressions", "sum"),
                                 vw=("viewable_impressions", "sum"),
                                 t=("_t", "sum"))
    g = g[g["imps"] >= 100_000].sort_values("imps", ascending=False)
    print("\n" + "=" * 78)
    print("DWELL — average seconds in view, of the impressions that became viewable")
    print("=" * 78)
    print(f"{'bidder':<18}{'imps':>12}{'viewable%':>11}{'avg secs in view':>18}")
    for b, r in g.iterrows():
        print(f"{b:<18}{int(r.imps):>12,}{_rate(r.vw, r.imps):>10.1f}%"
              f"{r.t / r.vw if r.vw else float('nan'):>18.1f}")

    unit = df.groupby("ad_unit_name").agg(vw=("viewable_impressions", "sum"),
                                          t=("_t", "sum"))
    print("\nPER UNIT, vs the peers in that same unit")
    for b in FOCUS:
        sub = df[df["bidder"] == b].groupby("ad_unit_name").agg(
            imps=("impressions", "sum"), vw=("viewable_impressions", "sum"),
            t=("_t", "sum"))
        sub = sub[sub["vw"] >= 500].sort_values("imps", ascending=False)
        if sub.empty:
            continue
        print(f"\n-- {b} --")
        print(f"{'unit':<14}{'imps':>12}{'viewable%':>11}{'its secs':>10}{'peer secs':>11}")
        for u, r in sub.head(8).iterrows():
            pv = unit.loc[u, "vw"] - r.vw
            pt = unit.loc[u, "t"] - r.t
            print(f"{str(u):<14}{int(r.imps):>12,}{_rate(r.vw, r.imps):>10.1f}%"
                  f"{r.t / r.vw:>10.1f}{pt / pv if pv else float('nan'):>11.1f}")


def _cut_by_filter(gam: GAMClient, dim: str, start: date, end: date,
                   title: str) -> None:
    """Break one dimension down per bidder, WITHOUT KEY_VALUES_NAME as a dimension.

    GAM refuses KEY_VALUES_NAME alongside device, country and browser
    (CONSTRAINTS_INCOMPATIBILITY), which killed the first attempt at these cuts.
    But hb_bidder is still usable as a FILTER, so pull the dimension once per
    bidder with `hb_bidder=<name>` filtered server-side, and once for all
    wrapper demand; peers are then the book minus that bidder, which is the
    same leave-one-out comparison by subtraction.
    """
    metrics = METRICS
    book = _pull(gam, [dim], start, end, kv_value="")
    if book is None:
        # Retry with the leanest metric set before giving up on the cut.
        metrics = _MIN_METRICS
        book = _pull(gam, [dim], start, end, kv_value="", metrics=metrics)
        if book is not None:
            print(f"  [{dim}] accepted with the lean metric set "
                  f"({', '.join(metrics)})")
    if book is None or book.empty:
        return
    book = book.groupby(dim)[["impressions", "viewable_impressions"]].sum()
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    for b in FOCUS:
        mine = _pull(gam, [dim], start, end, kv_value=b, metrics=metrics)
        if mine is None or mine.empty:
            continue
        mine = mine.groupby(dim)[["impressions", "viewable_impressions"]].sum()
        mine = mine[mine["impressions"] >= 1000].sort_values(
            "impressions", ascending=False)
        if mine.empty:
            continue
        total = mine["impressions"].sum()
        print(f"\n-- {b} --")
        print(f"{dim:<26}{'imps':>12}{'share':>8}{'this%':>8}{'peers%':>8}{'gap pp':>9}")
        for k, r in mine.head(8).iterrows():
            pi = book.loc[k, "impressions"] - r.impressions
            pv = book.loc[k, "viewable_impressions"] - r.viewable_impressions
            this, peer = _rate(r.viewable_impressions, r.impressions), _rate(pv, pi)
            print(f"{str(k)[:24]:<26}{int(r.impressions):>12,}"
                  f"{r.impressions / total * 100:>7.1f}%{this:>7.1f}%"
                  f"{peer:>7.1f}%{this - peer:>+9.1f}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=DAYS - 1)
    gam = GAMClient()

    print("=" * 78)
    print(f"SMILEWANTED FORENSICS  {start} → {end}  (display only, "
          f"excluding {', '.join(sorted(EXCLUDE_UNITS)) or 'nothing'})")
    print("=" * 78)
    print(f"metrics: {', '.join(METRICS)}")

    # ── the base cut: bidder x unit, with dwell and clicks ───────────────
    base = _pull(gam, ["KEY_VALUES_NAME", "AD_UNIT_NAME"], start, end)
    if base is None:
        raise SystemExit("the base cut was refused — nothing else will work")
    base.to_csv(OUT_DIR / "by_unit_with_dwell.csv", index=False)

    # ── 1. dwell: binary failure, or a slow creative? ────────────────────
    _dwell(base)

    # ── 2. clicks: kept, but GAM books none for wrapper demand ───────────
    # The Prebid universal creative renders the buyer's markup inside the GPT
    # iframe and the click leaves through the buyer's own click tracker, so
    # GAM's click server never sees it: AD_SERVER_CLICKS is 0 for EVERY
    # wrapper bidder including the healthy ones. That is a property of the
    # integration, not a signal about any creative — so report the column as
    # unusable rather than printing a 0.000% CTR that reads like a finding.
    if "clicks" in base.columns:
        tot_clicks = int(base["clicks"].sum())
        print(f"\n[clicks] AD_SERVER_CLICKS across all wrapper demand: {tot_clicks:,}"
              + (" — GAM books no clicks for wrapper demand (the click leaves"
                 " through the buyer's tracker inside the creative), so the"
                 " CTR-vs-viewability test cannot be run from GAM."
                 if tot_clicks == 0 else ""))

    # ── 3. does any mix explanation survive? ─────────────────────────────
    for dim, title in [
        ("DEVICE_CATEGORY_NAME", "BY DEVICE — is it a device-mix story?"),
        ("COUNTRY_NAME",
         "BY COUNTRY — is it a geo-mix story? (smilewanted is a French SSP)"),
        ("BROWSER_NAME", "BY BROWSER — is it a browser/webview story?"),
    ]:
        _cut_by_filter(gam, dim, start, end, title)

    print(f"\nCSVs: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
