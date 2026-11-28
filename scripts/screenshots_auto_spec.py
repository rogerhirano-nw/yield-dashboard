#!/usr/bin/env python3
"""Write the proof-of-placement deck spec from a capture run, untouched by hand.

`capture_screenshots.py` leaves `manifest.json` + `*_context_framed.png` in its
output folder. This turns that into the spec `build_screenshots_deck.py`
reads: advertiser + campaign from the order name (SOP 1.3 tokens 7 / 8), the
seller from the AE token through settings.json `ae_names`, and one slide per
framed in-context shot (desktop first, then mobile; no close-up crops —
docs/screenshots_document.md).

Usage: python scripts/screenshots_auto_spec.py SHOTS_DIR spec.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHOT = re.compile(r"(?P<li>\d+)_(?P<cid>\d+)_(?P<size>\d+x\d+)_(?P<dev>[a-z]+)_context_framed\.png$")
PLACEMENT = {"970x250": "Billboard", "728x90": "Leaderboard", "300x250": "Medium rectangle",
             "300x600": "Half page", "320x50": "Mobile banner", "300x50": "Mobile banner",
             "160x600": "Skyscraper", "1x1": "Interstitial"}


def build_spec(shots_dir: Path, ae_names: dict) -> dict:
    m = json.loads((shots_dir / "manifest.json").read_text())
    tok = (m.get("order_name") or "").split("_")
    adv = tok[7].replace("-", " ") if len(tok) > 8 else m.get("order_name", "")
    camp = tok[8].replace("-", " ") if len(tok) > 8 else (m.get("line_item_name") or "")
    seller = ae_names.get(tok[-1], "") if len(tok) >= 14 else ""
    page = re.sub(r"^https?://(www\.)?", "", m.get("article_url", "")).split("?")[0]
    shots = []
    for f in sorted(shots_dir.glob("*_context_framed.png"),
                    key=lambda p: (SHOT.match(p.name) and SHOT.match(p.name)["dev"] != "desktop", p.name)):
        g = SHOT.match(f.name)
        if not g:
            continue
        device = g["dev"].capitalize()
        kind = PLACEMENT.get(g["size"], "Ad")
        shots.append({"size": g["size"], "device": device,
                      "title": f"{kind} on {device.lower()}",
                      "file": f.name,
                      "alt": f"{adv} {g['size']} {kind.lower()} on a Newsweek article, {device.lower()}"})
    spec = {"advertiser": adv, "campaign": camp, "captured": m.get("captured", ""),
            "page_url": page,
            "summary_note": "Every creative on the line, shot in context on a live newsweek.com article.",
            "facts": [["Advertiser", adv], ["Campaign", camp], ["GAM order", m.get("order_id", "")],
                      ["Line item", m.get("line_item_id", "")], ["Page", page]],
            "shots": shots}
    if seller:
        spec["seller"] = seller
    return spec


def main() -> int:
    shots_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    ae = json.loads((REPO / "settings.json").read_text()).get("ae_names", {})
    spec = build_spec(shots_dir, ae)
    if not spec["shots"]:
        print("no framed in-context shots — nothing to put in a deck", file=sys.stderr)
        return 1
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False))
    print(f"{len(spec['shots'])} slide(s) for {spec['advertiser']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
