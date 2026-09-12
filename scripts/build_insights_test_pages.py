#!/usr/bin/env python3
"""Build a self-contained test page for the Insights native styles.

Design/stakeholder QA for docs/snippets/insights_native_style.{html,css}. Each
unit is rendered in an **iframe sized to exactly its slot** (970x250, 728x90,
300x250) — the same box GAM gives a native style, so the media queries that
pick the layout resolve identically here and on-page. Anything that fits in
this file fits in the ad slot.

Two views in one file:
  1. In-page context — the three units dropped into a mock article shell
     (billboard above the masthead, leaderboard mid-article, rectangle in the
     rail), to check they hold up next to editorial rather than in isolation.
  2. Isolated at 1:1 — each unit on its own with a size label and a 1px box,
     for pixel review.

Assets and copy come from a real GAM creative and are inlined as data URIs, so
the output is one file with no network dependency (fonts aside) — openable
locally, attachable to an email, shareable with a seller.

The mock shell is deliberately marked as an ad-QA harness with placeholder
body text. It is a slot-geometry stand-in, not a reproduction of a Newsweek
page, and shouldn't be passed off as one.

Usage:
    python3 scripts/build_insights_test_pages.py
    python3 scripts/build_insights_test_pages.py --creative-id 138561753906
"""

import argparse
import html as _html
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from preview_insights_native import (  # noqa: E402
    CSS_PATH, DEFAULT_CREATIVE_ID, build_doc, fetch_creative_values,
    load_values_file,
)

SIZES = [(970, 250), (728, 90), (300, 250)]

PAGE_CSS = """
:root{--paper:#F8F4E8;--ink:#1f1e19;--muted:#68645a;--rule:rgba(31,30,25,.14);--red:#e91d0c}
*{box-sizing:border-box}
body{margin:0;background:#efeade;color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif;-webkit-font-smoothing:antialiased}
.qa-bar{background:var(--ink);color:#f4efe1;padding:10px 18px;font-size:12px;
  letter-spacing:.09em;text-transform:uppercase;display:flex;gap:14px;align-items:center;
  position:sticky;top:0;z-index:5}
.qa-bar b{font-weight:700}
.qa-bar span{color:#a8a294;text-transform:none;letter-spacing:0}
.wrap{max-width:1100px;margin:0 auto;padding:26px 20px 60px}
h2.sec{font-family:Georgia,serif;font-size:19px;margin:34px 0 6px;font-weight:700}
p.note{margin:0 0 18px;color:var(--muted);font-size:13px;max-width:70ch;line-height:1.5}
.slot{display:flex;flex-direction:column;gap:7px;margin:0 0 30px}
.slot-l{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
/* no box on the harness side — the unit draws its own 1px border */
.slot iframe{border:0;display:block;background:var(--paper)}
.page{background:#fff;border:1px solid var(--rule);padding:0 0 30px}
.mast{border-bottom:2px solid var(--ink);margin:0 26px;padding:18px 0 12px;
  display:flex;align-items:baseline;justify-content:space-between}
.mast b{font-family:Georgia,serif;font-size:26px;letter-spacing:-.01em}
.mast i{font-style:normal;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.art{display:grid;grid-template-columns:1fr 300px;gap:34px;padding:24px 26px 0}
.art h1{font-family:Georgia,serif;font-size:31px;line-height:1.2;margin:0 0 14px}
.art p{font-size:15.5px;line-height:1.65;color:#2e2d27;margin:0 0 15px}
.art .ph{color:#8d8776}
.rail{display:flex;flex-direction:column;gap:9px}
/* The live page wraps ads in a full-bleed warm "ADVERTISING" band. The first
   harness put the units on white, which hid the fact that the unit's own paper
   ground blended straight into that band on a real article (Roger, 2026-09-08).
   Reproduce the band so the QA render can actually catch it. */
.adband{background:#f6f2e6;margin:26px -26px;padding:14px 0 20px;
  display:flex;flex-direction:column;align-items:center;gap:9px}
.adband-l{font-size:9.5px;letter-spacing:.18em;text-transform:uppercase;color:#9a9384}
.center{display:flex;justify-content:center;margin:26px 0}
@media(max-width:980px){.art{grid-template-columns:1fr}
  .slot iframe,.center iframe{max-width:100%}}
"""

