#!/usr/bin/env python3
"""Derive the Insights native style's copy limits by measurement, not by guess.

Every size clamps its text (`-webkit-line-clamp`), so over-long copy fails as a
silent ellipsis. The limits are a property of the *type scale*, not something to
write down once: change a font-size, a column width or a line count in
insights_native_style.css and these numbers move. Re-run this and update
docs/insights_native_ad.md rather than trusting the table there.

Clamping is line-based, so a character count depends on which characters. Each
length is therefore tested against many randomly-built editorial-style strings:

    SAFE = longest length where EVERY sample fits  -> the number to publish
    MAX  = longest length where ANY sample fits    -> lucky short/narrow words

Usage:
    python3 scripts/measure_insights_copy_limits.py
    python3 scripts/measure_insights_copy_limits.py --samples 20 --step 5
"""

import argparse
import pathlib
import random
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from preview_insights_native import (  # noqa: E402
    CSS_PATH, HTML_PATH, SIZES, _CHROMIUM_CANDIDATES,
)

# Editorial vocabulary in the register these headlines actually use — mixed word
# lengths and caps, so the estimate isn't skewed by uniformly short words.
WORDS = (
    "the a of and to in for why how what when institutional intelligence sovereignty "
    "imperative model models data enterprise leaders luxury bold inside anticipated "
    "list summer drive cabin styling spirited thoughtfully crafted arrives American "
    "driveways technology automotive future platform decisions context vendor moment "
    "endure own their must outlive any new all-new Newsweek sponsored partner"
).split()

FIELDS = {
    "TITLE": ".insights-hero__headline",
    "SUBTITLE": ".insights-hero__description",
    # the category moved into the header lockup; probing .insights-hero__tags
    # measured the wrong element (395 chars on the rectangle, "not shown" on the
    # wide sizes -- both artifacts, not real limits).
    "HASHTAG": ".insights-hero__cat",
}


def sample_text(n: int, rng: random.Random) -> str:
    out, total = [], 0
    while total < n:
        w = rng.choice(WORDS)
        out.append(w)
        total += len(w) + 1
    s = " ".join(out)[:n].rstrip()
    return (s[0].upper() + s[1:]) if s else s


def build_doc(html: str, css: str, **vals) -> str:
    for key in ("TITLE", "SUBTITLE", "HASHTAG", "IMAGE", "LOGO",
                "3RDPARTYTRACKING1", "3RDPARTYTRACKING2"):
        html = html.replace(f"[%{key}%]", vals.get(key, ""))
    html = html.replace("%%CLICK_URL_UNESC%%%%DEST_URL%%", "#")
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{css}</style></head><body>{html}</body></html>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=12, help="strings tested per length")
    ap.add_argument("--step", type=int, default=5, help="character granularity")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    css, html = CSS_PATH.read_text(), HTML_PATH.read_text()
    exe = next((p for p in _CHROMIUM_CANDIDATES if pathlib.Path(p).exists()), None)
    tmp = pathlib.Path(tempfile.mkdtemp()) / "probe.html"
    results: dict = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        ctx = browser.new_context()
        # Webfonts only widen the measurement; block them so a slow/blocked CDN
        # can't silently turn this into a Georgia-fallback estimate.
        ctx.route("**://fonts.googleapis.com/**", lambda r: r.abort())
        ctx.route("**://fonts.gstatic.com/**", lambda r: r.abort())
        page = ctx.new_page()

        for w, h in SIZES:
            label = f"{w}x{h}"
            page.set_viewport_size({"width": w, "height": h})
            for field, sel in FIELDS.items():
                def fits(n: int, seed: int) -> bool:
                    rng = random.Random(seed)
                    text = sample_text(n, rng)
                    vals = {"TITLE": "Probe headline", "SUBTITLE": "Probe dek",
                            "HASHTAG": "Technology", field: text}
                    tmp.write_text(build_doc(html, css, **vals))
                    page.goto(tmp.resolve().as_uri())
                    return page.evaluate(
                        f"() => {{const e=document.querySelector('{sel}');"
                        f" if(!e||!e.clientHeight) return null;"
                        f" return e.scrollHeight <= e.clientHeight"
                        f"        && e.scrollWidth <= e.clientWidth;}}")

                if fits(12, 0) is None:      # field not rendered at this size
                    results[(label, field)] = None
                    print(f"  {label:8s} {field:9s} not shown at this size")
                    continue
                safe = mx = args.step
                for n in range(args.step * 2, 400, args.step):
                    oks = [fits(n, s) for s in range(args.samples)]
                    if all(oks):
                        safe = n
                    if any(oks):
                        mx = n
                    else:
                        break
                results[(label, field)] = (safe, mx)
                print(f"  {label:8s} {field:9s} safe={safe:3d}  max={mx:3d}")
        browser.close()

    print("\n=== binding limit across the sizes that render each field ===")
    for field in FIELDS:
        shown = {l: v for (l, f), v in results.items() if f == field and v}
        if not shown:
            continue
        tightest = min(shown, key=lambda l: shown[l][0])
        print(f"  {field:9s} safe={shown[tightest][0]:3d} chars  (tightest: {tightest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
