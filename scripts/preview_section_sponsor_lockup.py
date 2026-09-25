#!/usr/bin/env python3
"""Render the section-hub sponsor lockup creative and check its placement.

Writes docs/snippets/section_sponsor_lockup_creative.html (GAM macros
substituted, the way GAM would) into a same-origin iframe inside
#dfp-ad-oop1 — i.e. exactly what GPT does for a SafeFrame-off out-of-page
creative — then screenshots desktop / tablet / mobile and asserts:
  - the lockup renders, centered under the dek, below it, not overflowing;
  - the GPT iframe was resized to the lockup (what Active View measures);
  - off-template (slot outside the hub header) it renders nothing.

Two targets:
  default     a local replica of the CategoryHub header (markup copied from
              qa.next.newsweek.com/ai-politics, 2026-09-25) — no network.
  --url URL   the real page. The creative is written into the page's own
              #dfp-ad-oop1 iframe, replacing whatever GAM served there.
              Basic-auth for QA comes from env NW_QA_AUTH="user:pass"
              (never commit it).

Usage:
    python3 scripts/preview_section_sponsor_lockup.py [--logo logo.png]
    NW_QA_AUTH=... python3 scripts/preview_section_sponsor_lockup.py \\
        --url https://qa.next.newsweek.com/ai-politics
Screenshots land in --out (default ./section_sponsor_preview/).
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNIPPET = ROOT / "docs" / "snippets" / "section_sponsor_lockup_creative.html"
WIDTHS = (1280, 768, 390)

# Neutral placeholder wordmark (no real brand art in the repo).
PLACEHOLDER_LOGO = "data:image/svg+xml;base64," + base64.b64encode(
    b"<svg xmlns='http://www.w3.org/2000/svg' width='120' height='30' viewBox='0 0 120 30'>"
    b"<rect width='120' height='30' rx='3' fill='#1f1e19'/>"
    b"<text x='60' y='20' font-family='Arial,sans-serif' font-size='13' font-weight='700' "
    b"letter-spacing='2' fill='#fefcf6' text-anchor='middle'>LOGO</text></svg>"
).decode()

# Replica of the live CategoryHub header (qa.next.newsweek.com/ai-politics,
# 2026-09-25): flex column, centered; the dek container is a flex column with
# a 32px gap (24px on mobile) whose second child is the oop1 slot.
MOCK_PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width">
<style>
body{margin:0;background:#fefcf6;font-family:Georgia,serif;color:#1a1a1a}
.CategoryHubHeader-module-scss-module__ocqZJG__container{display:flex;flex-direction:column;align-items:center;gap:12px;margin:92px 240px 0}
h1{font:600 64px/1.15 Georgia,serif;margin:0}
.CategoryHubHeader-module-scss-module__ocqZJG__descriptionContainer{display:flex;flex-direction:column;gap:32px;max-width:533px}
.CategoryHubHeader-module-scss-module__ocqZJG__description{margin:0;text-align:center;font-size:16.5px;line-height:1.6;color:#57534a}
main{margin:40px 240px;height:300px;background:#ece6d8}
@media(max-width:1024px){.CategoryHubHeader-module-scss-module__ocqZJG__container{margin:92px 0 0}h1{font-size:56px}main{margin:40px 16px}}
@media(max-width:640px){h1{font-size:48px}.CategoryHubHeader-module-scss-module__ocqZJG__descriptionContainer{gap:24px}.CategoryHubHeader-module-scss-module__ocqZJG__description{padding:0 16px}}
.offtemplate{margin:40px auto;max-width:533px}
</style></head><body>
<header class="CategoryHubHeader-module-scss-module__ocqZJG__container">
<svg width="48" height="48" viewBox="0 0 40 40"><rect width="40" height="40" fill="#E91D0C"/></svg>
<h1 class="text-display-semibold-md">AI Politics</h1>
<div class="CategoryHubHeader-module-scss-module__ocqZJG__descriptionContainer">
<p class="text-body-regular-md CategoryHubHeader-module-scss-module__ocqZJG__description">AI Politics covers how artificial intelligence is shaping government, elections and policy debates, including regulation, campaign use and national security implications.</p>
<div id="dfp-ad-oop1" class="dfp-tag-wrapper"><div id="google_ads_iframe_/22541732127/newsweek/oop1_0__container__" style="border:0pt none"><iframe id="google_ads_iframe_/22541732127/newsweek/oop1_0" width="1" height="1" frameborder="0" scrolling="no" style="border:0;vertical-align:bottom"></iframe></div></div>
</div></header>
<main class="CategoryPage-module-scss-module__6INaaq__content"></main>
<div class="offtemplate"><div id="dfp-ad-oop2" class="dfp-tag-wrapper"><div><iframe id="offtemplate_frame" width="1" height="1" frameborder="0" style="border:0"></iframe></div></div></div>
</body></html>"""

