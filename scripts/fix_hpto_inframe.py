"""Rebuild parent-DOM-injection HPTO display customs to render IN-IFRAME,
so GAM Active View measures the real unit (docs/mobkoi_viewability.md —
"rendering in the measured iframe is the only way to move AV").

Target pattern (the QX65 NewsMakers Centerstage-June display strips, e.g.
creative 138562096700): a SafeFrame-off CustomCreative whose snippet
reaches into window.parent.document, innerHTML-replaces a `#dfp-ad-*`
slot div with `<a href='%%CLICK_URL_UNESC%%%%DEST_URL%%'><img
src='%%FILE:XXX%%'>`, and (uselessly — 1b) pings %%VIEW_URL_UNESC%% from
an IntersectionObserver. AV reads 0.00% viewable / 100% measurable.

The rebuild keeps the same GAM plumbing — click macro, destinationUrl,
attached %%FILE%% asset — and renders the image inside the creative's own
iframe, wrapped in the proven nw-fullbleed shim (scripts/
fullbleed_creative.py, live on creative 138562400069 since 2026-06-11):
the shim widens the slot div + GPT iframe chain to 100vw from the parent
document and scales the content, so the visual stays an edge-to-edge
strip while AV scores the iframe users actually see. The dead
view-macro watcher is dropped, not ported.

Visual placement note: the original paints a HARD-CODED div regardless of
which slot it serves into; the rebuild paints the slot it serves into.
Confirm via ad-unit sizes that the creative's size only fits its intended
slot (970x250 -> homepage4 only) before applying, or accept the strip
rendering at the serving slot.

Idempotent: creatives already carrying the marker are skipped, as is
anything that isn't a CustomCreative or doesn't match the injection
pattern (printed, untouched). The old snippet is printed in full to the
run log — rollback is pasting it back (or Creative history in the UI).

Env: CREATIVE_IDS (comma-separated; defaults to all six QX65
Centerstage-June injection customs — three 970x250 desktop strips
painting homepage2/3/4 + three 300x250 mobile, per the 2026-06-12
inspection on PR #198), APPLY (default dry-run).

The two Responsiveads "Radical" ThirdPartyCreatives on the same LI
(138562854616/854658) are left alone — they render in-iframe and
measure 76-87% already.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    with open(_env) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

V = "v202605"
MARKER = "nw-hpto-inframe"

_DEFAULT_IDS = (
    "138562096700,138562855828,138562855831,"   # 970x250 strips -> hp4/hp3/hp2
    "138562855921,138562856644,138562856647"    # 300x250 mobile -> hp3/hp4/hp3
)
CREATIVE_IDS = [
    s.strip() for s in (os.environ.get("CREATIVE_IDS") or _DEFAULT_IDS).split(",")
    if s.strip()
]
APPLY = (os.environ.get("APPLY") or "").lower() in ("1", "true", "yes")

# The fullbleed shim from scripts/fullbleed_creative.py (claude/
# fervent-euler-8j1821), v1 verbatim — proven live on 138562400069.
# __W__/__H__ = the rendered box; __ORIGINAL__ = in-iframe markup.
SHIM = """\
<!--nw-hpto-inframe v1-->
<style>html,body{margin:0;padding:0;overflow:hidden}
#nw-fb{transform-origin:0 0;width:__W__px;height:__H__px}</style>
<div id="nw-fb">
__ORIGINAL__
</div>
<script>
(function () {
  var BW = __W__, BH = __H__;
  function fit() {
    try {
      var f = window.frameElement;             /* null => safeframed: bail */
      if (!f) return;
      var D = window.top.document;
      var vw = D.documentElement.clientWidth;
      if (!vw || vw < 320) return;
      var s = vw / BW, h = Math.round(BH * s);
      var slot = f, hops = 0;                  /* climb to the dfp-ad-* div */
      while (slot && hops++ < 6 && !(slot.id && slot.id.indexOf('dfp-ad-') === 0))
        slot = slot.parentElement;
      if (slot) slot.style.cssText +=
        ';width:100vw;max-width:100vw;height:' + h +
        'px;margin-left:calc(50% - 50vw);overflow:visible;';
      var p = f.parentElement;                 /* GPT container div */
      if (p) { p.style.width = vw + 'px'; p.style.height = h + 'px'; }
      f.style.width = vw + 'px'; f.style.height = h + 'px';
      f.width = vw; f.height = h;
      document.getElementById('nw-fb').style.transform = 'scale(' + s + ')';
      document.body.style.width = vw + 'px';
      document.body.style.height = h + 'px';
    } catch (e) {}
  }
  fit();
  var n = 0, iv = setInterval(function () { fit(); if (++n >= 40) clearInterval(iv); }, 300);
  try { window.top.addEventListener('resize', fit); } catch (e) {}
})();
</script>"""

INFRAME = (
    "<a href='%%CLICK_URL_UNESC%%%%DEST_URL%%' target='_blank' rel='noopener'>"
    "<img src='%%FILE:__MACRO__%%' "
    "style='display:block;border:0;width:100%;height:auto'></a>"
)


def get_client():
    sa_json = os.environ.get("GAM_SERVICE_ACCOUNT_JSON")
    network_id = os.environ.get("GAM_NETWORK_ID")
    if not sa_json or not network_id:
        sys.exit("GAM_SERVICE_ACCOUNT_JSON / GAM_NETWORK_ID env vars not set")
    from googleads import ad_manager, oauth2  # type: ignore

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(json.loads(sa_json), f)
        key_file = f.name
    oc = oauth2.GoogleServiceAccountClient(
        key_file, "https://www.googleapis.com/auth/dfp"
    )
    return ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=network_id
    ), ad_manager


def main() -> int:
    client, ad_manager = get_client()
    cr_svc = client.GetService("CreativeService", version=V)
    cids = ", ".join(CREATIVE_IDS)
    stmt = ad_manager.StatementBuilder(version=V).Where(f"id IN ({cids})").Limit(50)
    creatives = cr_svc.getCreativesByStatement(stmt.ToStatement()).results or []
    print(f"mode: {'APPLY' if APPLY else 'DRY RUN'}   creatives: {CREATIVE_IDS}\n")

    to_update = []
    for cr in creatives:
        cid = cr["id"]
        print("=" * 72)
        print(f"Creative {cid}  [{type(cr).__name__}]  {cr['name']}")
        if type(cr).__name__ != "CustomCreative":
            print("  SKIP: not a CustomCreative")
            continue
        snippet = cr["htmlSnippet"] or ""
        if MARKER in snippet:
            print("  SKIP: already rebuilt (marker present)")
            continue
        if "parent.document" not in snippet or "innerHTML" not in snippet:
            print("  SKIP: doesn't match the parent-DOM injection pattern")
            continue
        m = re.search(r"%%FILE:([A-Za-z0-9_]+)%%", snippet)
        if not m:
            print("  SKIP: no %%FILE%% asset macro in snippet (not a static-"
                  "image injection — needs its own treatment)")
            continue
        macro = m.group(1)
        target = re.search(r"querySelector\(['\"]#?([\w-]+)['\"]\)", snippet)
        w, h = cr["size"]["width"], cr["size"]["height"]

        # Box aspect: the injected strip rendered width:100%/height:auto,
        # so the IMAGE's natural aspect ruled the height, not the declared
        # placeholder. The API's asset.size is degenerate here (1x1 — the
        # creatives were trafficked as 1x1), so fall back to the WxH the
        # asset filename carries (NW__..._3200x700.png).
        bw, bh = w, h
        assets = cr["customCreativeAssets"] or []
        for a in assets:
            if a["macroName"] != macro or not a["asset"]:
                continue
            fname = a["asset"]["fileName"] or ""
            nat = a["asset"]["size"]
            nw, nh = (nat["width"], nat["height"]) if nat else (0, 0)
            src = "asset.size"
            if not (nw and nh and nw > 1 and nh > 1):
                fm = re.search(r"(\d{2,5})x(\d{2,5})", fname)
                if fm:
                    nw, nh, src = int(fm.group(1)), int(fm.group(2)), "filename"
            if nw > 1 and nh > 1:
                bh = round(bw * nh / nw)
                print(f"  asset {fname}: natural {nw}x{nh} ({src}) "
                      f"-> box {bw}x{bh}")
            else:
                print(f"  asset {fname}: natural size UNKNOWN -> keeping "
                      f"declared box {bw}x{bh}")
        print(f"  declared size: {w}x{h}   injection target: "
              f"{target.group(1) if target else '?'}")
        print(f"  destinationUrl: {cr['destinationUrl']}")

        print("  --- OLD snippet (rollback copy) ---")
        print(snippet)

        new_snippet = (
            SHIM.replace("__W__", str(bw)).replace("__H__", str(bh))
            .replace("__ORIGINAL__", INFRAME.replace("__MACRO__", macro))
        )
        print("  --- NEW snippet ---")
        print(new_snippet)
        cr["htmlSnippet"] = new_snippet
        to_update.append(cr)

    if not to_update:
        print("\nnothing to update.")
        return 0
    if not APPLY:
        print(f"\nDRY RUN: would update {len(to_update)} creative(s): "
              f"{[c['id'] for c in to_update]}")
        return 0
    updated = cr_svc.updateCreatives(to_update)
    print(f"\nUPDATED {len(updated)} creative(s): {[c['id'] for c in updated]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
