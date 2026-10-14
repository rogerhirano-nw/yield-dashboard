#!/usr/bin/env python3
"""Hunt for off-size Prebid bids and renders on mobile article pages.

Written 2026-10-14 to answer one question: when a partner is blamed for a
size that isn't in the slot's size array (a 300x600 on mobile, reported to
Ozone), did ANY bidder actually bid that size, or did a winner render an
off-size creative after bidding a legal one? See the "Off-size renders"
section of docs/prebid_viewability.md for what it found.

Per article load (mobile emulation, default 440x956 = iPhone 16 Pro Max) it
records every pbjs bidResponse / bidRequested / bidWon (size, bidderCode,
creativeId, meta), GPT slotRenderEnded with the slot's hb_* targeting, the raw
Magnite PBS / Ozone / Ogury POST responses, Ogury (`ogy-*`) DOM nodes with the
size + transform of the creative iframe inside, a screenshot, and a HAR
filtered to auction traffic -- the HAR a partner asks for.

Usage:
    python3 scripts/prebid_size_hunt.py OUT_DIR
    LOADS=14 SKIP=6 SIZE=300x600 python3 scripts/prebid_size_hunt.py OUT_DIR

Env: LOADS (6), HEADFUL (1 = show the browser window), SKIP (0, offset into the scraped article list), SIZE
(300x600), VIEWPORT (440x956), CHROME_PATH (Playwright's own Chromium when
unset), HTTPS_PROXY (passed to the browser when set).
Runs from a datacenter IP sees thinner demand than a real phone: absence of a
bid here is evidence, not proof.
"""
import json, os, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
LOADS = int(os.environ.get("LOADS", "6"))
SKIP = int(os.environ.get("SKIP", "0"))
SW, SH = (int(x) for x in os.environ.get("SIZE", "300x600").split("x"))
VW, VH = (int(x) for x in os.environ.get("VIEWPORT", "440x956").split("x"))
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
CTX = dict(viewport={"width": VW, "height": VH}, is_mobile=True, has_touch=True,
           user_agent=UA, device_scale_factor=3)
ART = re.compile(r"^https://www\.newsweek\.com/[a-z0-9-]+-\d{6,}$")
INIT = r"""
window.__h = {resp: [], won: [], req: [], render: []};
window.pbjs = window.pbjs || {que: []};
pbjs.que.push(function () {
  const pick = b => ({bidderCode: b.bidderCode, bidder: b.bidder, adapterCode: b.adapterCode,
    adUnitCode: b.adUnitCode, w: b.width, h: b.height, size: b.size, cpm: b.cpm,
    mediaType: b.mediaType, creativeId: b.creativeId, dealId: b.dealId, adId: b.adId,
    meta: b.meta, seatBidId: b.seatBidId, ttl: b.ttl, source: b.source,
    adSnippet: (b.ad || '').slice(0, 600)});
  pbjs.onEvent('bidResponse', b => window.__h.resp.push(pick(b)));
  pbjs.onEvent('bidWon', b => window.__h.won.push(pick(b)));
  pbjs.onEvent('bidRequested', r => window.__h.req.push({bidder: r.bidderCode,
    units: (r.bids||[]).map(x => ({code: x.adUnitCode, sizes: JSON.stringify(x.sizes),
      banner: JSON.stringify(((x.mediaTypes||{}).banner||{}).sizes)}))}));
});
window.googletag = window.googletag || {cmd: []};
googletag.cmd.push(() => googletag.pubads().addEventListener('slotRenderEnded', e => {
  const o = {}; try { e.slot.getTargetingKeys().forEach(k => { if (k.startsWith('hb_')) o[k] = e.slot.getTargeting(k).join(','); }); } catch (_) {}
  window.__h.render.push({slot: e.slot.getSlotElementId(), empty: e.isEmpty, size: JSON.stringify(e.size),
    li: e.lineItemId, cr: e.creativeId, hb: o});
}));
"""
DOM = r"""() => [...document.querySelectorAll('[id^="ogy-"], [class*="ogy-"]')].filter(e=>e.tagName==='IFRAME'||/root-container/.test(e.id)).map(e => {
  const r = e.getBoundingClientRect(); let inner = [];
  try { const d = e.contentDocument; if (d) inner = [...d.querySelectorAll('iframe')].map(f => ({style: f.getAttribute('style'), w: f.offsetWidth, h: f.offsetHeight})); } catch (_) {}
  let slot = e.closest('[id^="dfp-ad-"]');
  return {tag: e.tagName, id: e.id.slice(0, 80), cls: String(e.className).slice(0, 120), w: Math.round(r.width), h: Math.round(r.height), slot: slot && slot.id, inner};
})"""

