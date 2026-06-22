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
import re
from datetime import date
from functools import lru_cache

import pandas as pd

# Long-form video gets its own benchmark band: longer spots complete less,
# so grading a 60s film against 15s-preroll VCR targets just paints the
# table red. See benchmarks_by_format in the dashboard settings.
LONG_PREROLL_FORMAT = "Video Preroll >30s"
LONG_PREROLL_MIN_SECONDS = 30.0


def bump_video_format(fmt, max_duration_seconds):
    """Benchmark BAND for a line item: video whose longest creative runs
    longer than 30s grades as "Video Preroll >30s"; everything else keeps
    its format. The band only feeds threshold lookups (_bench_format) —
    the Format filter shows plain "Video". Unknown/NaN durations stay
    unchanged — the manual long_preroll_lines rules exist for those
    (3rd-party tags hide duration).
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


def idle_band(days) -> str:
    """Band for a stale-deal idle age (days with no bid response): red ≥ 180
    (6+ months gone), amber 90-179 (the staleness floor), "" below 90 (not
    stale). Drives the colored 'days idle' pill in the stale-deals list."""
    v = float(days)
    if v >= 180:
        return "red"
    return "amber" if v >= 90 else ""


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


def merge_lookups(primary: dict, secondary: dict) -> dict:
    """Overlay `primary` on `secondary` — primary wins on key collision.

    Used to give the PMP DV lookups a fallback key: order_name is the
    canonical PMP join (primary), but some deals are trafficked with the
    abbreviated word in `programmatic_deal_name` (the dashboard's "Deal"
    key) while DV's Order column carries the long form — e.g.
    "..._Tech_..._Video_$14" vs "..._Technology_..._Video_$14"
    (2026-06-15). DV's Line Item column mirrors the abbreviated spelling,
    so a line_item_name-keyed lookup (secondary) closes the gap. Verified
    on prod: 0 collisions between PMP-style line_item_name keys and the
    order lookup, so the fallback never overrides a real order match."""
    if not secondary:
        return dict(primary)
    merged = dict(secondary)
    merged.update(primary)
    return merged


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


def attention_daily_series_by_li(dv_df, group_col: str, n: int = 7) -> dict:
    """Per-line daily mean Attention Index (oldest-first, last `n` dates),
    keyed by str(line). One groupby pass — precompute once, then look up per
    row (the drawer builds eagerly for every row). Mirrors
    attention_current_and_prior's per-(line,date) averaging. Powers the
    drawer's Attention trend sparkline."""
    if dv_df is None or group_col not in getattr(dv_df, "columns", []):
        return {}
    if not {"attention_index", "date"}.issubset(dv_df.columns):
        return {}
    sub = dv_df.dropna(subset=[group_col, "attention_index", "date"])
    if sub.empty:
        return {}
    daily = sub.groupby([group_col, "date"])["attention_index"].mean().sort_index()
    out: dict = {}
    for line in daily.index.get_level_values(0).unique():
        out[str(line)] = [float(v) for v in daily.loc[line].sort_index().tail(n).tolist()]
    return out


def ivt_daily_series_by_li(ivt_df, group_col: str, fraud_label: str,
                           n: int = 7) -> dict:
    """Per-line daily impression-weighted IVT% for one Fraud bucket (MRC:
    Σ Monitored Ads of this bucket / Σ all Monitored Ads × 100, per date),
    oldest-first last `n` dates, keyed by str(line). One groupby pass. Days
    with zero monitored ads are dropped, never reported as 0% (days with
    traffic but no fraud are a real 0% and kept). Powers the drawer's
    SIVT/GIVT trend sparklines."""
    if ivt_df is None or group_col not in getattr(ivt_df, "columns", []):
        return {}
    if not {"traffic_validity", "monitored_ads", "date"}.issubset(ivt_df.columns):
        return {}
    sub = ivt_df.dropna(subset=[group_col, "date"])
    if sub.empty:
        return {}
    ads = pd.to_numeric(sub["monitored_ads"], errors="coerce").fillna(0)
    validity = sub["traffic_validity"].astype(str)
    tot_pd = ads.groupby([sub[group_col], sub["date"]]).sum()
    mask = validity == fraud_label
    frd_pd = ads[mask].groupby([sub.loc[mask, group_col], sub.loc[mask, "date"]]).sum()
    joined = pd.DataFrame({"tot": tot_pd, "frd": frd_pd}).fillna(0)
    joined["pct"] = (joined["frd"] / joined["tot"] * 100).where(joined["tot"] > 0)
    out: dict = {}
    for line in joined.index.get_level_values(0).unique():
        s = joined.loc[line]["pct"].dropna().sort_index()
        if len(s):
            out[str(line)] = [float(v) for v in s.tail(n).tolist()]
    return out


