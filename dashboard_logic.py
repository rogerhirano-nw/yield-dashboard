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

import pandas as pd

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


def attention_band(idx) -> str:
    """Band for the DV Attention Index (100 = DV's industry median):
    red < 85 (15%+ below median), amber 85-100, green ≥ 100."""
    v = float(idx)
    if v < 85:
        return "red"
    return "amber" if v < 100 else "green"


def ivt_band(pct) -> str:
    """Band for an impression-weighted IVT% (industry thresholds):
    green < 1, amber 1-3, red ≥ 3. Boundary semantics: exactly 1 is
    amber, exactly 3 is red — rising fraud rounds against us."""
    v = float(pct)
    if v >= 3:
        return "red"
    return "amber" if v >= 1 else "green"


# ── DV / IVT aggregation ───────────────────────────────────────────────


def choose_join_col(df, id_col: str = "line_item_id",
                    name_col: str = "line_item_name") -> str:
    """Prefer the numeric-ID join column (immune to line-item renames)
    when the frame carries any non-null IDs; fall back to the name
    column. This is the dashboard side of the #151 join — the IDs only
    work because the DV parsers normalize them to integer strings."""
    if id_col in df.columns and df[id_col].notna().any():
        return id_col
    return name_col


def attention_current_and_prior(dv_df, group_col: str) -> tuple[dict, dict]:
    """Per-group Attention Index lookups: (current = mean of per-date
    means over the whole window, prior = same excluding the latest date —
    powers the per-row Δ annotation). Multiple DV rows per (line, date)
    exist when DV measured several creatives on the line; averaging per
    date first keeps heavy-creative days from dominating the mean."""
    if group_col not in dv_df.columns:
        return {}, {}
    sub = dv_df.dropna(subset=[group_col, "attention_index", "date"])
    if sub.empty:
        return {}, {}
    daily = (sub.groupby([group_col, "date"])["attention_index"]
                .mean().sort_index())
    cur, prior = {}, {}
    for line in daily.index.get_level_values(0).unique():
        vals = daily.loc[line].sort_index()
        cur[line] = float(vals.mean())
        if len(vals) >= 2:
            prior[line] = float(vals.iloc[:-1].mean())
    return cur, prior


def ivt_share_with_prior(ivt_df, group_col: str,
                         fraud_label: str) -> tuple[dict, dict]:
    """Per-group impression-weighted IVT% lookups per the MRC standard:

        IVT % = Σ Monitored Ads (rows of this fraud bucket) /
                Σ Monitored Ads (all rows) × 100

    Returns (current = whole-window share, prior = share excluding the
    latest date — powers the per-row Δ annotation). Groups with zero
    monitored impressions are omitted, never reported as 0%."""
    if not {group_col, "traffic_validity", "monitored_ads", "date"}.issubset(ivt_df.columns):
        return {}, {}
    sub = ivt_df.dropna(subset=[group_col, "date"])
    if sub.empty:
        return {}, {}
    ads = pd.to_numeric(ivt_df["monitored_ads"], errors="coerce").fillna(0)
    validity = ivt_df["traffic_validity"].astype(str)
    ads_sub = ads.reindex(sub.index)
    mask = validity.reindex(sub.index) == fraud_label

    tot_pd = ads_sub.groupby([sub[group_col], sub["date"]]).sum()
    frd_pd = (ads_sub[mask]
                  .groupby([sub.loc[mask, group_col], sub.loc[mask, "date"]])
                  .sum())
    joined = pd.DataFrame({"tot": tot_pd, "frd": frd_pd}).fillna(0)

    cur, prior = {}, {}
    for line in joined.index.get_level_values(0).unique():
        rows = joined.loc[line].sort_index()
        tot_all = rows["tot"].sum()
        if tot_all <= 0:
            continue
        cur[line] = float(rows["frd"].sum() / tot_all * 100)
        if len(rows) >= 2:
            rows_prior = rows.iloc[:-1]
            tot_prior = rows_prior["tot"].sum()
            if tot_prior > 0:
                prior[line] = float(rows_prior["frd"].sum() / tot_prior * 100)
    return cur, prior


# ── Delta / ratio math ─────────────────────────────────────────────────


def classify_delta(d, lower_is_worse: bool = True,
                   new_line_threshold: float | None = 100.0,
                   noise_threshold: float = 0.05):
    """Decision half of the "▲/▼ X.Xpp" sub-cell annotation.

    Returns None (no signal: missing value or inside the noise band),
    the string "new" (magnitude says "brand-new line item", when
    new_line_threshold applies), or (arrow, is_improvement) where
    `lower_is_worse=True` means higher values are better (Viewability,
    CTR, VCR, Pace…) and False flips polarity for IVT-style metrics
    where rising = bad."""
    if d is None or pd.isna(d) or abs(d) < noise_threshold:
        return None
    if new_line_threshold is not None and abs(d) > new_line_threshold:
        return "new"
    arrow = "▲" if d > 0 else "▼"
    is_improvement = (d > 0) if lower_is_worse else (d < 0)
    return arrow, is_improvement


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def lt_minus_1d_ratio(lt_num, d1_num, lt_den, d1_den, scale: float = 100.0):
    """The "prior" value for ratio metrics: (lifetime − latest-day)
    numerator over the same-window denominator, scaled. None when any
    input is missing or the prior-window denominator is ≤ 0 (a line
    that only ever delivered on the latest day has no prior)."""
    ltn, d1n, ltd, d1d = _num(lt_num), _num(d1_num), _num(lt_den), _num(d1_den)
    if any(math.isnan(x) for x in (ltn, d1n, ltd, d1d)):
        return None
    den = max(ltd - d1d, 0)
    if den <= 0:
        return None
    return (ltn - d1n) / den * scale


def volume_pct_delta(lifetime, day):
    """% change of the latest day's volume vs everything before it —
    volumes need % change, not pp. None when inputs are missing or there
    is no prior volume to compare against."""
    lt, d1 = _num(lifetime), _num(day)
    if math.isnan(lt) or math.isnan(d1):
        return None
    prior = lt - d1
    if prior <= 0:
        return None
    return d1 / prior * 100
