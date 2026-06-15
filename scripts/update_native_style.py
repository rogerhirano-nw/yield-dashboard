#!/usr/bin/env python
"""Read or patch GAM newsletter native styles (htmlSnippet / cssSnippet).

Runs in CI (``.github/workflows/update_native_style.yml``) so GAM *write*
credentials stay in Actions secrets — cloud sessions have no local GAM
creds. Creds come from GAM_SERVICE_ACCOUNT_JSON / GAM_NETWORK_ID in the env
(set by the workflow from repo secrets).

Why: the 600x314 newsletter native renders the image at full height *and*
stacks the headline/body, so total content exceeds the 314px frame and the
text is clipped when GAM rasterizes the style to the email image. The fix
caps the rendered ad to the frame and constrains the image so the text stays
visible.

Modes:
  (default / --list)   Dump every native style — id, name, size, html, css.
                       Read-only. This is what the push-triggered run does.
  --style-id <id>      Target one style for the overflow fix. Without --apply
                       this is a dry-run: prints current + proposed cssSnippet
                       and writes nothing.
  --apply              Actually write the patch via NativeStyleService.
  --image-height <px>  Image height cap for the image+text layout (default
                       180 — leaves ~134px of the 314 frame for the text).
  --mode image-only    Image fills the whole 600x314 frame (text dropped /
                       overlaid) instead of sharing height with the text.

The fix is an idempotent CSS block appended to cssSnippet, delimited by
markers so a re-run replaces it instead of stacking copies. It overrides by
source order (it is last) plus !important on the geometry props, so the cap
wins over the template's own rules.

Usage:
    python scripts/update_native_style.py --list
    python scripts/update_native_style.py --style-id 12345            # dry-run
    python scripts/update_native_style.py --style-id 12345 --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gam_client import GAMClient  # noqa: E402

_FIX_START = "/* nw-overflow-fix:start */"
_FIX_END = "/* nw-overflow-fix:end */"


def _fix_block(image_height: int, mode: str) -> str:
    """The corrective CSS, wrapped in idempotency markers."""
    if mode == "image-only":
        body = (
            "html,body{margin:0!important;padding:0!important;}"
            "body{width:600px!important;height:314px!important;"
            "overflow:hidden!important;box-sizing:border-box!important;}"
            "img{display:block!important;width:600px!important;"
            "height:314px!important;object-fit:cover!important;}"
        )
    else:  # image-text (default)
        body = (
            "*{box-sizing:border-box!important;}"
            "html,body{margin:0!important;padding:0!important;}"
            "body{width:600px!important;height:314px!important;"
            "overflow:hidden!important;}"
            f"img{{display:block!important;width:100%!important;"
            f"height:{image_height}px!important;object-fit:cover!important;}}"
        )
    return f"{_FIX_START}\n{body}\n{_FIX_END}"


def apply_fix(css: str | None, image_height: int, mode: str) -> str:
    """Append (or replace, if already present) the corrective CSS block."""
    block = _fix_block(image_height, mode)
    css = css or ""
    if _FIX_START in css and _FIX_END in css:
        return re.sub(
            re.escape(_FIX_START) + r".*?" + re.escape(_FIX_END),
            lambda _m: block,
            css,
            flags=re.S,
        )
    sep = "" if (not css or css.endswith("\n")) else "\n"
    return f"{css}{sep}\n{block}\n"


_BG_START = "/* nw-bg:start */"
_BG_END = "/* nw-bg:end */"


def apply_background(css: str | None, color: str) -> str:
    """Append (or replace) a marker block forcing the background color on the
    root containers (.pb logo / .bt banner / .sc sponsored) + html,body, so the
    rasterized ad matches the newsletter canvas. Idempotent."""
    if not color.startswith("#"):
        color = "#" + color
    block = (f"{_BG_START}\n"
             f"html,body{{background:{color}!important}}"
             f".pb,.bt,.sc{{background:{color}!important}}\n"
             f"{_BG_END}")
    css = css or ""
    if _BG_START in css and _BG_END in css:
        return re.sub(re.escape(_BG_START) + r".*?" + re.escape(_BG_END),
                      lambda _m: block, css, flags=re.S)
    sep = "" if (not css or css.endswith("\n")) else "\n"
    return f"{css}{sep}\n{block}\n"


_SCTEXT_START = "/* nw-sctext:start */"
_SCTEXT_END = "/* nw-sctext:end */"


def apply_sc_text(css: str | None, color: str) -> str:
    """Override the Sponsored Content `.sc-link` color (which makes the
    headline/body/CTA render as blue underlined links over the design's black
    text). Pass 'inherit' to restore the per-section colors (black headline /
    dark body), or a hex. Drops the underline. Idempotent."""
    if re.fullmatch(r"[0-9a-fA-F]{3,6}", color or ""):
        color = "#" + color
    block = (f"{_SCTEXT_START}\n"
             f".sc-link{{color:{color}!important;text-decoration:none!important}}\n"
             f"{_SCTEXT_END}")
    css = css or ""
    if _SCTEXT_START in css and _SCTEXT_END in css:
        return re.sub(re.escape(_SCTEXT_START) + r".*?" + re.escape(_SCTEXT_END),
                      lambda _m: block, css, flags=re.S)
    sep = "" if (not css or css.endswith("\n")) else "\n"
    return f"{css}{sep}\n{block}\n"


_CTA_START = "/* nw-cta:start */"
_CTA_END = "/* nw-cta:end */"


def apply_cta_color(css: str | None, color: str) -> str:
    """Style only the Sponsored Content CTA (the last `.sc-body` link, since the
    CTA shares the body class) as a link — e.g. the newsletter's blue +
    underline — while the headline and body stay black. Idempotent."""
    if re.fullmatch(r"[0-9a-fA-F]{3,6}", color or ""):
        color = "#" + color
    block = (f"{_CTA_START}\n"
             f".sc-content .sc-body:last-child a"
             f"{{color:{color}!important;text-decoration:underline!important}}\n"
             f"{_CTA_END}")
    css = css or ""
    if _CTA_START in css and _CTA_END in css:
        return re.sub(re.escape(_CTA_START) + r".*?" + re.escape(_CTA_END),
                      lambda _m: block, css, flags=re.S)
    sep = "" if (not css or css.endswith("\n")) else "\n"
    return f"{css}{sep}\n{block}\n"


def _dump(s: dict) -> None:
    print("-" * 72)
    aspect = " (aspect-ratio)" if s.get("is_aspect_ratio") else ""
    print(
        f"id={s['id']}  name={s['name']!r}  "
        f"size={s['width']}x{s['height']}{aspect}  "
        f"status={s['status']}  creative_template_id={s['creative_template_id']}"
    )
    print("  --- htmlSnippet ---")
    print((s.get("html_snippet") or "").rstrip() or "  (empty)")
    print("  --- cssSnippet ---")
    print((s.get("css_snippet") or "").rstrip() or "  (empty)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Read or patch GAM newsletter native styles."
    )
    ap.add_argument(
        "--list", action="store_true", help="dump every native style (read-only)"
    )
    ap.add_argument("--style-id", help="native style id to patch")
    ap.add_argument(
        "--image-height",
        type=int,
        default=180,
        help="image height cap (px) for the image+text layout",
    )
    ap.add_argument(
        "--mode", choices=["image-text", "image-only"], default="image-text"
    )
    ap.add_argument(
        "--apply", action="store_true", help="write the patch (default: dry-run)"
    )
    ap.add_argument(
        "--inspect-creative", help="dump a creative's size + native template vars (read-only)"
    )
    ap.add_argument(
        "--inspect-li", help="dump line-item <-> creative associations for an LI (read-only)"
    )
    ap.add_argument(
        "--create-from", help="clone this native style id into a new size (Option 3)"
    )
    ap.add_argument("--new-width", type=int, default=600)
    ap.add_argument("--new-height", type=int, help="height for the cloned style")
    ap.add_argument(
        "--new-name", help="name for the new style (default: source name, size swapped)"
    )
    ap.add_argument(
        "--paper-bg", action="store_true",
        help="append an html,body paper background so the taller frame fills cleanly",
    )
    ap.add_argument("--set-background", help="set background color (hex) on --style-ids")
    ap.add_argument("--sc-text-color",
                    help="override .sc-link text color on --style-ids ('inherit' or hex)")
    ap.add_argument("--cta-color",
                    help="style the Sponsored Content CTA link as blue/underline (hex) on --style-ids")
    ap.add_argument("--style-ids", help="comma-separated native style ids for the bulk modes")
    args = ap.parse_args()

    gam = GAMClient()

    if args.inspect_creative or args.inspect_li:
        import pprint
        if args.inspect_creative:
            print(f"== creative {args.inspect_creative} ==")
            pprint.pprint(gam.get_creative_detail(args.inspect_creative))
        if args.inspect_li:
            df = gam.list_line_item_creative_associations([args.inspect_li])
            print(f"== LICAs for LI {args.inspect_li} ==")
            print(df.to_string(index=False) if not df.empty else "(none)")
        return 0

    styles = gam.list_native_styles()

    if args.set_background or args.sc_text_color or args.cta_color:
        if args.set_background:
            label = f"background {args.set_background}"
            transform = lambda css: apply_background(css, args.set_background)  # noqa: E731
        elif args.sc_text_color:
            label = f"sc-link color {args.sc_text_color}"
            transform = lambda css: apply_sc_text(css, args.sc_text_color)  # noqa: E731
        else:
            label = f"cta link color {args.cta_color}"
            transform = lambda css: apply_cta_color(css, args.cta_color)  # noqa: E731
        by_id = {s["id"]: s for s in styles}
        ids = [s.strip() for s in (args.style_ids or "").split(",") if s.strip()]
        if not ids:
            print("::error::--style-ids is required", file=sys.stderr)
            return 1
        for sid in ids:
            cur = by_id.get(sid)
            if not cur:
                print(f"::error::native style {sid} not found", file=sys.stderr)
                continue
            new_css = transform(cur.get("css_snippet"))
            print(f"== {sid} {cur['name']!r} ({cur['width']}x{cur['height']}) -> {label} ==")
            if not args.apply:
                print("(dry-run — add --apply to write)")
                continue
            gam.update_native_style(sid, css_snippet=new_css)
            print(f"::notice::applied {label} on native style {sid}")
        return 0

    if args.create_from:
        by_id = {s["id"]: s for s in styles}
        src = by_id.get(str(args.create_from))
        if not src:
            print(f"::error::source native style {args.create_from} not found", file=sys.stderr)
            return 1
        if not args.new_height:
            print("::error::--new-height is required with --create-from", file=sys.stderr)
            return 1
        w, h = args.new_width, args.new_height
        name = args.new_name
        if not name:
            base = src.get("name") or "Native Style"
            if re.search(r"\(\d+x\d+\)", base):
                name = re.sub(r"\(\d+x\d+\)", f"({w}x{h})", base)
            else:
                name = f"{base} ({w}x{h})"
        new_html = src.get("html_snippet")
        new_css = (src.get("css_snippet") or "")
        if args.paper_bg:
            new_css = new_css.rstrip() + "\nhtml,body{margin:0;padding:0;background:#f5f0e8}\n"
        print("=" * 72)
        print(f"CREATE native style: name={name!r}  size={w}x{h}  "
              f"template={src.get('creative_template_id')}  (cloned from {args.create_from})")
        print("--- htmlSnippet ---")
        print((new_html or "").rstrip())
        print("--- cssSnippet ---")
        print(new_css.rstrip())
        if not args.apply:
            print("::notice::dry-run — nothing created. Add --apply (or dispatch) to create.")
            return 0
        res = gam.create_native_style_from(
            args.create_from, width=w, height=h, name=name,
            html_snippet=new_html, css_snippet=new_css,
        )
        print(f"::notice::created native style {res['id']} ({res['width']}x{res['height']}) "
              f"name={res['name']!r} status={res['status']} template={res['creative_template_id']}")
        return 0

    if args.list or not args.style_id:
        print(f"== {len(styles)} native style(s) ==")
        for s in styles:
            _dump(s)
        if not args.style_id:
            return 0

    by_id = {s["id"]: s for s in styles}
    cur = by_id.get(str(args.style_id))
    if not cur:
        have = ", ".join(sorted(by_id)) or "none"
        print(f"::error::native style {args.style_id} not found (have: {have})",
              file=sys.stderr)
        return 1

    new_css = apply_fix(cur.get("css_snippet"), args.image_height, args.mode)
    print("=" * 72)
    print(
        f"TARGET id={cur['id']} name={cur['name']!r} "
        f"size={cur['width']}x{cur['height']}  "
        f"mode={args.mode} image_height={args.image_height}"
    )
    print("--- current cssSnippet ---")
    print((cur.get("css_snippet") or "").rstrip() or "  (empty)")
    print("--- proposed cssSnippet ---")
    print(new_css.rstrip())

    if not args.apply:
        print(
            "::notice::dry-run — nothing written. Re-run with --apply "
            "(or dispatch with apply=true) to update GAM."
        )
        return 0

    res = gam.update_native_style(cur["id"], css_snippet=new_css)
    print(f"::notice::updated native style {res['id']} (cssSnippet patched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
