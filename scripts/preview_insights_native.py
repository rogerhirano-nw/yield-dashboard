#!/usr/bin/env python3
"""Render the Insights native style at 970x250 / 728x90 / 300x250 and PNG it.

Local design QA for docs/snippets/insights_native_style.{html,css} -- no GAM
write, no on-site preview needed. Substitutes the creative-template macros with
a real creative's values (pulled from GAM by id, so the copy lengths and the
hero/logo aspect ratios are the ones that will actually ship), renders each
size in headless Chromium at its exact pixel box, and reports whether anything
overflowed its box.

The overflow report is the point: every size clamps its headline (and the dek
on 970x250), so a too-long TITLE fails silently as an ellipsis rather than as a
broken layout. `text overflow` / `dek clipped` in the output is the tell.

Usage:
    python3 scripts/preview_insights_native.py                 # Infiniti QX65 creative
    python3 scripts/preview_insights_native.py --creative-id 138561753906
    python3 scripts/preview_insights_native.py --out-dir /tmp/insights
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import tempfile
import warnings
from pathlib import Path
from urllib.request import urlopen

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent

_env = REPO / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

V = "v202605"
DEFAULT_CREATIVE_ID = 138561753906   # "Native Insight Infiniti"
SIZES = [(970, 250), (728, 90), (300, 250)]

HTML_PATH = REPO / "docs" / "snippets" / "insights_native_style.html"
CSS_PATH = REPO / "docs" / "snippets" / "insights_native_style.css"

# Chromium ships in the Claude Code remote image at a path Playwright's own
# version pin may not match; fall back to Playwright's bundled resolution.
_CHROMIUM_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
]


def _data_uri(url: str) -> str:
    """Inline a creative asset so the render never depends on network timing."""
    with urlopen(url) as r:
        blob = r.read()
        mime = r.headers.get_content_type() or mimetypes.guess_type(url)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(blob).decode()


def load_values_file(path: str) -> dict:
    """Read template values from a JSON file, for copy that isn't in GAM yet.

    Lets an AE proof a headline/dek/hero against the real layouts *before* the
    creative is trafficked — which is when a too-long TITLE is cheap to fix.
    Schema (all optional except TITLE):

        {"TITLE": "...", "SUBTITLE": "...", "HASHTAG": "Technology",
         "IMAGE": "<url | local path | data: uri>",
         "LOGO":  "<url | local path | data: uri>",
         "DEST":  "https://www.newsweek.com/insights/..."}

    IMAGE/LOGO are inlined as data URIs so the render never depends on network
    timing, exactly as the GAM asset path does.
    """
    values = json.loads(Path(path).read_text())
    for key in ("IMAGE", "LOGO"):
        ref = values.get(key)
        if not ref or ref.startswith("data:"):
            continue
        if ref.startswith(("http://", "https://")):
            values[key] = _data_uri(ref)
        else:
            blob = Path(ref).read_bytes()
            mime = mimetypes.guess_type(ref)[0] or "image/png"
            values[key] = f"data:{mime};base64," + base64.b64encode(blob).decode()
    values.setdefault("_name", Path(path).stem)
    values["_dest"] = values.get("DEST", "") or values.get("_dest", "") or "#"
    return values


def fetch_creative_values(creative_id: int) -> dict:
    from googleads import ad_manager, oauth2

    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        keyfile = f.name
    oc = oauth2.GoogleServiceAccountClient(keyfile, "https://www.googleapis.com/auth/dfp")
    client = ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"]
    )
    svc = client.GetService("CreativeService", version=V)
    stmt = (ad_manager.StatementBuilder(version=V)
            .Where("id = :i").WithBindVariable("i", int(creative_id)).Limit(1).ToStatement())
    results = list(getattr(svc.getCreativesByStatement(stmt), "results", []) or [])
    if not results:
        raise SystemExit(f"creative {creative_id} not found")
    creative = results[0]

    values = {}
    for var in creative.creativeTemplateVariableValues:
        asset = getattr(var, "asset", None)
        if asset is not None and getattr(asset, "assetUrl", None):
            values[var.uniqueName] = _data_uri(asset.assetUrl)
        else:
            values[var.uniqueName] = getattr(var, "value", None) or ""
    values["_name"] = creative.name
    values["_dest"] = getattr(creative, "destinationUrl", "") or ""
    return values


# The hidden 3rd-party pixel divs, stripped before any local render — see below.
_TRACKING_DIV = re.compile(
    r'\n?<div style="display:none"><img src="\[%3RDPARTYTRACKING[12]%\]" border="0"></div>')


def build_doc(values: dict) -> str:
    """Assemble a standalone document from the style + one creative's values.

    The 3rd-party tracking pixels are **dropped, not substituted**. They are
    real advertiser/measurement URLs (ml314 on the Infiniti creative), and a QA
    render is not an impression — firing them would put design previews into
    the buyer's counts. Nothing visual depends on them: they render hidden.
    """
    html = _TRACKING_DIV.sub("", HTML_PATH.read_text())
    css = CSS_PATH.read_text()
    for key in ("TITLE", "SUBTITLE", "HASHTAG", "IMAGE", "LOGO"):
        html = html.replace(f"[%{key}%]", values.get(key, "") or "")
    html = html.replace("%%CLICK_URL_UNESC%%%%DEST_URL%%", values.get("_dest", "#"))
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{css}</style></head><body>{html}</body></html>")


# A logo asset is sized by HEIGHT in the stylesheet, so transparent padding
# baked into the file shrinks the visible mark by exactly that proportion. The
# Cognizant asset shipped as a 3840x2160 gallery export whose wordmark filled
# 31% of the canvas height -- at --logo-h:20px that left ~6px of actual logo,
# which read as a speck (Roger, 2026-09-08). Nothing in CSS can crop padding it
# cannot see, so the check belongs here: measure the asset's ink box in a canvas
# (a data: URI never taints it) and say so before the creative ships.
_LOGO_INK_JS = """() => {
  const img = document.querySelector('.insights-hero__sponsor-logo');
  if (!img || !img.naturalWidth) return null;
  const W = Math.min(img.naturalWidth, 600);
  const H = Math.max(1, Math.round(img.naturalHeight * W / img.naturalWidth));
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const cx = cv.getContext('2d', {willReadFrequently: true});
  cx.drawImage(img, 0, 0, W, H);
  let d;
  try { d = cx.getImageData(0, 0, W, H).data; } catch (e) { return null; }
  let top = H, bot = -1, left = W, right = -1;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 4;
    // ink = not transparent AND not paper-white (covers flattened exports)
    const inked = d[i+3] > 16 && !(d[i] > 244 && d[i+1] > 244 && d[i+2] > 244);
    if (!inked) continue;
    if (y < top) top = y; if (y > bot) bot = y;
    if (x < left) left = x; if (x > right) right = x;
  }
  if (bot < 0) return null;
  return {natW: img.naturalWidth, natH: img.naturalHeight,
          inkH: (bot - top + 1) / H, inkW: (right - left + 1) / W};
}"""

# Below this the mark is small enough that a reader cannot identify the brand at
# the leaderboard's --logo-h:14px, which is what disclosure depends on.
_LOGO_INK_MIN = 0.70

# Minimum air between the headline and an out-of-flow CTA. Below this they read
# as colliding even before the boxes actually intersect.
_CTA_MIN_GAP = 8


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creative-id", type=int, default=DEFAULT_CREATIVE_ID)
    ap.add_argument("--values-json", help="proof un-trafficked copy instead of a GAM creative")
    ap.add_argument("--out-dir", default=str(REPO / "data" / "insights_preview"))
    ap.add_argument("--prefix", default="insights", help="output filename prefix")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    if args.values_json:
        values = load_values_file(args.values_json)
        print(f"values file {args.values_json}: {values['_name']}")
    else:
        values = fetch_creative_values(args.creative_id)
        print(f"creative {args.creative_id}: {values['_name']}")
    print(f"  TITLE    ({len(values.get('TITLE',''))} chars) {values.get('TITLE','')[:70]}...")
    print(f"  SUBTITLE ({len(values.get('SUBTITLE',''))} chars)")
    print(f"  HASHTAG  #{values.get('HASHTAG','')}\n")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doc = out / f"{args.prefix}_preview_doc.html"
    doc.write_text(build_doc(values))

    exe = next((p for p in _CHROMIUM_CANDIDATES if Path(p).exists()), None)
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        for w, h in SIZES:
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
            page.goto(doc.resolve().as_uri())
            page.wait_for_timeout(2500)   # webfonts + inlined assets
            path = out / f"{args.prefix}_{w}x{h}.png"
            page.screenshot(path=str(path))
            fit = page.evaluate("""() => {
              const q = s => document.querySelector(s);
              const t = q('.insights-hero__text'), d = q('.insights-hero__description');
              const c = q('.insights-hero__content'), h = q('.insights-hero__headline');
              // A flex child can be squeezed below its natural height and clip
              // internally (a clamped headline crops its descenders) while the
              // CONTAINER still reports no overflow -- so measure the headline's
              // own box against the lines it is clamped to, not just the parents.
              const lh = parseFloat(getComputedStyle(h).lineHeight);
              const lines = parseInt(getComputedStyle(h).webkitLineClamp) || 99;
              const wanted = Math.min(Math.round(h.scrollHeight / lh), lines) * lh;
              // How much air is there between the copy and the button? Two
              // different failure modes, one number:
              //   728x90  -- the CTA is position:absolute, so it is out of flow
              //     and does NOT push the text; the gutter is reserved by hand
              //     via --cta-gutter, and under-reserving it neither clips nor
              //     wraps -- the headline simply runs under the button. The gap
              //     is horizontal: button left edge vs headline right edge.
              //   970x250 / 300x250 -- the CTA is in flow and pinned to the
              //     bottom (margin-top:auto), so the gap is whatever vertical
              //     space is left over. It shrinks as the dek grows toward its
              //     clamp, which is the worst case worth reporting.
              const b = q('.insights-hero__cta');
              const abs = getComputedStyle(b).position === 'absolute';
              const bb = b.getBoundingClientRect();
              // last copy element above an in-flow CTA (the dek when shown)
              const above = (d.clientHeight ? d : h).getBoundingClientRect();
              const gap = abs ? Math.round(bb.left - h.getBoundingClientRect().right)
                              : Math.round(bb.top - above.bottom);
              return {text: t.scrollHeight - t.clientHeight,
                      content: c.scrollHeight - c.clientHeight,
                      dek: d.clientHeight ? d.scrollHeight - d.clientHeight : 0,
                      hedSqueeze: Math.max(0, Math.round(wanted - h.clientHeight)),
                      ctaGap: gap};
            }""")
            flags = []
            if fit["content"] > 0:
                flags.append(f"CONTENT OVERFLOW +{fit['content']}px")
            if fit["text"] > 0:
                flags.append(f"text overflow +{fit['text']}px")
            if fit["dek"] > 0:
                flags.append(f"dek clipped +{fit['dek']}px")
            if fit["hedSqueeze"] > 0:
                flags.append(f"HEADLINE SQUEEZED -{fit['hedSqueeze']}px")
            if fit["ctaGap"] is not None and fit["ctaGap"] < _CTA_MIN_GAP:
                flags.append(f"CTA OVERLAPS TEXT (gap {fit['ctaGap']}px)"
                             if fit["ctaGap"] < 0 else
                             f"cta gap only {fit['ctaGap']}px")
            print(f"  {w}x{h:<4} -> {path.name}  {'  '.join(flags) or 'fits'}")
            if (w, h) == SIZES[0]:
                ink = page.evaluate(_LOGO_INK_JS)
                if ink is None:
                    print("  logo    -> could not measure (no asset?)")
                elif ink["inkH"] < _LOGO_INK_MIN:
                    print(f"  logo    -> PADDED ASSET: mark fills {ink['inkH']:.0%} of the"
                          f" {ink['natW']}x{ink['natH']} file's height, so it renders"
                          f" {ink['inkH']:.0%} of --logo-h. Crop it to the mark.")
                else:
                    print(f"  logo    -> ok ({ink['natW']}x{ink['natH']},"
                          f" mark fills {ink['inkH']:.0%} of height)")
            page.close()
        browser.close()
    print(f"\nPNGs in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
