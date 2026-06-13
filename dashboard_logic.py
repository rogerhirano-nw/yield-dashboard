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


# ── Stale PMP deals ─────────────────────────────────────────────────────


def stale_deal_mask(lbd_df, cutoff_iso: str):
    """Boolean mask over a pmp_last_bid_date frame: stale = last bid
    strictly before the cutoff, or never bid at all and first seen before
    the cutoff. Date columns are ISO strings with NA for missing — string
    comparison is correct because ISO dates sort lexicographically."""
    lb = lbd_df["last_bid_date"]
    fs = lbd_df["first_seen_date"]
    return (lb.notna() & (lb < cutoff_iso)) | (lb.isna() & fs.notna() & (fs < cutoff_iso))


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