def daily_series_by_deal(daily_df, value_col: str = "revenue", n: int = 7):
    """Per-deal daily series of ``value_col`` over a *contiguous* last-`n`-day
    window, keyed by ``(ssp, deal)`` and ordered oldest→newest. `daily_df` has
    columns ``[ssp, deal, date, <value_col>]`` (one row per deal·day per
    source). One groupby pass — precompute once, then look up per row (the PMP
    drawer builds its charts eagerly). Missing days inside the window are filled
    0.0, so a gap reads as a dip rather than a dropped point. The window ends at
    the latest date present (PMP data lags a couple days), not "today".

    Returns ``(series_by_deal, window_dates)`` where window_dates is the list of
    `n` ``date`` objects (oldest→newest); both empty when there's no usable
    daily data. Powers the PMP drawer's revenue / total-requests / bid-responses
    7-day trend charts (and the mobile-card revenue sparkline via the wrapper
    below)."""
    cols = getattr(daily_df, "columns", [])
    if daily_df is None or not {"ssp", "deal", "date", value_col}.issubset(cols):
        return {}, []
    sub = daily_df.dropna(subset=["deal", "date"]).copy()
    if sub.empty:
        return {}, []
    sub[value_col] = pd.to_numeric(sub[value_col], errors="coerce").fillna(0.0)
    sub["date"] = pd.to_datetime(sub["date"], errors="coerce").dt.normalize()
    sub = sub.dropna(subset=["date"])
    if sub.empty:
        return {}, []
    end = sub["date"].max()
    window = [end - pd.Timedelta(days=i) for i in range(n - 1, -1, -1)]
    sub = sub[sub["date"].isin(window)]
    if sub.empty:
        return {}, []
    daily = sub.groupby(["ssp", "deal", "date"])[value_col].sum()
    out: dict = {}
    for ssp, deal in daily.index.droplevel(2).unique():
        s = daily.loc[(ssp, deal)]
        out[(str(ssp), str(deal))] = [float(s.get(d, 0.0)) for d in window]
    return out, [d.date() for d in window]


def revenue_daily_series_by_deal(daily_df, n: int = 7):
    """Per-deal daily **revenue** series — thin wrapper over
    ``daily_series_by_deal`` for the revenue column (kept as the named entry
    point the PMP table + its test use). See that function for the windowing
    and 0-fill contract."""
    return daily_series_by_deal(daily_df, "revenue", n)


def window_last_n_days(frame, n: int = 7, date_col: str = "date"):
    """Keep only rows within the last `n` calendar days of the frame's *own*
    latest date (the inclusive window ``[max_date - (n-1) … max_date]``).

    The source daily tables now retain more than `n` days (the pulls were
    widened to power week-vs-week spend momentum), but the PMP **summary** must
    stay a fixed `n`-day view so its revenue / impression / eCPM totals don't
    move when the retention grows. Anchored on the frame's max date, not
    "today", so each source tracks its own lag (PMP data trails a couple days).
    A frame with no usable `date_col` is returned unchanged."""
    cols = getattr(frame, "columns", [])
    if frame is None or date_col not in cols or len(frame) == 0:
        return frame
    d = pd.to_datetime(frame[date_col], errors="coerce")
    if d.notna().sum() == 0:
        return frame
    cutoff = d.max().normalize() - pd.Timedelta(days=n - 1)
    return frame[d >= cutoff]