WRITE_JS = """([sel, html]) => {
  let f = document.querySelector(sel);
  if (!f && sel.startsWith('#dfp-ad-oop1')) {
    /* Unfilled slot: GPT leaves the container empty. Create the friendly
       1x1 iframe GPT would have rendered the creative into. */
    const slot = document.getElementById('dfp-ad-oop1');
    if (!slot) return false;
    const c = slot.querySelector('[id$="__container__"]') || slot;
    f = document.createElement('iframe');
    f.width = 1; f.height = 1; f.frameBorder = 0; f.scrolling = 'no';
    f.style.cssText = 'border:0;vertical-align:bottom';
    c.appendChild(f);
  }
  if (!f) return false;
  const d = f.contentDocument;
  d.open(); d.write(html); d.close();
  return true;
}"""

MEASURE_JS = """() => {
  const r = e => e ? e.getBoundingClientRect().toJSON() : null;
  const slot = document.getElementById('dfp-ad-oop1');
  const f = slot && slot.querySelector('iframe');
  const a = f && f.contentDocument && f.contentDocument.getElementById('nw-ssl');
  const dek = document.querySelector('[class*="CategoryHubHeader"][class*="__description"]:not([class*="Container"])');
  const hdr = document.querySelector('header[class*="CategoryHubHeader"]');
  const off = document.getElementById('dfp-ad-oop2');
  let lockup = null;
  if (a && f) {
    const ar = a.getBoundingClientRect(), fr = f.getBoundingClientRect();
    const logo = a.querySelector('.ssl-logo'), lab = a.querySelector('.ssl-label');
    const lr = logo.getBoundingClientRect(), tr = lab.getBoundingClientRect();
    lockup = {shown: getComputedStyle(a).display !== 'none',
      left: fr.left + Math.min(lr.left, tr.left), right: fr.left + Math.max(lr.right, tr.right),
      top: fr.top + ar.top, bottom: fr.top + ar.bottom,
      logoH: lr.height, labelFont: getComputedStyle(lab).fontFamily, notoLoaded: f.contentDocument.fonts.check('600 14px "Noto Sans"'), logoLoaded: logo.complete && logo.naturalWidth > 0};
  }
  return {vw: innerWidth, docW: document.documentElement.scrollWidth, frame: r(f), lockup,
          dek: r(dek), header: r(hdr),
          offShown: off ? getComputedStyle(off).display !== 'none' : null};
}"""


def _logo_src(arg: str | None) -> str:
    if not arg:
        return PLACEHOLDER_LOGO
    if re.match(r"https?://|data:", arg):
        return arg
    p = Path(arg)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def render_snippet(logo: str, click: str) -> str:
    """The snippet as GAM would serve it: macros substituted."""
    html = SNIPPET.read_text()
    return (html.replace("%%VIEW_URL_UNESC%%", "")
                .replace("%%CLICK_URL_UNESC%%", "")
                .replace("%%DEST_URL%%", click)
                .replace("%%FILE:PNG1%%", logo)
                .replace("%%CACHEBUSTER%%", "1"))