BODY_TEXT = [
    "Placeholder body copy. This shell exists only to give the ad slots realistic "
    "neighbours — column width, leading and hierarchy — so the units can be judged "
    "against editorial rather than against a blank page.",
    "The three units below are the real native style markup and stylesheet, each in an "
    "iframe sized to exactly its slot. That is the same box GAM hands a native style, "
    "so the media query that selects the layout resolves here exactly as it will on "
    "newsweek.com.",
    "Nothing on this page is a Newsweek article. Swap in a live URL when you want to "
    "check the units against real surrounding content.",
]


def iframe(doc_escaped: str, w: int, h: int, extra: str = "") -> str:
    return (f'<iframe srcdoc="{doc_escaped}" width="{w}" height="{h}" '
            f'scrolling="no" frameborder="0" title="Insights {w}x{h}"{extra}></iframe>')


def build_page(values: dict) -> str:
    doc = _html.escape(build_doc(values), quote=True)
    name = _html.escape(values.get("_name", ""))
    title = _html.escape(values.get("TITLE", ""))

    slots = "\n".join(
        f'<div class="slot"><div class="slot-l">{w} &times; {h}</div>'
        f"{iframe(doc, w, h)}</div>"
        for w, h in SIZES
    )
    paras = "\n".join(f'<p class="ph">{t}</p>' for t in BODY_TEXT)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Insights native — ad QA test page</title>
<style>{PAGE_CSS}</style></head>
<body>
<div class="qa-bar"><b>Ad QA</b> Insights native style
  <span>creative {values.get('_creative_id','')} &middot; {name}</span></div>
<div class="wrap">

  <h2 class="sec">1 &middot; In-page context</h2>
  <p class="note">Mock article shell — placeholder text, not a Newsweek page. It is here
     to give the slots realistic neighbours. Billboard above the masthead, leaderboard
     mid-article, rectangle in the rail.</p>

  <div class="page">
    <div class="adband"><span class="adband-l">Advertising</span>{iframe(doc, 970, 250)}</div>
    <div class="mast"><b>Ad QA Harness</b><i>Test page &middot; not a live page</i></div>
    <div class="art">
      <div>
        <h1>{title}</h1>
        {paras}
        <div class="adband"><span class="adband-l">Advertising</span>{iframe(doc, 728, 90)}</div>
        {paras}
      </div>
      <div class="rail">
        <div class="slot-l">Rail &middot; 300 &times; 250</div>
        {iframe(doc, 300, 250)}
      </div>
    </div>
  </div>

  <h2 class="sec">2 &middot; Isolated at 1:1</h2>
  <p class="note">Each unit at its exact pixel size with a 1px box, for pixel review.
     Every size clamps its copy, so over-long TITLE / SUBTITLE shows here as an
     ellipsis rather than a broken box.</p>
  {slots}

</div></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creative-id", type=int, default=DEFAULT_CREATIVE_ID)
    ap.add_argument("--values-json", help="proof un-trafficked copy instead of a GAM creative")
    ap.add_argument("--out-dir", default=str(REPO / "data" / "insights_preview"))
    ap.add_argument("--prefix", default="insights", help="output filename prefix")
    args = ap.parse_args()

    if args.values_json:
        values = load_values_file(args.values_json)
        values["_creative_id"] = "not trafficked yet"
    else:
        values = fetch_creative_values(args.creative_id)
        values["_creative_id"] = args.creative_id
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.prefix}_test_page.html"
    path.write_text(build_page(values))
    print(f"source: {values['_name']}")
    print(f"stylesheet: {CSS_PATH.relative_to(REPO)}")
    print(f"wrote {path}  ({path.stat().st_size/1024:.0f} KB, self-contained)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
