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

Env: DAYS (default 21), SCOPE (display | video | all — default **display**,
since banner and video have different baselines and pooling them hides a
format-specific defect), MIN_UNIT_IMPS / MIN_BIDDER_IMPS (per-unit reporting
floors), PREBID_ADVERTISER_ID (default 5724335726, the advertiser the source
report filtered on), BIDDERS (extra bidders to detail), OUT_DIR. Requires GAM_SERVICE_ACCOUNT_JSON + GAM_NETWORK_ID, so it runs in
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
# SCOPE keeps banner and video apart. They are different questions with
# different baselines (~78% vs ~86%), and pooling them hides a format-specific
# defect inside a healthy average — onetag reads a clean 75.6% pooled while its
# video is 49% against an 86% peer rate. Display is the default because it is
# the book: ~99% of Prebid impressions here.
SCOPE = (os.environ.get("SCOPE") or "display").strip().lower()
# A unit with almost no volume produces a peer rate nobody should act on.
MIN_UNIT_IMPS = int(os.environ.get("MIN_UNIT_IMPS") or "5000")
MIN_BIDDER_IMPS = int(os.environ.get("MIN_BIDDER_IMPS") or "1000")

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


# GAM rejects some dimension combinations outright with
# REPORT_ERROR_CONSTRAINTS_INCOMPATIBILITY, and which ones is not documented
# — gam_client already carries three separate notes about it (DEAL_ID,
# INVENTORY_FORMAT_NAME, VIDEO_AD_DURATION each have to be pulled in their
# own report). Rather than burn a workflow round-trip per guess, try the
# richest grain first and fall back a dimension at a time, reporting which
# set the API actually accepted.
_CELL_DIMS_LADDER = [
    ["KEY_VALUES_NAME", "AD_UNIT_NAME", "DEVICE_CATEGORY_NAME",
     "RENDERED_CREATIVE_SIZE", "INVENTORY_FORMAT_NAME"],
    ["KEY_VALUES_NAME", "AD_UNIT_NAME", "DEVICE_CATEGORY_NAME",
     "RENDERED_CREATIVE_SIZE"],
    ["KEY_VALUES_NAME", "AD_UNIT_NAME", "DEVICE_CATEGORY_NAME"],
    ["KEY_VALUES_NAME", "AD_UNIT_NAME"],
    ["KEY_VALUES_NAME"],
]
# Rendered size is the decisive cut for smilewanted (is an outstream-shaped
# unit carrying the deficit?), so if it falls out of the cell grain above,
# pull it on its own — the same "incompatible dimensions go in their own
# report" pattern the delivery reports already use.
_SIZE_DIMS_LADDER = [
    ["KEY_VALUES_NAME", "RENDERED_CREATIVE_SIZE", "INVENTORY_FORMAT_NAME"],
    ["KEY_VALUES_NAME", "RENDERED_CREATIVE_SIZE"],
]


def _is_incompatible(exc: Exception) -> bool:
    return "CONSTRAINTS_INCOMPATIBILITY" in str(exc)


def _pull_first_workable(gam: GAMClient, ladder: list[list[str]],
                         start: date, end: date, label: str):
    """Return (df, dims) for the first dimension set GAM accepts."""
    last: Exception | None = None
    for dims in ladder:
        try:
            df = _pull(gam, dims, start, end)
            print(f"[{label}] grain accepted: {', '.join(dims)}")
            return df, dims
        except Exception as exc:  # noqa: BLE001 — only retry the known refusal
            if not _is_incompatible(exc):
                raise
            print(f"[{label}] rejected ({len(dims)} dims): {', '.join(dims)}")
            last = exc
    raise SystemExit(f"[{label}] every dimension set was rejected: {last}")


def _pull_raw(gam: GAMClient, dims: list[str], start: date, end: date) -> pd.DataFrame:
    """Same filters and metrics, no bidder column — for reports without the KV
    dimension (the unit→format map below)."""
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
    return df.rename(columns={
        "ad_server_impressions": "impressions",
        "active_view_eligible_impressions": "eligible",
        "active_view_measurable_impressions": "measurable",
        "active_view_viewable_impressions": "viewable_impressions",
    })


def _pull(gam: GAMClient, dims: list[str], start: date, end: date) -> pd.DataFrame:
    df = _pull_raw(gam, dims, start, end)
    df["bidder"] = df["key_values_name"].map(_bidder)
    return df[df["bidder"].notna()]


def _is_video_format(name: object) -> bool:
    s = str(name or "").lower()
    return "video" in s or "audio" in s


