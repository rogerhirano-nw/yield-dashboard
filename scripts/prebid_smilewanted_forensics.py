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

from gam_client import GAMClient, _M  # noqa: E402

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


def _known(names: list[str]) -> list[str]:
    out = []
    for n in names:
        try:
            _M[n]
            out.append(n)
        except (KeyError, ValueError):
            print(f"[metrics] not in this API version, skipping: {n}")
    return out


METRICS = _known(_WANT_METRICS)

_REN = {
    "ad_server_impressions": "impressions",
    "active_view_measurable_impressions": "measurable",
    "active_view_viewable_impressions": "viewable_impressions",
    "ad_server_clicks": "clicks",
}


def _bidder(kv: object) -> str | None:
    s = str(kv or "")
    return s.split("=", 1)[1].strip().lower() if s.startswith(_HB_PREFIX) else None


def _pull(gam: GAMClient, dims: list[str], start: date, end: date):
    """Return the cut as a frame, or None if GAM refuses this dimension set."""
    try:
        df = gam._run_report(
            dimensions=dims, metrics=METRICS, start_date=start, end_date=end,
            filters=[("ADVERTISER_ID", "IN", [ADVERTISER_ID]),
                     ("KEY_VALUES_NAME", "CONTAINS", [_HB_PREFIX])],
        )
    except Exception as exc:  # noqa: BLE001
        if "CONSTRAINTS_INCOMPATIBILITY" in str(exc):
            print(f"  [refused] {', '.join(dims)}")
            return None
        raise
    df = df.rename(columns=_REN)
    df["bidder"] = df["key_values_name"].map(_bidder)
    df = df[df["bidder"].notna()]
    if "ad_unit_name" in df.columns and EXCLUDE_UNITS:
        df = df[~df["ad_unit_name"].isin(EXCLUDE_UNITS)]
    return df


def _rate(n, d) -> float:
    d = float(d or 0)
    return float(n or 0) / d * 100.0 if d else float("nan")


def _cut(df: pd.DataFrame, key: str, title: str) -> None:
    """Focus bidders vs leave-one-out peers within each value of `key`.

    If the deficit holds inside every device / country / browser, then no
    composition of those explains it, and only the render is left.
    """
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    tot = df.groupby(key)[["impressions", "viewable_impressions"]].sum()
    for b in FOCUS:
        sub = df[df["bidder"] == b]
        if sub.empty:
            continue
        g = sub.groupby(key)[["impressions", "viewable_impressions"]].sum()
        g = g[g["impressions"] >= 1000].sort_values("impressions", ascending=False)
        if g.empty:
            continue
        print(f"\n-- {b} --")
        print(f"{key:<26}{'imps':>12}{'share':>8}{'this%':>8}{'peers%':>8}{'gap pp':>9}")
        share_base = sub["impressions"].sum()
        for k, r in g.head(10).iterrows():
            pi = tot.loc[k, "impressions"] - r.impressions
            pv = tot.loc[k, "viewable_impressions"] - r.viewable_impressions
            this, peer = _rate(r.viewable_impressions, r.impressions), _rate(pv, pi)
            print(f"{str(k)[:24]:<26}{int(r.impressions):>12,}"
                  f"{r.impressions / share_base * 100:>7.1f}%{this:>7.1f}%"
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

    # ── 1. clicks vs viewability: is the ad seen but mis-measured? ────────
    base = _pull(gam, ["KEY_VALUES_NAME", "AD_UNIT_NAME"], start, end)
    if base is None:
        raise SystemExit("the base cut was refused — nothing else will work")
    base.to_csv(OUT_DIR / "by_unit_with_clicks.csv", index=False)

    g = base.groupby("bidder")[
        [c for c in ["impressions", "viewable_impressions", "clicks"] if c in base]].sum()
    g = g[g["impressions"] >= 50_000].sort_values("impressions", ascending=False)
    print("\n" + "=" * 78)
    print("ENGAGEMENT vs VIEWABILITY — the creative test")
    print("A bidder whose CTR holds up while Active View scores it non-viewable is")
    print("being SEEN and mis-measured (the Mobkoi signature). One whose clicks fall")
    print("with its viewability is genuinely not being seen.")
    print("=" * 78)
    if "clicks" not in g.columns:
        print("(clicks unavailable in this API version — cannot run the test)")
    else:
        site_ctr = _rate(g["clicks"].sum(), g["impressions"].sum())
        site_vw = _rate(g["viewable_impressions"].sum(), g["impressions"].sum())
        print(f"\nbook: {site_vw:.1f}% viewable, CTR {site_ctr:.3f}%\n")
        print(f"{'bidder':<18}{'imps':>12}{'viewable%':>11}{'CTR%':>9}"
              f"{'CTR idx':>9}{'clicks/viewable impr%':>23}")
        for b, r in g.iterrows():
            ctr = _rate(r.clicks, r.impressions)
            print(f"{b:<18}{int(r.impressions):>12,}"
                  f"{_rate(r.viewable_impressions, r.impressions):>10.1f}%"
                  f"{ctr:>8.3f}%{ctr / site_ctr * 100 if site_ctr else float('nan'):>9.0f}"
                  f"{_rate(r.clicks, r.viewable_impressions):>22.3f}%")

    # ── 2. does the deficit survive inside every other cut? ───────────────
    for dims, key, title in [
        (["KEY_VALUES_NAME", "DEVICE_CATEGORY_NAME"], "device_category_name",
         "BY DEVICE — is it a device-mix story?"),
        (["KEY_VALUES_NAME", "COUNTRY_NAME"], "country_name",
         "BY COUNTRY — is it a geo-mix story? (smilewanted is a French SSP)"),
        (["KEY_VALUES_NAME", "BROWSER_NAME"], "browser_name",
         "BY BROWSER — is it a browser/webview story?"),
        (["KEY_VALUES_NAME", "OPERATING_SYSTEM_NAME"], "operating_system_name",
         "BY OS — is it an OS story?"),
    ]:
        print(f"\npulling {', '.join(dims)} …")
        df = _pull(gam, dims, start, end)
        if df is None or df.empty:
            continue
        df.to_csv(OUT_DIR / f"by_{key}.csv", index=False)
        _cut(df, key, title)

    print(f"\nCSVs: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
