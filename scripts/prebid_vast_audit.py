"""Capture the VAST every Prebid bidder returns for Newsweek in-stream video,
unwrap it, and compare OneTag against the healthy bidders.

Why: OneTag reads ~52% Active View viewable on vid.newsweek (Oct 2026 MTD)
while every other bidder sits at 84-87% on the same player, ad unit and
auction (docs/prebid_viewability.md). Auction latency is ruled out — AY's
PBS reports OneTag answering in 72-91ms, mid-pack — so the open question is
the creative it delivers after it wins. AY (Amanda Kruger, 2026-10-23) named
three things to check: VAST wrapper depth, VPAID, and OMID support. This
script checks all three on our own pages instead of asking OneTag.

How: load real Newsweek ARTICLE pages (they carry the mux/IMA player and the
`video` Prebid ad unit) and record every `/openrtb2/auction` exchange the page
makes — the AY Prebid Server call carries the video imp and every s2s
bidder's response, VAST included. Client-side pbjs video bids are caught
from `bidResponse` too. Then, outside the browser, follow each bid's wrapper
chain (VASTAdTagURI) to the InLine ad and record per bid:
  - wrapper depth, the host of each hop, and the time each hop took
  - MediaFiles: mp4 vs VPAID (apiFramework=VPAID / application/javascript),
    resolution, bitrate, and file size (HEAD) of the mp4s
  - AdVerifications vendors (OMID) and the creative's duration
Losing bids count: a bid's VAST is the creative the bidder would have served,
so wins aren't needed — which matters, because OneTag wins only ~4% of video.

Only ad markup is fetched. Impression / tracking / error URLs inside the VAST
are never requested — a QA run must not count as an impression. Following a
wrapper is itself an ad request to the next hop, same as the player makes.

Run it from a residential connection. From a datacenter IP no bidder bids at
all (0 bids in 20 auctions on 2026-10-23), so there is nothing to inspect.

    pip install playwright && playwright install chromium
    python scripts/prebid_vast_audit.py
    LOADS=20 PROFILES=desktop python scripts/prebid_vast_audit.py

Env knobs: LOADS (default 12), PROFILES (mobile,desktop), ARTICLE_URLS,
TARGET_BIDDERS (onetag), OUT_DIR (./prebid-vast-audit), SCROLL_STEPS,
SCROLL_DWELL, HEADFUL=1, BROWSER_CHANNEL=chrome. Writes report.txt and
bids.json (every bid with its full chain) to OUT_DIR.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Browser launch, article scraping and device profiles are shared with the
# on-page render forensics so both tools load pages the same way.
from prebid_render_forensics import (  # noqa: E402
    DESKTOP_UA, PROFILE_CFG, _article_urls, _launch,
)

LOADS = int(os.environ.get("LOADS") or "12")
PROFILES = [p.strip() for p in (os.environ.get("PROFILES") or "mobile,desktop").split(",") if p.strip()]
TARGET_BIDDERS = [b.strip().lower() for b in (os.environ.get("TARGET_BIDDERS") or "onetag").split(",") if b.strip()]
OUT_DIR = Path(os.environ.get("OUT_DIR") or "prebid-vast-audit")
SCROLL_STEPS = int(os.environ.get("SCROLL_STEPS") or "10")
SCROLL_DWELL = float(os.environ.get("SCROLL_DWELL") or "1.5")
MAX_HOPS = 8
FETCH_TIMEOUT = 8

# pbjs client-side video bids. s2s bids are read from the PBS response
# instead, which carries the raw markup rather than pbjs's processed copy.
INIT_JS = r"""
window.__nwv = [];
window.pbjs = window.pbjs || {que: []};
pbjs.que.push(function () {
  try {
    pbjs.onEvent('bidResponse', function (b) {
      if (!b || b.mediaType !== 'video' || b.source === 's2s') return;
      window.__nwv.push({bidder: b.bidderCode, code: b.adUnitCode, cpm: b.cpm,
        crid: b.creativeId, adomain: (b.meta || {}).advertiserDomains || [],
        vastXml: b.vastXml || null, vastUrl: b.vastUrl || null});
    });
  } catch (_) {}
});
"""


# ── VAST parsing (pure; pinned by tests/test_prebid_vast_audit.py) ─────────

def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_vast(xml: str) -> dict:
    """Summarize one VAST document.

    kind is 'wrapper' (points at another VAST), 'inline' (carries the ad),
    'empty' (valid VAST, no <Ad> — a no-fill) or 'error' (unparseable).
    Only the first <Ad> is read; pods aren't used on this player.
    """
    try:
        root = _strip_ns(ET.fromstring(xml.strip().lstrip("﻿")))
    except ET.ParseError as exc:
        return {"kind": "error", "error": f"parse: {exc}"}
    if root.tag != "VAST":
        return {"kind": "error", "error": f"root <{root.tag}> is not VAST"}
    ad = root.find("Ad")
    if ad is None:
        return {"kind": "empty", "version": root.get("version")}
    out: dict = {"version": root.get("version"), "verifications": []}
    body = ad.find("Wrapper")
    if body is not None:
        out["kind"] = "wrapper"
        out["next"] = _text(body.find("VASTAdTagURI"))
    else:
        body = ad.find("InLine")
        if body is None:
            return {"kind": "error", "error": "<Ad> has neither Wrapper nor InLine"}
        out["kind"] = "inline"
    out["ad_system"] = _text(body.find("AdSystem"))
    # OMID verification lives in <AdVerifications> (VAST 4.1+) or, on older
    # VAST, in an <Extension type="AdVerifications">. Wrappers may add their own.
    for v in body.iter("Verification"):
        out["verifications"].append(v.get("vendor") or "?")
    media = []
    duration = ""
    for lin in body.iter("Linear"):
        duration = duration or _text(lin.find("Duration"))
        for mf in lin.iter("MediaFile"):
            typ = (mf.get("type") or "").lower()
            api = (mf.get("apiFramework") or "").upper()
            media.append({
                "url": _text(mf), "type": typ, "api": api,
                "delivery": mf.get("delivery") or "",
                "w": int(mf.get("width") or 0), "h": int(mf.get("height") or 0),
                "bitrate": int(float(mf.get("bitrate") or mf.get("maxBitrate") or 0)),
                "vpaid": api == "VPAID" or "javascript" in typ,
            })
        # VAST 4 moved VPAID-style interactivity out of MediaFile.
        if lin.find("InteractiveCreativeFile") is not None:
            out["interactive"] = True
    out["media"] = media
    out["duration"] = duration
    return out


def summarize_media(media: list[dict]) -> dict:
    vpaid = [m for m in media if m["vpaid"]]
    mp4 = [m for m in media if not m["vpaid"] and "mp4" in m["type"]]
    return {
        "n_media": len(media),
        "has_vpaid": bool(vpaid),
        # The case IMA handles worst: nothing to play but a VPAID JS unit.
        "vpaid_only": bool(vpaid) and not mp4,
        "n_mp4": len(mp4),
        "max_bitrate": max((m["bitrate"] for m in mp4), default=0),
        "min_bitrate": min((m["bitrate"] for m in mp4 if m["bitrate"]), default=0),
        "max_height": max((m["h"] for m in mp4), default=0),
    }


def resolve_chain(first: str, fetch) -> dict:
    """Follow a bid's markup to its InLine ad.

    `first` is either VAST XML or a URL that returns VAST. `fetch(url)` returns
    (status, text, ms) and is injected so tests run offline.
    """
    hops: list[dict] = []
    doc = first.strip()
    url = None
    if re.match(r"^https?://", doc):
        url, doc = doc, None
    for _ in range(MAX_HOPS + 1):
        if doc is None:
            status, doc, ms = fetch(url)
            hop = {"host": urlparse(url).netloc, "ms": ms, "status": status}
            if status != 200 or not doc:
                hop["kind"] = "fetch_failed"
                hops.append(hop)
                return {"hops": hops, "result": "fetch_failed", "depth": _depth(hops)}
        else:
            hop = {"host": urlparse(url).netloc if url else "(in bid)", "ms": 0, "status": None}
        parsed = parse_vast(doc)
        hop.update({"kind": parsed["kind"], "ad_system": parsed.get("ad_system", ""),
                    "verifications": parsed.get("verifications", [])})
        hops.append(hop)
        if parsed["kind"] != "wrapper":
            out = {"hops": hops, "result": parsed["kind"], "depth": _depth(hops)}
            if parsed["kind"] == "inline":
                out.update(inline=parsed, **summarize_media(parsed["media"]))
            return out
        url, doc = parsed.get("next") or "", None
        if not url:
            return {"hops": hops, "result": "wrapper_without_uri", "depth": _depth(hops)}
    return {"hops": hops, "result": "too_deep", "depth": _depth(hops)}


def _depth(hops: list[dict]) -> int:
    """Number of Wrapper documents passed through before the ad."""
    return sum(1 for h in hops if h.get("kind") == "wrapper")


def video_bids_from_auction(req: dict, resp: dict) -> list[dict]:
    """Pull every bid on a video imp out of one ORTB request/response pair."""
    video_imps = {i.get("id") for i in req.get("imp") or [] if "video" in i}
    if not video_imps:
        return []
    rtm = (resp.get("ext") or {}).get("responsetimemillis") or {}
    out = []
    for sb in resp.get("seatbid") or []:
        for b in sb.get("bid") or []:
            if b.get("impid") not in video_imps:
                continue
            seat = (sb.get("seat") or "?").lower()
            cache = (((b.get("ext") or {}).get("prebid") or {}).get("cache") or {})
            markup = b.get("adm") or ((cache.get("vastXml") or {}).get("url")) or b.get("nurl") or ""
            out.append({"bidder": seat, "price": b.get("price"), "crid": b.get("crid"),
                        "adomain": b.get("adomain") or [], "markup": markup,
                        "pbs_ms": rtm.get(seat)})
    return out


# ── network ────────────────────────────────────────────────────────────────

def _fetch(url: str) -> tuple[int, str, int]:
    t = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": DESKTOP_UA,
                                                   "Referer": "https://www.newsweek.com/"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            body = r.read(2_000_000).decode("utf-8", "replace")
            return r.status, body, int((time.monotonic() - t) * 1000)
    except Exception:
        return 0, "", int((time.monotonic() - t) * 1000)


def _head_size(url: str) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": DESKTOP_UA})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


# ── browser ────────────────────────────────────────────────────────────────

def _capture(browser, url: str, profile: str) -> tuple[list[dict], dict]:
    ctx = browser.new_context(**PROFILE_CFG[profile])
    ctx.add_init_script(INIT_JS)
    pg = ctx.new_page()
    pg.set_default_timeout(30_000)
    bids: list[dict] = []
    stats = {"auctions": 0, "video_auctions": 0, "latency": defaultdict(list)}

    def on_resp(r):
        if not urlparse(r.url).path.endswith("/openrtb2/auction"):
            return
        try:
            req = json.loads(r.request.post_data or "{}")
            resp = r.json()
        except Exception:
            return
        stats["auctions"] += 1
        found = video_bids_from_auction(req, resp)
        if any("video" in i for i in req.get("imp") or []):
            stats["video_auctions"] += 1
            # Recorded for every bidder asked, bid or not: this is AY's own
            # timing and is what answers "is OneTag slow?".
            for bidder, ms in ((resp.get("ext") or {}).get("responsetimemillis") or {}).items():
                if bidder != "cache":   # PBS's own cache write, not a bidder
                    stats["latency"][bidder.lower()].append(ms)
        for b in found:
            b.update(page=url, profile=profile, source="s2s",
                     endpoint=urlparse(r.url).netloc)
        bids.extend(found)

    pg.on("response", on_resp)
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(5)
        step = PROFILE_CFG[profile]["viewport"]["height"] // 2
        for _ in range(SCROLL_STEPS):
            pg.mouse.wheel(0, step)
            time.sleep(SCROLL_DWELL)
        time.sleep(3)
        for b in pg.evaluate("() => window.__nwv || []"):
            bids.append({"bidder": (b.get("bidder") or "?").lower(), "price": b.get("cpm"),
                         "crid": b.get("crid"), "adomain": b.get("adomain") or [],
                         "markup": b.get("vastXml") or b.get("vastUrl") or "",
                         "pbs_ms": None, "page": url, "profile": profile,
                         "source": "client", "endpoint": "pbjs"})
    except Exception as exc:
        print(f"[warn] {profile} {url}: {exc}")
    finally:
        ctx.close()
    return bids, stats


# ── report ─────────────────────────────────────────────────────────────────

def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "—"


def report(bids: list[dict], latency: dict[str, list[int]]) -> str:
    lines = []
    by = defaultdict(list)
    for b in bids:
        by[b["bidder"]].append(b)
    lines.append("VIDEO BIDS BY BIDDER (each bid's VAST unwrapped to its InLine ad)")
    hdr = (f"{'bidder':<16}{'bids':>5}{'resolved':>9}{'wrap med':>9}{'wrap max':>9}"
           f"{'chain ms':>9}{'VPAID':>7}{'VPAID-only':>11}{'OMID':>6}{'mp4 kbps':>9}{'mp4 MB':>8}")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    order = sorted(by, key=lambda k: (k not in TARGET_BIDDERS, -len(by[k])))
    for bidder in order:
        rows = by[bidder]
        ok = [r for r in rows if r["chain"]["result"] == "inline"]
        omid = sum(1 for r in ok if any(h["verifications"] for h in r["chain"]["hops"]))
        size = _med([r.get("mp4_bytes") or None for r in ok])
        line = (f"{bidder:<16}{len(rows):>5}{len(ok):>9}"
                f"{_med([r['chain']['depth'] for r in ok]) or 0:>9}"
                f"{max((r['chain']['depth'] for r in ok), default=0):>9}"
                f"{_med([sum(h['ms'] for h in r['chain']['hops']) for r in ok]) or 0:>9.0f}"
                f"{_pct(sum(r['chain']['has_vpaid'] for r in ok), len(ok)):>7}"
                f"{_pct(sum(r['chain']['vpaid_only'] for r in ok), len(ok)):>11}"
                f"{_pct(omid, len(ok)):>6}"
                f"{_med([r['chain']['max_bitrate'] or None for r in ok]) or 0:>9.0f}"
                f"{(size or 0) / 1e6:>8.1f}")
        lines.append(line)
    lines.append("")
    lines.append("  wrap = Wrapper documents before the ad; chain ms = time to fetch them all "
                 "(from this machine, not the user's)")
    lines.append("  VPAID-only = no mp4 to fall back to; OMID = an AdVerifications vendor somewhere "
                 "in the chain")

    lines.append("")
    lines.append("PBS RESPONSE TIME ON THE VIDEO IMP (ms, AY's own per-bidder timing)")
    for bidder in sorted(latency, key=lambda k: _med(latency[k]) or 0):
        xs = latency[bidder]
        flag = "  <" if bidder in TARGET_BIDDERS else ""
        lines.append(f"  {bidder:<16} median {_med(xs):>5.0f}  max {max(xs):>5}  n={len(xs)}{flag}")

    for bidder in TARGET_BIDDERS:
        rows = by.get(bidder) or []
        lines.append("")
        lines.append(f"{bidder.upper()} — EVERY BID")
        if not rows:
            asked = len(latency.get(bidder) or [])
            if asked:
                # It answered every time (AY timed it) and still never bid:
                # it is selective, so catching a bid needs many more loads.
                lines.append(f"  asked in {asked} video auctions, bid 0 times. It is selective "
                             "here; run more LOADS to catch one.")
            else:
                lines.append("  never asked and no bids: either it isn't configured on this "
                             "video unit or this connection gets no demand (a datacenter/VPN IP).")
            continue
        for r in rows:
            c = r["chain"]
            path = " -> ".join(f"{h['host']}[{h['kind']}"
                               + (f" {h['ms']}ms" if h["ms"] else "") + "]" for h in c["hops"])
            lines.append(f"  ${r['price']}  crid={r['crid']}  adomain={','.join(r['adomain']) or '—'}"
                         f"  {r['profile']}")
            lines.append(f"    chain: {path}")
            if c["result"] == "inline":
                inl = c["inline"]
                ver = sorted({v for h in c["hops"] for v in h["verifications"]})
                lines.append(f"    ad: {inl.get('ad_system') or '?'}  duration {inl.get('duration') or '?'}"
                             f"  VAST {inl.get('version')}  OMID {', '.join(ver) or 'none'}")
                for m in inl["media"][:8]:
                    lines.append(f"      {m['type'] or '?':<24}{m['api'] or '':<7}{m['w']}x{m['h']}"
                                 f"  {m['bitrate'] or '?'}kbps  {m['delivery']}")
            else:
                lines.append(f"    unresolved: {c['result']}")
        doms = Counter(d for r in rows for d in r["adomain"])
        if doms:
            lines.append(f"  advertisers: {', '.join(f'{d} ×{n}' for d, n in doms.most_common(10))}")
    return "\n".join(lines)


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bids: list[dict] = []
    latency: dict[str, list[int]] = defaultdict(list)
    n_video_auctions = 0
    with sync_playwright() as pw:
        browser = _launch(pw)
        urls = _article_urls(browser, LOADS)
        for i, url in enumerate(urls, 1):
            for profile in PROFILES:
                got, stats = _capture(browser, url, profile)
                n_video_auctions += stats["video_auctions"]
                for k, v in stats["latency"].items():
                    latency[k].extend(v)
                bids.extend(got)
                n_t = sum(1 for b in got if b["bidder"] in TARGET_BIDDERS)
                print(f"[{i}/{len(urls)}] {profile:<7} video auctions {stats['video_auctions']}  "
                      f"video bids {len(got)} ({n_t} {'/'.join(TARGET_BIDDERS)})  {url}",
                      flush=True)
        browser.close()

    print(f"\n{n_video_auctions} video auctions, {len(bids)} video bids. Unwrapping…")
    for b in bids:
        b["chain"] = (resolve_chain(b["markup"], _fetch) if b["markup"]
                      else {"hops": [], "result": "no_markup", "depth": 0})
        if b["chain"]["result"] == "inline":
            mp4s = [m for m in b["chain"]["inline"]["media"] if not m["vpaid"] and "mp4" in m["type"]]
            # The largest rendition is roughly what a desktop player would pick.
            if mp4s:
                top = max(mp4s, key=lambda m: (m["h"], m["bitrate"]))
                b["mp4_bytes"] = _head_size(top["url"])
    if not bids:
        print("No video bids came back. From a datacenter/VPN IP bidders don't bid — run "
              "this from a home connection.")

    text = report(bids, latency)
    print("\n" + text)
    (OUT_DIR / "report.txt").write_text(text + "\n")
    (OUT_DIR / "bids.json").write_text(json.dumps(bids, indent=1, default=str))
    print(f"\nwrote {OUT_DIR / 'report.txt'} and {OUT_DIR / 'bids.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