def spend_momentum(df, name_col, rev_col, window: int = 7,
                   min_window_rev: float = 0.5, min_delta: float = 100.0):
    """Rank PMP deals by recent-vs-prior spend. `df` is one row per deal·day
    (sources pre-concatenated) with a deal-name column (`name_col`), a `_date`
    column, and a revenue column (`rev_col`).

    The comparison window is **adaptive**: with ``D`` distinct dates it uses
    ``w = min(window, D // 2)`` days on each side, so it grades
    **last-7-vs-prior-7** once the daily pulls carry 14 days, and degrades to a
    smaller symmetric split (3-vs-3 on the current ~7-day cache) without ever
    overlapping the two windows. At the 7-day regime this is behaviour-identical
    to the old inline 3-vs-3 split (``min(7, 7//2) == 3``).

    Returns ``(summary, n_gaining, n_losing)``; `summary` has one row per
    surviving deal (``_recent_rev / _prior_rev / _delta / _pct``), sorted by
    recent revenue — the top earner first, not by Δ. Filters, in order: drop
    deals below `min_window_rev` in *both* windows ($0→$0 noise), then drop
    ``|Δ| ≤ min_delta`` (not a meaningful move). This is the decision half of
    the spend-momentum list; the HTML rows are built in dashboard.py."""
    cols = getattr(df, "columns", [])
    if df is None or name_col not in cols or "_date" not in cols or rev_col not in cols:
        return pd.DataFrame(), 0, 0
    d = df.copy()
    d["_date"] = pd.to_datetime(d["_date"], errors="coerce")
    d[rev_col] = pd.to_numeric(d[rev_col], errors="coerce").fillna(0)
    d = d.dropna(subset=[name_col, "_date"])
    if d.empty:
        return pd.DataFrame(), 0, 0
    sorted_dates = sorted(d["_date"].unique(), reverse=True)
    w = min(window, len(sorted_dates) // 2)
    if w < 1:
        return pd.DataFrame(), 0, 0
    recent_dates = sorted_dates[:w]
    prior_dates  = sorted_dates[w:2 * w]
    recent = d[d["_date"].isin(recent_dates)].groupby(name_col)[rev_col].sum()
    prior  = d[d["_date"].isin(prior_dates)].groupby(name_col)[rev_col].sum()
    out = pd.DataFrame({"_recent_rev": recent, "_prior_rev": prior}).fillna(0).reset_index()
    # Hide deals with no visible revenue in either window ($0 → $0 noise).
    out = out[(out["_recent_rev"].abs() >= min_window_rev)
              | (out["_prior_rev"].abs() >= min_window_rev)].copy()
    if out.empty:
        return pd.DataFrame(), 0, 0
    out["_delta"] = out["_recent_rev"] - out["_prior_rev"]
    # Only meaningful movers — drop deals whose spend shifted by ≤ min_delta.
    out = out[out["_delta"].abs() > min_delta].copy()
    if out.empty:
        return pd.DataFrame(), 0, 0
    out["_pct"] = out.apply(
        lambda r: r["_delta"] / r["_prior_rev"] * 100 if r["_prior_rev"] > 0 else float("nan"),
        axis=1,
    )
    out = out.sort_values("_recent_rev", ascending=False)  # top revenue first, not by Δ
    return out, int((out["_delta"] > 0).sum()), int((out["_delta"] < -0.5).sum())


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


# ── Name-token parsing (14-field line-item convention) ─────────────────

# The AE short-handle rides in the "Team-USA_RShore" / "Team-INTL_AShah"
# tail of order and line-item names; the team token is the USA|INTL part.
AE_TOKEN_RE = r"Team-(?:USA|INTL)_([A-Za-z]+)"
TEAM_TOKEN_RE = r"_Team-(USA|INTL)_"


def parse_gam_salesperson(val):
    """Extract the short name from GAM's User.display_name.

    GAM returns values like "Newsweek - Sales - Theresa Hern" or
    "Newsweek - Sales- Jeremy Makin (jmakin@newsweek.com)" — strip the
    "Newsweek - Sales[-] " prefix and any trailing email parenthetical.
    Returns None for empty / non-string inputs."""
    if not isinstance(val, str) or not val.strip():
        return None
    m = re.search(r"-\s*([^-(]+?)\s*(?:\(|$)", val)
    return m.group(1).strip() if m else val.strip()


def li_part(name, idx: int):
    """Token at position `idx` of an underscore-delimited line-item name
    (the 14-field convention: advertiser=7, campaign=8, format=10).
    None for non-strings or names too short to carry the token."""
    if not isinstance(name, str):
        return None
    parts = name.split("_")
    return parts[idx].strip() if len(parts) > idx else None


@lru_cache(maxsize=8192)
def line_item_display_name(name) -> str:
    """Friendly "<Advertiser> — <Campaign>" label for a GAM line-item name
    (14-field convention: advertiser = token 7, campaign = token 8).

    The campaign token is where the *placement / product* lives
    (Newsmakers-Centerstage, Qx65-Homepage-Takeover, Apple-News,
    Custom-Audience-Pre-roll, MANV-Sponsorship, …), so it's the field that
    actually tells sibling line items apart. Token 10 (format) is deliberately
    NOT used here: it's redundant with the canonical-taxonomy chip that the row
    already shows, and the raw token is frequently wrong (an Apple-News or a
    Centerstage line both carry "Display" at token 10). Format belongs to the
    chip; the name carries identity.

    Cleaning: strips a leading "#N " ordinal badge; drops the advertiser prefix
    the campaign token usually repeats ("Infiniti-Newsmakers-…" → "Newsmakers
    …") so the name doesn't say "Infiniti — Infiniti…"; spaces out dashes; and
    preserves any trailing "(Article)" / "(copy N)" marker, the only thing that
    separates some same-campaign / same-format variants. Falls back to the
    advertiser alone, then mid-name tokens, then the cleaned name when the
    campaign token is missing. Also the alphabetical sort key for the Direct
    table (so an advertiser's campaigns group together A–Z), so it must match
    exactly what the row renders."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    raw = str(name)
    # A trailing parenthetical ("(Article)", "(copy 1)") is the only
    # disambiguator for some otherwise-identical campaign/format pairs — peel
    # it off before tokenizing and re-append it to whatever name we build.
    note = ""
    m_note = re.search(r"\s*(\([^()]*\))\s*$", raw)
    if m_note:
        note = " " + m_note.group(1).strip()
        raw = raw[:m_note.start()]
    clean = re.sub(r"^#\d+\s+", "", raw)
    tokens = clean.split("_")
    adv_raw = tokens[7] if len(tokens) >= 8 else ""
    camp_raw = tokens[8] if len(tokens) >= 9 else ""
    adv = adv_raw.strip() if adv_raw and adv_raw not in ("NA", "N/A", "") else ""
    camp = camp_raw.strip() if camp_raw and camp_raw not in ("NA", "N/A", "") else ""
    if adv and camp.lower().startswith(adv.lower() + "-"):
        camp = camp[len(adv) + 1:]
    adv_disp = adv.replace("-", " ")
    camp_disp = camp.replace("-", " ").strip()
    if adv_disp and camp_disp:
        return f"{adv_disp} — {camp_disp}{note}"
    if camp_disp:
        return camp_disp + note
    if adv_disp:
        return adv_disp + note
    if len(tokens) >= 5:
        return "_".join(tokens[2:5]) + note
    if len(tokens) >= 3:
        return "_".join(tokens[2:]) + note
    return clean + note


_PMP_CONV_RE = re.compile(r"^Newsweek_(PG|PD|PA|PMP)_")
_PMP_NA = ("", "NA", "N/A")


def pmp_deal_display_name(name):
    """(primary, sub) display label for a PMP deal name.

    The Newsweek deal-name convention —
    `Newsweek_<PG|PD|PA|PMP>_<vertical>_<exchange>_<dsp>_<holding>_<agency>_
    <advertiser>_<campaign>_<geo>_<format>_<floor>_<team>_<ae>` — puts the
    advertiser at token 7 and the campaign at token 8 (same positions as the
    Direct line-item convention), so the deal reads as
    **`<Advertiser> — <Campaign>`** with the buying **agency (· holding)** as
    the secondary line. The old name surfaced `<vertical>_<exchange>_<dsp>`
    (e.g. "Automotive_Adx_DV360") as the primary and buried the advertiser,
    collapsing distinct deals together. DSP / SSP / Format / eCPM / Deal Type /
    Seller are already their own columns, so the name carries identity only.

    SSP-native / non-convention names (Pubmatic "3PS_Pubmatic_DE_Display_High
    CTR", DSP-minted "Google_US_Always-On_…") have no such structure, so they're
    returned cleaned but whole (underscores → spaces) — the buyer-defined
    string *is* the identity. Empty / "NA" / "N/A" → ("—", "")."""
    if not isinstance(name, str) or name.strip() in _PMP_NA:
        return ("—", "")
    raw = name.strip()
    if _PMP_CONV_RE.match(raw):
        t = raw.split("_")

        def _tok(i):
            return t[i].strip() if len(t) > i and t[i].strip() not in _PMP_NA else ""

        adv, camp = _tok(7), _tok(8)
        if adv and camp.lower().startswith(adv.lower() + "-"):
            camp = camp[len(adv) + 1:]
        if adv or camp:
            primary = " — ".join(p.replace("-", " ") for p in (adv, camp) if p)
            sub_bits = list(dict.fromkeys(
                b.replace("-", " ") for b in (_tok(6), _tok(5)) if b))
            return (primary, " · ".join(sub_bits))
    # Non-convention SSP-native name: show it whole, lightly cleaned.
    return (re.sub(r"\s+", " ", raw.replace("_", " ")).strip(), "")


def pmp_deal_floor(name):
    """Configured floor (a CPM, as a float) parsed from a Newsweek-convention
    PMP deal name — token 11 (`<floor>`) of
    `Newsweek_<type>_<vertical>_<exchange>_<dsp>_<holding>_<agency>_<advertiser>_
    <campaign>_<geo>_<format>_<floor>_<team>_<ae>`. E.g. the `$14` in
    "…_US_Video_$14_Team-USA_ILee" → 14.0.

    This is the per-deal floor Newsweek set when creating the deal. The SSP
    delivery feeds don't carry one (Pubmatic/Magnite report none; GAM exposes
    `floor_price` only for PA deals, and not joinable to the revenue rows), and
    the deal name is already how the dashboard derives advertiser / campaign /
    format. Returns a positive float, or None for a non-convention name, a
    missing/NA floor token, or an unparseable / non-positive value."""
    if not isinstance(name, str) or name.strip() in _PMP_NA:
        return None
    raw = name.strip()
    if not _PMP_CONV_RE.match(raw):
        return None
    t = raw.split("_")
    if len(t) <= 11:
        return None
    tok = t[11].strip()
    if tok in _PMP_NA:
        return None
    m = re.search(r"\d+(?:\.\d+)?", tok.replace(",", ""))
    if not m:
        return None
    try:
        val = float(m.group(0))
    except ValueError:
        return None
    return val if val > 0 else None


# ── Pacing ──────────────────────────────────────────────────────────────


def pace_band(p, target) -> str:
    """Band for a pacing % against the configured target:
    red < 75% of target, amber < 90%, green ≤ 110%, "over" beyond that —
    overpacing renders amber too (burning budget early is also a flag).
    No/zero target → "over": can't judge, draw a look rather than show
    green."""
    ratio = p / target if target else None
    if ratio is not None and ratio < 0.75:
        return "red"
    if ratio is not None and ratio < 0.90:
        return "amber"
    if ratio is not None and ratio <= 1.10:
        return "green"
    return "over"


def prior_pacing(goal, lifetime, imp_1d, raw_start, raw_end, today):
    """Pace as of the day BEFORE yesterday — the 'prior' for the Pace Δ.

    Pro-rates the impression goal over the flight days elapsed through
    day-before-yesterday (clamped to the flight end) and divides the
    cumulative delivery excluding the latest day by it. None whenever the
    inputs can't support the math (no goal, missing dates, flight too
    young to have a prior). `today` is injected for testability."""
    try:
        if not (goal and goal > 0 and pd.notna(lifetime) and pd.notna(imp_1d)):
            return None
        start = pd.to_datetime(raw_start)
        end = pd.to_datetime(raw_end)
        if pd.isna(start) or pd.isna(end):
            return None
        dbf_yest = today - pd.Timedelta(days=2)
        total_days = max((end - start).days, 1)
        elapsed_dbf = max((min(dbf_yest, end) - start).days, 0)
        if elapsed_dbf <= 0:
            return None
        pro_rated_goal = goal * (elapsed_dbf / total_days)
        if pro_rated_goal <= 0:
            return None
        cum_dbf = max(lifetime - imp_1d, 0)
        return cum_dbf / pro_rated_goal * 100
    except Exception:  # noqa: BLE001 — row data is user/API input; None = no prior
        return None


def is_new_line_item(lifetime, latest_day) -> bool:
    """True when a line item's *first* delivery is the latest data day — it
    delivered on the latest day (`latest_day` > 0) and had no impressions
    before it (cumulative `lifetime` == that day). This is the existence-based
    "new line item" flag for the pace cell: a line that didn't exist the prior
    day has no real pace trend to compare against, so the pace Δ is meaningless.
    Reads cumulative `lifetime_impressions_delivered` vs the latest day's
    `impressions_1d`; it distinguishes a just-launched line (no prior delivery)
    from an old one that merely stopped delivering (which keeps its prior
    lifetime, so `lifetime > latest_day`)."""
    lt, ld = _num(lifetime), _num(latest_day)
    if math.isnan(lt) or math.isnan(ld):
        return False
    return bool(ld > 0 and (lt - ld) <= 0)


# ── Stale PMP deals ─────────────────────────────────────────────────────


def stale_deal_mask(lbd_df, cutoff_iso: str):
    """Boolean mask over a pmp_last_bid_date frame: stale = last bid
    strictly before the cutoff, or never bid at all and first seen before
    the cutoff. Date columns are ISO strings with NA for missing — string
    comparison is correct because ISO dates sort lexicographically."""
    lb = lbd_df["last_bid_date"]
    fs = lbd_df["first_seen_date"]
    return (lb.notna() & (lb < cutoff_iso)) | (lb.isna() & fs.notna() & (fs < cutoff_iso))


def recently_seen_mask(lbd_df, seen_cutoff_iso: str):
    """Boolean mask over a pmp_last_bid_date frame: True = keep (still live),
    False = hide (gone). A deal is "gone" when `last_seen_date` — the last day
    it appeared in ANY source row (bid or not) — is strictly before the cutoff,
    i.e. it stopped being reported (paused/removed). Deals with no
    `last_seen_date` (NA: pre-tracking rows) are kept, so the filter only ever
    hides deals we positively know went silent. Pairs with `stale_deal_mask`:
    *stale AND recently-seen* = active but not winning bids (actionable, keep);
    *stale AND not seen* = paused/removed (hide). When the column is absent
    (old cached frame) every row is kept — behaviour is unchanged until the
    refresh starts populating it."""
    if "last_seen_date" not in getattr(lbd_df, "columns", []):
        return pd.Series(True, index=lbd_df.index)
    ls = lbd_df["last_seen_date"]
    return ls.isna() | (ls >= seen_cutoff_iso)


def idle_days(last_bid_date, first_seen_date, today: date) -> int:
    """Days since the deal last showed life: last bid date when known,
    first-seen date for deals that never bid, 0 when neither parses."""
    for v in (last_bid_date, first_seen_date):
        if pd.notna(v) and str(v) not in ("", "None", "nan"):
            try:
                return (today - date.fromisoformat(str(v))).days
            except ValueError:
                pass
    return 0


# ── Format canonicalization ─────────────────────────────────────────────

# The format taxonomy (Roger, 2026-06-12). Note "Video Preroll >30s" is
# NOT here — it's a benchmark band layered on Video via bump_video_format
# and carried in a separate _bench_format column, never a filter format.
CANONICAL_FORMATS = ("Display", "Video", "Interstitial", "Interscroller",
                     "FITO", "Centerstage", "Apple News")

_SIZE_TOKEN_RE = re.compile(r"\d{2,4}x\d{2,4}")


def canonicalize_format(raw, aliases=None):
    """Collapse the ad-format zoo into the house taxonomy (per Roger,
    2026-06-12): Display, Video, Interstitial, FITO, Centerstage,
    Apple News — plus the derived "Video Preroll >30s" band the >30s
    bump layers on top of Video.

    The raw column mixes GAM's INVENTORY_FORMAT_NAME strings ("Banner",
    "In-stream video") with the freeform position-10 token of line-item
    names (FITO-Video, Contextual-PreRoll, Backfill-970x250…) and, for
    names that don't follow the 14-field convention, outright junk
    (initials, prices, geos). Resolution order:

      1. user format_aliases (settings) — matched case-insensitively on
         the raw value, and re-applied once to the rule result, so an
         alias can re-route a whole rule-derived bucket
      2. canonical names pass through (case-normalized)
      3. family rules: FITO before video/display (FITO-Video is FITO);
         Apple News before the article/multi folds; Centerstage before
         the generic display family; native/multi/branded-article promos
         fold into Display (no Native/Multi buckets in the taxonomy)
      4. anything else → None: junk tokens are not formats. None keeps
         them out of the Format filter; their rows stay table-visible on
         default benchmark fallbacks, as before.

    A new legitimate format surfaces as None until it gets a rule here
    or a settings alias — the Settings page's unmapped-formats panel is
    where to spot one."""
    alias_map = {str(k).strip().lower(): v for k, v in (aliases or {}).items()}

    def _alias(value):
        return alias_map.get(str(value).strip().lower(), value)

    if not isinstance(raw, str) or not raw.strip():
        return None
    s = _alias(raw.strip())
    if not isinstance(s, str) or not s.strip():
        return None
    low = s.strip().lower()

    canon_by_low = {c.lower(): c for c in CANONICAL_FORMATS}
    if low in canon_by_low:
        result = canon_by_low[low]
    elif "fito" in low:
        result = "FITO"
    elif "apple-news" in low or "apple news" in low:
        result = "Apple News"
    elif "centerstage" in low:
        result = "Centerstage"
    elif "interstitial" in low:
        result = "Interstitial"
    elif "interscroller" in low or "uniscroller" in low:
        # One format, two product names — Uniscroller folds into
        # Interscroller (Roger, 2026-06-12).
        result = "Interscroller"
    elif ("preroll" in low or "pre-roll" in low
          or "in-stream" in low or "video" in low):
        result = "Video"
    elif ("display" in low or "banner" in low or "native" in low
          or "multi" in low
          or low.startswith("backfill") or _SIZE_TOKEN_RE.search(low)
          or "article" in low or "insight" in low):
        # Display is the catch-all visual family: native and multi/branded-
        # article promo lines fold here (no Native/Multi buckets), as do
        # size-named placements.
        result = "Display"
    else:
        return None

    final = _alias(result)
    return final if isinstance(final, str) and final.strip() else None


# Formats GAM's INVENTORY_FORMAT_NAME cannot express — it reports web
# interstitials and high-impact/custom units as "Banner" (and FITO video
# as "In-stream video") — so the line-item NAME is the source of truth
# for these. Ordered by precedence; matched anywhere in the name, which
# also survives the token-position drift some names have (e.g. the
# AppleTv Cape Fear lines carry their format word at position 11, not 10).
NAME_FORMAT_KEYWORDS = (
    ("fito", "FITO"),
    ("apple-news", "Apple News"),
    ("apple news", "Apple News"),
    ("centerstage", "Centerstage"),
    ("interscroller", "Interscroller"),
    ("uniscroller", "Interscroller"),
    ("interstitial", "Interstitial"),
)


def derive_format(inventory_format_name, line_item_name, aliases=None):
    """Canonical format for a line item from its two signals.

    Resolution order:
      1. name keywords (NAME_FORMAT_KEYWORDS) — the name wins for formats
         GAM's inventory vocabulary flattens into Banner/In-stream
      2. the API's INVENTORY_FORMAT_NAME, canonicalized — authoritative
         for the display/video families
      3. the name's position-10 token, canonicalized (pre-dimension rows)
    User format_aliases re-route any of the three outcomes."""
    if isinstance(line_item_name, str):
        low = line_item_name.lower()
        for kw, fmt in NAME_FORMAT_KEYWORDS:
            if kw in low:
                return canonicalize_format(fmt, aliases)
    if isinstance(inventory_format_name, str) and inventory_format_name.strip():
        return canonicalize_format(inventory_format_name, aliases)
    return canonicalize_format(li_part(line_item_name, 10), aliases)


# ── Delivery landing risk (ending-soon under-delivery) ──────────────────


def landing_projection(goal, delivered, daily_rate, days_left):
    """Projected final delivery at the current daily pace.

    Returns ``{projected, projected_pct, short}`` or None when the line
    isn't goal-graded (no positive impressions_goal — e.g. house/AV
    lines). `days_left` is clamped at 0, so a line ending today projects
    to exactly what it has delivered. A missing/NaN daily rate counts as
    0 (no further delivery assumed) rather than erroring."""
    g = _num(goal)
    if math.isnan(g) or g <= 0:
        return None
    d = _num(delivered)
    if math.isnan(d):
        return None
    rem = max(int(days_left), 0) if days_left is not None else 0
    rate = _num(daily_rate)
    proj = d + (0.0 if math.isnan(rate) else rate) * rem
    return {"projected": proj, "projected_pct": proj / g * 100.0,
            "short": max(g - proj, 0.0)}


def landing_at_risk(days_left, projected_pct,
                    window_days: int = 7, threshold_pct: float = 100.0) -> bool:
    """True when a line is ending within `window_days` (and not already
    past) AND projected to finish under `threshold_pct` of goal. Both
    None-guarded: a line with no end date or no projection never flags.
    Defaults match the owner's pick (within 7 days, projected < 100%)."""
    if days_left is None or projected_pct is None:
        return False
    return 0 <= days_left <= window_days and projected_pct < threshold_pct


