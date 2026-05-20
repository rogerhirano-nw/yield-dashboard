"""
Outlook-safety assertions on the rendered HTML.

What we check (structural, not visual):
  1. <body> must NOT have any background-color style.
  2. The outermost <table> wrapping page content must carry both a bgcolor
     attribute AND a matching inline background-color.
  3. Every <td> that sets a background via inline style must ALSO carry the
     bgcolor= attribute (and vice-versa). Outlook strips one or the other
     depending on context; pairing both gets through everywhere.
  4. No <style> blocks (all styles inline).
  5. Max content width ≤ EMAIL_MAX_WIDTH_PX.
"""

from __future__ import annotations

import re

import pytest

from deal_health.colors import EMAIL_MAX_WIDTH_PX
from deal_health.render import render_email
from tests.fixtures.sample_payload import build_sample_payload


@pytest.fixture(scope="module")
def html() -> str:
    return render_email(build_sample_payload())


# ── #1 + #2: body and outer table ──────────────────────────────────────────

def test_body_has_no_background_style(html: str):
    m = re.search(r"<body[^>]*>", html, re.IGNORECASE)
    assert m, "<body> tag missing"
    body_attrs = m.group(0)
    # body MAY have bgcolor=, but its inline style MUST NOT set background-color
    style_match = re.search(r'style="([^"]*)"', body_attrs)
    if style_match:
        assert "background-color" not in style_match.group(1), (
            "Outlook strips background-color from <body> inline style — "
            "put it on the outermost <table> instead."
        )


def test_outer_table_has_paired_bgcolor(html: str):
    """The first <table> in the document (the page wrapper) must carry both
    bgcolor= and style background-color."""
    m = re.search(r"<table[^>]*>", html, re.IGNORECASE)
    assert m, "no outer <table> found"
    outer = m.group(0)
    assert 'bgcolor="' in outer, f"outer table missing bgcolor= attr: {outer}"
    assert "background-color:" in outer, f"outer table missing inline background-color: {outer}"


# ── #3: every colored <td> has both attrs ──────────────────────────────────

_TD_TAG = re.compile(r"<td\b[^>]*>", re.IGNORECASE)


def test_every_colored_td_has_paired_attrs(html: str):
    """For each <td>: if it has background-color in inline style, it must
    also have bgcolor=. If it has bgcolor=, it must also have background-color
    in style. Outlook PWA strips whichever isn't paired."""
    failures: list[str] = []
    for m in _TD_TAG.finditer(html):
        tag = m.group(0)
        has_bgcolor = re.search(r'bgcolor="([^"]+)"', tag)
        style_match = re.search(r'style="([^"]*)"', tag)
        has_bg_in_style = bool(style_match and "background-color" in style_match.group(1))
        if has_bgcolor and not has_bg_in_style:
            failures.append(f"bgcolor without inline style: {tag[:140]}")
        if has_bg_in_style and not has_bgcolor:
            failures.append(f"inline background without bgcolor= attr: {tag[:140]}")
    assert not failures, (
        f"{len(failures)} <td>(s) have unpaired background attributes; first 3:\n"
        + "\n".join(failures[:3])
    )


# ── #4: no <style> blocks ──────────────────────────────────────────────────

def test_no_style_blocks(html: str):
    assert not re.search(r"<style\b", html, re.IGNORECASE), (
        "<style> blocks not allowed; all styles must be inline."
    )


# ── #5: max width respected ────────────────────────────────────────────────

def test_max_width_respected(html: str):
    widths = [int(w) for w in re.findall(r'width="(\d+)"', html)]
    if widths:
        assert max(widths) <= EMAIL_MAX_WIDTH_PX, (
            f"At least one element exceeds EMAIL_MAX_WIDTH_PX ({EMAIL_MAX_WIDTH_PX}): "
            f"max found = {max(widths)}"
        )
