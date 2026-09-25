"""The section-hub sponsor lockup snippet: CFG rendering + GAM macros."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import setup_section_sponsor_lockup as ssl  # noqa: E402


def _cfg(html):
    return json.loads(re.search(r"/\*CFG\*/(\{.*?\})/\*CFG\*/", html, re.S).group(1))


def test_snippet_file_cfg_parses_and_carries_macros():
    html = ssl.SNIPPET_FILE.read_text()
    cfg = _cfg(html)
    # Logo through VIEW_URL (OOP impressions count when the logo loads) and
    # clicks through GAM's click macro.
    assert cfg["logo"] == "%%VIEW_URL_UNESC%%%%FILE:PNG1%%"
    assert cfg["href"] == "%%CLICK_URL_UNESC%%%%DEST_URL%%"
    assert "CategoryHubHeader" in cfg["host"]
    assert ssl.SENTINEL in html


def test_render_snippet_fills_cfg_only():
    out = ssl.render_snippet("Sponsored by", "Kia", ["https://px.example/i?ord=%%CACHEBUSTER%%"])
    cfg = _cfg(out)
    assert cfg["sponsor"] == "Kia"
    assert cfg["pixels"] == ["https://px.example/i?ord=%%CACHEBUSTER%%"]
    assert cfg["logo"] == "%%VIEW_URL_UNESC%%%%FILE:PNG1%%"
    src = ssl.SNIPPET_FILE.read_text()
    strip = lambda h: re.sub(r"/\*CFG\*/.*?/\*CFG\*/", "", h, flags=re.S)  # noqa: E731
    assert strip(out) == strip(src)
