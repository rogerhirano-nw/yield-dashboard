#!/usr/bin/env python3
"""Read-only diagnostic: why a video line item's creatives record no clicks.

A display creative clicks through GAM's click server, so GAM counts the
click. A video creative only does if the *click chain* survives three hops:

    1. the creative carries a click-through at all
       (VideoCreative.destinationUrl, or a `<ClickThrough>` inside a
       third-party VAST redirect, or a `%%CLICK_URL_UNESC%%` macro in a
       third-party snippet),
    2. GAM's click server is in that chain (a third-party VAST that
       carries the vendor's own ClickThrough bypasses it — the vendor
       counts the click, GAM never sees it),
    3. the *player* implements VAST `VideoClicks` and the placement is
       actually clickable (muted autoplay / non-linear outstream players
       frequently are not).

This script dumps the evidence for each hop and writes nothing:
  - the line item (environment, type, video duration, placeholders),
  - every associated creative with its click-relevant fields, incl. a
    fetch of the VAST redirect to see whether `<ClickThrough>` /
    `<ClickTracking>` are even present,
  - GAM's own numbers per creative (impressions / clicks / CTR, plus
    video viewership), and
  - a network-wide video-vs-display click comparison, which is what
    separates "this creative is broken" from "no video ad in this
    network has ever recorded a GAM click".

Usage:
    python3 scripts/diagnose_video_clicks.py --line-items 7415150292
    python3 scripts/diagnose_video_clicks.py --line-items 7415150292 --days 30
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

_envp = Path(__file__).resolve().parent.parent / ".env"
if _envp.exists():
    for _line in _envp.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from gam_client import GAMClient  # noqa: E402
from googleads import ad_manager  # noqa: E402

V = "v202605"

# Fields that decide whether a click can be counted at all.
_CLICK_FIELDS = re.compile(
    r"click|destination|tracking|vast|snippet|url|duration|thirdparty|third_party",
    re.I,
)
_GAM_CLICK_MACROS = ("%%CLICK_URL_UNESC%%", "%%CLICK_URL_ESC%%",
                     "%%CLICK_URL_ESC_ESC%%", "%c", "${CLICK_URL}")


def _g(obj, *names):
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _page(svc, method, where, limit=200, **binds):
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


def _values(obj) -> dict:
    """zeep CompoundValue -> plain dict (best effort)."""
    v = getattr(obj, "__values__", None)
    if isinstance(v, dict):
        return dict(v)
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}


def _redact(s: str) -> str:
    """The Actions logs are public — keep the network id out of them."""
    nid = os.environ.get("GAM_NETWORK_ID", "")
    return s.replace(nid, "<network-id>") if nid else s


def _short(v, n=1500) -> str:
    s = _redact(str(v))
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= n else s[:n] + f" …(+{len(s) - n} chars)"


def _print_df(df: pd.DataFrame, note: str = "") -> None:
    if df is None or df.empty:
        print(f"    (no rows){' — ' + note if note else ''}")
        return
    with pd.option_context("display.max_rows", 200, "display.width", 200,
                           "display.max_colwidth", 60):
        print(df.to_string(index=False))


def _report(gc: GAMClient, label: str, dimensions, metrics, start, end,
            filters=None, top: str | None = None, n: int = 15,
            pick: tuple[str, str] | None = None) -> pd.DataFrame | None:
    print(f"\n--- report: {label}")
    print(f"    dims={dimensions} metrics={metrics}")
    df = None
    for attempt in (1, 2):
        try:
            df = gc._run_report(dimensions=dimensions, metrics=metrics,
                                start_date=start, end_date=end, filters=filters)
            break
        except Exception as exc:  # noqa: BLE001 — each cut is independent
            transient = "500" in str(exc) or "try again later" in str(exc).lower()
            if attempt == 1 and transient:
                print("    transient GAM report error — retrying once")
                time.sleep(20)
                continue
            print(f"    FAILED: {type(exc).__name__}: {_short(exc, 400)}")
            return None
    if df is None:
        return None
    if pick and not df.empty and pick[0] in df.columns:
        df = df[df[pick[0]].astype(str) == pick[1]]
        print(f"    (rows where {pick[0]} == {pick[1]!r})")
    if top and not df.empty and top in df.columns:
        df = df.sort_values(top, ascending=False).head(n)
        print(f"    (top {n} by {top})")
    _print_df(df)
    return df


def _probe_enums() -> None:
    """Print the metric/dimension names GAM actually offers for clicks and
    video, so a 0 can be read against what is even measurable."""
    from google.ads import admanager_v1
    metrics = [m.name for m in admanager_v1.ReportDefinition.Metric]
    dims = [d.name for d in admanager_v1.ReportDefinition.Dimension]
    print("\n=== GAM metric vocabulary (click / video / interaction) ===")
    print("  CLICK metrics:      ", ", ".join(m for m in metrics if "CLICK" in m) or "none")
    print("  VIDEO metrics:      ", ", ".join(m for m in metrics if "VIDEO" in m) or "none")
    print("  INTERACTION metrics:", ", ".join(m for m in metrics if "INTERACTION" in m) or "none")
    print("  CREATIVE dims:      ", ", ".join(d for d in dims if "CREATIVE" in d) or "none")
    print("  ENVIRONMENT dims:   ", ", ".join(d for d in dims if "ENVIRONMENT" in d) or "none")


def _fetch_vast(url: str) -> None:
    """Fetch a VAST redirect and report whether a click chain exists in it."""
    import requests
    probe = url
    # Unfilled macros make some vendors 400; substitute obvious ones.
    probe = re.sub(r"%%[A-Z_]+%%", "", probe)
    probe = re.sub(r"\[[A-Za-z_]+\]", "", probe)
    try:
        r = requests.get(probe, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (diagnostic)"})
    except Exception as exc:  # noqa: BLE001
        print(f"      VAST fetch failed: {type(exc).__name__}: {_short(exc, 200)}")
        return
    body = r.text or ""
    print(f"      VAST fetch: HTTP {r.status_code}, {len(body)} bytes")
    for tag in ("VideoClicks", "ClickThrough", "ClickTracking", "VASTAdTagURI",
                "Wrapper", "InLine", "Linear", "NonLinear"):
        n = len(re.findall("<" + tag + r"\b", body, re.I))
        print(f"      <{tag}>: {n}")
    m = re.search(r"<ClickThrough[^>]*>(.*?)</ClickThrough>", body, re.I | re.S)
    if m:
        print(f"      ClickThrough target: {_short(m.group(1).strip(), 300)}")
    m = re.search(r"<VASTAdTagURI[^>]*>(.*?)</VASTAdTagURI>", body, re.I | re.S)
    if m:
        print(f"      wraps: {_short(m.group(1).strip(), 300)}")
    # A VPAID / SIMID creative runs its own click handling and commonly
    # never fires the serving wrapper's <ClickTracking> — the documented
    # way a real click goes uncounted by the ad server.
    api = re.findall(r'apiFramework\s*=\s*"([^"]+)"', body, re.I)
    print(f"      apiFramework values: {sorted(set(api)) or 'none'}")
    print(f"      <InteractiveCreativeFile>: "
          f"{len(re.findall('<InteractiveCreativeFile', body, re.I))}")
    print(f"      <AdParameters>: {len(re.findall('<AdParameters', body, re.I))}")
    mf = re.findall(r"<MediaFile\b([^>]*)>", body, re.I)
    print(f"      MediaFiles: {len(mf)}")
    for a in mf[:6]:
        t = re.search(r'type\s*=\s*"([^"]+)"', a, re.I)
        d = re.search(r'delivery\s*=\s*"([^"]+)"', a, re.I)
        w = re.search(r'width\s*=\s*"([^"]+)"', a, re.I)
        print(f"        type={t.group(1) if t else '?'} "
              f"delivery={d.group(1) if d else '?'} w={w.group(1) if w else '?'}")
    ext = re.findall(r'<Extension[^>]*type\s*=\s*"([^"]+)"', body, re.I)
    if ext:
        print(f"      Extension types: {sorted(set(ext))[:8]}")
    for ev in ("mute", "pause", "resume", "start", "complete", "progress"):
        n = len(re.findall(r'event\s*=\s*"' + ev + r'"', body, re.I))
        if n:
            print(f"      tracking event {ev}: {n}")


def _host(u: str) -> str:
    m = re.match(r"https?://([^/?#]+)", u.strip())
    return m.group(1) if m else _short(u, 80)


def _fetch_served_vast(url: str) -> None:
    """Fetch what GAM itself serves for this creative (the preview ad
    request), not the vendor tag. This is the hop that decides whether
    GAM's click server is in the chain at all: if GAM's wrapper carries
    no <ClickTracking> of its own, GAM has nothing to count no matter
    what the player does."""
    import requests
    try:
        r = requests.get(url, timeout=25,
                         headers={"User-Agent": "Mozilla/5.0 (diagnostic)"})
    except Exception as exc:  # noqa: BLE001
        print(f"      GAM-served VAST fetch failed: {type(exc).__name__}: "
              f"{_short(exc, 200)}")
        return
    body = r.text or ""
    print(f"      GAM-served VAST: HTTP {r.status_code}, {len(body)} bytes")
    for tag in ("Wrapper", "InLine", "VASTAdTagURI", "VideoClicks",
                "ClickThrough", "ClickTracking"):
        n = len(re.findall("<" + tag + r"\b", body, re.I))
        print(f"        <{tag}>: {n}")
    for tag in ("ClickTracking", "ClickThrough"):
        pat = "<" + tag + r"[^>]*>(?:\s*<!\[CDATA\[)?(.*?)(?:\]\]>\s*)?</" + tag + ">"
        for m in re.findall(pat, body, re.I | re.S)[:4]:
            print(f"        {tag} host: {_host(m)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--line-items", default="7415150292",
                    help="comma-separated GAM line item ids")
    ap.add_argument("--days", type=int, default=30,
                    help="report window, days back from yesterday")
    ap.add_argument("--fetch-vast", action="store_true", default=True)
    ap.add_argument("--no-fetch-vast", dest="fetch_vast", action="store_false")
    args = ap.parse_args()

    li_ids = [int(x) for x in args.line_items.split(",") if x.strip()]
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=args.days - 1)
    print(f"Report window: {start} .. {end}  (network tz America/New_York)")

    gc = GAMClient()
    client = gc._get_soap_client()
    li_svc = client.GetService("LineItemService", version=V)
    cr_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)

    _probe_enums()

    order_ids: set[int] = set()

    for lid in li_ids:
        li = (_page(li_svc, "getLineItemsByStatement", "id = :i", i=lid) or [None])[0]
        print("\n" + "=" * 78)
        print(f"LINE ITEM {lid}  {_g(li, 'name') if li else '(not found)'}")
        print("=" * 78)
        if li is None:
            continue
        oid = _g(li, "orderId")
        if oid:
            order_ids.add(int(oid))
        for f in ("orderId", "orderName", "lineItemType", "costType", "status",
                  "environmentType", "videoMaxDuration", "creativeRotationType",
                  "startDateTime", "endDateTime", "isArchived",
                  "companionDeliveryOption", "skipInventoryCheck",
                  "primaryGoal", "creativePlaceholders", "videoMaxDuration"):
            val = getattr(li, f, None)
            if val is not None:
                print(f"  {f}: {_short(val, 600)}")
        tgt = getattr(li, "targeting", None)
        rpt = getattr(tgt, "requestPlatformTargeting", None) if tgt else None
        if rpt is not None:
            print(f"  targeting.requestPlatformTargeting: {_short(rpt, 300)}")

        licas = _page(lica_svc, "getLineItemCreativeAssociationsByStatement",
                      "lineItemId = :l", l=lid)
        print(f"\n  {len(licas)} creative association(s)")
        cids = []
        for lica in licas:
            cid = _g(lica, "creativeId")
            if cid is None:
                continue
            cids.append(int(cid))
            print(f"    LICA creativeId={cid} status={_g(lica, 'status')} "
                  f"sizes={_short(getattr(lica, 'sizes', None), 200)} "
                  f"destinationUrl={getattr(lica, 'destinationUrl', None)}")

        for cid in cids:
            cr = (_page(cr_svc, "getCreativesByStatement", "id = :c", c=cid) or [None])[0]
            if cr is None:
                print(f"\n  CREATIVE {cid}: not found")
                continue
            ctype = type(cr).__name__
            print(f"\n  CREATIVE {cid}  [{ctype}]  {_g(cr, 'name')}")
            vals = _values(cr)
            for k in sorted(vals):
                if not _CLICK_FIELDS.search(k):
                    continue
                v = vals[k]
                if v is None or v == [] or v == "":
                    print(f"      {k}: {v!r}")
                    continue
                print(f"      {k}: {_short(v)}")

            # hop 1/2: is a GAM click macro present in a third-party snippet?
            snippet = " ".join(
                str(vals.get(k) or "") for k in ("snippet", "expandedSnippet",
                                                 "vastXmlUrl", "vastRedirectType",
                                                 "customCreativeAsset")
            )
            if snippet.strip():
                found = [m for m in _GAM_CLICK_MACROS if m in snippet]
                print(f"      GAM click macro in tag: {found or 'NONE'}")
            dest = vals.get("destinationUrl")
            print(f"      destinationUrl set: {bool(dest)}  -> {_short(dest, 200)}")

            vast_url = vals.get("vastXmlUrl")
            if vast_url and args.fetch_vast:
                _fetch_vast(str(vast_url))
            preview = vals.get("vastPreviewUrl")
            if preview and args.fetch_vast:
                _fetch_served_vast(str(preview))

    li_filter = [("LINE_ITEM_ID", "IN", [int(i) for i in li_ids])]

    print("\n" + "=" * 78)
    print("GAM's OWN NUMBERS")
    print("=" * 78)

    _report(gc, "per creative on the line item(s)",
            ["LINE_ITEM_ID", "CREATIVE_ID", "CREATIVE_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
            start, end, li_filter)

    _report(gc, "video viewership on the line item(s)",
            ["LINE_ITEM_ID", "CREATIVE_ID"],
            ["AD_SERVER_IMPRESSIONS", "VIDEO_VIEWERSHIP_STARTS",
             "VIDEO_VIEWERSHIP_COMPLETES"],
            start, end, li_filter)

    for oid in sorted(order_ids):
        _report(gc, f"every line item on order {oid} (display vs video)",
                ["LINE_ITEM_ID", "LINE_ITEM_NAME"],
                ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
                start, end, [("ORDER_ID", "IN", [oid])])

    # A PG line serves through the ad server but is bought programmatically,
    # so a 0 in AD_SERVER_CLICKS alone doesn't prove GAM logged no click —
    # check each click metric family separately (they are mutually
    # incompatible in one report, hence one cut each).
    for m in ("CLICKS", "AD_EXCHANGE_CLICKS", "AD_SERVER_CLICKS",
              "AD_SERVER_UNFILTERED_CLICKS", "VIDEO_VIEWERSHIP_CLICK_TO_PLAYS"):
        _report(gc, f"{m} on the line item(s)", ["LINE_ITEM_ID"], [m],
                start, end, li_filter)

    for m in ("VIDEO_INTERACTION_MUTES", "VIDEO_INTERACTION_UNMUTES",
              "VIDEO_INTERACTION_PAUSES", "VIDEO_INTERACTION_RESUMES",
              "VIDEO_INTERACTION_FULL_SCREENS", "VIDEO_INTERACTION_VIDEO_SKIPS",
              "VIDEO_VIEWERSHIP_ENGAGED_VIEWS", "VIDEO_VIEWERSHIP_AUTO_PLAYS"):
        _report(gc, f"{m} on the line item(s)", ["LINE_ITEM_ID"], [m],
                start, end, li_filter)

    # The control group: among video-player inventory, do the THIRD-PARTY
    # VAST redirects record clicks, or only the formats GAM serves itself?
    video_only = [("LINE_ITEM_ENVIRONMENT_TYPE_NAME", "IN", ["Video player"])]
    _report(gc, "video-player inventory by creative type",
            ["CREATIVE_TYPE_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
            start, end, video_only)
    _report(gc, "video-player inventory by third-party VAST vendor",
            ["CREATIVE_VIDEO_REDIRECT_THIRD_PARTY"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
            start, end, video_only)
    _report(gc, "video line items by redirect vendor",
            ["CREATIVE_VIDEO_REDIRECT_THIRD_PARTY", "LINE_ITEM_ID",
             "LINE_ITEM_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
            start, end, video_only, top="ad_server_impressions", n=30)
    _report(gc, "video ad units by clicks",
            ["AD_UNIT_NAME_TOP_LEVEL", "AD_UNIT_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
            start, end, video_only, top="ad_server_impressions", n=15)
    _report(gc, "every line item running Innovid video redirects",
            ["CREATIVE_VIDEO_REDIRECT_THIRD_PARTY", "LINE_ITEM_ID",
             "LINE_ITEM_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
            start, end, video_only,
            pick=("creative_video_redirect_third_party", "Innovid"),
            top="ad_server_impressions", n=25)
    _report(gc, "ad units this line item runs on",
            ["AD_UNIT_NAME_TOP_LEVEL", "AD_UNIT_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS"],
            start, end, li_filter)
    _report(gc, "video line items that DO record clicks",
            ["LINE_ITEM_ID", "LINE_ITEM_NAME"],
            ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
            start, end, video_only, top="ad_server_clicks", n=15)

    # The decisive comparison: does ANY video inventory in this network
    # record GAM clicks, or is a 0 here the norm for video?
    for dim in ("LINE_ITEM_ENVIRONMENT_TYPE_NAME", "INVENTORY_FORMAT_NAME",
                "CREATIVE_TYPE_NAME"):
        _report(gc, f"network-wide clicks by {dim}",
                [dim],
                ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_CTR"],
                start, end)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
