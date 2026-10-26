"""Is one Prebid bidder transacting on video in GAM, and if not, where does it stop?

Prompted 2026-09-29 by "SmileWanted video is not transacting in Prebid
client side". AssertiveYield's Prebid Analytics answered the wrapper half:
smilewanted only started returning video bids on 2026-09-26, bids ~$0.86 CPM
against a ~$3.95 floor (peers bid $6-7), and answers every video request at
size 300x250 while every other bidder answers 640x360 / 640x480. AY can't see
the ad-server half — it logs 0 video wins for *every* bidder, because the IMA
player, not GPT, spends the win — so this pulls that half from GAM. Read-only.

  1. Keys — does GAM know the bidder's keys (hb_bidder value, any
     hb_*_<bidder> send-all-bids keys), and are they reportable?
  2. Delivery — hb_bidder × line-item environment × top-level ad unit × day:
     does the bidder have ANY video-player impressions, and since when? Plus
     the video-unit bidder league table for context.
  3. Video errors — per bidder on the video unit: starts and VAST error codes
     (a bid that wins the line item but whose VAST fails shows here as
     impressions/errors without starts).
  4. Line-item setup — the orders that serve Prebid video, every non-archived
     line item's custom targeting resolved to key names, and any hb_bidder
     values they restrict to. A video line set targeting hb_bidder IN (...)
     without the bidder, or keyed on hb_pb_<bidder> keys that don't exist for
     it, means its win can never serve. Also samples the VAST creative URLs.

Each section runs independently and prints its own error, so one
incompatible report doesn't cost the rest.

Env: DAYS (default 14, ending yesterday), BIDDER (default smilewanted),
VIDEO_UNITS (top-level ad units that are video; default vid.newsweek),
OUT_DIR. Needs GAM_SERVICE_ACCOUNT_JSON + GAM_NETWORK_ID, so it runs in
Actions (.github/workflows/prebid_bidder_video.yml).
"""

from __future__ import annotations

import collections
import json
import os
import sys
import traceback
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

DAYS = int(os.environ.get("DAYS") or "14")
BIDDER = (os.environ.get("BIDDER") or "smilewanted").strip().lower()
VIDEO_UNITS = {u.strip().lower() for u in (
    os.environ.get("VIDEO_UNITS") or "vid.newsweek").split(",") if u.strip()}
OUT_DIR = Path(os.environ.get("OUT_DIR") or "/tmp/prebid-bidder-video")
V = "v202605"

_CUSTOM_DIM = (admanager_v1.CustomTargetingKeyReportableTypeEnum
               .CustomTargetingKeyReportableType.CUSTOM_DIMENSION)
# The VAST error codes that separate "the bid never produced a playable ad"
# from "the player gave up": 100/101/102 XML, 200s linearity/duration/size,
# 300s wrapper/timeout/empty, 400s media file, 900s undefined/VPAID.
ERROR_CODES = [100, 101, 102, 200, 201, 202, 203, 300, 301, 302, 303,
               400, 401, 402, 403, 405, 900, 901]


