#!/usr/bin/env python3
"""On-page forensics for an interstitial LI: is the ad shown, and does a tap
count as a GAM click? Read-only (a GAM preview never counts delivery).

For each LI:creative pair, mint a SOAP getPreviewUrl on an article (forces the
creative into its slot when the page requests it), load it in headless
Chromium (mobile + desktop), and report:
  - GPT events for every slot: slotRenderEnded (LI / creative / empty),
    slotVisibilityChanged max %, impressionViewable
  - the Innovid tag request as the page actually made it, with its
    `ivc_click_through` decoded — i.e. whether %%CLICK_URL_ESC%% arrived
    expanded to a GAM click URL
  - the interstitial slot div + GAM iframe geometry/visibility over time,
    and what element sits on top at the viewport centre (a cover/overlay)
  - a tap at the centre: every request fired after it (GAM click endpoints
    flagged) and any popup/navigation URL
Screenshots land in /tmp/shots.

Env: PAIRS="li:creative,…" (creative may be "auto"), ARTICLE_URL, ORGANIC=1
also loads the article once with no preview to see what the page does alone.
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gam_client import GAMClient  # noqa: E402

PAIRS = [p.strip() for p in (os.environ.get("PAIRS") or "").split(",") if p.strip()]
ARTICLE_URL = os.environ.get("ARTICLE_URL") or ""
ORGANIC = os.environ.get("ORGANIC", "1") == "1"
WARMUP = int(os.environ.get("WARMUP", "1"))
_SEEN_JS: set = set()
SHOTS = Path("/tmp/shots")
SHOTS.mkdir(parents=True, exist_ok=True)

CLICK_HOSTS = ("adclick.g.doubleclick.net", "googleads.g.doubleclick.net/pcs/click",
               "googleads.g.doubleclick.net/aclk", "pagead2.googlesyndication.com/pagead/iclk",
               "/pcs/click", "/aclk")

PROFILES = {
    "mobile": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                   is_mobile=True, has_touch=True,
                   user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                               "Mobile/15E148 Safari/604.1")),
    "desktop": dict(viewport={"width": 1440, "height": 900}),
}

INIT_JS = r"""
(() => {
  const S = window.__nwi = {events: [], t0: Date.now()};
  const push = (e) => S.events.push(Object.assign({t: Date.now() - S.t0}, e));
  window.googletag = window.googletag || {cmd: []};
  googletag.cmd.push(() => {
    const pa = googletag.pubads();
    const info = (s) => ({path: s.getAdUnitPath(), div: s.getSlotElementId()});
    pa.addEventListener('slotRequested', e => push(Object.assign({ev: 'requested'}, info(e.slot))));
    pa.addEventListener('slotRenderEnded', e => push(Object.assign({ev: 'render',
      empty: e.isEmpty, li: e.lineItemId, cr: e.creativeId, size: e.size && e.size.join('x')},
      info(e.slot))));
    pa.addEventListener('slotOnload', e => push(Object.assign({ev: 'onload'}, info(e.slot))));
    pa.addEventListener('impressionViewable', e => push(Object.assign({ev: 'viewable'}, info(e.slot))));
    const maxv = S.maxVis = {};
    pa.addEventListener('slotVisibilityChanged', e => {
      const k = e.slot.getSlotElementId();
      maxv[k] = Math.max(maxv[k] || 0, e.inViewPercentage);
    });
  });
})();
"""

INSPECT_JS = r"""
() => {
  const trim = (s, n) => (s || '').toString().slice(0, n);
  const box = el => { const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; };
  const st = el => { const c = getComputedStyle(el);
    return {d: c.display, v: c.visibility, o: c.opacity, pos: c.position, z: c.zIndex}; };
  const desc = el => el ? (el.tagName + '#' + trim(el.id, 50) + '.' + trim(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className, 60)) : null;
  const out = {vp: {w: innerWidth, h: innerHeight}, slots: [], innovid: [], overlays: []};
  let slots = [];
  try { slots = googletag.pubads().getSlots(); } catch (e) {}
  for (const s of slots) {
    const div = document.getElementById(s.getSlotElementId());
    const row = {path: s.getAdUnitPath(), div: s.getSlotElementId(),
                 oop: /interstitial|oop|out/i.test(s.getAdUnitPath() + s.getSlotElementId())};
    try { const r = s.getResponseInformation(); if (r) { row.li = r.lineItemId; row.cr = r.creativeId; } } catch (e) {}
    if (div) {
      row.box = box(div); row.style = st(div);
      // ancestors that hide it
      let p = div, hidden = [];
      while (p && p !== document.body) { const c = getComputedStyle(p);
        if (c.display === 'none' || c.visibility === 'hidden' || c.opacity === '0') hidden.push(desc(p) + ' ' + c.display + '/' + c.visibility + '/' + c.opacity);
        p = p.parentElement; }
      row.hiddenAncestors = hidden.slice(0, 5);
      row.iframes = [...div.querySelectorAll('iframe')].map(f => ({id: trim(f.id, 70), box: box(f), style: st(f)}));
    }
    out.slots.push(row);
  }
  // Innovid in the top document (a breakout) and inside same-origin GAM frames
  const scan = (doc, where) => {
    for (const el of doc.querySelectorAll('*')) {
      const s = (el.id + ' ' + (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) + ' ' + (el.src || '')).toLowerCase();
      if (/innovid|ivapps|\bivc|iv_/.test(s)) {
        out.innovid.push({where, el: desc(el), src: trim(el.src, 100), box: box(el), style: st(el)});
        if (out.innovid.length > 30) return;
      }
    }
  };
  scan(document, 'top');
  for (const f of document.querySelectorAll('iframe')) {
    try { if (f.contentDocument) scan(f.contentDocument, 'frame:' + trim(f.id, 50)); } catch (e) {}
  }
  const vw = innerWidth * innerHeight;
  for (const el of document.querySelectorAll('div,iframe,section,aside,dialog')) {
    const c = getComputedStyle(el);
    if (c.position !== 'fixed' && c.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height > vw * 0.25 && c.display !== 'none' && c.visibility !== 'hidden')
      out.overlays.push({el: desc(el), box: box(el), style: st(el)});
  }
  out.overlays = out.overlays.slice(0, 12);
  const cx = innerWidth / 2, cy = innerHeight / 2;
  const top = document.elementFromPoint(cx, cy);
  const chain = []; let p = top;
  while (p && chain.length < 6) { chain.push(desc(p)); p = p.parentElement; }
  out.centerStack = chain;
  const w = document.getElementById('dfp-ad-interstitial-wrapper');
  if (w) out.wrap = {cls: trim(w.className, 120), box: box(w), style: st(w),
    html: trim(w.innerHTML.replace(/\s+/g, ' '), 400)};
  out.ev = (window.__nwi && window.__nwi.events) || [];
  out.maxVis = (window.__nwi && window.__nwi.maxVis) || {};
  return out;
}
"""


def _decode_ivc(url: str) -> dict:
    q = parse_qs(urlparse(url).query)
    ct = (q.get("ivc_click_through") or [""])[0]
    return {"ivc_click_through": ct[:220],
            "looks_expanded": ct.startswith("http") and "%%" not in ct,
            "gam_click_url": any(h in ct for h in CLICK_HOSTS)}


def _run(browser, url: str, profile: str, tag: str) -> None:
    print(f"\n{'=' * 90}\n{tag} [{profile}]\n{'=' * 90}")
    ctx = browser.new_context(**PROFILES[profile])
    ctx.add_init_script(INIT_JS)
    pg = ctx.new_page()
    pg.set_default_timeout(30_000)
    reqs: list[tuple[float, str]] = []
    t0 = time.time()
    ctx.on("request", lambda r: reqs.append((time.time() - t0, r.url)))
    popups: list[str] = []
    ctx.on("page", lambda p: popups.append(p.url))
    # The wrapper carries `dfp-ad-count`: the interstitial is likely gated on
    # pageview count, so burn WARMUP plain pageviews in this context first.
    js_hits: list[str] = []

    def _on_resp(r):
        u = urlparse(r.url)
        if (tag == "organic" and len(js_hits) < 12 and u.path.endswith(".js")
                and "newsweek" in u.netloc and u.path not in _SEEN_JS):
            _SEEN_JS.add(u.path)
            try:
                body = r.text()
            except Exception:
                return
            k = body.find("is-revealed")
            if k >= 0:
                # The reveal gate: print its whole neighbourhood once, so the
                # predicate the site applies before showing the ad is visible.
                print(f"\n[reveal-gate source {u.path}]\n{body[max(0, k - 3500):k + 1800]}\n[/reveal-gate]")
            i = 0
            while len(js_hits) < 12:
                i = body.find("nterstitial", i)
                if i < 0:
                    break
                js_hits.append(f"{urlparse(r.url).path[-40:]}: …{body[max(0, i - 220):i + 260]}…")
                i += 800
    pg.on("response", _on_resp)
    try:
        for w in range(WARMUP):
            pg.goto(ARTICLE_URL, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(5)
            pg.mouse.wheel(0, 1500)
            time.sleep(2)
        t0 = time.time()
        pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:
        print(f"  !! load failed: {e}")
        ctx.close()
        return
    snaps = {}
    for t in (3, 8):
        time.sleep(max(0.0, t - (time.time() - t0)))
        try:
            snaps[t] = pg.evaluate(INSPECT_JS)
            pg.screenshot(path=str(SHOTS / f"{tag}-{profile}-t{t}.png"))
        except Exception as e:
            print(f"  !! inspect t={t}: {e}")
    # Scroll (lazy trigger), then sample again.
    for _ in range(6):
        pg.mouse.wheel(0, 600)
        time.sleep(1.5)
    for t in ("scrolled",):
        try:
            snaps[t] = pg.evaluate(INSPECT_JS)
            pg.screenshot(path=str(SHOTS / f"{tag}-{profile}-scrolled.png"))
        except Exception as e:
            print(f"  !! inspect scrolled: {e}")
    last = snaps.get("scrolled") or snaps.get(8) or snaps.get(3) or {}
    if js_hits:
        print("site JS mentioning 'interstitial':")
        for h in js_hits:
            print("   ", h.replace("\n", " ")[:520])

    print("GPT events (non-empty renders + all interstitial-ish slots):")
    for e in last.get("ev", []):
        if e.get("ev") == "render" and e.get("empty") and "interstitial" not in (e.get("path") or ""):
            continue
        if e.get("ev") in ("requested", "onload") and "interstitial" not in (e.get("path") or ""):
            continue
        print("  ", json.dumps(e))
    print("maxVis %:", json.dumps(last.get("maxVis", {})))

    innov = [u for _, u in reqs if "rtr.innovid.com/js/" in u]
    print(f"\nInnovid tag requests: {len(innov)}")
    for u in innov[:3]:
        print("   ", u[:160])
        print("    ->", json.dumps(_decode_ivc(u)))
    other_iv = sorted({urlparse(u).netloc + urlparse(u).path[:50] for _, u in reqs if "innovid" in u})
    print(f"Innovid hosts/paths hit ({len(other_iv)}):", other_iv[:15])

    for t, s in snaps.items():
        print(f"\n-- t={t}s")
        for row in s.get("slots", []):
            if "interstitial" in (row.get("path", "") + row.get("div", "")).lower() or row.get("li"):
                print("  slot", json.dumps(row)[:700])
        print("  innovid elements:", json.dumps(s.get("innovid", [])[:8])[:1500])
        print("  fixed overlays:", json.dumps(s.get("overlays", []))[:1200])
        print("  element stack at viewport centre:", s.get("centerStack"))
        print("  interstitial wrapper:", json.dumps(s.get("wrap")))

    # Tap the centre and see what fires.
    n_before = len(reqs)
    try:
        vp = PROFILES[profile]["viewport"]
        if PROFILES[profile].get("has_touch"):
            pg.touchscreen.tap(vp["width"] / 2, vp["height"] / 2)
        else:
            pg.mouse.click(vp["width"] / 2, vp["height"] / 2)
        time.sleep(4)
    except Exception as e:
        print(f"  !! tap failed: {e}")
    after = [u for _, u in reqs[n_before:]]
    clicks = [u for u in after if any(h in u for h in CLICK_HOSTS)]
    print(f"\nTAP at centre -> {len(after)} requests, GAM click hits: {len(clicks)}")
    for u in clicks[:5]:
        print("   CLICK", u[:200])
    for u in after[:25]:
        if u not in clicks:
            print("   ", u[:150])
    print("   popups:", popups[:3], " page url now:", pg.url[:150])
    try:
        pg.screenshot(path=str(SHOTS / f"{tag}-{profile}-after-tap.png"))
    except Exception:
        pass
    ctx.close()


def main() -> int:
    if not ARTICLE_URL:
        print("ARTICLE_URL required")
        return 1
    gam = GAMClient()
    svc = gam._get_soap_client().GetService("LineItemCreativeAssociationService",
                                            version=gam._SOAP_API_VERSION)
    autos = [p.split(":")[0] for p in PAIRS if p.split(":")[1] == "auto"]
    lica = gam.list_line_item_creative_associations(autos) if autos else None
    previews = []
    for p in PAIRS:
        li, cr = p.split(":")
        if cr == "auto":
            rows = lica[lica["line_item_id"] == li]
            cr = rows.iloc[0]["creative_id"] if not rows.empty else None
        if not cr:
            continue
        try:
            previews.append((li, cr, svc.getPreviewUrl(int(li), int(cr), ARTICLE_URL)))
        except Exception as e:
            print(f"!! getPreviewUrl {li}:{cr}: {e}")
    print("article:", ARTICLE_URL)
    for li, cr, u in previews:
        print(f"preview {li}:{cr} -> {u[:140]}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        for profile in ("mobile", "desktop"):
            if ORGANIC:
                _run(browser, ARTICLE_URL, profile, "organic")
            for li, cr, u in previews:
                _run(browser, u, profile, f"li{li}-cr{cr}")
        browser.close()
    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