def _unit_format_map(gam: GAMClient, start: date,
                     end: date) -> tuple[dict[str, str], str]:
    """Classify each ad unit as 'display' or 'video', by its own impressions.

    INVENTORY_FORMAT_NAME cannot ride along with KEY_VALUES_NAME (GAM answers
    CONSTRAINTS_INCOMPATIBILITY), so the format split cannot be a column on the
    bidder report. It does not need to be: on this network the format is a
    property of the ad unit — `vid.newsweek` is 100% of video and every other
    unit is 100% Banner (see the GAM facts in CLAUDE.md). So pull the split
    once WITHOUT the KV dimension and use it to scope the bidder report.

    Measuring it beats hardcoding `vid.newsweek`: if a second video unit ever
    appears, or an existing unit starts carrying outstream, this notices.
    Falls back to the name rule only if GAM refuses the report.
    """
    try:
        df = _pull_raw(gam, ["AD_UNIT_NAME", "INVENTORY_FORMAT_NAME"], start, end)
    except Exception as exc:  # noqa: BLE001
        if not _is_incompatible(exc):
            raise
        print(f"[formats] rejected, falling back to the name rule: {exc}")
        return {}, "name-rule fallback (GAM refused AD_UNIT_NAME × format)"
    df["_video"] = df["inventory_format_name"].map(_is_video_format)
    out: dict[str, str] = {}
    for unit, sub in df.groupby("ad_unit_name"):
        vid = sub.loc[sub["_video"], "impressions"].sum()
        tot = sub["impressions"].sum()
        # Majority rules, so a unit that is mostly banner with a trickle of
        # outstream still scopes as display rather than vanishing from it.
        out[str(unit)] = "video" if tot and vid / tot > 0.5 else "display"
        if tot and 0.02 < vid / tot <= 0.5:
            print(f"[formats] note: {unit} is {vid / tot:.0%} video but scoped "
                  f"display (majority rule)")
    return out, "measured from AD_UNIT_NAME × INVENTORY_FORMAT_NAME"


