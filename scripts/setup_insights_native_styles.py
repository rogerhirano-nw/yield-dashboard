#!/usr/bin/env python3
"""Create the three fixed-size "Insights" native styles in GAM.

Creative template 12412102 ("Insights Premium Spotlight", native-eligible:
TITLE / SUBTITLE / HASHTAG / IMAGE / LOGO / 3RDPARTYTRACKING1-2) already has
one native style -- id 989975, fluid 1x1, targeted at `homepage3`. That style
is what renders the in-article Insights card. It has no fixed-size sibling, so
the same native creative cannot serve a standard banner slot.

This adds three, all on the same template, sharing one markup + one stylesheet
(docs/snippets/insights_native_style.{html,css} -- the layout is picked by
media query off the slot viewport):

    Insights Premium Spotlight (970x250)
    Insights Premium Spotlight (728x90)
    Insights Premium Spotlight (300x250)

A native style is scoped to its creative template, so site-wide targeting here
only changes how *Insights* creatives render -- no other native creative in a
300x250 slot is affected.

Lookup-first: a style that already exists by name is skipped (or refreshed with
--update), so the script is safe to re-run after a partial apply.

Usage:
    python3 scripts/setup_insights_native_styles.py                 # dry run
    python3 scripts/setup_insights_native_styles.py --apply         # create
    python3 scripts/setup_insights_native_styles.py --apply --update  # + push
                                                    # html/css to existing ones
    python3 scripts/setup_insights_native_styles.py --apply --ad-unit 23207094803
"""

import argparse
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent

# ── .env ──────────────────────────────────────────────────────────────────────
_env = REPO / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from googleads import ad_manager, oauth2  # noqa: E402

V = "v202605"

# ── config ────────────────────────────────────────────────────────────────────
CREATIVE_TEMPLATE_ID = 12412102          # "Insights Premium Spotlight"
# `newsweek` site root -- includeDescendants, so every descendant slot of the
# right size can render the unit. Narrow it with --ad-unit for a pilot.
DEFAULT_AD_UNIT_ID = 23207092721

SIZES = [(970, 250), (728, 90), (300, 250)]
STYLE_NAME = "Insights Premium Spotlight ({w}x{h})"

HTML_PATH = REPO / "docs" / "snippets" / "insights_native_style.html"
CSS_PATH = REPO / "docs" / "snippets" / "insights_native_style.css"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="create in GAM (default: dry run)")
    ap.add_argument("--update", action="store_true",
                    help="also push the local html/css onto styles that already exist")
    ap.add_argument("--ad-unit", type=int, default=DEFAULT_AD_UNIT_ID,
                    help=f"ad unit to target (default {DEFAULT_AD_UNIT_ID}, the newsweek site root)")
    ap.add_argument("--style-ids",
                    help="comma-separated NativeStyle ids to push the local html/css onto, "
                         "whatever template or name they carry. Use this to update styles this "
                         "script did not create (e.g. the live 'Native (WxH)' set on template "
                         "12552841, which runs byte-identical copies of these files). Everything "
                         "else -- lookup-by-name, create -- is skipped.")
    args = ap.parse_args()

    html = HTML_PATH.read_text()
    css = CSS_PATH.read_text()

    sa = json.loads(os.environ["GAM_SERVICE_ACCOUNT_JSON"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sa, f)
        keyfile = f.name
    oc = oauth2.GoogleServiceAccountClient(keyfile, "https://www.googleapis.com/auth/dfp")
    client = ad_manager.AdManagerClient(
        oc, "NewsweekDashboard/1.0", network_code=os.environ["GAM_NETWORK_ID"]
    )
    svc = client.GetService("NativeStyleService", version=V)

    targeting = {
        "inventoryTargeting": {
            "targetedAdUnits": [{"adUnitId": str(args.ad_unit), "includeDescendants": True}]
        }
    }

    if args.style_ids:
        ids = [int(x) for x in args.style_ids.split(",") if x.strip()]
        print("=" * 72)
        print(f"PUSH STYLE HTML/CSS TO {ids}  ({'APPLY' if args.apply else 'DRY RUN'})")
        print("=" * 72)
        found = list(getattr(svc.getNativeStylesByStatement(
            ad_manager.StatementBuilder(version=V)
            .Where(f"id IN ({', '.join(str(i) for i in ids)})").Limit(50).ToStatement()),
            "results", []) or [])
        for st in found:
            same = (st.cssSnippet or "") == css and (st.htmlSnippet or "") == html
            print(f"  {st.id} {st.name!r} tmpl={st.creativeTemplateId} "
                  f"size={(st.size.width, st.size.height)} status={st.status} "
                  f"{'already current' if same else 'WILL UPDATE'}")
        missing = set(ids) - {s_.id for s_ in found}
        if missing:
            print(f"  !! not found: {sorted(missing)}")
        if not args.apply:
            print("\nRe-run with --apply to write to GAM.")
            return 0
        for st in found:
            st.htmlSnippet, st.cssSnippet = html, css
            out = svc.updateNativeStyles([st])[0]
            print(f"updated {out.id} {out.name!r} status={out.status}")
        return 0

    print("=" * 72)
    print(f"INSIGHTS NATIVE STYLES  ({'APPLY' if args.apply else 'DRY RUN'})")
    print("=" * 72)
    print(f"template   : {CREATIVE_TEMPLATE_ID}")
    print(f"ad unit    : {args.ad_unit} (includeDescendants)")
    print(f"markup     : {HTML_PATH.relative_to(REPO)} ({len(html)} chars)")
    print(f"stylesheet : {CSS_PATH.relative_to(REPO)} ({len(css)} chars)")
    print()

    to_create, to_update = [], []
    for w, h in SIZES:
        name = STYLE_NAME.format(w=w, h=h)
        stmt = (ad_manager.StatementBuilder(version=V)
                .Where("name = :n").WithBindVariable("n", name).Limit(1).ToStatement())
        found = list(getattr(svc.getNativeStylesByStatement(stmt), "results", []) or [])
        if found:
            existing = found[0]
            if args.update:
                existing.htmlSnippet = html
                existing.cssSnippet = css
                to_update.append(existing)
                print(f"  {name:44s} [exists id={existing.id} -> will refresh html/css]")
            else:
                print(f"  {name:44s} [exists id={existing.id} -> skip]")
            continue
        to_create.append({
            "name": name,
            "htmlSnippet": html,
            "cssSnippet": css,
            "creativeTemplateId": CREATIVE_TEMPLATE_ID,
            "isFluid": False,
            "size": {"width": w, "height": h, "isAspectRatio": False},
            "targeting": targeting,
        })
        print(f"  {name:44s} [will create]")

    if not args.apply:
        print("\nRe-run with --apply to write to GAM.")
        return 0

    for style in svc.createNativeStyles(to_create) if to_create else []:
        print(f"created  id={style.id}  {style.name}  status={style.status}")
    for style in svc.updateNativeStyles(to_update) if to_update else []:
        print(f"updated  id={style.id}  {style.name}")

    print("\nNext (GAM UI, not automated here): add 970x250 / 728x90 / 300x250 to the "
          "line item's creative placeholders so the 1x1 native creative is eligible "
          "for those slots.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
