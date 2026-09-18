"""Probe the Newsweek video slot's ad path on live pages.

Why this exists (2026-09-18): Magnite's Seller-Integration-Type report puts
Open Bidding video at 265,819,907 ad requests for 2026-08-18 → 2026-09-16,
while GAM's `YIELD_GROUP_CALLOUTS` for the same buyer/window/yield-group is
52,036,623 — a 5.11x gap (docs/ob_vs_prebid_video_requests.md). The site runs
ONE video slot and fires a fresh request at the end of each video, so the
question was whether that re-request explains the multiplication.

READ THIS BEFORE TRUSTING A RUN
-------------------------------
**Playwright's bundled Chromium has no proprietary codecs.** `canPlayType`
returns '' for H.264, AAC and HLS. The site's video is H.264, so under the
bundled browser the player NEVER STARTS: readyState and networkState stay 0,
currentTime stays 0, and no ad break — hence no VAST request — ever happens.

`video.paused === false` does NOT mean playback. It only means play() was
called. A first pass at this probe read zero VAST requests as evidence that
the ad call is made server-side by the player vendor. That conclusion was
wrong and was withdrawn; it was an artifact of a browser that cannot play
video. This script now refuses to draw that conclusion: it checks codec
support up front and reports INCONCLUSIVE when it cannot play the media.

To get a real answer, run against a browser with proprietary codecs:

    BROWSER_CHANNEL=chrome python scripts/video_slot_forensics.py

from a machine with Chrome installed (the same escape hatch
scripts/prebid_render_forensics.py documents). A datacenter headless
Chromium cannot answer this question.

What a bundled-Chromium run still establishes usefully: whether the IMA SDK
loads and `google.ima.AdsLoader` is instantiated. It does — so the
client-side video ad path exists, and a real play requests VAST from
securepubads.g.doubleclick.net/gampad/ads, which is a GAM video callout and
is counted in the 52.0M. That means the end-of-video re-request reaches GAM
as its own callout, inflating both sides equally, and so cannot explain the
5.11x on its own.

Usage:
    python scripts/video_slot_forensics.py
    URLS="https://www.newsweek.com/foo-123456" DWELL=120 python scripts/…

Env: URLS (comma-separated; default = homepage + 3 scraped articles), DWELL
(seconds per page, default 70), BROWSER_CHANNEL (e.g. "chrome" — takes
precedence over CHROME_PATH), CHROME_PATH, HTTPS_PROXY.

Proxy/TLS note: launch args mirror scripts/prebid_render_forensics.py::_launch —
the session's egress proxy re-terminates TLS and rejects Chromium's TLS 1.3
ClientHello, so --ssl-version-max=tls1.2 keeps certificate verification fully
on rather than disabling it.
"""

from __future__ import annotations

import os
import re
import sys
import time
from urllib.parse import urlparse, parse_qs

DWELL = int(os.environ.get("DWELL") or "70")
URLS = [u.strip() for u in (os.environ.get("URLS") or "").split(",") if u.strip()]
BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL")
CHROME_PATH = os.environ.get("CHROME_PATH") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
HOME_URL = "https://www.newsweek.com/"
_ARTICLE_RE = re.compile(r"^https://www\.newsweek\.com/[a-z0-9-]+-\d{6,}$")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def _launch(pw):
    kw = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--ssl-version-max=tls1.2",
            "--autoplay-policy=no-user-gesture-required",
        ],
    }
    if BROWSER_CHANNEL:
        # channel and executable_path are mutually exclusive; channel wins. Use
        # this to get proprietary codecs — the bundled build cannot play H.264.
        kw["channel"] = BROWSER_CHANNEL
    elif CHROME_PATH and os.path.exists(CHROME_PATH):
        kw["executable_path"] = CHROME_PATH
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        kw["proxy"] = {"server": proxy}
    return pw.chromium.launch(**kw)


def _is_video_req(url: str) -> bool:
    """A GAM video ad request: VAST/VMAP output, or a video position param."""
    low = url.lower()
    q = parse_qs(urlparse(url).query)
    out = (q.get("output") or [""])[0].lower()
    return "vast" in out or "vmap" in out or bool(q.get("vpos")) or "vast" in low


def codec_support(ctx) -> dict:
    """Can this browser play the site's media at all? Decides whether a zero
    VAST count means anything."""
    pg = ctx.new_page()
    pg.goto("about:blank")
    caps = pg.evaluate("""() => { const v = document.createElement('video'); return {
        h264: v.canPlayType('video/mp4; codecs="avc1.42E01E"'),
        aac:  v.canPlayType('audio/mp4; codecs="mp4a.40.2"'),
        hls:  v.canPlayType('application/vnd.apple.mpegurl'),
    }; }""")
    pg.close()
    return caps


