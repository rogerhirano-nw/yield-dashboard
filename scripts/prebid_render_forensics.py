"""On-page render forensics for the Prebid bidders whose GAM Active View
viewability reads far below the site baseline.

Why: the 2026-08-14→09-03 PreBid GAM report shows, impression-weighted,
smilewanted 40.4% viewable (4.6M banner imps), ogury 54.4% (2.8M), oms 56.4%
(38k) and onetag 47.7% on in-stream video (138k) — against a 75.8% banner /
86.0% video baseline across all other bidders on the same slots. The Mobkoi
debrief (docs/mobkoi_viewability.md) taught us that a *render location*
problem reads as ~0% viewable / 100% measurable, and that the fix is to make
the element Active View measures (the GPT iframe) track the element the user
sees. 40-56% is NOT that signature, so this script exists to tell the two
apart with evidence instead of inference:

  * breakout  -> creative leaves the GPT iframe; iframe hidden/collapsed
                 while content renders in the parent DOM  => Mobkoi-class,
                 the iframe mirror applies.
  * geometry  -> iframe stays but is smaller/offset from what renders
                 (e.g. a 300x250 slot rendering a 1x1 + parent-DOM overlay).
  * placement -> render is clean; the bidder simply wins on slots/positions
                 that are less viewable (below-the-fold, refreshed, sticky
                 collapsed) => a yield/mix conversation, not a render fix.

How: load real Newsweek ARTICLE pages in headless Chromium (article pages are
the only inventory these bidders buy — the homepage runs a different slot
set), instrument GPT + Prebid before they boot, scroll through the article
with dwell time so Active View's own 50%-for-1s clock can run, then dump per
slot:
  - the winning Prebid bidder (pbjs `bidWon`) and the GAM line item/creative
    that rendered it (GPT `slotRenderEnded`)
  - whether GPT itself fired `impressionViewable` for that slot -- this is
    Active View's own client-side verdict, the closest thing to reproducing
    the report number in the browser
  - the in-view% timeline from `slotVisibilityChanged`
  - iframe-vs-slot geometry and computed style (the breakout signature)
  - parent-DOM nodes the render injected outside the slot subtree

Usage:
    python scripts/prebid_render_forensics.py            # scrape fresh articles
    ARTICLE_URLS="https://…,https://…" python scripts/prebid_render_forensics.py
    TARGET_BIDDERS=smilewanted,oms,onetag,ogury LOADS=12 python …

Env knobs: ARTICLE_URLS, LOADS, TARGET_BIDDERS, PROFILES (mobile,desktop),
SHOTS_DIR, CHROME_PATH, BROWSER_PROXY, HEADFUL=1, BROWSER_CHANNEL=chrome,
INCOGNITO=1, SCROLL_STEPS, SCROLL_DWELL.

Running it from a laptop (recommended for SmileWanted):

    pip install playwright && playwright install chromium
    BROWSER_CHANNEL=chrome INCOGNITO=1 HEADFUL=1 LOADS=15 \
      TARGET_BIDDERS=smilewanted python scripts/prebid_render_forensics.py

A residential IP is the point: from a datacenter runner SmileWanted is
requested on every auction and essentially never bids, so its render cannot
be observed there at all.
Output is plain text (the companion workflow posts it as a PR comment) plus
a JSON dump of every observed render at $SHOTS_DIR/renders.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

HOME_URL = os.environ.get("HOME_URL") or "https://www.newsweek.com/"
ARTICLE_URLS = [u.strip() for u in (os.environ.get("ARTICLE_URLS") or "").split(",") if u.strip()]
LOADS = int(os.environ.get("LOADS") or "8")
TARGET_BIDDERS = [b.strip().lower() for b in (
    os.environ.get("TARGET_BIDDERS") or "smilewanted,oms,onetag,ogury"
).split(",") if b.strip()]
PROFILES = [p.strip() for p in (os.environ.get("PROFILES") or "mobile,desktop").split(",") if p.strip()]
SHOTS = Path(os.environ.get("SHOTS_DIR") or "/tmp/prebid-forensics")
CHROME_PATH = os.environ.get("CHROME_PATH") or ""
# Default to the session's own proxy rather than a hardcoded port: the agent
# proxy can move ports mid-session, and a stale port fails every page load
# with ERR_PROXY_CONNECTION_FAILED while curl (which reads the env) still
# works — an hour-wasting way to look like a tuning problem.
def _current_proxy() -> str:
    """Read the proxy fresh each time. The agent proxy can move ports *during*
    a run, not just between runs, and a browser launched against the old port
    then fails every subsequent load with ERR_PROXY_CONNECTION_FAILED — which
    looks like the site refusing us rather than a stale socket. Reading at
    launch time is what lets the relaunch-on-proxy-error path below recover.
    """
    return (os.environ.get("BROWSER_PROXY")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy") or "")
HEADFUL = os.environ.get("HEADFUL") == "1"
# Use a real installed browser instead of Playwright's bundled Chromium:
# BROWSER_CHANNEL=chrome (or chrome-beta, msedge). Worth doing from a laptop
# rather than a datacenter runner — SmileWanted is requested on every auction
# here and essentially never bids, and a residential IP + a real Chrome build
# is the most likely way to see its demand at all.
BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL") or ""
# Playwright's default context is already incognito-equivalent (throwaway
# profile, no cookies, history or extensions). INCOGNITO=1 additionally passes
# Chrome's own --incognito flag.
INCOGNITO = os.environ.get("INCOGNITO") == "1"
# "accept" (default) dismisses the consent banner before the scroll pass;
# "decline" leaves it up. Comparing the two separates "this creative is
# broken" from "this creative won't render without consent".
CONSENT = (os.environ.get("CONSENT") or "accept").lower()
# Capture throughput knobs. The default dwell exists so Active View's 1s
# clock can run; when the goal is catching a rare bidder's RENDER rather than
# measuring viewability, a shorter dwell buys far more page loads per hour.
SCROLL_STEPS = int(os.environ.get("SCROLL_STEPS") or "24")
SCROLL_DWELL = float(os.environ.get("SCROLL_DWELL") or "1.6")
# Write results after every load so a long sweep survives being interrupted.
INCREMENTAL = os.environ.get("INCREMENTAL", "1") == "1"

# Article URLs look like /slug-1234567 — the homepage also links sections and
# live blogs, which run a different slot set, so match the numeric id suffix.
_ARTICLE_RE = re.compile(r"^https://www\.newsweek\.com/[a-z0-9-]+-\d{6,}$")

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

PROFILE_CFG = {
    "mobile": dict(viewport={"width": 390, "height": 844}, is_mobile=True,
                   has_touch=True, user_agent=IPHONE_UA),
    "desktop": dict(viewport={"width": 1440, "height": 900}, is_mobile=False,
                    has_touch=False, user_agent=DESKTOP_UA),
}

# Installed before any page script: GPT and Prebid both queue callbacks, so
# registering from `cmd`/`que` works no matter how early we run.
INIT_JS = r"""
window.__nwf = {gpt: [], pb: [], viz: {}, t0: Date.now()};
(function () {
  const push = (bucket, o) => { o.t = Date.now() - window.__nwf.t0; window.__nwf[bucket].push(o); };
  window.googletag = window.googletag || {cmd: []};
  googletag.cmd.push(function () {
    const pa = googletag.pubads();
    pa.addEventListener('slotRenderEnded', e => push('gpt', {
      type: 'slotRenderEnded', slot: e.slot.getSlotElementId(),
      unit: e.slot.getAdUnitPath(), empty: e.isEmpty,
      size: JSON.stringify(e.size), lineItemId: e.lineItemId,
      creativeId: e.creativeId, advertiserId: e.advertiserId,
      sourceAgnosticLineItemId: e.sourceAgnosticLineItemId,
      // hb_* targeting on the slot names the Prebid winner GAM actually served
      hb: (function () {
        const o = {};
        try { (e.slot.getTargetingKeys() || []).forEach(k => {
          if (k.indexOf('hb_') === 0) o[k] = e.slot.getTargeting(k).join(',');
        }); } catch (_) {}
        return o;
      })()
    }));
    pa.addEventListener('slotOnload', e => push('gpt', {type: 'slotOnload', slot: e.slot.getSlotElementId()}));
    // Active View's own verdict, client-side: GPT fires this when the MRC
    // criteria are met for the slot (50% for 1s, 30% for large creatives).
    pa.addEventListener('impressionViewable', e => push('gpt', {type: 'impressionViewable', slot: e.slot.getSlotElementId()}));
    pa.addEventListener('slotVisibilityChanged', e => {
      const id = e.slot.getSlotElementId();
      const v = window.__nwf.viz[id] || (window.__nwf.viz[id] = []);
      v.push([Date.now() - window.__nwf.t0, e.inViewPercentage]);
    });
  });
  // In-stream video does NOT render in a GPT slot — it plays in the page's
  // IMA player container — so slot forensics can't see it at all. Sample the
  // player's geometry and in-view ratio on a timer instead; the pbjs bidWon
  // event for the 'video' ad unit names the bidder.
  window.__nwf.player = {max: 0, samples: 0, box: null};
  // One resolver, exported on __nwf so the inspect pass reads the SAME
  // element: sampling geometry off one node while listing iframes off
  // another produces a record describing two different things.
  // Preference order matters and a comma-list can't express it —
  // querySelector returns the first match in DOCUMENT order, not selector
  // order, which is how an earlier version ended up measuring the player
  // shell (<mux-player>, no ad iframes in it) instead of the ad container.
  window.__nwf.pickPlayer = function () {
    return document.querySelector('.nw-ima-ad-container, [id*="ima-ad"], [class*="ima-ad"]')
        || document.querySelector('[id*="video-player"], [class*="video-player"]');
  };
  setInterval(function () {
    const el = window.__nwf.pickPlayer();
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const vis = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0)) *
                Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
    const pct = Math.round(vis / (r.width * r.height) * 100);
    const p = window.__nwf.player;
    p.samples += 1;
    if (pct > p.max) p.max = pct;
    p.box = {w: Math.round(r.width), h: Math.round(r.height)};
    p.sel = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
            (el.className ? '.' + String(el.className).slice(0, 40) : '');
  }, 500);
  // WHO hides the ad iframe? The end state (0x0 display:none) doesn't say
  // whether the creative's own script did it, GPT collapsed an empty render,
  // or a slot refresh orphaned the frame — and the publisher-side fix is
  // different for each. Patch the style/attribute paths to grab a stack at
  // the moment an AV-measured frame is hidden or zeroed. Purely
  // observational: every patch calls through to the original, and any
  // failure is swallowed so the page behaves normally either way.
  window.__nwf.hides = [];
  (function () {
    const isAdFrame = el => {
      try { return el && el.tagName === 'IFRAME' && /^google_ads_iframe_/.test(el.id || ''); }
      catch (_) { return false; }
    };
    const note = (el, how, value) => {
      try {
        window.__nwf.hides.push({
          t: Date.now() - window.__nwf.t0, id: (el.id || '').slice(0, 80),
          how: how, value: String(value).slice(0, 40),
          slot: (function () { let n = el.parentElement;
            while (n) { if (/^dfp-ad-/.test(n.id || '')) return n.id; n = n.parentElement; }
            return null; })(),
          // The stack names the script that did it — vendor CDN vs GPT vs ours.
          stack: (new Error()).stack.split('\n').slice(1, 7).join(' | ').slice(0, 700)
        });
      } catch (_) {}
    };
    const hiding = (prop, val) => {
      const v = String(val).trim().toLowerCase();
      return (prop === 'display' && v === 'none') || (prop === 'visibility' && v === 'hidden') ||
             ((prop === 'width' || prop === 'height') && (v === '0' || v === '0px'));
    };
    try {
      const sp = CSSStyleDeclaration.prototype.setProperty;
      CSSStyleDeclaration.prototype.setProperty = function (prop, val) {
        try { if (hiding(prop, val)) {
          const el = this.parentRule ? null : (this.__nwfEl || null);
          if (isAdFrame(el)) note(el, 'style.setProperty:' + prop, val);
        } } catch (_) {}
        return sp.apply(this, arguments);
      };
      // el.style.display = 'none' goes through the accessor, not setProperty.
      ['display', 'visibility', 'width', 'height'].forEach(function (prop) {
        const d = Object.getOwnPropertyDescriptor(CSSStyleDeclaration.prototype, prop);
        if (!d || !d.set) return;
        Object.defineProperty(CSSStyleDeclaration.prototype, prop, Object.assign({}, d, {
          set: function (val) {
            try { if (hiding(prop, val) && isAdFrame(this.__nwfEl)) {
              note(this.__nwfEl, 'style.' + prop, val);
            } } catch (_) {}
            return d.set.call(this, val);
          }
        }));
      });
      // CSSStyleDeclaration doesn't expose its element, so tag it on access.
      const sd = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'style');
      if (sd && sd.get) {
        Object.defineProperty(HTMLElement.prototype, 'style', Object.assign({}, sd, {
          get: function () { const st = sd.get.call(this);
            try { st.__nwfEl = this; } catch (_) {} return st; }
        }));
      }
      const sa = Element.prototype.setAttribute;
      Element.prototype.setAttribute = function (name, val) {
        try {
          if (isAdFrame(this)) {
            const n = String(name).toLowerCase();
            if ((n === 'width' || n === 'height') && String(val).trim() === '0') {
              note(this, 'setAttribute:' + n, val);
            } else if (n === 'style' && /display\s*:\s*none|visibility\s*:\s*hidden/i.test(String(val))) {
              note(this, 'setAttribute:style', val);
            }
          }
        } catch (_) {}
        return sa.apply(this, arguments);
      };
    } catch (_) {}
  })();
  window.pbjs = window.pbjs || {que: []};
  window.__nwf.auction = {};   // bidder -> {requested, bid, noBid, timeout, error, cpms:[]}
  pbjs.que.push(function () {
    const tally = (b, k, cpm) => {
      if (!b) return;
      const a = window.__nwf.auction[b] || (window.__nwf.auction[b] =
        {requested: 0, bid: 0, noBid: 0, timeout: 0, error: 0, cpms: []});
      a[k] += 1;
      if (cpm != null) a.cpms.push(Number(cpm));
    };
    try {
      pbjs.onEvent('bidRequested', d => tally(d && d.bidderCode, 'requested'));
      pbjs.onEvent('bidResponse', d => tally(d && d.bidderCode, 'bid', d && d.cpm));
      pbjs.onEvent('noBid', d => tally(d && (d.bidder || d.bidderCode), 'noBid'));
      pbjs.onEvent('bidTimeout', d => (Array.isArray(d) ? d : [d]).forEach(
        x => tally(x && (x.bidder || x.bidderCode), 'timeout')));
      pbjs.onEvent('bidderError', d => tally(
        d && d.bidderRequest && d.bidderRequest.bidderCode, 'error'));
    } catch (_) {}
    // On every win, immediately record the slot's contents and any large
    // node that is NOT inside an ad slot. For a 0x0/display:none iframe this
    // is what separates "the unit rendered somewhere else" (a breakout, i.e.
    // a measurement problem) from "nothing rendered at all" (an impression
    // counted with no ad, a different and worse problem).
    window.__nwf.snaps = [];
    try {
      pbjs.onEvent('bidWon', function (b) {
        setTimeout(function () {
          const code = b && b.adUnitCode;
          const div = code && document.getElementById(code);
          const bx = el => { const r = el.getBoundingClientRect();
            return {w: Math.round(r.width), h: Math.round(r.height),
                    y: Math.round(r.y + scrollY)}; };
          const slotEls = [...document.querySelectorAll('[id^="dfp-ad-"]')];
          const big = [...document.querySelectorAll('body *')].filter(el => {
            const c = getComputedStyle(el);
            if (!['fixed', 'absolute', 'sticky'].includes(c.position)) return false;
            if (c.display === 'none' || c.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            if (r.width * r.height < 20000) return false;
            return !slotEls.some(s => s.contains(el));
          }).slice(0, 25).map(el => ({
            tag: el.tagName.toLowerCase(), id: (el.id || '').slice(0, 50),
            cls: String(el.className || '').slice(0, 70), box: bx(el),
            position: getComputedStyle(el).position,
            iframes: el.querySelectorAll('iframe').length,
            srcs: [...el.querySelectorAll('iframe')].map(
              f => (f.getAttribute('src') || f.getAttribute('name') || '').slice(0, 90))
          }));
          window.__nwf.snaps.push({
            t: Date.now() - window.__nwf.t0,
            bidder: b && b.bidder, code: code,
            slotHTML: div ? div.innerHTML.slice(0, 1200) : null,
            slotBox: div ? bx(div) : null,
            iframes: div ? [...div.querySelectorAll('iframe')].map(f => ({
              // id is decisive: google_ads_iframe_* is the GPT-served frame
              // Active View measures; anything else is a vendor frame.
              id: (f.id || '').slice(0, 80), gpt: /^google_ads_iframe_/.test(f.id || ''),
              box: bx(f), display: getComputedStyle(f).display,
              src: (f.getAttribute('src') || '').slice(0, 90)})) : [],
            bodyBig: big
          });
        }, 400);   // let the creative's own JS run before looking
      });
    } catch (_) {}
    ['bidWon', 'adRenderSucceeded', 'adRenderFailed', 'bidderError'].forEach(function (ev) {
      try {
        pbjs.onEvent(ev, function (d) {
          push('pb', {type: ev, code: (d && (d.adUnitCode || (d.bid && d.bid.adUnitCode))) || null,
                      bidder: (d && (d.bidder || (d.bid && d.bid.bidder))) || null,
                      cpm: (d && (d.cpm || (d.bid && d.bid.cpm))) || null,
                      size: (d && (d.size || (d.bid && d.bid.size))) || null,
                      mediaType: (d && (d.mediaType || (d.bid && d.bid.mediaType))) || null,
                      creativeId: (d && (d.creativeId || (d.bid && d.bid.creativeId))) || null});
        });
      } catch (_) {}
    });
  });
})();
"""

# Runs after the scroll pass. Everything here is same-realm DOM inspection —
# cross-origin iframe interiors are deliberately not touched.
INSPECT_JS = r"""
() => {
  const rect = el => { const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y + scrollY), w: Math.round(r.width), h: Math.round(r.height)}; };
  const sty = el => { const c = getComputedStyle(el);
    return {display: c.display, visibility: c.visibility, opacity: c.opacity,
            position: c.position, zIndex: c.zIndex, overflow: c.overflow}; };
  const vis = b => b.w > 1 && b.h > 1;

  const slots = [];
  const gslots = (window.googletag && googletag.pubads) ? googletag.pubads().getSlots() : [];
  gslots.forEach(s => {
    const id = s.getSlotElementId();
    const div = document.getElementById(id);
    if (!div) { slots.push({slot: id, unit: s.getAdUnitPath(), missing: true}); return; }
    const dbox = rect(div);
    const iframes = [...div.querySelectorAll('iframe')].map(f => ({
      id: f.id || null, name: (f.getAttribute('name') || '').slice(0, 80),
      src: (f.getAttribute('src') || '').slice(0, 120),
      sandbox: f.getAttribute('sandbox') || null,
      // GPT names SafeFrame containers with an sf channel; friendly iframes don't.
      safeframe: /sfchannel|safeframe/i.test((f.getAttribute('name') || '') + (f.getAttribute('src') || '')),
      box: rect(f), style: sty(f)
    }));
    slots.push({
      slot: id, unit: s.getAdUnitPath(), box: dbox, style: sty(div),
      childTags: [...div.children].map(c => c.tagName.toLowerCase() + (c.id ? '#' + c.id : '')).slice(0, 8),
      iframes,
      // The breakout signature: the slot holds an iframe, but the iframe is
      // hidden/collapsed (Mobkoi) or far smaller than the slot well.
      iframeHidden: iframes.length > 0 && iframes.every(f => f.style.display === 'none' ||
                       f.style.visibility === 'hidden' || !vis(f.box)),
      iframeFillsSlot: iframes.some(f => vis(f.box) && vis(dbox) &&
                       Math.abs(f.box.w - dbox.w) <= 4 && Math.abs(f.box.h - dbox.h) <= 4),
      // Slot well grown far beyond the iframe = something else props it open.
      slotTallerThanIframe: iframes.some(f => vis(dbox) && dbox.h - f.box.h > 60)
    });
  });

  // Parent-DOM residents: large fixed/absolute/sticky elements that are NOT
  // inside any ad slot. This is what a breakout renderer leaves behind.
  const slotEls = gslots.map(s => document.getElementById(s.getSlotElementId())).filter(Boolean);
  const inSlot = el => slotEls.some(s => s.contains(el));
  const overlays = [...document.querySelectorAll('body *')].filter(el => {
    const c = getComputedStyle(el);
    if (!['fixed', 'absolute', 'sticky'].includes(c.position)) return false;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < 40000) return false;   // ignore chrome/small UI
    return !inSlot(el);
  }).slice(0, 40).map(el => ({
    tag: el.tagName.toLowerCase(), id: (el.id || '').slice(0, 60),
    cls: (el.className || '').toString().slice(0, 80),
    box: rect(el), style: sty(el),
    iframes: el.querySelectorAll('iframe').length
  }));

  const scripts = [...document.querySelectorAll('script[src]')]
    .map(s => { try { return new URL(s.src).hostname; } catch (_) { return null; } })
    .filter(Boolean);

  return {
    url: location.href, viewport: {w: innerWidth, h: innerHeight},
    docHeight: document.documentElement.scrollHeight,
    slots, overlays,
    scriptHosts: [...new Set(scripts)],
    player: (function () {
      const p = (window.__nwf && window.__nwf.player) || null;
      const el = (window.__nwf && window.__nwf.pickPlayer) ? window.__nwf.pickPlayer() : null;
      return Object.assign({}, p, el ? {
        iframes: [...el.querySelectorAll('iframe')].map(f => ({box: rect(f), style: sty(f)})),
        videos: [...el.querySelectorAll('video')].map(v => ({box: rect(v), style: sty(v)})),
        inSlot: false
      } : {});
    })(),
    auction: window.__nwf ? window.__nwf.auction : {},
    snaps: window.__nwf ? window.__nwf.snaps : [],
    hides: window.__nwf ? window.__nwf.hides : [],
    gpt: window.__nwf ? window.__nwf.gpt : [],
    pb: window.__nwf ? window.__nwf.pb : [],
    viz: window.__nwf ? window.__nwf.viz : {}
  };
}
"""


def _launch(pw):
    args = ["--no-sandbox", "--disable-dev-shm-usage",
            # The session's egress proxy re-terminates TLS and rejects
            # Chromium's TLS 1.3 ClientHello; capping the version keeps
            # certificate verification fully on. Harmless off-proxy.
            "--ssl-version-max=tls1.2"]
    if INCOGNITO:
        args.append("--incognito")
    kw = {"headless": not HEADFUL, "args": args}
    if BROWSER_CHANNEL:
        # channel and executable_path are mutually exclusive; channel wins.
        kw["channel"] = BROWSER_CHANNEL
    elif CHROME_PATH:
        kw["executable_path"] = CHROME_PATH
    proxy = _current_proxy()
    if proxy:
        kw["proxy"] = {"server": proxy}
    return pw.chromium.launch(**kw)


def _article_urls(browser, want: int) -> list[str]:
    """Scrape fresh article links off the homepage. Article inventory is the
    only place these bidders buy, so never fall back to the homepage itself."""
    if ARTICLE_URLS:
        return (ARTICLE_URLS * ((want // len(ARTICLE_URLS)) + 1))[:want]
    ctx = browser.new_context(**PROFILE_CFG["desktop"])
    pg = ctx.new_page()
    urls: list[str] = []
    try:
        pg.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(4)
        hrefs = pg.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)")
        seen = set()
        for h in hrefs:
            if _ARTICLE_RE.match(h) and h not in seen:
                seen.add(h)
                urls.append(h)
    except Exception as exc:  # homepage hiccup shouldn't kill the run
        print(f"[warn] could not scrape article links: {exc}")
    finally:
        ctx.close()
    if not urls:
        raise SystemExit("no article URLs found — pass ARTICLE_URLS=…")
    print(f"[info] {len(urls)} article URLs scraped; using {min(want, len(urls))}")
    return (urls * ((want // len(urls)) + 1))[:want]


def _run_one(browser, url: str, profile: str, idx: int) -> dict:
    ctx = browser.new_context(**PROFILE_CFG[profile])
    ctx.add_init_script(INIT_JS)
    pg = ctx.new_page()
    hosts: Counter = Counter()
    pg.on("request", lambda r: hosts.update([urlparse(r.url).netloc]))
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as exc:
        print(f"[warn] {profile} {url}: {exc}")
        ctx.close()
        # Surface proxy failures distinctly: they mean the browser is pointed
        # at a dead socket, which no amount of retrying the URL will fix.
        if "ERR_PROXY_CONNECTION_FAILED" in str(exc) or "ERR_TUNNEL" in str(exc):
            return {"_proxy_error": True}
        return {}
    time.sleep(6)
    # Consent matters for what renders: a creative that refuses to paint
    # without consent looks identical to one that is simply broken. Accept
    # by default so the run resembles a consenting user; CONSENT=decline
    # leaves the banner up, which is what makes the two distinguishable.
    consent_clicked = False
    consent_controls: list[str] = []
    try:
        for el in pg.query_selector_all("[id*='ketch'] button, [id*='ketch'] a,"
                                        "[class*='cmp'] button, [id*='onetrust'] button"):
            t = (el.inner_text() or "").strip()
            if t:
                consent_controls.append(t[:40])
    except Exception:
        pass
    if CONSENT == "accept":
        for sel in ("#ketch-banner button", "[id*='ketch'] button",
                    "button[title*='Accept' i]", "button"):
            try:
                for btn in pg.query_selector_all(sel)[:12]:
                    txt = (btn.inner_text() or "").strip().lower()
                    if txt in ("accept all", "accept", "i agree", "agree",
                               "got it", "ok", "allow all", "accept cookies"):
                        btn.click(timeout=2000)
                        consent_clicked = True
                        break
            except Exception:
                pass
            if consent_clicked:
                break
        time.sleep(3)
    # Scroll the article in viewport-ish steps with dwell time: lazy slots need
    # to enter view, and Active View needs the ad on screen for >=1s to count.
    step = PROFILE_CFG[profile]["viewport"]["height"] // 2
    shot_n = 0
    seen_snaps = 0
    for _ in range(SCROLL_STEPS):
        pg.mouse.wheel(0, step)
        time.sleep(SCROLL_DWELL)
        # Catch a target bidder's render while it is still on screen — the
        # end-of-scroll pass would only see whatever survived.
        try:
            snaps = pg.evaluate("() => (window.__nwf && window.__nwf.snaps) || []")
        except Exception:
            continue
        if len(snaps) > seen_snaps:
            for sn in snaps[seen_snaps:]:
                if (sn.get("bidder") or "").lower() in TARGET_BIDDERS:
                    shot_n += 1
                    try:
                        pg.screenshot(path=str(
                            SHOTS / f"{idx:02d}-{profile}-win{shot_n}-"
                                    f"{sn['bidder']}-{sn.get('code', 'x')}.png"))
                    except Exception:
                        pass
            seen_snaps = len(snaps)
    time.sleep(4)
    try:
        data = pg.evaluate(INSPECT_JS)
    except Exception as exc:
        print(f"[warn] inspect failed on {url}: {exc}")
        data = {}
    if data:
        data["profile"] = profile
        data["consent"] = CONSENT
        data["consentClicked"] = consent_clicked
        data["consentControls"] = consent_controls
        data["reqHosts"] = hosts.most_common(30)
        try:
            shot = SHOTS / f"{idx:02d}-{profile}.png"
            pg.screenshot(path=str(shot), full_page=False)
            data["screenshot"] = str(shot)
        except Exception:
            pass
    ctx.close()
    return data


def _renders(page: dict) -> list[dict]:
    """One record per slot that actually rendered something, joining the GPT
    render event, the Prebid winner, the AV verdict and the DOM geometry."""
    gpt = page.get("gpt") or []
    pb = page.get("pb") or []
    viz = page.get("viz") or {}
    slots = {s["slot"]: s for s in page.get("slots") or [] if "slot" in s}

    won: dict[str, dict] = {}
    for e in pb:
        if e.get("type") == "bidWon" and e.get("code"):
            won[e["code"]] = e
    viewable = {e["slot"] for e in gpt if e.get("type") == "impressionViewable"}

    out = []
    for e in gpt:
        if e.get("type") != "slotRenderEnded" or e.get("empty"):
            continue
        sid = e["slot"]
        hb = e.get("hb") or {}
        w = won.get(sid) or {}
        series = viz.get(sid) or []
        dom = slots.get(sid)
        # AV's own threshold: 50% of pixels for 1s, but 30% for creatives
        # larger than 242,500px² (a full-height in-article unit is "large").
        area, thresh, met = None, None, None
        ifr = [f for f in ((dom or {}).get("iframes") or [])
               if (f.get("box") or {}).get("w", 0) > 1 and (f.get("box") or {}).get("h", 0) > 1]
        if ifr:
            big = max(ifr, key=lambda f: f["box"]["w"] * f["box"]["h"])
            area = big["box"]["w"] * big["box"]["h"]
            thresh = 30 if area > 242_500 else 50
        max_in_view = max([p[1] for p in series], default=None)
        if thresh is not None and max_in_view is not None:
            met = max_in_view >= thresh
        out.append({
            "url": page.get("url"), "profile": page.get("profile"),
            "slot": sid, "unit": e.get("unit"), "size": e.get("size"),
            # pbjs bidWon only fires when the Prebid creative actually
            # rendered, so it is the trustworthy attribution. hb_bidder
            # targeting only says who won the *client* auction — GAM may
            # still have served AdX or a direct line over it.
            "bidder": (w.get("bidder") or hb.get("hb_bidder") or "").lower() or None,
            "attribution": "pbjs_bidWon" if w.get("bidder") else (
                "hb_targeting" if hb.get("hb_bidder") else "none"),
            "pb_bidder": (w.get("bidder") or "").lower() or None,
            "hb_bidder": hb.get("hb_bidder"),
            "cpm": w.get("cpm"), "mediaType": w.get("mediaType"),
            "lineItemId": e.get("lineItemId"), "creativeId": e.get("creativeId"),
            "advertiserId": e.get("advertiserId"),
            "viewable": sid in viewable,
            "maxInView": max_in_view,
            "creativeArea": area, "avThresholdPct": thresh, "metThreshold": met,
            "dom": dom,
        })
    return out


def _fmt_slot(d: dict | None) -> str:
    if not d:
        return "no DOM record"
    b = d.get("box") or {}
    ifr = d.get("iframes") or []
    head = f"slot {b.get('w')}x{b.get('h')} @y={b.get('y')} display={d.get('style', {}).get('display')}"
    if not ifr:
        return head + " | NO IFRAME in slot"
    parts = []
    for f in ifr[:3]:
        fb, fs = f.get("box") or {}, f.get("style") or {}
        parts.append(f"iframe {fb.get('w')}x{fb.get('h')} display={fs.get('display')} "
                     f"vis={fs.get('visibility')} sf={'Y' if f.get('safeframe') else 'N'}")
    flags = []
    if d.get("iframeHidden"):
        flags.append("IFRAME HIDDEN/COLLAPSED")
    if d.get("iframeFillsSlot"):
        flags.append("iframe==slot")
    if d.get("slotTallerThanIframe"):
        flags.append("slot >> iframe")
    return head + " | " + " ; ".join(parts) + (" | " + ", ".join(flags) if flags else "")


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    pages: list[dict] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        urls = _article_urls(browser, LOADS)
        for i, url in enumerate(urls):
            for profile in PROFILES:
                print(f"[load {i + 1}/{len(urls)} {profile}] {url}")
                page = _run_one(browser, url, profile, i)
                if page.get("_proxy_error"):
                    # Relaunch against whatever port the proxy is on now and
                    # retry this load once, so a mid-sweep port move costs one
                    # page instead of silently ending the run.
                    print(f"    [proxy moved] relaunching browser on {_current_proxy()}")
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser = _launch(pw)
                    page = _run_one(browser, url, profile, i)
                if not page or page.get("_proxy_error"):
                    continue
                pages.append(page)
                hits = [r for r in _renders(page)
                        if (r.get("bidder") or "") in TARGET_BIDDERS]
                for h in hits:
                    blank = (h.get("dom") or {}).get("iframeHidden")
                    print(f"    *** CAPTURED {h['bidder']} on {h['slot']} "
                          f"({'BLANK' if blank else 'rendered'})")
                if INCREMENTAL:
                    try:
                        (SHOTS / "renders.json").write_text(json.dumps(
                            [r for p in pages for r in _renders(p)], indent=1))
                    except Exception:
                        pass
        browser.close()

    renders = [r for p in pages for r in _renders(p)]
    (SHOTS / "renders.json").write_text(json.dumps(renders, indent=1))

    print("\n" + "=" * 78)
    print("PREBID RENDER FORENSICS")
    print("=" * 78)
    print(f"{len(pages)} page loads, {len(renders)} non-empty slot renders, "
          f"targets={','.join(TARGET_BIDDERS)}")
    ok_consent = sum(1 for p in pages if p.get("consentClicked"))
    ctrls = sorted({c for p in pages for c in (p.get("consentControls") or [])})
    print(f"consent mode={CONSENT}, banner dismissed on {ok_consent}/{len(pages)} loads")
    if ctrls:
        print(f"  consent controls present: {', '.join(ctrls)}")
        if not any(re.search(r"accept|agree|allow|got it", c, re.I) for c in ctrls):
            # Not a failed click: there is nothing to accept. A US state
            # privacy NOTICE permits processing by default, so "no consent"
            # is not a reason a creative would refuse to render.
            print("  -> NOTICE-ONLY banner (no accept control): consent is not "
                  "being withheld, so it cannot explain a creative failing to render")

    # ── viewability + render mode by bidder ──────────────────────────────
    by_bidder: dict[str, list[dict]] = defaultdict(list)
    for r in renders:
        by_bidder[r["bidder"] or "(unknown)"].append(r)
    print("\n-- observed renders by bidder (GPT impressionViewable = AV's own verdict) --")
    print(f"{'bidder':<16}{'renders':>8}{'viewable':>9}{'iframe==slot':>13}"
          f"{'HIDDEN':>8}{'med px h':>9}{'med maxInView':>14}")
    for bidder, rs in sorted(by_bidder.items(), key=lambda kv: -len(kv[1])):
        vw = sum(1 for r in rs if r["viewable"])
        fills = sum(1 for r in rs if (r.get("dom") or {}).get("iframeFillsSlot"))
        hid = sum(1 for r in rs if (r.get("dom") or {}).get("iframeHidden"))
        heights = sorted(max((f.get("box") or {}).get("h", 0)
                             for f in ((r.get("dom") or {}).get("iframes") or [{}]))
                         for r in rs)
        views = sorted(r["maxInView"] for r in rs if r["maxInView"] is not None)
        mh = heights[len(heights) // 2] if heights else "-"
        mv = views[len(views) // 2] if views else "-"
        print(f"{bidder:<16}{len(rs):>8}{vw:>9}{fills:>13}{hid:>8}{mh:>9}{mv:>14}")

    # ── slot mix: which positions does each bidder win? ──────────────────
    print("\n-- slot mix by bidder (placement drives viewability as much as render) --")
    for bidder, rs in sorted(by_bidder.items(), key=lambda kv: -len(kv[1])):
        mix = Counter(r["slot"] for r in rs)
        print(f"  {bidder:<16} {', '.join(f'{k}×{v}' for k, v in mix.most_common(8))}")

    # ── per-render detail for the targets ────────────────────────────────
    print("\n" + "=" * 78)
    print("TARGET BIDDER RENDERS (full detail)")
    print("=" * 78)
    hits = [r for r in renders if (r["bidder"] or "") in TARGET_BIDDERS]
    if not hits:
        print("None of the target bidders won a slot in this sample. They are a "
              "few % of impressions each, so raise LOADS or re-run; every render "
              "is still in renders.json.")
    for r in hits:
        print(f"\n[{r['profile']}] {r['slot']} ({r['unit']}) — {r['bidder']} "
              f"cpm={r['cpm']} size={r['size']} mediaType={r['mediaType']}")
        print(f"  LI {r['lineItemId']} creative {r['creativeId']} "
              f"(advertiser {r['advertiserId']}, attribution={r['attribution']})")
        print(f"  impressionViewable={'YES' if r['viewable'] else 'no'} | "
              f"max in-view={r['maxInView']}% vs AV threshold "
              f"{r['avThresholdPct']}% (creative area {r['creativeArea']}px²) "
              f"-> {'met' if r['metThreshold'] else 'NEVER MET' if r['metThreshold'] is False else 'n/a'}")
        print(f"  {_fmt_slot(r.get('dom'))}")
        print(f"  {r['url']}")

    # ── win-time snapshots ───────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("WIN-TIME DOM SNAPSHOTS (target bidders, captured 400ms after bidWon)")
    print("=" * 78)
    any_snap = False
    for p in pages:
        for sn in p.get("snaps") or []:
            if (sn.get("bidder") or "").lower() not in TARGET_BIDDERS:
                continue
            any_snap = True
            sb = sn.get("slotBox") or {}
            print(f"\n[{p.get('profile')}] {sn.get('bidder')} -> {sn.get('code')} "
                  f"at t={sn.get('t')}ms, slot {sb.get('w')}x{sb.get('h')}")
            for f in sn.get("iframes") or []:
                tag = "GPT/AV-measured" if f.get("gpt") else "vendor"
                print(f"    iframe [{tag}] {f['box']['w']}x{f['box']['h']} "
                      f"display={f['display']} id={f.get('id') or '-'}")
            big = sn.get("bodyBig") or []
            # The decisive line: with a hidden slot iframe, is there anything
            # big outside the slots that could BE the unit?
            print(f"    {len(big)} large node(s) outside ad slots at win time:")
            for b in big[:10]:
                print(f"      {b['tag']}#{b['id'] or '-'} .{(b['cls'] or '-')[:38]} "
                      f"{b['box']['w']}x{b['box']['h']} {b['position']} "
                      f"iframes={b['iframes']} {';'.join(b.get('srcs') or [])[:70]}")
            html = (sn.get("slotHTML") or "").strip()
            print(f"    slot innerHTML ({len(html)} chars): {html[:300] or '(EMPTY)'}")
    if not any_snap:
        print("no target-bidder wins in this sample")

    # ── who hid the ad iframe ────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("WHO HID THE AV-MEASURED IFRAME (stack at the hiding call)")
    print("=" * 78)
    hides = [(p, h) for p in pages for h in (p.get("hides") or [])]
    if not hides:
        print("no ad iframe was hidden or zeroed in this sample")
    for p, h in hides:
        print(f"\n[{p.get('profile')}] t={h.get('t')}ms {h.get('slot')} "
              f"via {h.get('how')}={h.get('value')}")
        print(f"    frame: {h.get('id')}")
        for line in (h.get("stack") or "").split(" | "):
            if line.strip():
                print(f"    {line.strip()[:140]}")

    # ── auction outcomes: why a bidder never renders ─────────────────────
    print("\n" + "=" * 78)
    print("AUCTION OUTCOMES BY BIDDER (a bidder absent from the tables above is")
    print("explained here: not bidding vs bidding-and-losing vs erroring)")
    print("=" * 78)
    tot: dict[str, dict] = defaultdict(
        lambda: {"requested": 0, "bid": 0, "noBid": 0, "timeout": 0, "error": 0, "cpms": []})
    for p in pages:
        for b, a in (p.get("auction") or {}).items():
            for k in ("requested", "bid", "noBid", "timeout", "error"):
                tot[b][k] += a.get(k, 0)
            tot[b]["cpms"].extend(a.get("cpms") or [])
    wins = Counter(r["bidder"] for r in renders if r["bidder"])
    print(f"{'bidder':<18}{'requested':>10}{'bids':>7}{'noBid':>7}{'timeout':>9}"
          f"{'error':>7}{'wins':>6}{'median cpm':>12}")
    for b, a in sorted(tot.items(), key=lambda kv: -kv[1]["requested"]):
        cpms = sorted(a["cpms"])
        med = f"{cpms[len(cpms) // 2]:.2f}" if cpms else "-"
        print(f"{b:<18}{a['requested']:>10}{a['bid']:>7}{a['noBid']:>7}"
              f"{a['timeout']:>9}{a['error']:>7}{wins.get(b, 0):>6}{med:>12}")
    for b in TARGET_BIDDERS:
        a = tot.get(b)
        if not a:
            print(f"\n  {b}: never even requested — not configured on these slots, "
                  f"or dropped before the auction (consent/geo module).")
        elif a["bid"] == 0:
            print(f"\n  {b}: requested {a['requested']}× and NEVER BID "
                  f"(noBid {a['noBid']}, timeout {a['timeout']}, error {a['error']}). "
                  f"No amount of extra page loads will catch a render — the demand "
                  f"isn't reaching this client. Geo/IP is the first suspect.")
        elif not wins.get(b):
            print(f"\n  {b}: bid {a['bid']}× (median cpm "
                  f"{sorted(a['cpms'])[len(a['cpms']) // 2]:.2f}) but won 0 — "
                  f"losing the auction, so more loads may eventually catch one.")

    # ── in-stream video ──────────────────────────────────────────────────
    # Not a GPT slot, so none of the tables above can contain it. onetag's
    # deficit is on video, so this section is the only place it can surface.
    print("\n" + "=" * 78)
    print("IN-STREAM VIDEO (IMA player — outside every GPT slot)")
    print("=" * 78)
    vid_wins = [e for p in pages for e in (p.get("pb") or [])
                if e.get("type") == "bidWon" and (e.get("mediaType") == "video"
                                                  or e.get("code") == "video")]
    if vid_wins:
        print("pbjs video bidWon: " + ", ".join(
            f"{b}×{n}" for b, n in Counter((e.get("bidder") or "?") for e in vid_wins).most_common()))
    else:
        print("no video bids won in this sample")
    for p in pages:
        pl = p.get("player") or {}
        if not pl.get("box"):
            continue
        print(f"  [{p.get('profile')}] {pl.get('sel', '?')[:60]} "
              f"{pl['box']['w']}x{pl['box']['h']} max in-view={pl.get('max')}% "
              f"({pl.get('samples')} samples) iframes={len(pl.get('iframes') or [])} "
              f"videos={len(pl.get('videos') or [])}")
        break  # geometry is per-profile, not per-page; one example each is enough

    # ── parent-DOM overlays, the breakout tell ───────────────────────────
    print("\n" + "=" * 78)
    print("PARENT-DOM OVERLAYS (large fixed/absolute nodes outside every ad slot)")
    print("=" * 78)
    seen: set[tuple] = set()
    for p in pages:
        for o in p.get("overlays") or []:
            key = (o.get("tag"), o.get("id"), o.get("cls")[:40])
            if key in seen:
                continue
            seen.add(key)
            b = o.get("box") or {}
            print(f"  {o['tag']}#{o.get('id') or '-'} .{(o.get('cls') or '-')[:44]} "
                  f"{b.get('w')}x{b.get('h')} {o.get('style', {}).get('position')} "
                  f"iframes={o.get('iframes')}")
    if not seen:
        print("  none — nothing large renders outside the ad slots")

    print(f"\nrenders.json + screenshots: {SHOTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