def _scope_of(unit: object, fmt_map: dict[str, str]) -> str:
    u = str(unit)
    if u in fmt_map:
        return fmt_map[u]
    # Fallback when the format report was refused: this network names its
    # video inventory `vid.*`.
    return "video" if u.lower().startswith("vid.") or u.lower() == "vid" else "display"


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
    cell, cell_dims = _pull_first_workable(gam, _CELL_DIMS_LADDER, start, end, "cells")
    # Everything downstream keys off whatever grain survived.
    cell_cols = [d.lower() for d in cell_dims if d != "KEY_VALUES_NAME"
                 and d != "INVENTORY_FORMAT_NAME"]
    if "inventory_format_name" not in cell.columns:
        cell["inventory_format_name"] = "(all formats)"

    # ── scope to one format family ───────────────────────────────────────
    # Done HERE, before any baseline is computed, so every number downstream
    # — peer rates, mix/render, the per-unit tables — is computed within the
    # scope. Filtering the output instead would grade display bidders against
    # a baseline that still contained video.
    if "ad_unit_name" not in cell.columns:
        print(f"\n[scope] AD_UNIT_NAME is not in the accepted grain, so SCOPE="
              f"{SCOPE} cannot be applied — reporting all formats.")
    elif SCOPE in ("display", "video"):
        fmt_map, how = _unit_format_map(gam, start, end)
        print(f"\n[scope] unit formats {how}")
        keep = cell["ad_unit_name"].map(lambda u: _scope_of(u, fmt_map)) == SCOPE
        dropped = cell.loc[~keep].groupby("ad_unit_name")["impressions"].sum()
        cell = cell[keep]
        print(f"[scope] SCOPE={SCOPE} — keeping "
              f"{cell['ad_unit_name'].nunique()} units, "
              f"{int(cell['impressions'].sum()):,} impressions")
        for unit, imps in dropped.sort_values(ascending=False).items():
            print(f"[scope]   excluded {unit}: {int(imps):,} impressions")
        if cell.empty:
            raise SystemExit(f"[scope] nothing left at SCOPE={SCOPE}")
    else:
        print(f"\n[scope] SCOPE={SCOPE} — all formats pooled. Note that a "
              f"format-specific defect hides in a pooled average.")

    # Label the pooled-format placeholder with the scope it now represents,
    # so the section headers read "display" rather than "(all formats)".
    if SCOPE in ("display", "video") and \
            set(cell["inventory_format_name"].unique()) == {"(all formats)"}:
        cell["inventory_format_name"] = SCOPE

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
        adj = dl.viewability_mix_adjusted(sub, "bidder", cell_cols)
        adj = adj[adj["impressions"] >= 1000]
        print(f"\n-- {fmt} --")
        print(f"{'bidder':<18}{'imps':>12}{'actual%':>9}{'expected%':>11}"
              f"{'MIX pp':>9}{'RENDER pp':>11}{'uncovered':>11}")
        for _, r in adj.iterrows():
            print(f"{r.bidder:<18}{int(r.impressions):>12,}{r.actual_pct:>8.1f}%"
                  f"{r.expected_pct:>10.1f}%{r.mix_gap_pp:>+9.1f}{r.render_gap_pp:>+11.1f}"
                  f"{int(r.uncovered_impressions):>11,}")
        adj.to_csv(OUT_DIR / f"mix_vs_render_{str(fmt).replace(' ', '_')}.csv", index=False)

    # ── the breakdown by ad unit ─────────────────────────────────────────
    # The per-unit view is what an SSP conversation needs: "you are 37pp below
    # everyone else ON THIS UNIT" is unanswerable, where a site-wide average
    # invites "your inventory is hard to view". Peer rates are leave-one-out
    # WITHIN the unit, so no bidder is graded against its own impressions.
    #
    # `lost` = (peer_rate − this_rate) × imps: viewable impressions given up
    # against the rate this bidder's own peers achieve on the same unit. It is
    # the column to sort by — a 40pp gap on 3k impressions is noise, the same
    # gap on 2.8M is the whole problem.
    if "ad_unit_name" in cell.columns:
        unit_tot = cell.groupby("ad_unit_name")[
            ["impressions", "viewable_impressions"]].sum()
        unit_tot = unit_tot.sort_values("impressions", ascending=False)

        print("\n" + "=" * 78)
        print(f"AD UNITS ({SCOPE}) — site rate per unit")
        print("=" * 78)
        print(f"{'ad unit':<24}{'imps':>14}{'viewable%':>12}{'bidders':>10}")
        for unit, r in unit_tot.iterrows():
            n = cell.loc[cell["ad_unit_name"] == unit, "bidder"].nunique()
            print(f"{str(unit):<24}{int(r.impressions):>14,}"
                  f"{_rate(r.viewable_impressions, r.impressions):>11.1f}%{n:>10,}")

        rows = []
        for unit, r in unit_tot.iterrows():
            sub = cell[cell["ad_unit_name"] == unit]
            g = sub.groupby("bidder")[["impressions", "viewable_impressions"]].sum()
            for b, br in g.iterrows():
                peer_i = r.impressions - br.impressions
                peer_v = r.viewable_impressions - br.viewable_impressions
                this_pct = _rate(br.viewable_impressions, br.impressions)
                peer_pct = _rate(peer_v, peer_i)
                gap = this_pct - peer_pct
                rows.append({
                    "ad_unit_name": unit, "bidder": b,
                    "impressions": int(br.impressions),
                    "unit_impressions": int(r.impressions),
                    "viewable_pct": round(this_pct, 1),
                    "peer_pct": round(peer_pct, 1),
                    "gap_pp": round(gap, 1),
                    "lost_viewable": int(max(0.0, -gap) / 100.0 * br.impressions),
                })
        by_unit = pd.DataFrame(rows)
        by_unit.to_csv(OUT_DIR / "by_ad_unit.csv", index=False)
        by_unit.pivot_table(index="bidder", columns="ad_unit_name",
                            values="viewable_pct").to_csv(
            OUT_DIR / "unit_bidder_matrix.csv")

        print("\n" + "=" * 78)
        print(f"BY AD UNIT ({SCOPE}) — bidders sorted by viewable impressions lost")
        print("vs their own peers ON THAT UNIT (leave-one-out)")
        print("=" * 78)
        for unit, r in unit_tot.iterrows():
            if r.impressions < MIN_UNIT_IMPS:
                continue
            sub = by_unit[(by_unit["ad_unit_name"] == unit)
                          & (by_unit["impressions"] >= MIN_BIDDER_IMPS)]
            if sub.empty:
                continue
            sub = sub.sort_values(["lost_viewable", "impressions"],
                                  ascending=[False, False])
            print(f"\n-- {unit}  ({int(r.impressions):,} imps, unit rate "
                  f"{_rate(r.viewable_impressions, r.impressions):.1f}%) --")
            print(f"{'bidder':<18}{'imps':>12}{'this%':>8}{'peers%':>8}"
                  f"{'gap pp':>9}{'lost vw':>12}")
            for _, br in sub.iterrows():
                print(f"{br.bidder:<18}{int(br.impressions):>12,}"
                      f"{br.viewable_pct:>7.1f}%{br.peer_pct:>7.1f}%"
                      f"{br.gap_pp:>+9.1f}{int(br.lost_viewable):>12,}")

        print("\n" + "=" * 78)
        print(f"WORST OFFENDERS ({SCOPE}) — biggest single bidder×unit losses")
        print("=" * 78)
        top = by_unit[by_unit["impressions"] >= MIN_BIDDER_IMPS].sort_values(
            "lost_viewable", ascending=False).head(20)
        print(f"{'bidder':<18}{'ad unit':<16}{'imps':>12}{'this%':>8}"
              f"{'peers%':>8}{'gap pp':>9}{'lost vw':>12}")
        for _, br in top.iterrows():
            print(f"{br.bidder:<18}{str(br.ad_unit_name):<16}"
                  f"{int(br.impressions):>12,}{br.viewable_pct:>7.1f}%"
                  f"{br.peer_pct:>7.1f}%{br.gap_pp:>+9.1f}"
                  f"{int(br.lost_viewable):>12,}")
        print(f"\ntotal lost across all bidder×unit cells: "
              f"{int(by_unit['lost_viewable'].sum()):,} viewable impressions "
              f"({_rate(by_unit['lost_viewable'].sum(), cell['impressions'].sum()):.1f}"
              f"pp of the {SCOPE} book)")

    # ── where the focus bidders actually buy ─────────────────────────────
    print("\n" + "=" * 78)
    print("FOCUS BIDDERS — top cells, with the peer rate in the same cell")
    print("=" * 78)
    for b in FOCUS:
        sub = cell[cell["bidder"] == b]
        if sub.empty:
            print(f"\n-- {b}: no impressions in the window --")
            continue
        keys = cell_cols
        mine = sub.groupby(keys)[["impressions", "viewable_impressions"]].sum()
        allc = cell.groupby(keys)[["impressions", "viewable_impressions"]].sum()
        peer_i = allc["impressions"].reindex(mine.index).fillna(0) - mine["impressions"]
        peer_v = allc["viewable_impressions"].reindex(mine.index).fillna(0) - mine["viewable_impressions"]
        print(f"\n-- {b} ({int(sub['impressions'].sum()):,} imps) --")
        print(f"{' / '.join(cell_cols):<52}{'imps':>10}{'this%':>8}{'peers%':>8}")
        for k, r in mine.sort_values("impressions", ascending=False).head(12).iterrows():
            # A one-column groupby gives scalar keys; only tuples get joined.
            label = (" / ".join(str(x) for x in k) if isinstance(k, tuple)
                     else str(k))[:50]
            print(f"{label:<52}{int(r.impressions):>10,}"
                  f"{_rate(r.viewable_impressions, r.impressions):>7.1f}%"
                  f"{_rate(peer_v.loc[k], peer_i.loc[k]):>7.1f}%")

    # ── viewability by rendered creative size ────────────────────────────
    # The smilewanted question in one table: does an outstream-shaped unit
    # carry the deficit while its standard display measures like peers?
    if "rendered_creative_size" not in cell.columns:
        try:
            size_df, _ = _pull_first_workable(gam, _SIZE_DIMS_LADDER, start, end, "sizes")
        except SystemExit as exc:
            print(exc)
            size_df = None
    else:
        size_df = cell
    if size_df is not None and not size_df.empty:
        print("\n" + "=" * 78)
        print("VIEWABILITY BY RENDERED CREATIVE SIZE (focus bidders vs peers)")
        print("=" * 78)
        for b in FOCUS:
            sub = size_df[size_df["bidder"] == b]
            if sub.empty:
                continue
            g = sub.groupby("rendered_creative_size")[
                ["impressions", "viewable_impressions"]].sum()
            allg = size_df.groupby("rendered_creative_size")[
                ["impressions", "viewable_impressions"]].sum()
            print(f"\n-- {b} --")
            print(f"{'size':<20}{'imps':>12}{'this%':>9}{'peers%':>9}")
            for k, r in g.sort_values("impressions", ascending=False).head(10).iterrows():
                pi = allg["impressions"].get(k, 0) - r.impressions
                pv = allg["viewable_impressions"].get(k, 0) - r.viewable_impressions
                print(f"{str(k):<20}{int(r.impressions):>12,}"
                      f"{_rate(r.viewable_impressions, r.impressions):>8.1f}%"
                      f"{_rate(pv, pi):>8.1f}%")

    # ── regression check: structural, or did it start on a date? ─────────
    daily, _ = _pull_first_workable(
        gam, [["DATE", "KEY_VALUES_NAME", "INVENTORY_FORMAT_NAME"],
              ["DATE", "KEY_VALUES_NAME"]], start, end, "daily")
    daily.to_csv(OUT_DIR / "by_day.csv", index=False)
    print("\n" + "=" * 78)
    print("DAILY viewable% — a step change dates a regression; a flat line is structural")
    print("=" * 78)
    # DATE + KEY_VALUES_NAME is the richest grain GAM accepts here, so this
    # series carries no ad unit and CANNOT be scoped: for a display-only
    # bidder it is the display series, but for one selling both (onetag) it
    # pools formats and will look healthier than its display or video alone.
    print(f"(pooled across formats — the daily grain has no ad unit, so SCOPE="
          f"{SCOPE} does not apply here)")
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