def check(m: dict) -> list[str]:
    errs = []
    lk, dek, fr = m["lockup"], m["dek"], m["frame"]
    if not lk or not lk["shown"]:
        return ["lockup not rendered"]
    if lk.get("notoLoaded") is False and m.get("live"):
        errs.append("label fell back from Noto Sans")
    if not lk["logoLoaded"]:
        errs.append("logo image did not load")
    centre = (lk["left"] + lk["right"]) / 2
    if abs(centre - m["vw"] / 2) > 6:
        errs.append(f"lockup off-centre: centre {centre:.1f} vs viewport {m['vw'] / 2:.1f}")
    if dek and lk["top"] < dek["bottom"]:
        errs.append(f"lockup top {lk['top']:.0f} is above the dek bottom {dek['bottom']:.0f}")
    if lk["left"] < 0 or lk["right"] > m["vw"]:
        errs.append("lockup overflows the viewport")
    # Compare against the page's own width before the creative ran: the QA
    # hub already overflows from its content carousel (slick-track), which
    # is not ours to fail on.
    if m["docW"] > max(m["vw"], m.get("baseW", 0)):
        errs.append(f"creative widened the page ({m.get('baseW')} -> {m['docW']}px)")
    if fr and not (fr["height"] >= lk["bottom"] - lk["top"] and fr["height"] < 80):
        errs.append(f"GPT iframe not fitted to the lockup (height {fr['height']})")
    if m.get("offShown") is True:
        errs.append("scope guard failed: creative rendered outside the hub header")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="real page to test on (default: local replica)")
    ap.add_argument("--logo", help="logo file/URL (default: neutral placeholder)")
    ap.add_argument("--click", default="https://www.newsweek.com/")
    ap.add_argument("--out", default="section_sponsor_preview")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    html = render_snippet(_logo_src(args.logo), args.click)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    launch: dict = {"args": ["--no-sandbox"]}
    if os.path.exists("/opt/pw-browsers/chromium"):
        launch["executable_path"] = "/opt/pw-browsers/chromium"
    ctx_kw: dict = {}
    if args.url and os.environ.get("NW_QA_AUTH"):
        u, _, p = os.environ["NW_QA_AUTH"].partition(":")
        ctx_kw["http_credentials"] = {"username": u, "password": p}
    if os.environ.get("NW_PROXY"):  # sandboxed runners with an egress proxy
        launch["proxy"] = {"server": os.environ["NW_PROXY"]}
    if os.environ.get("NW_TRUST_SPKI"):
        launch["args"].append("--ignore-certificate-errors-spki-list=" + os.environ["NW_TRUST_SPKI"])

    failures = 0
    with sync_playwright() as pw:
        b = pw.chromium.launch(**launch)
        for w in WIDTHS:
            pg = b.new_page(viewport={"width": w, "height": 900}, **ctx_kw)
            if args.url:
                pg.goto(args.url, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_selector("#dfp-ad-oop1", state="attached", timeout=30000)
                pg.wait_for_timeout(6000)  # let GPT fill (or leave empty) the slot
                pg.wait_for_timeout(2000)
            else:
                pg.set_content(MOCK_PAGE)
                pg.evaluate(WRITE_JS, ["#offtemplate_frame", html])
            base_w = pg.evaluate("document.documentElement.scrollWidth")
            ok = pg.evaluate(WRITE_JS, ["#dfp-ad-oop1 iframe", html])
            if not ok:
                print(f"[{w}] no #dfp-ad-oop1 iframe on the page")
                failures += 1
                continue
            pg.wait_for_timeout(1500)
            m = pg.evaluate(MEASURE_JS)
            m["baseW"] = base_w
            m["live"] = bool(args.url)
            hdr = m["header"]
            clip = None
            if hdr:
                top = max(0, hdr["y"] - 24)
                clip = {"x": 0, "y": top, "width": w,
                        "height": min(900, hdr["y"] + hdr["height"] + 48 - top)}
            shot = out / f"lockup_{w}.png"
            pg.screenshot(path=str(shot), clip=clip)
            errs = check(m)
            lk = m["lockup"] or {}
            print(f"[{w}] {'OK' if not errs else 'FAIL'}  lockup "
                  f"{lk.get('left', 0):.0f}-{lk.get('right', 0):.0f}px  "
                  f"frame h={m['frame']['height'] if m['frame'] else '-'}  "
                  f"noto={lk.get('notoLoaded')}  → {shot}")
            for e in errs:
                print(f"      ✗ {e}")
            failures += bool(errs)
            (out / f"lockup_{w}.json").write_text(json.dumps(m, indent=1))
            pg.close()
        b.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