def _section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _run(title: str, fn) -> None:
    _section(title)
    try:
        fn()
    except Exception as exc:  # keep going: each section is independent
        print(f"!! section failed: {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=2)


def _keys() -> dict[str, object]:
    creds = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"]), scopes=_SCOPES)
    client = admanager_v1.CustomTargetingKeyServiceClient(credentials=creds)
    return {(k.ad_tag_name or "").strip().lower(): k
            for k in client.list_custom_targeting_keys(
                parent=f"networks/{os.environ['GAM_NETWORK_ID']}")}


def _key_id(k) -> int:
    return int(k.custom_targeting_key_id or 0) or int(str(k.name).rsplit("/", 1)[-1])


def _page(svc, method, where, limit=500, **binds):
    from googleads import ad_manager
    sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(limit)
    for k, v in binds.items():
        sb = sb.WithBindVariable(k, v)
    out = []
    while True:
        resp = getattr(svc, method)(sb.ToStatement())
        results = list(getattr(resp, "results", []) or [])
        out.extend(results)
        if not results:
            break
        sb.offset += sb.limit
        if sb.offset >= getattr(resp, "totalResultSetSize", 0):
            break
    return out


def _chunks(xs, n):
    xs = list(xs)
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def _walk_criteria(node, out: list) -> None:
    """Flatten a CustomCriteriaSet tree into (keyId, operator, valueIds)."""
    if node is None:
        return
    key_id = getattr(node, "keyId", None)
    if key_id is not None:
        out.append((int(key_id), str(getattr(node, "operator", "")),
                    [int(v) for v in (getattr(node, "valueIds", None) or [])]))
    for child in (getattr(node, "children", None) or []):
        _walk_criteria(child, out)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=DAYS - 1)
    gam = GAMClient()
    state: dict[str, object] = {}

    print(f"{BIDDER} ON VIDEO — GAM side   {start} → {end}   "
          f"video units {sorted(VIDEO_UNITS)}")

    # ── 1. keys ─────────────────────────────────────────────────────────
    def keys():
        allk = _keys()
        hb = {n: k for n, k in allk.items() if n.startswith("hb_")}
        state["keys"] = allk
        print(f"{len(hb)} hb_* keys in the network")
        generic = ["hb_bidder", "hb_pb", "hb_adid", "hb_size", "hb_format",
                   "hb_source", "hb_uuid", "hb_cache_id", "hb_cache_host",
                   "hb_cache_path", "hb_deal"]
        for n in generic:
            k = allk.get(n)
            print(f"  {n:<16}" + ("NOT FOUND" if k is None else
                  f"id {_key_id(k):<10} reportable {k.reportable_type.name}"))
        mine = sorted(n for n in hb if n.endswith("_" + BIDDER)
                      or n.endswith("_" + BIDDER[:10]))
        print(f"\n  bidder-specific keys for {BIDDER}: {mine or 'NONE'}")
        # Which bidders DO have send-all-bids keys, for comparison.
        sab = collections.Counter(n.split("_", 2)[-1] for n in hb
                                  if n.startswith("hb_pb_"))
        print(f"  bidders with an hb_pb_<bidder> key: {sorted(sab)}")

        hb_bidder = allk.get("hb_bidder")
        if hb_bidder is not None:
            cts = gam._get_soap_client().GetService(
                "CustomTargetingService", version=V)
            vals = _page(cts, "getCustomTargetingValuesByStatement",
                         "customTargetingKeyId = :k", k=_key_id(hb_bidder))
            names = sorted(str(getattr(v, "name", "")).lower() for v in vals)
            state["hb_bidder_values"] = {int(v.id): str(v.name).lower()
                                         for v in vals}
            print(f"\n  hb_bidder values ({len(names)}): {names}")
            print(f"  → '{BIDDER}' registered as an hb_bidder value: "
                  f"{BIDDER in names}")

    _run("1. CUSTOM TARGETING KEYS", keys)

    def _bidder_key():
        k = (state.get("keys") or {}).get("hb_bidder")
        if k is None or k.reportable_type != _CUSTOM_DIM:
            raise SystemExit("hb_bidder is not a reportable custom dimension")
        return _key_id(k)

    # ── 2. delivery by environment ──────────────────────────────────────
    def delivery():
        df = gam._run_report(
            dimensions=["CUSTOM_DIMENSION_0_VALUE",
                        "LINE_ITEM_ENVIRONMENT_TYPE_NAME",
                        "AD_UNIT_NAME_TOP_LEVEL", "DATE"],
            metrics=["AD_SERVER_IMPRESSIONS", "AD_SERVER_REVENUE"],
            start_date=start, end_date=end,
            custom_dimension_key_ids=[_bidder_key()],
        ).rename(columns={"custom_dimension_0_value": "bidder",
                          "line_item_environment_type_name": "env",
                          "ad_unit_name_top_level": "unit",
                          "ad_server_impressions": "imps",
                          "ad_server_revenue": "revenue"})
        df["bidder"] = df["bidder"].astype(str).str.strip().str.lower()
        df["unit"] = df["unit"].astype(str).str.strip()
        df = df[~df["bidder"].isin(["", "none", "nan", "-"])]
        df.to_csv(OUT_DIR / "bidder_env_unit_day.csv", index=False)
        is_video = (df["env"].astype(str).str.lower().str.contains("video")
                    | df["unit"].str.lower().isin(VIDEO_UNITS))
        state["delivery"] = df

        print("-- all Prebid-keyed impressions by environment --")
        print(df.groupby("env")[["imps", "revenue"]].sum()
              .sort_values("imps", ascending=False).to_string())

        print(f"\n-- video bidder league (video env or unit), {start}→{end} --")
        v = (df[is_video].groupby("bidder")[["imps", "revenue"]].sum()
             .sort_values("imps", ascending=False))
        v["eCPM"] = (v["revenue"] / v["imps"] * 1000).round(2)
        print(v.to_string())

        mine = df[df["bidder"] == BIDDER]
        print(f"\n-- {BIDDER}: by environment × unit --")
        if mine.empty:
            print(f"   NO impressions carry hb_bidder={BIDDER} in the window")
        else:
            print(mine.groupby(["env", "unit"])[["imps", "revenue"]].sum()
                  .sort_values("imps", ascending=False).to_string())
        mv = mine[is_video.loc[mine.index]] if not mine.empty else mine
        print(f"\n-- {BIDDER}: video by day --")
        print("   none" if mv.empty else
              mv.groupby("date")[["imps", "revenue"]].sum().to_string())

    _run("2. DELIVERY — hb_bidder × environment × top-level unit × day", delivery)

    # ── 3. video errors ─────────────────────────────────────────────────
    def errors():
        metrics = (["AD_SERVER_IMPRESSIONS", "VIDEO_VIEWERSHIP_STARTS",
                    "VIDEO_VIEWERSHIP_TOTAL_ERROR_COUNT"]
                   + [f"VIDEO_ERROR_{c}_COUNT" for c in ERROR_CODES])
        df = gam._run_report(
            dimensions=["CUSTOM_DIMENSION_0_VALUE", "AD_UNIT_NAME_TOP_LEVEL"],
            metrics=metrics, start_date=start, end_date=end,
            custom_dimension_key_ids=[_bidder_key()],
        ).rename(columns={"custom_dimension_0_value": "bidder",
                          "ad_unit_name_top_level": "unit"})
        df["bidder"] = df["bidder"].astype(str).str.strip().str.lower()
        df = df[df["unit"].astype(str).str.strip().str.lower().isin(VIDEO_UNITS)]
        df = df[~df["bidder"].isin(["", "none", "nan", "-"])]
        df.to_csv(OUT_DIR / "video_errors_by_bidder.csv", index=False)
        g = df.groupby("bidder").sum(numeric_only=True)
        g = g.sort_values("ad_server_impressions", ascending=False)
        out = pd.DataFrame({
            "imps": g["ad_server_impressions"],
            "starts": g["video_viewership_starts"],
            "errors": g["video_viewership_total_error_count"],
        })
        out["start%"] = (out["starts"] / out["imps"] * 100).round(1)
        out["err/imp%"] = (out["errors"] / out["imps"] * 100).round(1)
        print(out.to_string())
        err_cols = [f"video_error_{c}_count" for c in ERROR_CODES]
        focus = [BIDDER] + [b for b in g.index[:3] if b != BIDDER]
        print(f"\n-- VAST error codes (non-zero), {BIDDER} vs top peers --")
        for b in focus:
            if b not in g.index:
                print(f"  {b}: no rows on the video unit")
                continue
            nz = {c.split("_")[2]: int(g.loc[b, c]) for c in err_cols
                  if c in g.columns and g.loc[b, c]}
            print(f"  {b:<14}{nz or '(none)'}")

    _run("3. VIDEO STARTS + VAST ERRORS BY BIDDER (video units)", errors)

    # ── 4. line-item setup ──────────────────────────────────────────────
    def setup():
        rep = gam._run_report(
            dimensions=["ORDER_ID", "ORDER_NAME",
                        "LINE_ITEM_ENVIRONMENT_TYPE_NAME",
                        "AD_UNIT_NAME_TOP_LEVEL", "CUSTOM_DIMENSION_0_VALUE"],
            metrics=["AD_SERVER_IMPRESSIONS"], start_date=start, end_date=end,
            custom_dimension_key_ids=[_bidder_key()],
        )
        rep["bidder"] = (rep["custom_dimension_0_value"].astype(str)
                         .str.strip().str.lower())
        rep = rep[~rep["bidder"].isin(["", "none", "nan", "-"])]
        vid = rep[rep["line_item_environment_type_name"].astype(str).str.lower()
                  .str.contains("video")
                  | rep["ad_unit_name_top_level"].astype(str).str.lower()
                  .isin(VIDEO_UNITS)]
        orders = (vid.groupby(["order_id", "order_name"])["ad_server_impressions"]
                  .sum().sort_values(ascending=False))
        print("-- orders serving Prebid video --")
        print(orders.to_string() if not orders.empty else "   none")
        if orders.empty:
            return

        client = gam._get_soap_client()
        li_svc = client.GetService("LineItemService", version=V)
        cts = client.GetService("CustomTargetingService", version=V)
        cr_svc = client.GetService("CreativeService", version=V)
        lica_svc = client.GetService(
            "LineItemCreativeAssociationService", version=V)
        key_names: dict[int, str] = {}
        bidder_vals = state.get("hb_bidder_values") or {}

        for (oid, oname), imps in orders.head(6).items():
            lis = _page(li_svc, "getLineItemsByStatement",
                        "orderId = :o AND isArchived = false", o=int(oid))
            print(f"\n  order {oid}  {oname}  ({int(imps):,} Prebid video imps)")
            status = collections.Counter(str(getattr(li, "status", "?")) for li in lis)
            envs = collections.Counter(str(getattr(li, "environmentType", "?"))
                                       for li in lis)
            print(f"    {len(lis)} line items   status {dict(status)}   env {dict(envs)}")
            crit = []
            for li in lis:
                tgt = getattr(li, "targeting", None)
                c: list = []
                _walk_criteria(getattr(tgt, "customTargeting", None), c)
                crit.append((li, c))
            used = {k for _, c in crit for k, _, _ in c}
            missing = sorted(used - key_names.keys())
            for ch in _chunks(missing, 400):
                for k in _page(cts, "getCustomTargetingKeysByStatement",
                               f"id IN ({', '.join(map(str, ch))})"):
                    key_names[int(k.id)] = str(k.name).lower()
            per_key = collections.Counter(key_names.get(k, str(k))
                                          for _, c in crit for k in {x[0] for x in c})
            print(f"    targeted keys (line items using each): {dict(per_key)}")
            for li, c in crit[:1]:
                print(f"    sample '{getattr(li, 'name', '')}': " + "; ".join(
                    f"{key_names.get(k, k)} {op} {len(v)} value(s)" for k, op, v in c))
            # Restrictions on hb_bidder: the bidder has to be among the values.
            restr = collections.Counter()
            for _, c in crit:
                for k, op, vids in c:
                    if key_names.get(k) == "hb_bidder":
                        restr[(op, tuple(sorted(bidder_vals.get(v, str(v))
                                                for v in vids)))] += 1
            if restr:
                for (op, vals), n in restr.items():
                    print(f"    hb_bidder {op} {list(vals)}  on {n} LIs  → "
                          f"{BIDDER} {'INCLUDED' if BIDDER in vals else 'NOT included'}")
            else:
                print("    no hb_bidder restriction (any bidder can serve)")
            if any(key_names.get(k, "").endswith("_" + BIDDER) for k in used):
                print(f"    uses {BIDDER}-specific keys")
            # Creative sample: how the VAST is fetched.
            ids = [int(li.id) for li in lis[:2]]
            if ids:
                licas = _page(lica_svc, "getLineItemCreativeAssociationsByStatement",
                              f"lineItemId IN ({', '.join(map(str, ids))})")
                cids = sorted({int(la.creativeId) for la in licas})[:4]
                if cids:
                    for cr in _page(cr_svc, "getCreativesByStatement",
                                    f"id IN ({', '.join(map(str, cids))})"):
                        url = (getattr(cr, "vastXmlUrl", None)
                               or getattr(cr, "vastRedirectUrl", None) or "")
                        print(f"    creative {cr.id} {type(cr).__name__ if not hasattr(cr, '_xsi_type') else cr._xsi_type}"
                              f" '{getattr(cr, 'name', '')}'  {str(url)[:160]}")

    _run("4. LINE-ITEM SETUP ON THE PREBID VIDEO ORDERS", setup)
    print(f"\nCSVs: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
