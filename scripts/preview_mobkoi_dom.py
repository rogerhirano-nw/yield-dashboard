"""One-off: render GAM on-site previews of the Mobkoi creatives in headless
Chromium and report what happens to the measured elements.

For each LI:creative pair: mint an on-site preview URL via SOAP
`LineItemCreativeAssociationService.getPreviewUrl` (forces GAM to serve that
creative on the page), load it with Playwright mobile emulation, scroll
through the article, then report:
  - every `iframe[id^=google_ads_iframe]`: in DOM? display/visibility/
    opacity/rect (hidden vs detached is THE question for the AV mirror plan)
  - every `[id^=dfp-ad-inarticle]` slot div: rect + children outline
  - Mobkoi artifacts: scripts from *mobkoi*, elements with mobkoi in
    id/class, and any large fixed/absolute elements (the unit + its window)
Screenshots land in /tmp/shots (uploaded as a workflow artifact).

Caveat: the runner has a US datacenter IP. The Invesco/Cartier campaigns are
UK-geo'd — GAM preview bypasses LI targeting, but Mobkoi's ad server may
still geo-gate the payload, so the US-targeted Mobkoi-Publisher-Testing
pairs are included as fallbacks ("auto" resolves the LI's first creative).

Env: PREVIEW_PAIRS="li:creative,li:auto,…", ARTICLE_URL.
"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_env = REPO_ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from gam_client import GAMClient  # noqa: E402

DEFAULT_PAIRS = (
    "7310815861:138557481462,"      # Invesco interscroller (UK flight)
    "7255084258:auto,"              # Mobkoi-Publisher-Testing Portrait (US)
    "7256561225:auto,"              # Mobkoi-Publisher-Testing Landscape (US)
    "7253027964:auto"               # Mobkoi-Publisher-Testing Square (US)
)
PAIRS = [p for p in (os.environ.get("PREVIEW_PAIRS") or DEFAULT_PAIRS).split(",") if p.strip()]
ARTICLE_URL = (os.environ.get("ARTICLE_URL")
               or "https://www.newsweek.com/trump-admin-vows-to-hold-8647-national-mall-vandals-accountable-threat-12062049")

SHOTS = Path("/tmp/shots")
SHOTS.mkdir(parents=True, exist_ok=True)

INSPECT_JS = """
() => {
  const trim = (s, n) => (s || '').toString().slice(0, n);
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y + window.scrollY),
            w: Math.round(r.width), h: Math.round(r.height)};
  };
  const styleOf = (el) => {
    const cs = getComputedStyle(el);
    return {display: cs.display, visibility: cs.visibility,
            opacity: cs.opacity, position: cs.position, zIndex: cs.zIndex};
  };
  const out = {url: location.href, viewport: {w: innerWidth, h: innerHeight},
               scrollY: Math.round(window.scrollY)};

  out.gam_iframes = [...document.querySelectorAll('iframe[id^="google_ads_iframe"]')]
    .map(f => ({id: f.id, box: box(f), style: styleOf(f),
                parent: trim(f.parentElement && (f.parentElement.id || f.parentElement.className), 60)}));

  out.slot_divs = [...document.querySelectorAll('[id^="dfp-ad-inarticle"]')]
    .map(d => ({id: d.id, box: box(d), style: styleOf(d),
                children: [...d.children].map(c =>
                  c.tagName + '#' + trim(c.id, 40) + '.' + trim(c.className, 40))}));

  out.mobkoi_scripts = [...document.querySelectorAll('script[src*="mobkoi"]')]
    .map(s => trim(s.src, 120));

  out.mobkoi_nodes = [...document.querySelectorAll('*')]
    .filter(el => /mobkoi/i.test(el.id + ' ' + el.className + ' ' +
            (el.getAttributeNames ? el.getAttributeNames().join(' ') : '')))
    .slice(0, 20)
    .map(el => ({tag: el.tagName, id: trim(el.id, 50), cls: trim(el.className, 60),
                 box: box(el), style: styleOf(el)}));

  const vw = innerWidth * innerHeight;
  out.big_overlays = [...document.querySelectorAll('div,iframe,section,aside')]
    .filter(el => {
      const cs = getComputedStyle(el);
      if (cs.position !== 'fixed' && cs.position !== 'sticky' && cs.position !== 'absolute') return false;
      const r = el.getBoundingClientRect();
      return r.width * r.height > vw * 0.4;
    })
    .slice(0, 12)
    .map(el => ({tag: el.tagName, id: trim(el.id, 50), cls: trim(el.className, 60),
                 box: box(el), style: styleOf(el)}));
  return out;
}
"""


def resolve_pairs(gam: GAMClient) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    autos = [p.split(":")[0].strip() for p in PAIRS if p.split(":")[1].strip() == "auto"]
    lica = gam.list_line_item_creative_associations(autos) if autos else None
    for p in PAIRS:
        li, cr = (x.strip() for x in p.split(":"))
        if cr == "auto":
            rows = lica[lica["line_item_id"] == li] if lica is not None else None
            if rows is None or rows.empty:
                print(f"  !! no LICA found for LI {li}, skipping")
                continue
            cr = rows.iloc[0]["creative_id"]
        pairs.append((li, cr))
    return pairs


def main() -> int:
    gam = GAMClient()
    client = gam._get_soap_client()
    svc = client.GetService("LineItemCreativeAssociationService",
                            version=gam._SOAP_API_VERSION)

    pairs = resolve_pairs(gam)
    print(f"Article: {ARTICLE_URL}")
    print(f"Pairs:   {pairs}\n")

    previews: list[tuple[str, str, str]] = []
    for li, cr in pairs:
        try:
            url = svc.getPreviewUrl(int(li), int(cr), ARTICLE_URL)
            previews.append((li, cr, url))
            print(f"preview URL minted for {li}:{cr}")
        except Exception as e:
            print(f"  !! getPreviewUrl({li},{cr}) failed: {e}")
    if not previews:
        print("no preview URLs — nothing to render")
        return 1

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        for li, cr, url in previews:
            print("\n" + "=" * 72)
            print(f"RENDER LI {li} / creative {cr}")
            print("=" * 72)
            ctx = browser.new_context(
                viewport={"width": 390, "height": 844},
                device_scale_factor=3, is_mobile=True, has_touch=True,
                user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                            "Version/17.5 Mobile/15E148 Safari/604.1"),
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(6_000)
                # best-effort consent dismissal so ads can load
                for sel in ("#onetrust-accept-btn-handler",
                            "button:has-text('Accept')",
                            "button:has-text('Continue')",
                            "button:has-text('I Agree')"):
                    try:
                        page.locator(sel).first.click(timeout=1_500)
                        page.wait_for_timeout(1_000)
                        break
                    except Exception:
                        pass
                # scroll through the article to trigger lazy slots + the
                # scroller reveal, sampling the DOM along the way
                for frac in (0.2, 0.4, 0.6, 0.8):
                    page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {frac})")
                    page.wait_for_timeout(2_500)
                report = page.evaluate(INSPECT_JS)
                shot = SHOTS / f"li{li}_cr{cr}.png"
                page.screenshot(path=str(shot), full_page=False)
                print(json.dumps(report, indent=1)[:9_000])
                n_if = len(report.get("gam_iframes", []))
                n_mk = len(report.get("mobkoi_nodes", [])) + len(report.get("mobkoi_scripts", []))
                print(f"\nVERDICT {li}:{cr} — google_ads_iframes in DOM: {n_if}; "
                      f"mobkoi artifacts: {n_mk}; screenshot: {shot.name}")
            except Exception as e:
                print(f"  !! render failed: {e}")
            finally:
                ctx.close()
        browser.close()

    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
