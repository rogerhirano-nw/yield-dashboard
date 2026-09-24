"""SmileWanted (or any bidder) viewability by ad unit, Prebid Server vs Prebid.js.

Follow-up to docs/prebid_viewability.md: the audit there graded smilewanted
against its peers per ad unit but never split it by *where the bid came from*
— `hb_source` = `s2s` (Prebid Server) vs `client` (Prebid.js in the page).
That split matters for the SSP conversation: a render defect in their
client-side adapter/creative would show on PBJS only, while a problem shared
by both paths points at the creative itself.

Why not the existing audit's grain: KEY_VALUES_NAME splits every impression
into one row per key, so it can't cross hb_bidder with hb_source. Custom
dimensions can — each key reportable as a custom dimension becomes its own
CUSTOM_DIMENSION_<n>_VALUE column. The script looks both keys up, and:

  A. both keys are custom dimensions → hb_bidder × hb_source × ad unit;
  B. only hb_bidder is → hb_bidder (custom dim) × KEY_VALUES_NAME filtered
     to `hb_source=` × ad unit (each impression carries exactly one
     hb_source, so that KV row split is safe).

Peers are every other bidder on the same ad unit *and the same source*, so
each path is graded against like-for-like demand.

Env: DAYS (default 30, ending yesterday), PREBID_ADVERTISER_IDS (default
5724335726,5713671547 — the filter on the GAM report RevOps pulled),
BIDDER (default smilewanted), OUT_DIR. Needs GAM_SERVICE_ACCOUNT_JSON +
GAM_NETWORK_ID, so it runs in Actions (.github/workflows/
prebid_source_viewability.yml).
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
from google.ads import admanager_v1  # noqa: E402
from google.oauth2 import service_account  # noqa: E402

from gam_client import GAMClient, _SCOPES  # noqa: E402

DAYS = int(os.environ.get("DAYS") or "30")
ADVERTISER_IDS = [int(x) for x in (
    os.environ.get("PREBID_ADVERTISER_IDS") or "5724335726,5713671547"
).split(",") if x.strip()]
BIDDER = (os.environ.get("BIDDER") or "smilewanted").strip().lower()
OUT_DIR = Path(os.environ.get("OUT_DIR") or "/tmp/prebid-source-viewability")
# Extra one-at-a-time cuts of the bidder vs its peers. Lower-case names are
# custom-targeting keys (hb_size / hb_format carry the winning bid's real
# size and media type — GAM's own creative size is the universal creative's
# 1x1); upper-case names are standard report dimensions.
BREAKDOWNS = [b.strip() for b in (
    os.environ.get("BREAKDOWNS")
    or "hb_size,hb_format,DEVICE_CATEGORY_NAME"
).split(",") if b.strip()]

METRICS = [
    "AD_SERVER_IMPRESSIONS",
    "ACTIVE_VIEW_ELIGIBLE_IMPRESSIONS",
    "ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
    "ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
    "AD_SERVER_REVENUE",
]
_RENAME = {
    "ad_server_impressions": "impressions",
    "active_view_eligible_impressions": "eligible",
    "active_view_measurable_impressions": "measurable",
    "active_view_viewable_impressions": "viewable",
    "ad_server_revenue": "revenue",
}
_SOURCE_LABEL = {"s2s": "PBS", "client": "PBJS"}
_CUSTOM_DIM = (admanager_v1.CustomTargetingKeyReportableTypeEnum
               .CustomTargetingKeyReportableType.CUSTOM_DIMENSION)


def _find_keys(names: set[str]) -> dict[str, object]:
    creds = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"]), scopes=_SCOPES)
    client = admanager_v1.CustomTargetingKeyServiceClient(credentials=creds)
    found: dict[str, object] = {}
    for k in client.list_custom_targeting_keys(
            parent=f"networks/{os.environ['GAM_NETWORK_ID']}"):
        tag = (k.ad_tag_name or "").strip().lower()
        if tag.startswith("hb_"):
            print(f"  seen key {tag}: {k.reportable_type.name}")
        if tag in names:
            found[tag] = k
    return found


def _key_id(k) -> int:
    """The REST list leaves custom_targeting_key_id at 0; the id lives in the
    resource name (networks/<n>/customTargetingKeys/<id>)."""
    kid = int(k.custom_targeting_key_id or 0) or int(str(k.name).rsplit("/", 1)[-1])
    if not kid:
        raise SystemExit(f"no id for custom targeting key {k.name!r}")
    return kid


def _rate(num, den) -> float:
    den = float(den or 0)
    return float(num or 0) / den * 100.0 if den else float("nan")


def _pct(x: float) -> str:
    return "   —  " if pd.isna(x) else f"{x:5.1f}%"


def _breakdown(gam, name, keys, bidder_key_id, adv, start, end) -> None:
    """The bidder vs every other bidder, cut by one dimension or key."""
    if name.islower():
        k = keys.get(name)
        if k is None:
            print(f"\n-- by {name}: no such key, skipped --")
            return
        if k.reportable_type == _CUSTOM_DIM:
            dims = ["CUSTOM_DIMENSION_0_VALUE", "CUSTOM_DIMENSION_1_VALUE"]
            ids = [bidder_key_id, _key_id(k)]
        else:
            # Not a custom dimension: read it as a key-value row instead —
            # safe because each impression carries one value of this key.
            dims = ["CUSTOM_DIMENSION_0_VALUE", "KEY_VALUES_NAME"]
            ids = [bidder_key_id]
            adv = [adv, ("KEY_VALUES_NAME", "CONTAINS", [f"{name}="])]
    else:
        dims = ["CUSTOM_DIMENSION_0_VALUE", name]
        ids = [bidder_key_id]
    try:
        df = gam._run_report(dimensions=dims, metrics=METRICS, start_date=start,
                             end_date=end,
                             filters=adv if isinstance(adv, list) else [adv],
                             custom_dimension_key_ids=ids)
    except Exception as exc:  # noqa: BLE001 — one cut failing shouldn't sink the rest
        if not (name.islower() and dims[1] == "KEY_VALUES_NAME"):
            print(f"\n-- by {name}: report failed: {str(exc)[:600]} --")
            return
        # GAM won't put a custom dimension and KEY_VALUES_NAME in one report,
        # so filter on the bidder instead and pull the key-value twice: this
        # bidder, then everyone else.
        print(f"(by {name}: cross rejected — {str(exc)[-300:]} — retrying as bidder-filtered pulls)")
        parts = []
        for op, label in (("IN", BIDDER), ("NOT_IN", "(peers)")):
            try:
                part = gam._run_report(
                    dimensions=["KEY_VALUES_NAME"], metrics=METRICS,
                    start_date=start, end_date=end,
                    filters=adv + [("CUSTOM_DIMENSION_0_VALUE", op, [BIDDER])],
                    custom_dimension_key_ids=[bidder_key_id])
            except Exception as exc2:  # noqa: BLE001
                print(f"\n-- by {name}: report failed: {str(exc2)[:600]} --")
                return
            part.insert(0, "bidder", label)
            parts.append(part)
        df = pd.concat(parts, ignore_index=True)
    df = df.rename(columns=_RENAME)
    df.columns = ["bidder", "value"] + list(df.columns[2:])
    df["bidder"] = df["bidder"].astype(str).str.strip().str.lower()
    df["value"] = df["value"].astype(str)
    if name.islower() and df["value"].str.startswith(f"{name}=").any():
        df = df[df["value"].str.startswith(f"{name}=")]
        df["value"] = df["value"].str.split("=", n=1).str[1]
    df.to_csv(OUT_DIR / f"by_{name.lower()}.csv", index=False)
    cols = ["impressions", "eligible", "measurable", "viewable", "revenue"]
    mine = df[df["bidder"] == BIDDER].groupby("value")[cols].sum()
    peers = df[df["bidder"] != BIDDER].groupby("value")[cols].sum()
    tot = mine["impressions"].sum()
    print(f"\n-- {BIDDER} by {name} (peers = all other bidders, same value) --")
    print(f"{'value':<28}{'imps':>12}{'share':>7}{'meas%':>8}{'vw%':>8}"
          f"{'peers%':>8}{'peer imps':>12}")
    for v, r in mine.sort_values("impressions", ascending=False).head(15).iterrows():
        p = peers.loc[v] if v in peers.index else None
        print(f"{v[:27]:<28}{int(r.impressions):>12,}"
              f"{(r.impressions / tot * 100 if tot else 0):>6.1f}%"
              f"{_pct(_rate(r.measurable, r.eligible)):>8}"
              f"{_pct(_rate(r.viewable, r.impressions)):>8}"
              f"{_pct(_rate(p.viewable, p.impressions) if p is not None else float('nan')):>8}"
              f"{(int(p.impressions) if p is not None else 0):>12,}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=DAYS - 1)
    gam = GAMClient()

    print("=" * 78)
    print(f"{BIDDER} VIEWABILITY BY AD UNIT — PBS (s2s) vs PBJS (client)")
    print(f"{start} → {end}   advertisers {ADVERTISER_IDS}")
    print("=" * 78)

    keys = _find_keys({"hb_bidder", "hb_source"} | {b for b in BREAKDOWNS if b.islower()})
    for name in ("hb_bidder", "hb_source"):
        k = keys.get(name)
        print(f"key {name}: " + ("NOT FOUND" if k is None else
              f"id {_key_id(k)}, reportable_type "
              f"{k.reportable_type.name}"))
    bidder_key = keys.get("hb_bidder")
    source_key = keys.get("hb_source")
    if bidder_key is None or bidder_key.reportable_type != _CUSTOM_DIM:
        raise SystemExit("hb_bidder is not a reportable custom dimension — "
                         "can't cross bidder with source.")

    adv = ("ADVERTISER_ID", "IN", ADVERTISER_IDS)
    if source_key is not None and source_key.reportable_type == _CUSTOM_DIM:
        print("\nstrategy A: hb_bidder × hb_source as custom dimensions")
        df = gam._run_report(
            dimensions=["CUSTOM_DIMENSION_0_VALUE", "CUSTOM_DIMENSION_1_VALUE",
                        "AD_UNIT_NAME"],
            metrics=METRICS, start_date=start, end_date=end, filters=[adv],
            custom_dimension_key_ids=[_key_id(bidder_key), _key_id(source_key)],
        ).rename(columns={"custom_dimension_0_value": "bidder",
                          "custom_dimension_1_value": "source"})
    else:
        print("\nstrategy B: hb_bidder custom dimension × hb_source key-value")
        df = gam._run_report(
            dimensions=["CUSTOM_DIMENSION_0_VALUE", "KEY_VALUES_NAME",
                        "AD_UNIT_NAME"],
            metrics=METRICS, start_date=start, end_date=end,
            filters=[adv, ("KEY_VALUES_NAME", "CONTAINS", ["hb_source="])],
            custom_dimension_key_ids=[_key_id(bidder_key)],
        ).rename(columns={"custom_dimension_0_value": "bidder"})
        df = df[df["key_values_name"].astype(str).str.startswith("hb_source=")]
        df["source"] = df["key_values_name"].str.split("=", n=1).str[1]

    df = df.rename(columns=_RENAME)
    df["bidder"] = df["bidder"].astype(str).str.strip().str.lower()
    df["source"] = (df["source"].astype(str).str.strip().str.lower()
                    .map(lambda s: _SOURCE_LABEL.get(s, s or "(none)")))
    df["ad_unit"] = df["ad_unit_name"].astype(str)
    df.to_csv(OUT_DIR / "bidder_source_adunit.csv", index=False)
    cols = ["impressions", "eligible", "measurable", "viewable", "revenue"]
    print(f"{len(df):,} rows, {int(df['impressions'].sum()):,} impressions, "
          f"sources seen: {sorted(df['source'].unique())}")

    # ── all bidders by source: is one path worse across the board? ──────
    print("\n-- All bidders, by source --")
    s = df.groupby("source")[cols].sum()
    print(f"{'source':<8}{'imps':>13}{'measurable%':>13}{'viewable%':>11}")
    for src, r in s.iterrows():
        print(f"{src:<8}{int(r.impressions):>13,}{_pct(_rate(r.measurable, r.eligible)):>13}"
              f"{_pct(_rate(r.viewable, r.impressions)):>11}")

    mine = df[df["bidder"] == BIDDER]
    if mine.empty:
        print(f"\n{BIDDER}: no impressions in the window")
        return 0

    # ── bidder totals by source vs peers on the same source ─────────────
    print(f"\n-- {BIDDER} totals by source (peers = other bidders, same source) --")
    print(f"{'source':<8}{'imps':>12}{'revenue':>11}{'eCPM':>7}"
          f"{'meas%':>8}{'viewable%':>11}{'peers%':>9}")
    peers = df[df["bidder"] != BIDDER]
    for src, r in mine.groupby("source")[cols].sum().iterrows():
        # Peer rate re-weighted to this bidder's ad-unit mix on this source,
        # so the comparison isn't flattered or hurt by placement.
        m_u = mine[mine["source"] == src].groupby("ad_unit")["impressions"].sum()
        p_u = peers[peers["source"] == src].groupby("ad_unit")[cols].sum()
        p_rate = (p_u["viewable"] / p_u["impressions"]).reindex(m_u.index)
        w = m_u[p_rate.notna()]
        peer_mix = (p_rate.dropna() * w).sum() / w.sum() * 100 if w.sum() else float("nan")
        print(f"{src:<8}{int(r.impressions):>12,}{r.revenue:>11,.0f}"
              f"{(r.revenue / r.impressions * 1000 if r.impressions else 0):>7.2f}"
              f"{_pct(_rate(r.measurable, r.eligible)):>8}"
              f"{_pct(_rate(r.viewable, r.impressions)):>11}{_pct(peer_mix):>9}")

    # ── the ask: per ad unit, PBS vs PBJS side by side ──────────────────
    print(f"\n-- {BIDDER} by ad unit: PBS vs PBJS (peers = same unit, same source) --")
    print(f"{'ad unit':<22}{'PBS imps':>11}{'PBS vw%':>9}{'peers':>8}"
          f"{'PBJS imps':>12}{'PBJS vw%':>10}{'peers':>8}")
    out = []
    units = mine.groupby("ad_unit")["impressions"].sum().sort_values(ascending=False)
    for unit in units.index:
        row = {"ad_unit": unit}
        for src in ("PBS", "PBJS"):
            m = mine[(mine["ad_unit"] == unit) & (mine["source"] == src)][cols].sum()
            p = peers[(peers["ad_unit"] == unit) & (peers["source"] == src)][cols].sum()
            row[f"{src}_imps"] = int(m.impressions)
            row[f"{src}_viewable_pct"] = _rate(m.viewable, m.impressions)
            row[f"{src}_measurable_pct"] = _rate(m.measurable, m.eligible)
            row[f"{src}_peer_viewable_pct"] = _rate(p.viewable, p.impressions)
        out.append(row)
        print(f"{unit[:21]:<22}{row['PBS_imps']:>11,}{_pct(row['PBS_viewable_pct']):>9}"
              f"{_pct(row['PBS_peer_viewable_pct']):>8}{row['PBJS_imps']:>12,}"
              f"{_pct(row['PBJS_viewable_pct']):>10}{_pct(row['PBJS_peer_viewable_pct']):>8}")
    pd.DataFrame(out).to_csv(OUT_DIR / f"{BIDDER}_pbs_vs_pbjs_by_adunit.csv", index=False)

    other = mine[~mine["source"].isin(["PBS", "PBJS"])]
    if not other.empty:
        print(f"\nnote: {int(other['impressions'].sum()):,} {BIDDER} impressions carried "
              f"another hb_source value: {sorted(other['source'].unique())}")
    for b in BREAKDOWNS:
        _breakdown(gam, b, keys, _key_id(bidder_key), adv, start, end)

    print(f"\nCSVs: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