def probe(ctx, url: str) -> dict:
    reqs: list[dict] = []
    pg = ctx.new_page()
    t0 = time.time()

    def on_req(r):
        u = r.url
        low = u.lower()
        if "/gampad/ads" in low or "vast" in low or "vmap" in low:
            q = parse_qs(urlparse(u).query)
            reqs.append({
                "t": round(time.time() - t0, 1),
                "video": _is_video_req(u),
                "iu": (q.get("iu") or [""])[0],
                "sz": (q.get("sz") or [""])[0],
                "vpos": (q.get("vpos") or [""])[0],
                # pod signals — a pod would let one callout carry several ads
                "pmad": (q.get("pmad") or [""])[0],
                "pod": (q.get("pod") or [""])[0],
                "correlator": (q.get("correlator") or [""])[0],
            })

    pg.on("request", on_req)
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=70_000)
    except Exception as exc:  # a dead socket is not worth retrying the URL over
        pg.close()
        return {"url": url, "error": str(exc)[:120]}

    pg.wait_for_timeout(3000)
    try:
        pg.eval_on_selector("#nw-video-player", "e => e.scrollIntoView({block:'center'})")
    except Exception:
        pass

    # Dwell, nudging playback each pass: the slot re-requests at video end, so a
    # single page-load snapshot would miss the sequential behaviour entirely.
    end = time.time() + DWELL
    while time.time() < end:
        pg.evaluate("""() => document.querySelectorAll('video').forEach(v => {
            v.muted = true;
            try { const p = v.play(); if (p && p.catch) p.catch(() => {}); } catch (e) {}
        })""")
        pg.mouse.wheel(0, 500)
        pg.wait_for_timeout(2000)

    state = pg.evaluate("""() => ({
        player: !!document.querySelector('#nw-video-player'),
        nvids: document.querySelectorAll('video').length,
        // NB: !paused means play() was called, NOT that media is playing.
        // currentTime/readyState are the honest signals.
        playing: [...document.querySelectorAll('video')].filter(v => !v.paused).length,
        maxCurrentTime: Math.max(0, ...[...document.querySelectorAll('video')].map(v => v.currentTime || 0)),
        maxReadyState: Math.max(0, ...[...document.querySelectorAll('video')].map(v => v.readyState || 0)),
        ima: typeof google !== 'undefined' && !!(window.google && google.ima),
        adsLoader: typeof google !== 'undefined' && !!(window.google && google.ima && google.ima.AdsLoader),
        slots: (window.googletag && googletag.pubads)
            ? googletag.pubads().getSlots().map(s => s.getAdUnitPath()) : [],
    })""")
    pg.close()
    return {"url": url, "state": state, "reqs": reqs}


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, user_agent=UA)

        caps = codec_support(ctx)
        can_play = bool(caps.get("h264"))
        print(f"codec support: h264={caps['h264']!r} aac={caps['aac']!r} hls={caps['hls']!r}")
        if not can_play:
            print("  !! This browser CANNOT play H.264. The site's video will never\n"
                  "     start, so a zero VAST count proves nothing. Re-run with\n"
                  "     BROWSER_CHANNEL=chrome on a machine with Chrome installed.\n")

        targets = URLS
        if not targets:
            pg = ctx.new_page()
            pg.goto(HOME_URL, wait_until="domcontentloaded", timeout=70_000)
            hrefs = pg.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            pg.close()
            seen, arts = set(), []
            for h in hrefs:
                if _ARTICLE_RE.match(h) and h not in seen:
                    seen.add(h)
                    arts.append(h)
                if len(arts) >= 3:
                    break
            targets = [HOME_URL] + arts

        players = vids = 0
        played = ima_seen = False
        for url in targets:
            res = probe(ctx, url)
            print("=" * 70)
            print(url)
            if res.get("error"):
                print(f"  ERROR {res['error']}")
                continue
            st = res["state"]
            v = [r for r in res["reqs"] if r["video"]]
            players += bool(st["player"])
            vids += len(v)
            played = played or st["maxCurrentTime"] > 0
            ima_seen = ima_seen or bool(st["adsLoader"])
            print(f"  #nw-video-player: {st['player']}   <video>: {st['nvids']}   "
                  f"play() called on {st['playing']}")
            print(f"  ACTUALLY PLAYED: currentTime max={st['maxCurrentTime']:.1f}s "
                  f"readyState max={st['maxReadyState']}   "
                  f"IMA AdsLoader: {st['adsLoader']}")
            print(f"  GPT slots ({len(st['slots'])}): {st['slots']}")
            print(f"  /gampad/ads requests: {len(res['reqs'])}   VIDEO ones: {len(v)}")
            for x in v:
                print(f"    t={x['t']}s iu={x['iu']} sz={x['sz']} vpos={x['vpos']} "
                      f"pmad={x['pmad']!r} pod={x['pod']!r} corr={x['correlator'][:10]}")
        browser.close()

    print("\n" + "=" * 70)
    print(f"surfaces probed: {len(targets)}   with player: {players}   "
          f"video ad requests seen: {vids}")
    print(f"media actually played: {played}   IMA AdsLoader present: {ima_seen}")
    if vids > 0:
        print("  -> The player requests VAST client-side, and those requests are GAM\n"
              "     video callouts (counted in YIELD_GROUP_CALLOUTS). Compare the\n"
              "     per-video request count against docs/ob_vs_prebid_video_requests.md.")
    elif not played:
        print("  -> INCONCLUSIVE. The video never played, so no ad break could occur\n"
              "     and a zero VAST count means nothing. This is the expected result\n"
              "     under a browser without proprietary codecs. Re-run with\n"
              "     BROWSER_CHANNEL=chrome. Do NOT conclude the ad call is server-side\n"
              "     from this — that inference was made once and was wrong.")
    else:
        print("  -> Media played and still no VAST request. THAT is a real finding;\n"
              "     see docs/ob_vs_prebid_video_requests.md before acting on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
