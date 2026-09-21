#!/usr/bin/env python3
"""Capture proof-of-placement screenshots of a line item's creatives, live on a
newsweek.com article page.

For every creative on the line item, and every viewport the creative can
actually fill, this mints a GAM on-site preview URL
(`LineItemCreativeAssociationService.getPreviewUrl` — which forces GAM to serve
THAT creative on THAT page, bypassing targeting), loads it in Playwright,
scrolls the lazy content slot into view, and takes two shots: the ad in
context, and a close crop.

Three things it does that a generic screenshot does not:

1. **Gates the page.** The article must pass both tests from
   `docs/screenshots_document.md` before anything is shot — `cat`/`sitecat`
   matching the campaign's vertical, and `brandsafe` = `y` with `adexclusion`
   empty. A failing page aborts with the reason; `--no-gate` overrides for a
   deliberate off-vertical shot.
2. **Skips impossible combinations.** A 970x250 cannot fill a 390px mobile
   slot; GAM falls through to house inventory and you get a screenshot of a
   Newsweek membership ad instead of the client's creative (seen for real,
   2026-09-21). Each creative is only shot where its width fits.
3. **Hides the consent overlay** rather than clicking it. The Ketch "Your
   Privacy Choices" panel covers the lower half of every shot. This sets
   `display:none` on it — it does not accept, decline or otherwise answer the
   banner, so no consent is given on anyone's behalf.

Shots land in --out-dir, named
`<lineitem>_<creative>_<size>_<viewport>_<context|crop>.png`.

Usage:
  python scripts/capture_screenshots.py --line-item 7431083515 \
      --article-url https://www.newsweek.com/... [--out-dir /tmp/shots]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

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
from googleads import ad_manager  # noqa: E402

V = "v202605"

# (name, width, height, is_mobile). A creative is shot on a viewport only when
# it fits inside it with room for the page's own margins.
VIEWPORTS = [
    ("desktop", 1600, 1000, False),
    ("mobile", 390, 844, True),
]

# The consent overlay, and anything else that sits over the page. Hidden, never
# clicked — hiding is not consenting.
HIDE_OVERLAYS_JS = """
() => {
  const sels = ['#ketch-consent-banner', '[class*="ketch"]', '[id*="ketch"]',
                '[class*="consent"]', '[id*="onetrust"]', '[class*="onetrust"]',
                '[aria-label*="Privacy"]', '[class*="privacy-choices"]'];
  let n = 0;
  for (const s of sels) {
    for (const el of document.querySelectorAll(s)) {
      const r = el.getBoundingClientRect();
      // only kill things that actually overlay the page
      const cs = getComputedStyle(el);
      if ((cs.position === 'fixed' || cs.position === 'sticky') && r.height > 40) {
        el.style.setProperty('display', 'none', 'important');
        n++;
      }
    }
  }
  return n;
}
"""

PAGE_KVS_JS = """
() => {
  const out = {cat: [], brandsafe: [], adexclusion: [], slots: 0};
  try {
    const p = window.googletag && window.googletag.pubads && window.googletag.pubads();
    if (p) {
      out.cat = p.getTargeting('cat') || [];
      out.brandsafe = p.getTargeting('brandsafe') || [];
      out.adexclusion = p.getTargeting('adexclusion') || [];
      out.slots = (p.getSlots() || []).length;
    }
  } catch (e) { out.err = String(e); }
  return out;
}
"""


def _vertical_slugs(order_name: str) -> list[str]:
    """Reuse the pull script's vertical map so the two stay in step."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from pull_screenshots_source import _vertical  # noqa: E402
    _v, slugs = _vertical(order_name)
    return slugs


