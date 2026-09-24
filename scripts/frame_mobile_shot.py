#!/usr/bin/env python3
"""Wrap a mobile screenshot in an iPhone-style device frame.

For the proof-of-placement decks: a bare 390px-wide screenshot reads as a
cropped desktop page, not as the phone render it is. A device frame says
"mobile" before anyone reads the caption.

**It frames, it never paints over the capture.** No notch, no dynamic island,
no status bar is drawn on top of the screenshot — every captured pixel stays
visible, because the screenshot is the evidence. The frame is bezel, corner
rounding and side buttons, all of it outside the captured area.

Output is a transparent PNG, so it drops onto any slide panel.

Usage:
  python scripts/frame_mobile_shot.py shot.png [-o shot_framed.png]
  python scripts/frame_mobile_shot.py *.png --suffix _framed
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

# Proportions as fractions of the screenshot's width, taken from a 390pt-wide
# iPhone: 47pt display corner radius, ~12pt bezel, ~3.5pt of button sticking out.
R_SCREEN = 0.1205
BEZEL = 0.036
BUTTON_DEPTH = 0.0075

BODY = (31, 30, 25, 255)      # --ink, the deck's frame color
RIM = (74, 71, 62, 255)       # a hair lighter, so the edge reads as a band
SS = 4                        # supersample factor — Pillow's shapes don't antialias

# (side, top as a fraction of body height, length as a fraction of body height).
# Left: silent switch, then the two volume keys. Right: the side button.
BUTTONS = (
    ("l", 0.150, 0.032),
    ("l", 0.212, 0.072),
    ("l", 0.298, 0.072),
    ("r", 0.232, 0.116),
)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """An antialiased rounded-rectangle mask, drawn big and scaled down."""
    w, h = size
    big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def frame(shot: Image.Image) -> Image.Image:
    shot = shot.convert("RGBA")
    w, h = shot.size
    bezel = max(2, round(BEZEL * w))
    btn = max(1, round(BUTTON_DEPTH * w))
    r_screen = round(R_SCREEN * w)
    r_body = r_screen + bezel

    body_w, body_h = w + 2 * bezel, h + 2 * bezel
    out = Image.new("RGBA", (body_w + 2 * btn, body_h), (0, 0, 0, 0))

    # Side buttons first, so the body paints over their inner half and they
    # read as protruding from it rather than sitting on top.
    for side, y0, frac in BUTTONS:
        x0 = 0 if side == "l" else out.width - btn * 2
        top = round(body_h * y0)
        bot = top + round(body_h * frac)
        bar = Image.new("RGBA", (btn * 2, bot - top), RIM)
        out.paste(bar, (x0, top), _rounded_mask(bar.size, btn))

    body = Image.new("RGBA", (body_w, body_h), BODY)
    body_mask = _rounded_mask((body_w, body_h), r_body)
    out.paste(body, (btn, 0), body_mask)

    # A one-pixel-ish rim inside the outer edge: the band around a real device.
    rim = Image.new("RGBA", (body_w, body_h), RIM)
    inner = _rounded_mask((body_w - 2 * max(1, bezel // 8),
                           body_h - 2 * max(1, bezel // 8)), r_body)
    rim_mask = body_mask.copy()
    rim_mask.paste(0, (max(1, bezel // 8), max(1, bezel // 8)), inner)
    out.paste(rim, (btn, 0), rim_mask)

    out.paste(shot, (btn + bezel, bezel), _rounded_mask((w, h), r_screen))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path,
                    help="output path (single input only)")
    ap.add_argument("--suffix", default="_framed",
                    help="appended to each stem when --out is not given")
    args = ap.parse_args()

    if args.out and len(args.shots) > 1:
        ap.error("--out takes a single input; use --suffix for several")

    for path in args.shots:
        dest = args.out or path.with_name(f"{path.stem}{args.suffix}{path.suffix}")
        framed = frame(Image.open(path))
        framed.save(dest)
        print(f"{path.name} {Image.open(path).size} -> {dest.name} {framed.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
