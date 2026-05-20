"""
Snapshot test for the rendered HTML against a fixed fixture payload.

Update the golden file with `pytest --snapshot-update` semantics — here we
just allow regeneration via env var REGENERATE_SNAPSHOT=1.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from deal_health.render import render_email
from tests.fixtures.sample_payload import build_sample_payload

GOLDEN = Path(__file__).parent / "fixtures" / "expected_email.html"


def test_render_matches_snapshot():
    actual = render_email(build_sample_payload())

    if os.environ.get("REGENERATE_SNAPSHOT") == "1" or not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(actual, encoding="utf-8")
        pytest.skip(f"snapshot regenerated at {GOLDEN}; rerun to verify")

    expected = GOLDEN.read_text(encoding="utf-8")
    assert actual == expected, (
        f"rendered HTML diverged from {GOLDEN}.\n"
        "Run REGENERATE_SNAPSHOT=1 pytest tests/test_render_snapshot.py to update."
    )


def test_render_size_under_budget():
    """The Gmail clip threshold is ~102KB; we accept clipping but want to stay
    under 200KB so non-Gmail clients render the full thing."""
    html = render_email(build_sample_payload())
    size_kb = len(html.encode("utf-8")) / 1024
    assert size_kb < 200, f"email is {size_kb:.1f}KB — over the 200KB budget"


def test_render_contains_required_sections():
    """Smoke-check: every spec'd section appears in the output."""
    html = render_email(build_sample_payload())
    for needle in (
        "Newsweek",
        "Weekly Deal Health",
        "Unhealthy deals",
        "Bid requests unanswered",
        "Top DSP concentration",
        "Executive summary",
        "By SSP",
        "Top dark advertisers",
        "Download CSV",
        "Open dashboard",
        "Methodology",
    ):
        assert needle in html, f"missing required section text: {needle!r}"
