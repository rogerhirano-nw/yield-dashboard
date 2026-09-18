"""Does the Newsweek video slot make its ad request in the browser? (No.)

Why this exists (2026-09-18): Magnite's Seller-Integration-Type report puts
Open Bidding video at 265,819,907 ad requests for 2026-08-18 → 2026-09-16,
while GAM's `YIELD_GROUP_CALLOUTS` for the same buyer/window/yield-group is
52,036,623 — a 5.11x gap (docs/ob_vs_prebid_video_requests.md). The site runs
ONE video slot and fires a fresh request at the end of each video, so the
obvious question was whether that re-request explains the multiplication.

It does not, and this script is how we know: each re-request is its own auction
and GAM counts each as a callout, so it inflates both sides equally. What the
probe found instead is more useful —

  * the player is present and actively playing on most surfaces, and
  * it issues **zero** client-side VAST/VMAP requests, and
  * **no video ad unit is registered in GPT at all** (every slot is display).

So the video ad call is made server-side in the player vendor's layer
(cs.minutemedia-prebid.com / prebid.videostep.com): player → vendor server →
GAM → OB callout. That hop appears in neither side's report and is the one
place a request can be multiplied without showing up in GAM's callout count.

**Corollary worth not re-learning the hard way:** because the ad call never
crosses the browser, no amount of on-page instrumentation will count these
requests. Resolving the fan-out needs the vendor's own request logs or
Magnite's definition of an OB "ad request" — not a DOM repro.

Usage:
    python scripts/video_slot_forensics.py
    URLS="https://www.newsweek.com/foo-123456" DWELL=120 python scripts/…

Env: URLS (comma-separated; default = homepage + 3 scraped articles), DWELL
(seconds to dwell per page, default 70), CHROME_PATH, HTTPS_PROXY.

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
    if CHROME_PATH and os.path.exists(CHROME_PATH):
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
        playing: [...document.querySelectorAll('video')].filter(v => !v.paused).length,
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
            print(f"  #nw-video-player: {st['player']}   <video>: {st['nvids']} "
                  f"(playing {st['playing']})")
            print(f"  GPT slots ({len(st['slots'])}): {st['slots']}")
            print(f"  /gampad/ads requests: {len(res['reqs'])}   VIDEO ones: {len(v)}")
            for x in v:
                print(f"    t={x['t']}s iu={x['iu']} sz={x['sz']} vpos={x['vpos']} "
                      f"pmad={x['pmad']!r} pod={x['pod']!r} corr={x['correlator'][:10]}")
        browser.close()

    print("\n" + "=" * 70)
    print(f"surfaces probed: {len(targets)}   with player: {players}   "
          f"client-side video ad requests: {vids}")
    if vids == 0:
        print("  -> unchanged from 2026-09-18: the video ad call is not made in the\n"
              "     browser. See docs/ob_vs_prebid_video_requests.md.")
    else:
        print("  -> CHANGED: the player now requests client-side. The doc's conclusion\n"
              "     about the server-side hop needs revisiting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