def articles(b):
    c = b.new_context(**CTX); p = c.new_page()
    p.goto("https://www.newsweek.com/", wait_until="domcontentloaded", timeout=60000); time.sleep(4)
    hs = p.eval_on_selector_all("a[href]", "e=>e.map(x=>x.href)"); c.close()
    seen = []
    for h in hs:
        if ART.match(h) and h not in seen: seen.append(h)
    return seen

with sync_playwright() as pw:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    b = pw.chromium.launch(executable_path=os.environ.get("CHROME_PATH") or None,
                           proxy={"server": proxy} if proxy else None,
                           headless=os.environ.get("HEADFUL") != "1")
    urls = articles(b)[SKIP:][:LOADS]
    print(f"{len(urls)} articles", flush=True)
    summary = []
    for i, url in enumerate(urls):
        har = OUT / f"load{i:02d}.har"
        c = b.new_context(**CTX, record_har_path=str(har), record_har_content="embed",
                          record_har_url_filter=re.compile(r"rubiconproject|ozone|ogury|presage|adnxs|openrtb|auction|bid"))
        c.add_init_script(INIT)
        p = c.new_page(); p.set_default_timeout(30000)
        raw = []
        def onresp(r):
            u = r.url
            if any(k in u for k in ("prebid-server.rubiconproject", "ozone", "ogury", "presage")) and r.request.method == "POST":
                try: raw.append({"url": u, "body": r.text()[:200000]})
                except Exception as e: raw.append({"url": u, "err": str(e)})
        p.on("response", onresp)
        try:
            p.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("load fail", url, e); c.close(); continue
        time.sleep(6)
        for btn in p.query_selector_all("button")[:40]:
            try:
                if (btn.inner_text() or "").strip().lower() in ("accept all", "accept", "i agree", "agree", "ok", "got it"):
                    btn.click(timeout=2000); break
            except Exception: pass
        doms = []
        for _ in range(28):
            p.mouse.wheel(0, 478); time.sleep(1.0)
            try:
                d = p.evaluate(DOM)
                if d: doms.append(d)
            except Exception: pass
        time.sleep(2)
        h = p.evaluate("() => window.__h")
        try: p.screenshot(path=str(OUT / f"load{i:02d}.png"))
        except Exception: pass
        c.close()
        rec = {"url": url, "h": h, "raw": raw, "ogy_dom": doms[-1] if doms else [], "ogy_dom_all": doms}
        (OUT / f"load{i:02d}.json").write_text(json.dumps(rec, indent=1, default=str))
        big = [r for r in h["resp"] if (r.get("w"), r.get("h")) == (SW, SH)]
        print(f"[{i}] {url}\n   bids={len(h['resp'])} won={len(h['won'])} {SW}x{SH} bids={len(big)} "
              f"ogyDOM={len(rec['ogy_dom'])} raw={len(raw)}", flush=True)
        for r in big: print(f"   {SW}x{SH}:", r["bidderCode"], r.get("adapterCode"), r["adUnitCode"], r["cpm"], r.get("creativeId"), json.dumps(r.get("meta"))[:200])
        for w in h["won"]: print("   WON:", w["bidderCode"], w.get("adapterCode"), w["adUnitCode"], f"{w['w']}x{w['h']}", w["cpm"], w.get("creativeId"))
        for d in rec["ogy_dom"]: print("   OGY:", d)
    b.close()