def gate_page(page, slugs: list[str], no_gate: bool) -> tuple[bool, str]:
    kvs = page.evaluate(PAGE_KVS_JS)
    cat = (kvs.get("cat") or [None])[0]
    bs = (kvs.get("brandsafe") or [None])[0]
    ax = kvs.get("adexclusion") or []
    detail = f"cat={cat} brandsafe={bs} adexclusion={ax}"

    problems = []
    if slugs and cat not in [f"nwus-{s}" for s in slugs]:
        problems.append(
            f"wrong vertical: cat is {cat}, expected one of "
            + ", ".join(f"nwus-{s}" for s in slugs)
            + " (the section listing is cross-posted — only the page's KV counts)"
        )
    if bs != "y":
        problems.append(f"not brand safe: brandsafe={bs}")
    # Only a BRAND-SAFETY exclusion disqualifies a page. `adexclusion` also
    # carries unrelated serving controls — a GAM on-site preview adds
    # `nopassfq` (no passback / no frequency capping), which says nothing
    # about the content and is absent from the same article loaded normally.
    # Treating any exclusion as a failure blocked a page that passes.
    unsafe = [v for v in ax if "brand_safety" in str(v).lower()]
    if unsafe:
        problems.append(f"brand-safety exclusion set: {unsafe}")
    other = [v for v in ax if v not in unsafe]

    if other:
        detail += f"  (ignoring non-brand-safety exclusions: {other})"

    if problems and not no_gate:
        return False, detail + "\n     " + "\n     ".join("! " + p for p in problems)
    if problems:
        return True, detail + "  [GATE OVERRIDDEN] " + "; ".join(problems)
    return True, detail + "  [passes both tests]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--line-item", default=os.environ.get("CAPTURE_LINE_ITEM"))
    ap.add_argument("--article-url", default=os.environ.get("CAPTURE_ARTICLE_URL"))
    ap.add_argument("--out-dir", default=os.environ.get("CAPTURE_OUT_DIR", "/tmp/shots"))
    ap.add_argument("--no-gate", action="store_true",
                    default=os.environ.get("CAPTURE_NO_GATE") == "1",
                    help="shoot even if the page fails the vertical/brand-safety tests")
    args = ap.parse_args()
    if not args.line_item or not args.article_url:
        ap.error("--line-item and --article-url are required")

    from playwright.sync_api import sync_playwright  # noqa: E402

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    gam = GAMClient()
    client = gam._get_soap_client()

    # --- the line item, its order (for the vertical), and its creatives ---
    li_svc = client.GetService("LineItemService", version=V)
    sb = ad_manager.StatementBuilder(version=V).Where(
        f"id = {int(args.line_item)}").Limit(1)
    lis = list(getattr(li_svc.getLineItemsByStatement(sb.ToStatement()),
                       "results", []) or [])
    if not lis:
        print(f"!! line item {args.line_item} not found", file=sys.stderr)
        return 1
    li = lis[0]
    order_id = getattr(li, "orderId", None)

    o_svc = client.GetService("OrderService", version=V)
    sbo = ad_manager.StatementBuilder(version=V).Where(
        f"id = {int(order_id)}").Limit(1)
    orders = list(getattr(o_svc.getOrdersByStatement(sbo.ToStatement()),
                          "results", []) or [])
    order_name = getattr(orders[0], "name", "") if orders else ""
    slugs = _vertical_slugs(order_name)

    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
    sbl = ad_manager.StatementBuilder(version=V).Where(
        f"lineItemId = {int(args.line_item)}").Limit(100)
    licas = list(getattr(lica_svc.getLineItemCreativeAssociationsByStatement(
        sbl.ToStatement()), "results", []) or [])
    cre_ids = [getattr(la, "creativeId", None) for la in licas]
    cre_ids = [c for c in cre_ids if c is not None]
    if not cre_ids:
        print(f"!! line item {args.line_item} has no creatives — nothing to shoot",
              file=sys.stderr)
        return 1

    cr_svc = client.GetService("CreativeService", version=V)
    sbc = ad_manager.StatementBuilder(version=V).Where(
        "id IN (" + ", ".join(str(int(c)) for c in cre_ids) + ")").Limit(100)
    creatives = {}
    for c in (getattr(cr_svc.getCreativesByStatement(sbc.ToStatement()),
                      "results", []) or []):
        sz = getattr(c, "size", None)
        creatives[str(getattr(c, "id"))] = {
            "name": getattr(c, "name", ""),
            "w": getattr(sz, "width", 0) if sz else 0,
            "h": getattr(sz, "height", 0) if sz else 0,
        }

    print(f"LI {args.line_item} on order {order_id}")
    print(f"  order    : {order_name}")
    print(f"  vertical : {slugs or '(none derivable)'}")
    print(f"  article  : {args.article_url}")
    print(f"  creatives: {len(creatives)}")

    shots, skipped = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-dev-shm-usage"])
        gated_ok = None

        for cid, meta in creatives.items():
            cw = meta["w"]
            for vname, vw, vh, is_mobile in VIEWPORTS:
                # A creative wider than the viewport cannot fill the slot; GAM
                # serves house inventory instead. Don't waste a shot on it.
                if cw and cw > vw - 48:
                    skipped.append(
                        f"{meta['name']} ({cw}x{meta['h']}) on {vname} "
                        f"({vw}px) — too wide, would serve a house ad")
                    continue

                try:
                    url = lica_svc.getPreviewUrl(
                        int(args.line_item), int(cid), args.article_url)
                except Exception as e:
                    print(f"  !! getPreviewUrl({args.line_item},{cid}): {e}")
                    continue

                ctx = browser.new_context(
                    viewport={"width": vw, "height": vh},
                    device_scale_factor=2,
                    is_mobile=is_mobile, has_touch=is_mobile,
                )
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                    page.wait_for_timeout(5000)

                    if gated_ok is None:
                        gated_ok, detail = gate_page(page, slugs, args.no_gate)
                        print(f"  page gate: {detail}")
                        if not gated_ok:
                            print("  ABORTING — pick a page that passes, or "
                                  "pass --no-gate deliberately", file=sys.stderr)
                            ctx.close(); browser.close()
                            return 2

                    hidden = page.evaluate(HIDE_OVERLAYS_JS)

                    # Lazy slots only exist once scrolled near — walk the page.
                    for y in range(0, 6000, 600):
                        page.evaluate(f"window.scrollTo(0, {y})")
                        page.wait_for_timeout(350)
                    page.wait_for_timeout(2500)
                    page.evaluate(HIDE_OVERLAYS_JS)

                    # The creative's OWN iframe first. A bare
                    # `iframe[id^=google_ads_iframe]` matches every slot on the
                    # page, and a selector LIST returns whichever comes first in
                    # document order — not the one we asked for — so the sized
                    # match has to be its own query, tried first.
                    frame = page.query_selector(
                        f'iframe[id^="google_ads_iframe"][width="{cw}"]')
                    exact = frame is not None
                    if frame is None:
                        frame = page.query_selector(
                            'iframe[id^="google_ads_iframe"]')
                    if frame:
                        frame.scroll_into_view_if_needed()
                        page.wait_for_timeout(1200)
                        page.evaluate(HIDE_OVERLAYS_JS)

                    tag = f"{args.line_item}_{cid}_{cw}x{meta['h']}_{vname}"
                    ctx_path = out / f"{tag}_context.png"
                    page.screenshot(path=str(ctx_path), full_page=False)
                    shots.append(ctx_path.name)

                    # Element screenshot, not page+clip: a clip rect is in page
                    # coordinates while bounding_box() is viewport-relative, so
                    # on a scrolled page the two disagree and the crop lands
                    # somewhere else entirely. Shooting the slot wrapper gives
                    # the padding a bare iframe would not.
                    if frame:
                        target = frame
                        wrapper = page.query_selector(
                            '[id^="dfp-ad-inarticle"], [id^="dfp-ad-"]')
                        if wrapper:
                            wbox, fbox = wrapper.bounding_box(), frame.bounding_box()
                            # only prefer the wrapper when it actually contains
                            # this iframe, rather than some other slot's
                            if wbox and fbox and abs(wbox["y"] - fbox["y"]) < 400:
                                target = wrapper
                        try:
                            crop_path = out / f"{tag}_crop.png"
                            target.screenshot(path=str(crop_path))
                            shots.append(crop_path.name)
                        except Exception as e:
                            print(f"     crop failed: {e}")

                    if frame and not exact:
                        print(f"     WARNING: no {cw}-wide ad iframe on the "
                              f"page; shot the first slot found, which may be "
                              f"a different placement \u2014 check this one by eye")
                    print(f"  shot {tag}: overlays hidden={hidden}, "
                          f"ad iframe={'exact' if exact else ('fallback' if frame else 'NOT FOUND')}")
                except Exception as e:
                    print(f"  !! {cid}/{vname}: {e}")
                finally:
                    ctx.close()

        browser.close()

    print(f"\n{len(shots)} shot(s) -> {out}")
    for s in sorted(shots):
        print(f"  {s}")
    if skipped:
        print("\nskipped (would not have served the client's creative):")
        for s in skipped:
            print(f"  - {s}")
    return 0 if shots else 1


if __name__ == "__main__":
    raise SystemExit(main())