# ── TTD Luckyland CPA ────────────────────────────────────────────────────

def ttd_cpa_summary(df: pd.DataFrame, start=None, end=None) -> dict:
    """Summarise a `ttd_luckyland` DataFrame for the CPA accordion.

    Returns a dict:
      date_min / date_max  — window edges (date objects)
      impressions          — total int
      clicks               — total int
      conversions          — total int (0 when column absent)
      spend_usd            — total float
      cpa                  — spend / conversions (None when 0 conversions)
      conv_rate            — conversions / clicks × 100 (None when 0 clicks)
      by_media_type        — list of dicts {media_type, impressions, clicks,
                             conversions, spend_usd, cpa, conv_rate}
      by_ad_size           — same shape keyed on `ad_size`, parsed as a WxH
                             token from the creative name (or a creative_size
                             column if a future report adds one) — the ad-size
                             breakdown shown when the card is opened; empty when
                             no creative carries a size (e.g. video-only)
      daily_conversions    — list of (date, n) sorted by date
      daily_cpa            — list of (date, cpa_float) sorted by date (only
                             days with conversions > 0)
      delta_conversions    — % change recent-half vs prior-half (None when
                             insufficient date range)
      delta_cpa            — absolute CPA change (recent − prior; None same)
      delta_spend          — % spend change (None same)

    `start` / `end` — optional datetime.date window; rows are kept where
    `start <= date <= end` (either bound may be None for open-ended). The
    dashboard passes `start` = the earliest start_date of the campaign's LIs
    that pass the active Status filter, so each card's window follows the
    dashboard filter (e.g. "Delivering" → starts this month, dropping last
    month's now-inactive flight). Both None = the whole frame (flight-to-date).
    All keys are always present; missing/filtered-out data becomes 0/None/[].
    """
    empty = {
        "date_min": None, "date_max": None,
        "impressions": 0, "clicks": 0, "conversions": 0,
        "spend_usd": 0.0, "cpa": None, "conv_rate": None,
        "by_media_type": [], "by_ad_size": [],
        "daily_conversions": [], "daily_cpa": [],
        "delta_conversions": None, "delta_cpa": None, "delta_spend": None,
    }
    if df.empty:
        return empty

    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        df = df.dropna(subset=["date"])
        if start is not None:
            df = df[df["date"].map(lambda d: d >= start)]
        if end is not None:
            df = df[df["date"].map(lambda d: d <= end)]
    if df.empty:
        return empty

    # Derive ad size for the breakdown. The TTD tables have no creative_size
    # column — size is encoded in the creative name (e.g.
    # "…_DisplayBanner_300x250_May_…"), so parse a WxH token from `creative`
    # (fall back to a real creative_size column if a future report adds one).
    # No match — video creatives carry a duration (RT_30s), not a pixel size —
    # yields NaN, which groupby drops, so those rows just don't appear in the
    # size table.
    if "creative_size" in df.columns:
        df["_ad_size"] = df["creative_size"]
    elif "creative" in df.columns:
        df["_ad_size"] = df["creative"].astype(str).str.extract(
            r"(\d{2,4}x\d{2,4})", expand=False)

    _c = df.columns.tolist()
    impr   = int(pd.to_numeric(df["impressions"], errors="coerce").fillna(0).sum()) if "impressions" in _c else 0
    clicks = int(pd.to_numeric(df["clicks"],      errors="coerce").fillna(0).sum()) if "clicks"      in _c else 0
    # Use Media Cost (media_spend_usd) to match the manual CPA reports; fall
    # back to Advertiser Cost (spend_usd) only when the media column is absent.
    _spend_col = "media_spend_usd" if "media_spend_usd" in _c else "spend_usd"
    spend  = float(pd.to_numeric(df[_spend_col], errors="coerce").fillna(0.0).sum()) if _spend_col in _c else 0.0
    convs  = int(pd.to_numeric(df.get("attributed_conversions", pd.Series(dtype=float)),
                               errors="coerce").fillna(0).sum()) if "attributed_conversions" in _c else 0

    cpa        = round(spend / convs, 2) if convs > 0 else None
    conv_rate  = round(convs / clicks * 100, 3) if clicks > 0 else None
    date_min   = df["date"].min() if "date" in _c else None
    date_max   = df["date"].max() if "date" in _c else None

    # ── breakdowns: by media type and by ad size (creative_size) ──
    # Same shape, one helper; sorted by spend desc. Blank/NaN group keys
    # (e.g. video rows that carry no creative size) are skipped.
    def _breakdown(col, label_key):
        out: list[dict] = []
        if col in _c and "date" in _c:
            for key, grp in df.groupby(col):
                if key is None or (isinstance(key, float) and pd.isna(key)) or str(key).strip() == "":
                    continue
                g_impr  = int(pd.to_numeric(grp.get("impressions", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
                g_clk   = int(pd.to_numeric(grp.get("clicks",      pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
                g_spend = float(pd.to_numeric(grp.get(_spend_col, pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
                g_conv  = int(pd.to_numeric(grp.get("attributed_conversions", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if "attributed_conversions" in _c else 0
                out.append({
                    label_key:     str(key),
                    "impressions": g_impr,
                    "clicks":      g_clk,
                    "conversions": g_conv,
                    "spend_usd":   g_spend,
                    "cpa":         round(g_spend / g_conv, 2) if g_conv > 0 else None,
                    "conv_rate":   round(g_conv / g_clk * 100, 3) if g_clk > 0 else None,
                })
            out.sort(key=lambda r: r["spend_usd"], reverse=True)
        return out

    by_media = _breakdown("media_type", "media_type")
    by_size  = _breakdown("_ad_size", "ad_size")

    # ── daily series (summed across all ad groups / media types) ──
    daily_convs: list[tuple] = []
    daily_cpa_s: list[tuple] = []
    if "date" in _c:
        day_agg: dict[object, dict] = {}
        for _, row in df.iterrows():
            d = row["date"]
            if d not in day_agg:
                day_agg[d] = {"spend": 0.0, "convs": 0}
            day_agg[d]["spend"] += float(pd.to_numeric(row.get(_spend_col, 0), errors="coerce") or 0)
            day_agg[d]["convs"] += int(pd.to_numeric(row.get("attributed_conversions", 0), errors="coerce") or 0) if "attributed_conversions" in _c else 0
        for d in sorted(day_agg):
            n = day_agg[d]["convs"]
            s = day_agg[d]["spend"]
            daily_convs.append((d, n))
            if n > 0:
                daily_cpa_s.append((d, round(s / n, 2)))

    # ── window-half deltas (recent half vs prior half by distinct dates) ──
    delta_convs = delta_cpa_v = delta_spend = None
    if "date" in _c and "attributed_conversions" in _c:
        dates_sorted = sorted(day_agg.keys()) if daily_convs else []
        D = len(dates_sorted)
        if D >= 6:
            half = D // 2
            prior_dates = set(dates_sorted[:half])
            recent_dates = set(dates_sorted[half:])
            p_spend = sum(day_agg[d]["spend"] for d in prior_dates)
            r_spend = sum(day_agg[d]["spend"] for d in recent_dates)
            p_conv  = sum(day_agg[d]["convs"] for d in prior_dates)
            r_conv  = sum(day_agg[d]["convs"] for d in recent_dates)
            if p_conv > 0:
                delta_convs = round((r_conv - p_conv) / p_conv * 100, 1)
            if p_spend > 0:
                delta_spend = round((r_spend - p_spend) / p_spend * 100, 1)
            p_cpa = round(p_spend / p_conv, 2) if p_conv > 0 else None
            r_cpa = round(r_spend / r_conv, 2) if r_conv > 0 else None
            if p_cpa is not None and r_cpa is not None:
                delta_cpa_v = round(r_cpa - p_cpa, 2)

    return {
        "date_min":           date_min,
        "date_max":           date_max,
        "impressions":        impr,
        "clicks":             clicks,
        "conversions":        convs,
        "spend_usd":          spend,
        "cpa":                cpa,
        "conv_rate":          conv_rate,
        "by_media_type":      by_media,
        "by_ad_size":         by_size,
        "daily_conversions":  daily_convs,
        "daily_cpa":          daily_cpa_s,
        "delta_conversions":  delta_convs,
        "delta_cpa":          delta_cpa_v,
        "delta_spend":        delta_spend,
    }


# ── TTD CPA ↔ GAM line-item linkage ───────────────────────────────────────
# The TTD feed has no GAM line_item_id, and gam_campaigns has no TTD deal_id,
# but both names encode the same two dimensions for the gambling CPA flights —
# audience (Casino/Social) + ad size — so we join on those. The TTD ad_group
# (`LC_ACQ_TTD_US_Display_..._728x90-300x250_CasinoGamblers`) and the GAM LI
# name (`..._Display-CasinoGamblers-JUNE-728x90-300x250_...`) both carry them.
_CPA_AUD_RE = re.compile(r"(casino|social)gamblers", re.I)
_CPA_SIZE_RE = re.compile(r"(\d{2,4}x\d{2,4}(?:-\d{2,4}x\d{2,4})*)")


def cpa_goal_delta(cpa, goal):
    """Compare a CPA to the acquisition goal for the Priority-flights cards.
    Returns ``{"over": bool, "delta": abs($ from goal)}`` — `over` True when the
    CPA exceeds the goal (bad), with the absolute distance. None when either
    side is missing/NaN or the goal isn't positive, so the card only shows a
    verdict when both a CPA and a real goal exist. Exactly at goal = not over."""
    try:
        c = float(cpa)
        g = float(goal)
    except (TypeError, ValueError):
        return None
    if pd.isna(c) or pd.isna(g) or g <= 0:
        return None
    return {"over": c > g, "delta": abs(c - g)}


def cpa_join_key(name):
    """Key linking a TTD ad_group ↔ a GAM line-item name for the gambling CPA
    flights: ``"<audience>|<size>"`` (e.g. ``"casino|728x90-300x250"``). None
    when the name carries neither token (most LIs, and the video ad_groups that
    have no pixel size) — those simply don't get a CPA block."""
    if not isinstance(name, str):
        return None
    a = _CPA_AUD_RE.search(name)
    s = _CPA_SIZE_RE.search(name)
    if not a or not s:
        return None
    return f"{a.group(1).lower()}|{s.group(1)}"


def _norm_deal_id(v) -> str:
    """Normalize a deal id for joining — string, stripped, trailing ``.0`` gone
    (the #151 float-suffix hazard: a numeric read of an int id stringifies as
    ``"4211124.0"`` and never matches ``"4211124"``)."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _ttd_cpa_window(df, start, end):
    """Coerce `date` to dates and clamp to [start, end] (either may be None)."""
    if "date" not in df.columns:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df = df.dropna(subset=["date"])
    if start is not None:
        df = df[df["date"].map(lambda d: d >= start)]
    if end is not None:
        df = df[df["date"].map(lambda d: d <= end)]
    return df


def _ttd_cpa_aggregate(df):
    """Roll a filtered + windowed TTD frame into the per-LI CPA summary dict
    ``{cpa, conversions, spend_usd, daily_cpa: [(date, cpa), …]}`` — shared by
    the deal-id (`ttd_cpa_for_deal`) and name-token (`ttd_cpa_for_li`) joins."""
    conv  = int(pd.to_numeric(df.get("attributed_conversions", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    # Use Media Cost (media_spend_usd) to match the manual CPA reports; fall
    # back to Advertiser Cost (spend_usd) only when the media column is absent.
    _sc = "media_spend_usd" if "media_spend_usd" in df.columns else "spend_usd"
    spend = float(pd.to_numeric(df.get(_sc, pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    daily_cpa: list[tuple] = []
    if "date" in df.columns:
        agg: dict[object, dict] = {}
        for _, r in df.iterrows():
            e = agg.setdefault(r["date"], {"spend": 0.0, "convs": 0})
            e["spend"] += float(pd.to_numeric(r.get(_sc, 0), errors="coerce") or 0)
            e["convs"] += int(pd.to_numeric(r.get("attributed_conversions", 0), errors="coerce") or 0)
        for d in sorted(agg):
            if agg[d]["convs"] > 0:
                daily_cpa.append((d, round(agg[d]["spend"] / agg[d]["convs"], 2)))
    return {
        "cpa":         round(spend / conv, 2) if conv > 0 else None,
        "conversions": conv,
        "spend_usd":   spend,
        "daily_cpa":   daily_cpa,
    }


def ttd_cpa_for_deal(df, deal_id, start=None, end=None):
    """Per-LI CPA summary keyed by the GAM/TTD shared **DEAL_ID** — the robust
    join. GAM's report DEAL_ID equals the TTD feed's `deal_id` for our PG
    flights, so this sidesteps every failure mode of the name-token join
    (`cpa_join_key`): RShore↔ILee re-trafficking, the `Casino-Gamblers` hyphen,
    and the GAM-vs-TTD ad-size taxonomies. Same shape as `ttd_cpa_for_li`.
    None when `deal_id` is blank/0, the frame has no `deal_id` column, or no
    rows match the window."""
    if df is None or df.empty or "deal_id" not in df.columns:
        return None
    key = _norm_deal_id(deal_id)
    if not key or key.lower() in {"0", "nan", "none", "<na>"}:
        return None
    df = df[df["deal_id"].map(_norm_deal_id) == key]
    if df.empty:
        return None
    df = _ttd_cpa_window(df, start, end)
    if df.empty:
        return None
    return _ttd_cpa_aggregate(df)


def ttd_cpa_for_li(df, key, start=None, end=None):
    """Aggregate the TTD rows whose ad_group matches `key` (cpa_join_key) into a
    per-LI CPA summary for the line-item drawer:
    ``{cpa, conversions, spend_usd, daily_cpa: [(date, cpa), …]}``. Windowed to
    [start, end] (dates; either may be None). None when `key` is None or no
    matching rows fall in the window. **Name-token fallback** — used only when
    the LI has no `deal_id` (`ttd_cpa_for_deal` is the primary join)."""
    if df is None or df.empty or key is None or "ad_group" not in df.columns:
        return None
    df = _ttd_cpa_window(df.copy(), start, end)
    if df.empty:
        return None
    df = df[df["ad_group"].map(lambda n: cpa_join_key(n) == key)]
    if df.empty:
        return None
    return _ttd_cpa_aggregate(df)
