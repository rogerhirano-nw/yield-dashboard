"""Make a GAM display creative render FULL BLEED — edge-to-edge viewport
width — by wrapping its snippet in a breakout shim.

The shim keeps the original (agency) tag rendering at its declared size so
impressions/pixels/clicks are untouched, and adds JS that, from the parent
document (requires SafeFrame OFF, same trick as the sponsor-logo creative —
docs/article_sponsor_logo.md):

  - widens the `dfp-ad-*` slot container to 100vw and pulls it out of the
    centered content column (margin-left: calc(50% - 50vw))
  - resizes the GPT iframe chain to viewport width
  - scales the creative content proportionally (970x250 → vw x vw*250/970)
  - re-fits on resize, and re-asserts for ~12s to survive Next.js
    hydration re-renders

Scope safety: pair this with creative-level article targeting
(scripts/restrict_creative_to_article.py) so the breakout only ever runs
where intended. In a SafeFrame or cross-origin iframe the shim does
nothing and the creative renders normally at its declared size.

Idempotent: a `nw-fullbleed` marker in the snippet makes re-runs no-ops.
Rollback: the dry-run/apply output archives the original snippet — paste
it back in the GAM UI (and restore SafeFrame) to revert.

Usage:
    python scripts/fullbleed_creative.py --creative-id 138557893457          # dry run
    python scripts/fullbleed_creative.py --creative-id 138557893457 --apply  # write
"""

import argparse
import json
import os
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
MARKER = "nw-fullbleed"

# __W__/__H__ = declared creative size; __ORIGINAL__ = untouched agency tag.
SHIM = """\
<!--nw-fullbleed v1-->
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


def get_client():
    sa_json = os.environ.get("GAM_SERVICE_ACCOUNT_JSON")
    network_id = os.environ.get("GAM_NETWORK_ID")
    if not sa_json or not network_id:
        sys.exit("GAM_SERVICE_ACCOUNT_JSON / GAM_NETWORK_ID env vars not set")
    from googleads import ad_manager, oauth2  # type: ignore

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(json.loads(sa_json), f)
        key_file = f.name
    oc = oauth2.GoogleServiceAccountClient(key_file, "https://www.googleapis.com/auth/dfp")
    return ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=network_id
    ), ad_manager


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--creative-id", type=int, required=True)
    p.add_argument("--apply", action="store_true", help="write to GAM (default: dry run)")
    args = p.parse_args()

    client, ad_manager = get_client()
    cr_svc = client.GetService("CreativeService", version=V)
    sb = (ad_manager.StatementBuilder(version=V)
          .Where("id = :id").WithBindVariable("id", args.creative_id).Limit(1))
    cr = (getattr(cr_svc.getCreativesByStatement(sb.ToStatement()), "results", None)
          or [None])[0]
    if cr is None:
        sys.exit(f"Creative {args.creative_id} not found")

    ctype = type(cr).__name__
    # snippet field differs by creative type
    field = ("snippet" if ctype == "ThirdPartyCreative"
             else "htmlSnippet" if ctype == "CustomCreative" else None)

    print("=" * 72)
    print(f"FULLBLEED CREATIVE  ({'APPLY' if args.apply else 'DRY RUN'})")
    print("=" * 72)
    print(f"Creative {cr['id']}: {cr['name']}")
    w, h = cr['size']['width'], cr['size']['height']
    print(f"  type={ctype}  size={w}x{h}  "
          f"isSafeFrameCompatible={cr['isSafeFrameCompatible']}")
    if field is None:
        print(f"\nUnsupported creative type {ctype!r} — only ThirdPartyCreative /")
        print("CustomCreative snippets can be wrapped. Full object for reference:")
        print(cr)
        sys.exit(1)

    original = cr[field] or ""
    if MARKER in original:
        print(f"\nAlready wrapped ({MARKER!r} marker found) — nothing to do.")
        print("Current snippet:\n" + original)
        return

    wrapped = (SHIM.replace("__W__", str(w)).replace("__H__", str(h))
               .replace("__ORIGINAL__", original))

    print(f"\n── current {field} (archive for rollback) " + "─" * 26)
    print(original)
    print("\n── wrapped snippet to be written " + "─" * 35)
    print(wrapped)
    print()
    print("Changes: %s wrapped in fullbleed shim; isSafeFrameCompatible → False" % field)

    if not args.apply:
        print("\nDry run only — re-run with --apply to write to GAM.")
        return

    cr[field] = wrapped
    cr['isSafeFrameCompatible'] = False
    cr = cr_svc.updateCreatives([cr])[0]
    print(f"\nUpdated creative {cr['id']} — isSafeFrameCompatible="
          f"{cr['isSafeFrameCompatible']}, {field} length {len(cr[field])}.")
    print("QA on the targeted article (desktop): unit should span the full")
    print("viewport width, scaled from %dx%d." % (w, h))


if __name__ == "__main__":
    main()
