#!/usr/bin/env python3
"""Wrap a proof-of-placement screenshot in a device frame — phone or laptop.

For the screenshots decks: a bare screenshot reads as a cropped page, not as
the render it is. A device frame says "phone" or "desktop" before anyone reads
the caption.

**It frames, it never paints over the capture.** Nothing is drawn on top of
the screenshot — no notch, no dynamic island, no status bar, no menu bar.
Every captured pixel stays visible, because the screenshot is the evidence and
a deck that retouches it is worth less than one that doesn't. The MacBook
notch lives in the top bezel, above the capture, for exactly that reason.

Output is a transparent PNG, so it drops onto any slide panel.

Usage:
  python scripts/frame_device_shot.py shot.png [-o shot_framed.png]
  python scripts/frame_device_shot.py *.png --device laptop --suffix _framed

--device defaults to `auto`: portrait shots get the phone, landscape the
laptop.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

BODY = (31, 30, 25, 255)      # --ink, the deck's frame color
RIM = (74, 71, 62, 255)       # a hair lighter, so the edge reads as a band
SS = 4                        # supersample factor — Pillow's shapes don't antialias

# --- phone, as fractions of the screenshot's width -------------------------
# Taken from a 390pt-wide iPhone: 47pt display corner radius, ~12pt bezel,
# ~3.5pt of button sticking out.
P_R_SCREEN = 0.1205
P_BEZEL = 0.036
P_BUTTON_DEPTH = 0.0075

# (side, top as a fraction of body height, length as a fraction of body height).
# Left: silent switch, then the two volume keys. Right: the side button.
P_BUTTONS = (
    ("l", 0.150, 0.032),
    ("l", 0.212, 0.072),
    ("l", 0.298, 0.072),
    ("r", 0.232, 0.116),
)

# --- laptop, as fractions of the screenshot's width ------------------------
# A 14" MacBook Pro: thin even bezel, a deeper top bezel carrying the notch,
# and the base edge visible below the hinge.
L_BEZEL = 0.014
L_TOP_BEZEL_RATIO = 1.9       # top bezel, as a multiple of the side bezel
L_R_SCREEN = 0.006
L_R_LID = 0.017
L_CAMERA_D = 0.0055           # the FaceTime camera, centred in the top bezel
L_BASE_H = 0.028
L_BASE_OVERHANG = 0.035       # how far the base sticks out past the lid, each side
L_GROOVE_W = 0.085


def _rounded_mask(size: tuple[int, int], radius: int,
                  corners: tuple[bool, bool, bool, bool] | None = None) -> Image.Image:
    """An antialiased rounded-rectangle mask, drawn big and scaled down."""
    w, h = size
    big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=255,
        corners=corners)
    return big.resize((w, h), Image.LANCZOS)


def phone_frame(shot: Image.Image) -> Image.Image:
    shot = shot.convert("RGBA")
    w, h = shot.size
    bezel = max(2, round(P_BEZEL * w))
    btn = max(1, round(P_BUTTON_DEPTH * w))
    r_screen = round(P_R_SCREEN * w)
    r_body = r_screen + bezel

    body_w, body_h = w + 2 * bezel, h + 2 * bezel
    out = Image.new("RGBA", (body_w + 2 * btn, body_h), (0, 0, 0, 0))

    # Side buttons first, so the body paints over their inner half and they
    # read as protruding from it rather than sitting on top.
    for side, y0, frac in P_BUTTONS:
        x0 = 0 if side == "l" else out.width - btn * 2
        top = round(body_h * y0)
        bot = top + round(body_h * frac)
        bar = Image.new("RGBA", (btn * 2, bot - top), RIM)
        out.paste(bar, (x0, top), _rounded_mask(bar.size, btn))

    body = Image.new("RGBA", (body_w, body_h), BODY)
    body_mask = _rounded_mask((body_w, body_h), r_body)
    out.paste(body, (btn, 0), body_mask)

    # A one-pixel-ish rim inside the outer edge: the band around a real device.
    inset = max(1, bezel // 8)
    rim = Image.new("RGBA", (body_w, body_h), RIM)
    rim_mask = body_mask.copy()
    rim_mask.paste(0, (inset, inset),
                   _rounded_mask((body_w - 2 * inset, body_h - 2 * inset), r_body))
    out.paste(rim, (btn, 0), rim_mask)

    out.paste(shot, (btn + bezel, bezel), _rounded_mask((w, h), r_screen))
    return out


def laptop_frame(shot: Image.Image) -> Image.Image:
    shot = shot.convert("RGBA")
    w, h = shot.size
    bezel = max(2, round(L_BEZEL * w))
    top_bezel = max(bezel + 2, round(bezel * L_TOP_BEZEL_RATIO))
    r_screen = max(1, round(L_R_SCREEN * w))
    r_lid = max(2, round(L_R_LID * w))
    base_h = max(3, round(L_BASE_H * w))
    overhang = max(2, round(L_BASE_OVERHANG * w))

    lid_w, lid_h = w + 2 * bezel, h + top_bezel + bezel
    out = Image.new("RGBA", (lid_w + 2 * overhang, lid_h + base_h), (0, 0, 0, 0))

    lid = Image.new("RGBA", (lid_w, lid_h), BODY)
    out.paste(lid, (overhang, 0), _rounded_mask((lid_w, lid_h), r_lid))

    # A camera dot in the top bezel, not the notch a real MacBook Pro has:
    # the notch hangs down INTO the display, so drawing one would either cover
    # captured pixels or read as a grey tab stuck to the bezel. The dot sits
    # entirely above the capture.
    cam_d = max(2, round(L_CAMERA_D * w))
    cam = Image.new("RGBA", (cam_d, cam_d), RIM)
    out.paste(cam, (overhang + (lid_w - cam_d) // 2, (top_bezel - cam_d) // 2),
              _rounded_mask((cam_d, cam_d), cam_d // 2))

    out.paste(shot, (overhang + bezel, top_bezel),
              _rounded_mask((w, h), r_screen))

    # The base edge below the hinge: wider than the lid, rounded at the front.
    base_w = lid_w + 2 * overhang
    base = Image.new("RGBA", (base_w, base_h), BODY)
    out.paste(base, (0, lid_h),
              _rounded_mask((base_w, base_h), max(2, base_h // 2),
                            corners=(False, False, True, True)))

    # The finger groove in the front edge.
    groove_w = round(L_GROOVE_W * w)
    groove_h = max(2, round(base_h * 0.5))
    groove = Image.new("RGBA", (groove_w, groove_h), RIM)
    out.paste(groove, ((base_w - groove_w) // 2, lid_h + base_h - groove_h),
              _rounded_mask((groove_w, groove_h), max(1, groove_h // 2),
                            corners=(False, False, True, True)))
    return out


def frame(shot: Image.Image, device: str = "auto") -> Image.Image:
    if device == "auto":
        device = "phone" if shot.height >= shot.width else "laptop"
    if device == "phone":
        return phone_frame(shot)
    if device == "laptop":
        return laptop_frame(shot)
    raise ValueError(f"unknown device {device!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path,
                    help="output path (single input only)")
    ap.add_argument("--device", default="auto",
                    choices=("auto", "phone", "laptop"))
    ap.add_argument("--suffix", default="_framed",
                    help="appended to each stem when --out is not given")
    args = ap.parse_args()

    if args.out and len(args.shots) > 1:
        ap.error("--out takes a single input; use --suffix for several")

    for path in args.shots:
        dest = args.out or path.with_name(f"{path.stem}{args.suffix}{path.suffix}")
        src = Image.open(path)
        framed = frame(src, args.device)
        framed.save(dest)
        print(f"{path.name} {src.size} -> {dest.name} {framed.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
