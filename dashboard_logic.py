"""Pure decision logic extracted from dashboard.py.

Importable without Streamlit, so the rules that classify formats and color
cells are unit-testable. The extraction exists because two silent bugs
lived in this exact logic while it was inline in the 7k-line app script,
and no test *could* catch them there: the DV ".0" join keys (#151) and the
"Video Preroll >30s" recategorization running before ad_format existed
(#156 — every long-form video line graded under the plain Video
benchmarks).

Division of labor: dashboard.py owns rendering (HTML, pills, layout);
this module owns decisions (what format a line is, what thresholds apply,
which band a value falls in). When dashboard.py grows a new decision,
put it here with a test, not inline.
"""

from __future__ import annotations

import math

# Long-form video gets its own benchmark band: longer spots complete less,
# so grading a 60s film against 15s-preroll VCR targets just paints the
# table red. See benchmarks_by_format in the dashboard settings.
LONG_PREROLL_FORMAT = "Video Preroll >30s"
LONG_PREROLL_MIN_SECONDS = 30.0


def bump_video_format(fmt, max_duration_seconds):
    """Benchmark format for a line item: video whose longest creative runs
    longer than 30s is graded as "Video Preroll >30s"; everything else
    keeps its format. Unknown/NaN durations stay unchanged — the manual
    long_preroll_lines rules exist for those (3rd-party tags hide duration).
    """
    if not isinstance(fmt, str) or "video" not in fmt.lower():
        return fmt
    try:
        dur = float(max_duration_seconds)
    except (TypeError, ValueError):
        return fmt
    if math.isnan(dur):
        return fmt
    return LONG_PREROLL_FORMAT if dur > LONG_PREROLL_MIN_SECONDS else fmt


def matches_long_preroll(row, rules) -> bool:
    """True when a user-curated long_preroll_lines rule matches this row.

    `row` is any mapping with line_item_id / order_name / line_item_name
    (a pandas Series works). Rules: {match_field, match_value} where
    line_item_id matches exactly and the *_name fields match as
    case-insensitive substrings (order_name tolerates a trailing * or %).
    Malformed rules are skipped, never raised on — settings are user input.
    """
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        field = (rule.get("match_field") or "").strip()
        val = (rule.get("match_value") or "").strip()
        if not field or not val:
            continue
        if field == "line_item_id":
            if str(row.get("line_item_id") or "") == val:
                return True
        elif field == "order_name":
            cell = str(row.get("order_name") or "").lower()
            needle = val.lower().rstrip("*").rstrip("%")
            if needle and needle in cell:
                return True
        elif field == "line_item_name":
            cell = str(row.get("line_item_name") or "").lower()
            if val.lower() in cell:
                return True
    return False


def bench_target(bench_cfg, fmt, key, fallback_key=None, fallback=None):
    """The green floor for `key` (e.g. 'vcr_pct') for this format: the
    format's own configured value, else `fallback_key`'s, else the literal
    `fallback` (None = metric not benchmarked for this format)."""
    if isinstance(fmt, str) and fmt in bench_cfg:
        v = bench_cfg[fmt].get(key)
        if v is not None:
            return float(v)
    if fallback_key and fallback_key in bench_cfg:
        v = bench_cfg[fallback_key].get(key)
        if v is not None:
            return float(v)
    return float(fallback) if fallback is not None else None


def bench_red_cut(bench_cfg, fmt, key, target, fallback_key=None):
    """The red ceiling for `key` (e.g. 'vcr'): the format's configured
    `<key>_red_below`, else `fallback_key`'s, else `target * 0.85` (the
    original implicit band). None when there's no target at all."""
    red_key = f"{key}_red_below"
    if isinstance(fmt, str) and fmt in bench_cfg:
        v = bench_cfg[fmt].get(red_key)
        if v is not None:
            return float(v)
    if fallback_key and fallback_key in bench_cfg:
        v = bench_cfg[fallback_key].get(red_key)
        if v is not None:
            return float(v)
    return target * 0.85 if target else None


def band(value, target, red_cut) -> str:
    """'red' | 'amber' | 'green' for a metric value: below the red ceiling
    is red, below the green floor is amber, at-or-above the floor is green.
    Boundary semantics match the cell renderers: value == red_cut is amber,
    value == target is green."""
    if red_cut is not None and value < red_cut:
        return "red"
    if target is not None and value < target:
        return "amber"
    return "green"
