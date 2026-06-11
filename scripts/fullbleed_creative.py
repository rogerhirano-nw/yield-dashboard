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

Two paths by creative type:
  - ThirdPartyCreative / CustomCreative: wrap the snippet in place.
  - ImageCreative (no snippet to wrap): SWAP — create a CustomCreative
    that reproduces the image + click-through (%%CLICK_URL_UNESC%%%%DEST_URL%%
    keeps GAM click counting + the DCM clicktracker) + the third-party
    impression pixels (%%CACHEBUSTER%% expanded by GAM; ${GDPR*} macros
    stripped — US flight, GAM doesn't expand them in snippets), associate
    it to the line item under the SAME LICA targetingName as the original
    (so the article scoping carries over), then deactivate the original's
    LICA. Requires --line-item-id; refuses to run if the original LICA has
    no targetingName (run restrict_creative_to_article.py first so the
    breakout stays scoped).

Idempotent: a `nw-fullbleed` marker / lookup-first by name make re-runs
no-ops. Rollback: re-activate the original LICA and deactivate the new
one in the GAM UI; the output archives everything needed.

Usage:
    python scripts/fullbleed_creative.py --creative-id 138557893457 \
        --line-item-id 7309466805            # dry run
    ... --apply                              # write
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


def _g(obj, key, default=None):
    """Field access tolerant of zeep types that lack the field entirely."""
    try:
        return obj[key]
    except Exception:
        return default


def _image_markup(cr):
    """Reproduce an ImageCreative as snippet markup: clickable image +
    third-party impression pixels."""
    import re
    w, h = cr['size']['width'], cr['size']['height']
    parts = [
        f'<a href="%%CLICK_URL_UNESC%%%%DEST_URL%%" target="_blank" rel="noopener">'
        f'<img src="{cr["primaryImageAsset"]["assetUrl"]}" width="{w}" height="{h}" '
        f'style="display:block;border:0;width:{w}px;height:{h}px" alt=""></a>'
    ]
    for u in (cr['thirdPartyImpressionTrackingUrls'] or []):
        u = re.sub(r"\$\{GDPR[^}]*\}", "", u)  # US flight; not expanded in snippets
        parts.append(f'<img src="{u}" width="1" height="1" '
                     f'style="position:absolute;left:-9999px" alt="">')
    return "\n".join(parts)


def swap_image_creative(args, ad_manager, cr_svc, lica_svc, cr, w, h):
    """ImageCreative path: new fullbleed CustomCreative + LICA swap."""
    def stmt(where, **binds):
        sb = ad_manager.StatementBuilder(version=V).Where(where).Limit(50)
        for k, v in binds.items():
            sb.WithBindVariable(k, v)
        return sb.ToStatement()

    if not args.line_item_id:
        sys.exit("ImageCreative has no snippet to wrap — swap path needs "
                 "--line-item-id to re-associate a fullbleed CustomCreative")

    old_lica = (getattr(lica_svc.getLineItemCreativeAssociationsByStatement(stmt(
        "lineItemId = :li AND creativeId = :cr",
        li=args.line_item_id, cr=cr['id'])), "results", None) or [None])[0]
    if old_lica is None:
        sys.exit(f"Creative {cr['id']} is not associated with LI {args.line_item_id}")
    tname = old_lica['targetingName']
    if not tname:
        sys.exit("Original LICA has no targetingName — run "
                 "restrict_creative_to_article.py first so the fullbleed "
                 "swap inherits the article scoping")

    new_name = (cr['name'] + " (fullbleed)")[:255]
    markup = _image_markup(cr)
    wrapped = (SHIM.replace("__W__", str(w)).replace("__H__", str(h))
               .replace("__ORIGINAL__", markup))

    print(f"  asset: {cr['primaryImageAsset']['fileName']}")
    print(f"  destinationUrl (DCM clicktracker): {cr['destinationUrl']}")
    print(f"  impression pixels: {len(cr['thirdPartyImpressionTrackingUrls'] or [])}")
    print(f"  old LICA: status={old_lica['status']}  targetingName={tname}")

    existing = (getattr(cr_svc.getCreativesByStatement(stmt(
        "name = :n", n=new_name)), "results", None) or [None])[0]

    print(f"\n── fullbleed CustomCreative htmlSnippet " + "─" * 30)
    print(wrapped)
    print(f"\nPlan:")
    print(f"  1. CustomCreative {new_name!r} size {w}x{h}, SafeFrame off"
          + (f"  [exists: id={existing['id']}]" if existing is not None else "  [create]"))
    print(f"  2. LICA on LI {args.line_item_id} with targetingName={tname}")
    print(f"  3. deactivate old LICA (creative {cr['id']}) — image creative "
          f"stays in GAM untouched for rollback")

    if not args.apply:
        print("\nDry run only — re-run with --apply to write to GAM.")
        return

    print()
    new_cr = existing
    if new_cr is None:
        new_cr = cr_svc.createCreatives([{
            "xsi_type": "CustomCreative",
            "name": new_name,
            "advertiserId": cr['advertiserId'],
            "size": {"width": w, "height": h, "isAspectRatio": False},
            "destinationUrl": cr['destinationUrl'],
            "htmlSnippet": wrapped,
            "isSafeFrameCompatible": False,
        }])[0]
        print(f"Created CustomCreative id={new_cr['id']}")
    else:
        print(f"CustomCreative already exists id={new_cr['id']} — reusing")

    try:
        lica = lica_svc.createLineItemCreativeAssociations([{
            "lineItemId": args.line_item_id,
            "creativeId": new_cr['id'],
            "targetingName": tname,
        }])[0]
        print(f"LICA created: cr={lica['creativeId']}  status={lica['status']}  "
              f"targetingName={lica['targetingName']}")
    except Exception as e:
        if "CommonError.ALREADY_EXISTS" not in str(e):
            raise
        print(f"LICA for cr={new_cr['id']} already exists — skipping create")

    res = lica_svc.performLineItemCreativeAssociationAction(
        {"xsi_type": "DeactivateLineItemCreativeAssociations"},
        stmt("lineItemId = :li AND creativeId = :cr",
             li=args.line_item_id, cr=cr['id']))
    print(f"Old LICA deactivated ({getattr(res, 'numChanges', '?')} change(s))")

    print(f"\nDone. Fullbleed creative {new_cr['id']} now serves in place of "
          f"{cr['id']} under {tname!r}.")
    print(f"Preview: {_g(new_cr, 'previewUrl') or '(open from the LI in the GAM UI)'}")
    print(f"QA on the targeted article (desktop): unit should span the full "
          f"viewport width, scaled from {w}x{h}.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--creative-id", type=int, required=True)
    p.add_argument("--line-item-id", type=int, default=None,
                   help="required for the ImageCreative swap path")
    p.add_argument("--apply", action="store_true", help="write to GAM (default: dry run)")
    args = p.parse_args()

    client, ad_manager = get_client()
    cr_svc = client.GetService("CreativeService", version=V)
    lica_svc = client.GetService("LineItemCreativeAssociationService", version=V)
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
    size = _g(cr, 'size')
    w, h = (size['width'], size['height']) if size else (0, 0)
    print(f"  type={ctype}  size={w}x{h}  "
          f"isSafeFrameCompatible={_g(cr, 'isSafeFrameCompatible')}")
    if ctype == "ImageCreative":
        swap_image_creative(args, ad_manager, cr_svc, lica_svc, cr, w, h)
        return
    if field is None:
        print(f"\nUnsupported creative type {ctype!r} — only ThirdPartyCreative /")
        print("CustomCreative (wrap) and ImageCreative (swap) are handled.")
        print("Full object for reference:")
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
