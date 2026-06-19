"""
Minimal Streamlit dashboard pointing at the local cache.

Run with:
    streamlit run dashboard.py

Loads only from the SQLite cache populated by refresh_cache.py — never hits
Magnite directly. That's the whole point: the dashboard stays snappy regardless
of Magnite's queue.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _fmt_last_refresh(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(_ET)
        tz_label = "EDT" if dt_et.dst().seconds else "EST"
        return dt_et.strftime(f"%Y-%m-%d %I:%M %p {tz_label}")
    except Exception:
        return str(ts)


def _fmt_header_freshness(ts) -> str | None:
    """Compact data-freshness label for the header timestamp.
    'today'   → '8:13 AM EDT'
    'yesterday' → 'Yesterday 11:28 PM EDT'
    older     → 'May 18 · 11:28 PM EDT'
    Returns None for unparseable input.
    """
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_et = dt.astimezone(_ET)
        tz_label = "EDT" if dt_et.dst().seconds else "EST"
        time_str = dt_et.strftime(f"%-I:%M %p {tz_label}")
        today_et = datetime.now(_ET).date()
        delta_days = (today_et - dt_et.date()).days
        if delta_days <= 0:
            return time_str
        if delta_days == 1:
            return f"Yesterday {time_str}"
        return dt_et.strftime("%b %-d · ") + time_str
    except Exception:
        return None

import altair as alt
import pandas as pd
import sqlalchemy
import streamlit as st

import dashboard_logic as dl

def _load_dotenv() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for _line in env_file.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

_load_dotenv()


@st.cache_resource
def _engine() -> sqlalchemy.Engine:
    try:
        url = st.secrets["DATABASE_URL"]
    except Exception:
        url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env or Streamlit secrets.")
    return sqlalchemy.create_engine(url, pool_size=2, max_overflow=1, pool_recycle=300)


# ── Settings ─────────────────────────────────────────────────────────────────

_SETTINGS_PATH = Path(__file__).parent / "settings.json"

_DEFAULT_SETTINGS: dict = {
    "ssps": [
        {
            "name": "GAM", "enabled": True, "table": "gam_pmp_deals",
            "deal_types": ["Private Auction", "Preferred Deal", "Programmatic Guaranteed"],
            "deal_source_default": "Publisher",
            "columns": {
                "Deal": "programmatic_deal_name", "Deal Type": "[auto]", "DSP": "dsp",
                "Format": "ad_format", "Seller": "[auto]",
                "Paid Impressions": "ad_server_impressions",
                "Revenue": "ad_server_cpm_and_cpc_revenue", "eCPM": "ad_server_average_ecpm",
                "Win Rate %": "", "Total Requests": "", "Bid Responses": "",
            },
        },
        {
            "name": "Magnite", "enabled": True, "table": "magnite_deal_daily",
            "deal_types": ["Private Auction", "Preferred Deal", "Private Marketplace", "Auction Package"],
            "columns": {
                "Deal": "deal", "Deal Type": "[auto]", "DSP": "partner",
                "Format": "ad_format", "Seller": "[auto]",
                "Paid Impressions": "paid_impression",
                "Revenue": "publisher_gross_revenue", "eCPM": "ecpm",
                "Win Rate %": "[computed: impressions / requests]",
                "Total Requests": "bid_requests", "Bid Responses": "bid_responses",
            },
        },
        {
            "name": "Pubmatic", "enabled": True, "table": "pubmatic_deals",
            "deal_types": ["Private Auction", "Preferred Deal", "Programmatic Guaranteed", "Private Marketplace"],
            "columns": {
                "Deal": "deal", "Deal Type": "[auto]", "DSP": "dsp",
                "Format": "ad_format", "Seller": "[auto]",
                "Paid Impressions": "paid_impressions",
                "Revenue": "revenue", "eCPM": "ecpm",
                "Win Rate %": "win_rate", "Total Requests": "total_requests",
                "Bid Responses": "non_zero_bid_responses",
            },
        },
    ],
    "ae_names": {
        "AShah": "Amit Shah", "Ashah": "Amit Shah",
        "BKaretny": "Ben Karetny", "Bkaretny": "Ben Karetny",
        "BRobinson": "Brian Robinson",
        "CMamboury": "Chantal Mamboury",
        "DDivack": "Dana Divack", "DVarvaro": "Danielle Varvaro",
        "House": "House",
        "ILee": "Ivy Lee", "Ilee": "Ivy Lee", "Ivy": "Ivy Lee",
        "JAmalfi": "Julie Amalfi", "JGentile": "Jeremy Gentile", "JMakin": "Jeremy Makin",
        "KWebb": "House",
        "NAkhtar": "Nabeel Akhtar",
        "RHirano": "Roger Hirano", "RShore": "Rob Shore",
        "SCarroll": "Summer Carroll", "SCaroll": "Summer Carroll",
        "THern": "Theresa Hern", "Thern": "Theresa Hern", "THearn": "Theresa Hern",
    },
    "team_names": {
        "USA": "USA", "INTL": "International",
    },
    # AE → Account Manager mapping. AEs (Sellers) are the ones who close
    # the deal; AMs are the ops people who manage the campaign once live.
    # Each AE typically has a paired AM; populate this mapping in Configure
    # → Section 4 → Account Manager mapping. The Direct campaigns view
    # surfaces this as an "Account Manager" filter dropdown (AM names are
    # derived from the seller_ae of each line item via this map).
    # Empty AM values are kept as scaffolding (so the codes show up in the
    # Configure table even before assignment); rows with empty AMs are
    # treated as "Unassigned" by the dashboard filter.
    "account_managers": {
        "AShah": "", "Ashah": "",
        "BKaretny": "", "Bkaretny": "",
        "BRobinson": "",
        "CMamboury": "",
        "DDivack": "",
        "DVarvaro": "",
        "House": "",
        "ILee": "", "Ilee": "", "Ivy": "",
        "JAmalfi": "", "JGentile": "", "JMakin": "",
        "KWebb": "",
        "NAkhtar": "",
        "RHirano": "",
        "RShore": "",
        "SCarroll": "", "SCaroll": "",
        "THearn": "", "THern": "", "Thern": "",
    },
    # Per-format thresholds. *_pct is the green floor (≥ target = green). The
    # matching *_red_below is the red ceiling (< red_below = red); anything
    # between red_below and target renders amber. Leaving *_red_below null
    # falls back to target × 0.85 (the original implicit band), so existing
    # settings keep working without change.
    # Keys follow the canonical taxonomy (dashboard_logic.CANONICAL_FORMATS):
    # Display, Video (+ the derived Video Preroll >30s band), Interstitial,
    # FITO, Centerstage, Apple News. Native/Multi rows are legacy — those
    # formats fold into Display at canonicalization — kept so older saved
    # settings merge cleanly; safe to delete from the editor.
    "benchmarks_by_format": {
        "Display":            {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": None,  "vcr_red_below": None},
        "Video":              {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": 70.0,  "vcr_red_below": None},
        "Video Preroll >30s": {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": 50.0,  "vcr_red_below": None},
        "FITO":               {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": None,  "vcr_red_below": None},
        "Interscroller":      {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": None,  "vcr_red_below": None},
        "Centerstage":        {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": None,  "vcr_red_below": None},
        "Apple News":         {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": None,  "vcr_red_below": None},
        "Native":             {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": None,  "vcr_red_below": None},
        "Multi":              {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": 70.0,  "vcr_red_below": None},
        "Interstitial":       {"viewability_pct": 70.0, "viewability_red_below": None, "ctr_pct": 0.30, "ctr_red_below": None, "vcr_pct": None,  "vcr_red_below": None},
    },
    "pacing_target_pct": 100.0,
    # Landing-risk card (Direct tab): flag lines ending within
    # `landing_window_days` that are projected — at the current daily pace —
    # to finish under `landing_threshold_pct` of goal. Owner default is a
    # 7-day window (Roger, 2026-06-17); widen to ~14 to catch big shortfalls
    # earlier (a 14-day window surfaces ~10 lines vs ~1 at 7, since the worst
    # under-delivery risks are still 2 weeks out when they're most fixable).
    "landing_window_days": 7,
    "landing_threshold_pct": 100.0,
    # Stale-deals "still live" window (PMP signals): a deal stops counting as
    # live (and drops out of Stale deals) once it hasn't appeared in the GAM
    # bid feed for this many days. gam_deal_bid_daily retains ~7 days, so 7 ≈
    # "currently live in GAM" — a paused deal clears within ~a week instead of
    # the old 90-day grace, which left paused deals lingering for months
    # (Roger 2026-06-17). Distinct from the 90-day no-bid staleness test.
    "stale_seen_window_days": 7,
    # Manual long-preroll override — list of rules that force a line into
    # the "Video Preroll >30s" benchmark when creative duration can't be
    # auto-detected from GAM (Newsweek's 3rd-party video tags via Innovid /
    # DCM hide the duration behind JS, so the SOAP duration + Reports
    # VIDEO_AD_DURATION + VAST parse all return null for those creatives).
    # Each rule: {match_field, match_value}.
    #   match_field: "order_name" | "line_item_name" | "line_item_id"
    #   match_value: substring (case-insensitive) for the *_name fields,
    #                exact match for line_item_id
    "long_preroll_lines": [],
    "gam_network_id": "",  # set in Configure → Direct campaigns → GAM integration
    "airtable_base_id": "appX7xp1veDq9ndUe",
    "airtable_form_id": "pagN88p2kwQBcjqZf",
    "airtable_field_names": {
        "Request Type": "Request Type",
        "Line Item":    "Line Item",
        "GAM ID":       "GAM ID",
        "Severity":     "Severity",
        "Seller":       "Seller",
        "Reporter":     "Reporter",
        "Notes":        "Notes",
    },
    "airtable_request_type_routing": [
        {"context": "Direct line · delivery problem",        "request_type": "Direct Campaign - Troubleshooting"},
        {"context": "Direct line · viewability anomaly",     "request_type": "Direct Campaign - Screenshot"},
        {"context": "Direct line · healthy end-of-flight",   "request_type": "Direct Campaign - IO Review"},
        {"context": "Direct line · social media component",  "request_type": "Direct Campaign - Social Posts"},
        {"context": "PMP deal · any issue",                  "request_type": "PMP - Adjust"},
    ],
    "airtable_reporter": "Roger Hirano",
    "status_colors": [
        # Newsweek light state tokens (status chips are a sanctioned
        # saturated-green surface per the asymmetric-green rule).
        {"keyword": "Delivering", "color": "#3c6b14"},  # --state-positive
        {"keyword": "Paused",     "color": "#8a6d00"},  # --state-warning
        {"keyword": "Completed",  "color": "#8c887b"},  # --text-muted
    ],
    "seller_colors": {},  # per-seller overrides; sellers absent fall back to hash
    "deal_type_codes": {
        "PA": "Private Auction", "PD": "Preferred Deal",
        "PG": "Programmatic Guaranteed", "PMP": "Private Marketplace",
    },
    "dsp_aliases": {
        "Amazon DSP": "Amazon",
        "BasisTechnologies": "Basis",
        "Basis Technologies": "Basis",
        "RTBHouse": "RTB House",
        "RTB House (APAC)": "RTB House",
        "RTB House (US)": "RTB House",
        "RTB House PL": "RTB House",
        "Beeswax io": "Beeswax",
        "DeepIntent-OpenRTB": "DeepIntent",
        "Stackadapt": "StackAdapt",
        "Adelphic-DV360": "Adelphic",
        "Adelphic/DV360": "Adelphic",
        "Adobe NA (fka TubeMogul)": "Adobe",
        "MEDIA FORCE COMMUNICATIONS (2007) LTD": "Mediaforce",
        "Fidelity (Display & Video 360)": "DV360",
        "Google Internal Marketing - NA/EMEA/APAC (Display & Video 360)": "DV360",
        "H&S | AT&T (Display & Video 360)": "DV360",
        "Horizon Media (Display & Video 360)": "DV360",
        "Lavazza HUB (Display & Video 360)": "DV360",
        "MightyHive - Goldman Sachs Consumer Lending - US (Display & Video 360)": "DV360",
        "Nexus Media Solutions IT (Display & Video 360)": "DV360",
        "OMD Apple USA (Display & Video 360)": "DV360",
        "TP - Bitdefender - DLV - DV - RO (Display & Video 360)": "DV360",
        "TP - LLYC USA - PRO - DV360 - US (Display & Video 360)": "DV360",
        "TP - Turismo de Portugal - Dentsu - DV360 - PT (Display & Video 360)": "DV360",
        "Horizon Media Inc - nj1zgju (The Trade Desk)": "The Trade Desk",
        "Matterkind US Google - The Trade Desk (The Trade Desk)": "The Trade Desk",
        "Quigley Simpson - 1ufz33r (The Trade Desk)": "The Trade Desk",
        "Virtual Gaming World - 7xcsg31 (The Trade Desk)": "The Trade Desk",
        "Tara-Group": "TaraGroup",
        "TTD": "The Trade Desk",
        "ZetaGlobal": "Zeta DSP",
    },
    "format_aliases": {
        "Banner": "Display",
        "In-stream video": "Video",
    },
    "deal_source_aliases": {
        "Publisher Deals": "Publisher",
    },
    "deal_type_aliases": {
        "PMP": "Private Auction",
        "PMP Preferred": "Preferred Deal",
        "Marketplace Deal": "Private Marketplace",
        "Preferred Deals": "Preferred Deal",
        "Programmatic Guaranteed Deal": "Programmatic Guaranteed",
        "Private Marketplace Deal": "Private Marketplace",
    },
    "included_order_patterns": ["Newsweek_Direct%"],
    "default_statuses": ["Delivering", "Upcoming"],
    "direct_sources": [
        {
            "name": "GAM Direct",
            "enabled": True,
            "table": "gam_campaigns",
            "order_name_prefix": "Newsweek_Direct",
            "columns": {
                "Seller":        "seller_ae",
                "Advertiser":    "advertiser",
                "Campaign":      "campaign_name",
                "Line Item":     "line_item_name",
                "Format":        "ad_format",
                "Start Date":    "start_date",
                "End Date":      "end_date",
                "Goal":          "impressions_goal",
                "CPM Rate":      "cpm_rate",
                "Delivered":     "lifetime_impressions_delivered",
                "Remaining":     "remaining_impressions",
                "Clicks":        "ad_server_clicks",
                "Pace":          "pacing_pct",
                "Δ":             "pacing_delta",
                "Viewability %": "ad_server_active_view_viewable_impressions_rate",
                "CTR %":         "ad_server_ctr",
                "VCR %":         "vcr",
                "Revenue":       "ad_server_cpm_and_cpc_revenue",
            },
        },
    ],
}


def _load_settings() -> dict:
    def _with_defaults(loaded: dict) -> dict:
        """Return loaded settings with any missing top-level keys filled from _DEFAULT_SETTINGS.
        Deep-merges a small set of dict-valued keys so new default sub-entries
        (e.g. a newly-added benchmark format) flow through to deployments
        whose DB already has a saved version of that top-level key. User-set
        values still win — the deep-merge order is defaults first, loaded last."""
        result = {**_DEFAULT_SETTINGS, **loaded}
        for _k in ("ae_names", "team_names", "account_managers",
                   "benchmarks_by_format", "airtable_field_names"):
            result[_k] = {
                **(_DEFAULT_SETTINGS.get(_k) or {}),
                **(loaded.get(_k) or {}),
            }
        return result

    def _patch_direct_columns(cfg: dict) -> dict:
        """Reconcile direct_sources columns against settings.json — add missing keys
        from the file AND drop DB-only keys that the file no longer carries.

        File is the canonical column set. The DB store is allowed to override
        VALUES (user customization persists), but cannot keep entries for keys
        the file has dropped — otherwise removing a column from the canonical
        set leaves it stranded in prod forever (the original bug behind the
        'Impressions (1d) won't go away' report)."""
        if not _SETTINGS_PATH.exists():
            return cfg
        try:
            with open(_SETTINGS_PATH) as _pf:
                file_cfg = json.load(_pf)
        except Exception:
            return cfg
        file_by_name = {s["name"]: s for s in file_cfg.get("direct_sources", [])}
        patched = []
        for src in cfg.get("direct_sources", []):
            file_cols = file_by_name.get(src["name"], {}).get("columns", {})
            db_cols   = src.get("columns", {})
            if file_cols:
                merged_cols = {k: db_cols.get(k, v) for k, v in file_cols.items()}
            else:
                merged_cols = db_cols
            patched.append({**src, "columns": merged_cols})
        return {**cfg, "direct_sources": patched}

    def _patch_ssp_defaults(cfg: dict) -> dict:
        """Backfill per-SSP fields (e.g. deal_source_default) from settings.json onto DB-loaded ssps.

        Without this, adding a new SSP-level field to settings.json (like the GAM=Publisher
        deal-source rule from PR #10) is silently dropped for any environment whose DB snapshot
        predates the field — DB values always win in _load_settings, so user-edited fields
        survive while genuinely-new keys flow through.
        """
        file_by_name: dict = {}
        if _SETTINGS_PATH.exists():
            try:
                with open(_SETTINGS_PATH) as _pf:
                    file_by_name = {s["name"]: s for s in json.load(_pf).get("ssps", [])}
            except Exception:
                file_by_name = {}
        default_by_name = {s["name"]: s for s in _DEFAULT_SETTINGS.get("ssps", [])}
        patched = []
        for ssp in cfg.get("ssps", []):
            base = {**default_by_name.get(ssp["name"], {}), **file_by_name.get(ssp["name"], {})}
            patched.append({**base, **ssp})
        return {**cfg, "ssps": patched}

    def _patch_ae_names(cfg: dict) -> dict:
        """Merge settings.json's ae_names over the loaded ae_names dict.

        _with_defaults already deep-merges _DEFAULT_SETTINGS.ae_names with the
        DB-loaded ae_names, but settings.json edits (e.g. new AE aliases for
        typo'd deal-name spellings) never reach prod because the DB load wins
        and the file is only consulted as a last-resort fallback. This helper
        layers settings.json on top of (defaults + DB) so file edits propagate
        — same shape as _patch_direct_columns / _patch_ssp_defaults.
        """
        file_aes: dict = {}
        if _SETTINGS_PATH.exists():
            try:
                with open(_SETTINGS_PATH) as _pf:
                    file_aes = json.load(_pf).get("ae_names", {}) or {}
            except Exception:
                file_aes = {}
        merged = {**cfg.get("ae_names", {}), **file_aes}
        return {**cfg, "ae_names": merged}

    # Primary: database (survives redeployments on Streamlit Cloud)
    try:
        with _engine().connect() as conn:
            row = conn.execute(
                sqlalchemy.text("SELECT value FROM dashboard_settings WHERE key = 'main'")
            ).fetchone()
            if row:
                return _patch_ae_names(_patch_ssp_defaults(_patch_direct_columns(_with_defaults(json.loads(row[0])))))
    except Exception:
        pass
    # Fallback: local file (useful for first-run and local dev)
    if _SETTINGS_PATH.exists():
        try:
            with open(_SETTINGS_PATH) as f:
                return _patch_ae_names(_patch_ssp_defaults(_with_defaults(json.load(f))))
        except Exception:
            pass
    return _DEFAULT_SETTINGS


def _save_settings(data: dict) -> None:
    json_str = json.dumps(data, indent=2)
    now = datetime.now(timezone.utc).isoformat()
    # Write to database so changes survive redeployments
    with _engine().begin() as conn:
        conn.execute(sqlalchemy.text("""
            CREATE TABLE IF NOT EXISTS dashboard_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        conn.execute(sqlalchemy.text(
            "DELETE FROM dashboard_settings WHERE key = 'main'"
        ))
        conn.execute(sqlalchemy.text(
            "INSERT INTO dashboard_settings (key, value, updated_at) VALUES ('main', :v, :t)"
        ), {"v": json_str, "t": now})
    # Also write locally so file stays in sync for dev
    try:
        with open(_SETTINGS_PATH, "w") as f:
            f.write(json_str)
    except Exception:
        pass
    st.cache_data.clear()


_cfg = _load_settings()
_ssp_enabled: dict[str, bool] = {s["name"]: s.get("enabled", True) for s in _cfg["ssps"]}


# Salesperson short-name parsing lives in dashboard_logic (tested); this
# alias keeps the historical name used across both table views.
_parse_gam_salesperson = dl.parse_gam_salesperson


def _attention_html(idx, prior=None) -> str:
    """Render the DV Attention Index. 100 = DV's industry median; higher
    = better attention. Color bands:
      red    < 85   (15%+ below median — meaningful underperformance)
      amber  85-100 (slightly below median)
      green  ≥ 100  (at or above median)
    None / NaN → em-dash (line/deal not in the DV report).

    Optional `prior` (latest-day-excluded mean) appends a "▲/▼ Xpp"
    delta below the value, matching the Pace cell's pattern. Higher is
    better, so up = green, down = red.

    Defined at module level (not inside the campaigns view) because both
    the Direct campaigns table AND the PMP deals table use it — each from
    a different scope inside the active_view block."""
    if idx is None or pd.isna(idx):
        return '<div class="cell-dash">—</div>'
    v = float(idx)
    _b = dl.attention_band(v)
    if _b == "red":
        cell = f'<div class="pill pill-red">{v:.0f}</div>'
    elif _b == "amber":
        cell = f'<div class="txt-amber">{v:.0f}</div>'
    else:
        cell = f'<div class="txt-green">{v:.0f}</div>'
    if prior is not None and not pd.isna(prior):
        cell += _delta_below_html(v - float(prior), lower_is_worse=True)
    return cell


def _delta_below_html(
    d,
    lower_is_worse: bool = True,
    unit: str = "pp",
    *,
    new_line_threshold: float = 100.0,
    noise_threshold: float = 0.05,
) -> str:
    """Return the secondary "▲/▼ X.Xpp" row that sits under a main value
    cell — matches the visual the Pace column has used since the redesign.

    `d` is current − prior in pp/index-point units, OR pct change in %
    (set `unit="%"`). `lower_is_worse=True` means "higher is better"
    (Viewability, Attention, CTR, VCR, Pace, Revenue, Impressions);
    set False for IVT-style metrics where rising = bad (SIVT, GIVT).

    For VOLUME columns (Revenue, Impressions) where doubling vs prior
    is a real signal not a "this line is new" flag, pass
    `new_line_threshold=None` to disable the italic-flag branch entirely.

    Returns "" when there's no signal (None / NaN / |d| < noise band).
    """
    verdict = dl.classify_delta(d, lower_is_worse,
                                new_line_threshold=new_line_threshold,
                                noise_threshold=noise_threshold)
    if verdict is None:
        return ""
    if verdict == "new":
        return '<div class="pace-delta" style="font-style:italic">new line item</div>'
    arrow, is_improvement = verdict
    cls = "pace-delta up" if is_improvement else "pace-delta"
    # Sub-1 deltas get 2 decimals (so 0.04 vs 0.10 are distinguishable);
    # larger deltas stay at 1 decimal for tidiness.
    body = f"{abs(d):.2f}{unit}" if abs(d) < 1 else f"{abs(d):.1f}{unit}"
    return f'<div class="{cls}">{arrow} {body}</div>'


def _ivt_html(pct, prior=None) -> str:
    """Render a DV IVT impression-weighted percentage (used by both
    the SIVT and GIVT columns).

    Optional `prior` (latest-day-excluded mean) appends a "▲/▼ Xpp"
    delta below the value. IVT is "lower is better", so up = red
    (rising fraud is bad), down = green (improving). Polarity flipped
    via lower_is_worse=False.


    Calculation (per-line, last 7 days):
        IVT % = Σ Monitored Ads (Fraud rows) / Σ Monitored Ads (all rows) × 100

    Why SIVT and GIVT are tracked separately (MRC standard):
      - GIVT = General Invalid Traffic: self-identifies as invalid;
        standard detection (declared bots, known data-center IPs).
      - SIVT = Sophisticated Invalid Traffic: hard to detect; needs
        advanced analytics. Sub-categories include Data Center Traffic,
        Bot Fraud, Hijacked Devices, Emulator Devices, App/Site Fraud,
        Injected Ads, Laundering. The sub-category breakdown isn't in
        the current export — see project_yield_dashboard_dv_attention.md
        memory note for the asks-of-DV list.

    Color bands tuned to industry-standard impression-weighted IVT
    thresholds:
      green  < 1%   (excellent — Newsweek's overall publisher IVT
                     hovers around 0.5-1% per the 2026-05-24 export)
      amber  1-3%   (acceptable but watch — typical industry tolerance)
      red    ≥ 3%   (problem — escalate; risk to buyer relationships
                     and IAB Tag Lab cert)
    Shows 2 decimal places under 1% (where small movements matter) and
    1 decimal at higher values. None / NaN → em-dash."""
    if pct is None or pd.isna(pct):
        return '<div class="cell-dash">—</div>'
    v = float(pct)
    _b = dl.ivt_band(v)
    if _b == "red":
        cell = f'<div class="pill pill-red">{v:.1f}%</div>'
    elif _b == "amber":
        cell = f'<div class="txt-amber">{v:.1f}%</div>'
    elif v == 0:
        cell = '<div class="txt-green">0%</div>'
    else:
        cell = f'<div class="txt-green">{v:.2f}%</div>'
    if prior is not None and not pd.isna(prior):
        cell += _delta_below_html(v - float(prior), lower_is_worse=False)
    return cell


PRESETS = ["Year to date", "Month to date", "Last quarter", "Last 7 days", "Yesterday", "Custom"]


def _preset_range(preset: str, dmin: date, dmax: date) -> tuple[date, date]:
    today = date.today()
    if preset == "Yesterday":
        d = today - timedelta(days=1)
        return d, d
    if preset == "Last 7 days":
        return today - timedelta(days=7), today - timedelta(days=1)
    if preset == "Month to date":
        return today.replace(day=1), today - timedelta(days=1)
    if preset == "Last quarter":
        m = today.month
        y = today.year
        if m <= 3:
            return date(y - 1, 10, 1), date(y - 1, 12, 31)
        elif m <= 6:
            return date(y, 1, 1), date(y, 3, 31)
        elif m <= 9:
            return date(y, 4, 1), date(y, 6, 30)
        else:
            return date(y, 7, 1), date(y, 9, 30)
    if preset == "Year to date":
        return date(today.year, 1, 1), today - timedelta(days=1)
    return dmin, dmax  # Custom


def date_filter(key: str, dmin: date, dmax: date) -> tuple[date, date]:
    preset = st.selectbox("Date range", PRESETS, index=PRESETS.index("Last 7 days"), key=f"{key}_preset")
    if preset == "Custom":
        dr = st.date_input("Custom range", value=(dmin, dmax), min_value=dmin, max_value=dmax, key=f"{key}_custom")
        start, end = dr if isinstance(dr, tuple) and len(dr) == 2 else (dmin, dmax)
    else:
        start, end = _preset_range(preset, dmin, dmax)
    return max(start, dmin), min(end, dmax)

DEAL_TYPE_NAMES = _cfg["deal_type_codes"]

KNOWN_FORMATS = {"Display", "Native", "Video", "CTV", "OLV", "Banner"}

# GAM orders to exclude from the Direct campaigns view entirely (table + KPIs).
# Internal test / QA orders per Roger (2026-06-15):
#   3648897741 — GMC "Terrain Diverse Owned TEST PAGE" / CITIQ3 test setups.
#   4082002976 — "Newsweek_Test-2" ([TEST] Newsletter / Apple-FITO / Sponsor-Logo).
# order_id is text in gam_campaigns; matched as strings.
_EXCLUDED_ORDER_IDS = {"3648897741", "4082002976"}


def _compact_pager(name, cur_page, total_pages, on_prev, on_next, main_txt, sub_txt=""):
    """One-row table pager: ``‹  centered page label  ›`` on a single inline
    row. Replaces the ``st.columns([1, 4, 1])`` pager that stacked into three
    full-width blocks on mobile (the thing Roger didn't like). Shared by the
    Direct and PMP tables; renders nothing for a single page.

    The keyed ``st.container(horizontal=True)`` keeps the row inline on mobile
    (same pattern as the filter bars); arrows are small fixed squares and the
    label fills the middle — styled via the ``.st-key-nwpgrwrap_*`` hook in the
    style block. ``name`` must be unique per pager instance (e.g. ``direct_top``)."""
    if total_pages <= 1:
        return
    with st.container(horizontal=True, key=f"nwpgrwrap_{name}"):
        st.button("‹", key=f"nwpgrbtn_{name}_prev", on_click=on_prev,
                  disabled=(cur_page == 0), help="Previous page")
        _sub = f'<span>{sub_txt}</span>' if sub_txt else ""
        st.markdown(f'<div class="nw-pager-cap"><b>{main_txt}</b>{_sub}</div>',
                    unsafe_allow_html=True)
        st.button("›", key=f"nwpgrbtn_{name}_next", on_click=on_next,
                  disabled=(cur_page == total_pages - 1), help="Next page")


@lru_cache(maxsize=8192)
def _parse_deal(deal: str) -> dict:
    """Extract fields from a Newsweek structured deal name.

    Format: Newsweek_TYPE_VERTICAL_PLATFORM_DSP_..._FORMAT_$PRICE_Team-X_AE

    Returns a plain **dict** (not a pd.Series — building a Series here cost
    ~280µs vs ~1µs for a dict, and this runs per row across the PMP frames) and
    is **memoized** on the deal string, so the same name repeated across its
    ~14 daily rows (and the three per-field Magnite applies) parses once. The
    result is cached and shared — callers must treat it **read-only**. Parsing
    logic is byte-identical to the prior Series version, so field values are
    unchanged; only the container + caching changed."""
    raw = str(deal).strip() if deal else ""
    if not raw or raw.upper().replace("-", "").replace("/", "") in ("NA", "0"):
        return {
            "revenue_source": "Open Market",
            "deal_type_label": None,
            "dsp": None,
            "ad_format": None,
            "floor_price": None,
        }

    parts = raw.split("_")

    # Position 1 → deal type
    deal_type_label = None
    if len(parts) > 1:
        dt = parts[1].strip()
        deal_type_label = DEAL_TYPE_NAMES.get(dt)  # None if not a recognized type code

    # Position 3 → platform / revenue source
    revenue_source = "Publisher"
    if len(parts) > 3:
        platform = parts[3].strip().lower()
        if platform == "magnite":
            revenue_source = "Magnite"

    # Position 4 → DSP
    dsp = parts[4].strip() if len(parts) > 4 else None

    # Scan for format and floor price
    ad_format = floor_price = None
    for part in parts:
        p = part.strip()
        if p in KNOWN_FORMATS and ad_format is None:
            ad_format = p
        if p.startswith("$") and floor_price is None:
            floor_price = p

    return {
        "revenue_source":  revenue_source,
        "deal_type_label": deal_type_label,
        "dsp":             dsp,
        "ad_format":       ad_format,
        "floor_price":     floor_price,
    }

AE_NAMES = _cfg["ae_names"]

st.set_page_config(page_title="Overall performance", layout="wide")

# ──────────────────────────────────────────────────────────────────────────
# Global polish: typography, sentence case, tabular nums, tab underline,
# eyebrow / timestamp affordances, border radius tokens. Streamlit honors
# inline CSS via st.markdown(unsafe_allow_html=True).
# ──────────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* ════════════════════════════════════════════════════════════════════
   NEWSWEEK DESIGN TOKENS — light "Paper" canvas (2026-06 rebrand).
   Source of truth: docs/design_handoff/newsweek-dashboard.css (the
   Claude Design handoff). Recolor/rescale by editing this token tier;
   component rules below must only read tokens.
   Brand red is CHROME ONLY (eyebrow tick, active-tab underline, mark).
   Severity owns its own red (--state-critical). Acceptance rule: if a
   red pixel is not the mark, a tab, or a breach, it's a bug.
   ════════════════════════════════════════════════════════════════════ */
/* Fonts are declared in .streamlit/config.toml ([theme] font/headingFont
   + [[theme.fontFaces]] -> static/fonts/*) so the brand faces also reach
   Streamlit natives, including canvas-rendered dataframes. The licensed
   binaries are NOT committed — see static/fonts/README.md; the fallback
   stacks in --font-sans / --font-display apply while the dir is empty. */
:root {
  /* Brand chrome — identity only, NEVER on data. */
  --brand-red:        #e91d0c;
  --brand-red-strong: #c41608;
  /* Surfaces — warm Paper. */
  --surface-0:     #fefcf6;   /* app background (Newsweek Paper) */
  --surface-1:     #ffffff;   /* cards, tiles — lifted off paper */
  --surface-2:     #f6f2e6;   /* sunk rows, hover fills, chips */
  --border:        #e7e0c9;   /* warm hairline */
  --border-strong: #1f1e19;   /* 2px editorial ink rule */
  /* Text — Ink on paper. */
  --text-primary:   #1f1e19;
  --text-secondary: #57564f;
  --text-muted:     #8c887b;
  /* State — severity scale (green/amber/red grammar preserved,
     re-toned for light: saturated text on pale tints).
     Green is ASYMMETRIC (green-overwhelm rule, #200): the muted tier
     carries high-frequency "fine/improving" signals (deltas, in-range
     text, progress fills, all-clear banners, on-track chart lines);
     saturated green is reserved for green-as-a-signal (status chips,
     enabled badges). Amber/red are always loud. */
  --state-positive: #3c6b14;  --state-positive-surface: rgba(76,122,25,.12);
  --state-positive-muted: #6f8f56;
  --state-positive-surface-quiet: rgba(76,122,25,.07);
  --state-warning:  #8a6d00;  --state-warning-surface:  rgba(214,170,0,.18);
  --state-critical: #c41608;  --state-critical-surface: rgba(233,29,12,.10);
  /* Data-viz categorical palette (chart series only). */
  --viz-1:#4b62e0; --viz-2:#2d8d92; --viz-3:#824dd7;
  --viz-4:#d84f86; --viz-5:#b08900; --viz-6:#5f9e2a;
  /* Type. */
  --font-sans:    "Franklin Gothic", "Helvetica Neue", Arial, sans-serif;
  --font-display: "Benton Modern Display", Georgia, "Times New Roman", serif;
  --track-eyebrow: 0.08em;
  --num-feature:  "tnum" 1;
  /* Spacing (4px base) + radius (square-ish, editorial). */
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px; --space-6:24px;
  --radius-sm:  4px;
  --radius-md:  8px;
  --radius-pill:999px;
  /* App extension tokens — NOT in the handoff, derived from it: the
     info accent + categorical deal-type tints derive from the viz
     palette; --spark-ref is the dashed target line in inline SVGs. */
  --accent-info:         #3a4cc0;              /* from --viz-1 */
  --accent-info-surface: rgba(75,98,224,.10);
  --accent-info-border:  rgba(75,98,224,.35);
  --cat-green:           #4a7a1c;              /* from --viz-6 (PG pill) */
  --cat-green-surface:   rgba(95,158,42,.14);
  --cat-purple:          #6a3cb8;              /* from --viz-3 (PMP pill) */
  --cat-purple-surface:  rgba(130,77,215,.12);
  --spark-ref:           rgba(31,30,25,.35);
  /* Legacy aliases (pre-rebrand rules read these). lg flattens to 8px —
     the editorial radius scale tops out at --radius-md. */
  --border-radius-md: var(--radius-md);
  --border-radius-lg: var(--radius-md);
}
/* ── App canvas. config.toml [theme] carries the same values for
   Streamlit-native widgets + the data grid; this covers custom markup
   and everything that inherits. ─────────────────────────────────── */
.stApp { background: var(--surface-0); color: var(--text-primary);
         font-family: var(--font-sans); }
/* ── Streamlit defaults override ──────────────────────────────────────
   Streamlit's global anchor styling (primary-color + underline) beats
   unprefixed class selectors on specificity. Override with .stApp-prefixed
   rules + !important + all link pseudo-classes so chrome links render as
   plain text, never as blue underlined hyperlinks. */
.stApp .nw-tab,
.stApp .nw-tab:link,
.stApp .nw-tab:visited,
.stApp .nw-tab:active {
  color: var(--text-secondary) !important;
  text-decoration: none !important;
}
.stApp .nw-tab:hover {
  color: var(--text-primary) !important;
  text-decoration: none !important;
}
.stApp .nw-tab.nw-tab-active,
.stApp .nw-tab.nw-tab-active:link,
.stApp .nw-tab.nw-tab-active:visited {
  color: var(--text-primary) !important;
  text-decoration: none !important;
}
/* Hide Streamlit's top toolbar / hamburger / running-status indicator
   AND the auto-generated heading anchor link icon (the chain glyph). */
#MainMenu, header[data-testid="stHeader"],
[data-testid="stToolbar"], [data-testid="stStatusWidget"],
[data-testid="stDecoration"], [data-testid="stAppDeployButton"],
[data-testid="stHeaderActionElements"],
[data-testid="stHeadingWithActionElements"] a,
.stApp h1 a, .stApp h2 a, .stApp h3 a {
  visibility: hidden !important;
  height: 0 !important;
  display: none !important;
}
/* Belt-and-suspenders for Streamlit's deploy/iframe top accent (the
   stray red line above the eyebrow). */
.stApp::before, .stApp::after { display: none !important; }
[data-testid="stAppViewContainer"] > .stApp { border-top: none !important; }
iframe[title="streamlit_app"] { border-top: none !important; }
/* ── Streamlit multiselect chip overrides ──────────────────────────
   The theme primary must never read as a data signal on a chip. Force
   a neutral paper chip for all filters, then color the Status chip per
   its value so "Delivering" reads as healthy, not as a warning.
   BaseWeb tag exposes aria-label like "Delivering, close by backspace"
   so we can target by prefix. */
.stMultiSelect [data-baseweb="tag"] {
  background-color: var(--surface-2) !important;
  border-color: var(--border) !important;
  border-radius: var(--radius-sm) !important;
}
.stMultiSelect [data-baseweb="tag"] span {
  color: var(--text-primary) !important;
}
.stMultiSelect [data-baseweb="tag"] svg {
  fill: var(--text-secondary) !important;
}
.stMultiSelect [data-baseweb="tag"]:hover {
  background-color: var(--border) !important;
}
/* Status-specific chip color (matches the table pill palette). */
.stMultiSelect [data-baseweb="tag"][aria-label^="Delivering"] {
  background-color: var(--state-positive-surface) !important;
  border-color: transparent !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Delivering"] span {
  color: var(--state-positive) !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Delivering"] svg {
  fill: var(--state-positive) !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Paused"] {
  background-color: var(--state-warning-surface) !important;
  border-color: transparent !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Paused"] span {
  color: var(--state-warning) !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Paused"] svg {
  fill: var(--state-warning) !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Upcoming"] {
  background-color: var(--accent-info-surface) !important;
  border-color: transparent !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Upcoming"] span {
  color: var(--accent-info) !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Upcoming"] svg {
  fill: var(--accent-info) !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Completed"] {
  background-color: var(--surface-2) !important;
  border-color: transparent !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Completed"] span {
  color: var(--text-muted) !important;
}
.stMultiSelect [data-baseweb="tag"][aria-label^="Completed"] svg {
  fill: var(--text-muted) !important;
}
/* Compact the top of the main container AND cap width on wide screens. */
.stApp .main .block-container,
.stApp [data-testid="stMain"] .block-container,
.stApp [data-testid="stAppViewContainer"] .block-container {
  padding-top: 1.5rem !important;
  padding-bottom: 2rem !important;
  padding-left: 1.5rem !important;
  padding-right: 1.5rem !important;
  max-width: 1600px !important;
  margin-left: auto !important;
  margin-right: auto !important;
}
/* H1 — editorial serif (Benton Modern Display), 22px per spec. */
h1, .stMarkdown h1 { font-family: var(--font-display); font-size: 22px !important;
                     font-weight: 700; margin: 0 0 4px 0; line-height: 1.15;
                     letter-spacing: -0.01em; }
/* Tabular numbers across every cell + KPI value. */
[data-testid="stMetricValue"], [data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th,
.kpi-value, .kpi-target, .nw-num { font-variant-numeric: tabular-nums;
                                   font-feature-settings: var(--num-feature); }
/* (Old st.tabs overrides removed — chrome is now custom HTML.) */
/* Eyebrow label (page-level kicker above the H1) — brand chrome: red
   text + 8px red tick. One of the three sanctioned red chrome elements
   (mark, active tab, eyebrow tick); section-level eyebrows below stay
   neutral so red remains scarce. */
.nw-eyebrow { display: inline-flex; align-items: center; gap: var(--space-2);
              font-size: 12px; line-height: 1; letter-spacing: var(--track-eyebrow);
              text-transform: uppercase; color: var(--brand-red); font-weight: 600; }
.nw-eyebrow::before { content: ""; width: 8px; height: 8px; background: var(--brand-red); }
.nw-timestamp { font-size: 12px; color: var(--text-secondary);
                font-variant-numeric: tabular-nums; }
/* Filter labels — field labels above selects. Visibly less prominent than
   the page eyebrow: smaller font, lighter weight, less tracked, dimmer. */
.nw-filter-label { font-size: 9px; letter-spacing: 0.02em; text-transform: uppercase;
                   color: var(--text-muted); font-weight: 400; margin-bottom: 3px; }
/* Campaigns + PMP filter bars: a single "Filters" popover trigger + removable
   active-filter chips (replaces the 6-up dropdown rows that pushed the data
   below the fold on mobile). .st-key-* hooks Streamlit's keyed containers. */
.st-key-nw_filter_bar, .st-key-nw_pmp_filter_bar { gap: 8px !important; align-items: center;
                        flex-wrap: wrap !important; margin: 2px 0 14px; }
.st-key-nw_filter_bar [data-testid="stPopover"] button,
.st-key-nw_pmp_filter_bar [data-testid="stPopover"] button {
  border-radius: var(--radius-pill) !important;
  border: 1px solid var(--border-strong) !important;
  background: var(--surface-1) !important; color: var(--text-primary) !important;
  font-weight: 700 !important; padding: 6px 14px !important; min-height: 0 !important;
}
/* Active-filter chips: quiet paper pills that flush red on hover to signal a
   click removes them. */
.st-key-nw_filter_bar .stButton button,
.st-key-nw_pmp_filter_bar .stButton button {
  border-radius: var(--radius-pill) !important;
  border: 1px solid var(--border) !important;
  background: var(--surface-1) !important; color: var(--text-secondary) !important;
  font-weight: 600 !important; padding: 4px 12px !important; min-height: 0 !important;
}
.st-key-nw_filter_bar .stButton button:hover,
.st-key-nw_pmp_filter_bar .stButton button:hover {
  border-color: var(--state-critical) !important; color: var(--state-critical) !important;
}
/* Compact one-row table pager (Direct + PMP): ‹ small arrow · centered page
   label · arrow › on a single inline row, replacing the st.columns([1,4,1])
   that stacked into three full-width blocks on mobile. .st-key-nwpgrwrap_*
   hooks the keyed horizontal containers; space-between pins the arrows to the
   edges of a capped-width bar with the label centered between. */
[class*="st-key-nwpgrwrap_"] { gap: 8px !important; align-items: center !important;
  justify-content: space-between !important; flex-wrap: nowrap !important;
  max-width: 430px; margin: 6px auto; }
[class*="st-key-nwpgrwrap_"] [data-testid="stMarkdown"] { flex: 1 1 auto; }
[class*="st-key-nwpgrwrap_"] .stButton { flex: 0 0 auto; width: auto !important; }
[class*="st-key-nwpgrwrap_"] .stButton button {
  min-width: 46px !important; width: 46px !important; height: 44px !important;
  padding: 0 !important; font-size: 20px !important; font-weight: 600 !important;
  line-height: 1 !important; border-radius: var(--radius-md) !important;
  border: 1px solid var(--border-strong) !important; min-height: 0 !important;
  background: var(--surface-1) !important; color: var(--text-primary) !important;
}
[class*="st-key-nwpgrwrap_"] .stButton button:disabled { opacity: .4 !important; }
.nw-pager-cap { text-align: center; line-height: 1.15; }
.nw-pager-cap b { font-size: 13.5px; font-weight: 700; color: var(--text-primary); }
.nw-pager-cap span { display: block; font-size: 10.5px; color: var(--text-secondary);
  margin-top: 1px; font-variant-numeric: tabular-nums; }
/* Exception banners — left severity bar, equal-height grid row. Tinted
   state surface + state-colored head; body text stays ink-secondary.
   margin-bottom gives breathing room before the KPI strip. */
.nw-banner-row { display: grid; grid-template-columns: 1fr 1fr 1fr;
                 gap: 8px; align-items: stretch; margin: 6px 0 1rem; }
.nw-banner { border-radius: 0 var(--radius-md) var(--radius-md) 0;
             padding: 10px 12px; font-size: 12px; line-height: 1.35;
             border: none; border-left: 3px solid transparent;
             height: 100%; box-sizing: border-box; color: var(--text-secondary); }
.nw-banner .nw-banner-head { font-size: 11px; letter-spacing: 0.04em;
                             text-transform: uppercase; font-weight: 600; margin-bottom: 4px; }
.nw-banner.sev-red    { background: var(--state-critical-surface); border-left-color: var(--state-critical); }
.nw-banner.sev-red .nw-banner-head    { color: var(--state-critical); }
.nw-banner.sev-amber  { background: var(--state-warning-surface);  border-left-color: var(--state-warning); }
.nw-banner.sev-amber .nw-banner-head  { color: var(--state-warning); }
.nw-banner.sev-ok     { background: var(--state-positive-surface-quiet); border-left-color: var(--state-positive-muted); }
.nw-banner.sev-ok .nw-banner-head     { color: var(--state-positive-muted); }
/* ── "Needs attention" panel (Campaigns tab): one card, a row per alert
   category. Rows with offenders are native <details> accordions that reveal
   the specific line items inline (browser-native toggle, no Streamlit rerun;
   the HTML sanitizer passes <details>/<summary>). Clear categories render as a
   static sev-ok row. Replaces the three stacked banners here; the PMP tab
   keeps the simpler .nw-banner style above. */
.nw-na { background: var(--surface-1); border: 1px solid var(--border);
         border-radius: var(--radius-md); overflow: hidden; margin: 6px 0 1rem;
         max-width: 760px; }
.nw-na-head { padding: 9px 13px; font-size: 11px; letter-spacing: 0.06em;
              text-transform: uppercase; font-weight: 600; color: var(--text-secondary);
              border-bottom: 1px solid var(--border); display: flex;
              align-items: center; }
.nw-na-head .cnt { color: var(--text-muted); font-weight: 600; margin-left: auto; }
/* The card is a <details> when there are flags: collapsed to one header line
   on mobile (it was dominating the first screen), tap to reveal the category
   accordion. Desktop/tablet force the body open below so layout is unchanged. */
.nw-na > summary.nw-na-head { cursor: pointer; list-style: none; }
.nw-na > summary.nw-na-head::-webkit-details-marker { display: none; }
.nw-na > summary.nw-na-head::marker { content: ""; }
.nw-na:not([open]) > .nw-na-head { border-bottom: none; }
.nw-na-h-chev { color: var(--text-muted); font-size: 14px; margin-left: 8px;
                display: inline-block; transition: transform .15s ease; }
.nw-na[open] .nw-na-h-chev { transform: rotate(90deg); }
@media (min-width: 641px) {
  .nw-na .nw-na-body { display: block !important; }  /* always-expanded on desktop/tablet */
  .nw-na-h-chev { display: none; }
  .nw-na > summary.nw-na-head { cursor: default; }
  /* Offenders shown inline on desktop — force each category's sub-list open
     regardless of the <details> toggle, hide the per-row chevron, and tint
     the category header by severity (the [open] tint can't fire when we're
     not actually toggling it open). Click-to-expand stays on mobile. */
  .nw-na-row > .nw-na-sub { display: block !important; }
  .nw-na-row > summary .nw-na-chev { display: none; }
  .nw-na-row > summary { cursor: default; }
  .nw-na-row.sev-red   > summary { background: var(--state-critical-surface); }
  .nw-na-row.sev-amber > summary { background: var(--state-warning-surface); }
}
/* `--always` opt-out of the mobile collapse: this card stays expanded at
   ALL widths (Roger wants Needs-attention's triage categories never a tap
   away, even on mobile). Only the per-category line-item lists underneath
   stay independently collapsible, so the open card is the ~4 category rows,
   not a screenful. Applied to the Needs-attention card only; the
   ending-soon and PMP-signals cards keep the default mobile collapse. */
.nw-na--always .nw-na-body { display: block !important; }
.nw-na--always > summary.nw-na-head .nw-na-h-chev { display: none; }
.nw-na--always > summary.nw-na-head { cursor: default; }
.nw-na-row { border-bottom: 1px solid var(--border); }
.nw-na-row:last-child { border-bottom: none; }
.nw-na-row > summary, .nw-na-static { list-style: none; display: flex;
              align-items: center; gap: 11px; padding: 11px 13px; }
.nw-na-row > summary { cursor: pointer; }
.nw-na-row > summary::-webkit-details-marker { display: none; }
.nw-na-row > summary::marker { content: ""; }
.nw-na-row.sev-red[open]   > summary { background: var(--state-critical-surface); }
.nw-na-row.sev-amber[open] > summary { background: var(--state-warning-surface); }
.nw-na-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; }
.nw-na-n { font-family: var(--font-display); font-weight: 700; font-size: 19px;
           min-width: 16px; text-align: right; font-variant-numeric: tabular-nums; }
.nw-na-l { font-weight: 700; font-size: 13px; color: var(--text-primary); }
.nw-na-d { color: var(--text-muted); font-size: 11.5px; margin-left: auto;
           text-align: right; max-width: 48%; overflow: hidden;
           text-overflow: ellipsis; white-space: nowrap; }
.nw-na-chev { color: var(--text-muted); font-size: 13px; margin-left: 8px;
              transition: transform .15s ease; }
.nw-na-row[open] .nw-na-chev { transform: rotate(90deg); }
.nw-na-row.sev-red   .nw-na-dot { background: var(--state-critical); }
.nw-na-row.sev-red   .nw-na-n   { color: var(--state-critical); }
.nw-na-row.sev-amber .nw-na-dot { background: var(--state-warning); }
.nw-na-row.sev-amber .nw-na-n   { color: var(--state-warning); }
.nw-na-row.sev-ok    .nw-na-dot { background: var(--state-positive-muted); }
.nw-na-row.sev-ok    .nw-na-n   { color: var(--state-positive-muted); font-size: 14px; }
.nw-na-sub { padding: 2px 13px 9px 37px; background: var(--surface-2); }
/* Flagged line-item row: two-tier identifiable label (advertiser bold over
   muted campaign) + the metric as colored text on the right. No proportional
   bar — the metric value + category tint carry severity. */
.nw-na-srow { display: flex; align-items: flex-start; gap: 10px; padding: 7px 0;
              font-size: 11.5px; border-top: 1px solid var(--border); }
.nw-na-srow:first-child { border-top: none; }
.nw-na-srow .nm { flex: 1 1 auto; min-width: 0; }
.nw-na-srow .nm .adv { display: block; font-weight: 700; color: var(--text-primary);
                       line-height: 1.3; }
.nw-na-srow .nm .camp { display: block; font-size: 10px; color: var(--text-muted);
                        line-height: 1.25; margin-top: 1px; }
.nw-na-srow .pct { flex: 0 0 auto; text-align: right; font-weight: 700;
                   font-variant-numeric: tabular-nums; }
.nw-na-srow.sev-red   .pct { color: var(--state-critical); }
.nw-na-srow.sev-amber .pct { color: var(--state-warning); }
/* Landing-risk row: meta line (days left · ends · delivered) + a compact
   projected-vs-goal bar. Faint fill = projected at current pace, solid =
   delivered, tick at right = goal. The gap to the tick is the shortfall. */
.nw-lr-meta { display: block; font-size: 10px; color: var(--text-muted);
              line-height: 1.25; margin-top: 1px; }
.nw-lr-bar { display: block; position: relative; height: 6px; border-radius: 999px;
             background: var(--surface-2); margin-top: 5px; max-width: 420px; }
.nw-lr-proj { position: absolute; left: 0; top: 0; height: 100%;
              border-radius: 999px; opacity: .38; }
.nw-lr-done { position: absolute; left: 0; top: 0; height: 100%; border-radius: 999px; }
.nw-na-srow.sev-red   .nw-lr-proj, .nw-na-srow.sev-red   .nw-lr-done { background: var(--state-critical); }
.nw-na-srow.sev-amber .nw-lr-proj, .nw-na-srow.sev-amber .nw-lr-done { background: var(--state-warning); }
.nw-lr-short { display: block; font-size: 10px; margin-top: 1px; }
.nw-na-srow.sev-red   .nw-lr-short { color: var(--state-critical); }
.nw-na-srow.sev-amber .nw-lr-short { color: var(--text-muted); }
/* KPI strip — single grid so all nine tiles render at exactly the same
   height. Tile = white card with a 2px ink top rule and a serif number;
   the sparkline runs full-width under the figures (neutral stroke —
   state lives in the delta text, never the trend line). */
.nw-kpi-row { display: grid; grid-template-columns: repeat(9, 1fr);
              gap: var(--space-2); margin: 4px 0 10px;
              background: transparent; border: none; }
/* PMP overview strip carries 4 tiles, not 9. The two-class selector outranks
   the ≤1024 auto-fit rule at every width, so this holds 4-up on desktop and
   tablet without the inline style it used to need. */
.nw-kpi-row.nw-kpi-row--pmp { grid-template-columns: repeat(4, 1fr); }
.kpi-tile  { display: flex; flex-direction: column; justify-content: flex-start;
             padding: var(--space-3); position: relative; overflow: hidden;
             border-radius: var(--radius-sm);
             background: var(--surface-1);
             border: 1px solid var(--border);
             border-top: 2px solid var(--text-primary);
             box-sizing: border-box; }
.kpi-label { font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
             color: var(--text-secondary); font-weight: 600; }
.kpi-value { font-family: var(--font-display); font-size: 23px; font-weight: 700;
             line-height: 1.05; margin: 7px 0 2px;
             color: var(--text-primary); font-variant-numeric: tabular-nums; }
.kpi-spark { display: block; width: 100%; height: 22px; margin-top: var(--space-2); }
.kpi-target{ font-size: 10.5px; color: var(--text-muted); }
.kpi-delta-up    { color: var(--state-positive-muted); }
.kpi-delta-down  { color: var(--state-critical); }
.kpi-delta-amber { color: var(--state-warning); }
.kpi-delta-flat  { color: var(--text-muted); }
.kpi-delta-neutral { color: var(--text-secondary); }
/* ── TTD Luckyland CPA accordion ──────────────────────────────────── */
/* Reuses the .nw-na accordion shell + .kpi-tile/.kpi-row atoms;
   adds a bar-chart row, a breakdown table, and a date-range eyebrow. */
.nw-ttd-wrap { margin: 6px 0 1rem; }
.nw-ttd-date { font-size: 10px; letter-spacing: var(--track-eyebrow);
               text-transform: uppercase; color: var(--text-muted);
               margin-bottom: 4px; }
.nw-ttd-kpi-row { display: grid; gap: 8px; margin-bottom: 12px;
                  grid-template-columns: repeat(5, 1fr); }
.nw-ttd-kpi-row .kpi-value { font-size: 17px; white-space: nowrap; overflow: hidden;
                              text-overflow: ellipsis; }
/* bar chart container */
.nw-ttd-charts { display: grid; grid-template-columns: 1fr 1fr;
                 gap: 10px; margin-bottom: 12px; }
.nw-ttd-chart { background: var(--surface-1); border: 1px solid var(--border);
                border-radius: var(--radius-sm); padding: 10px 12px; }
.nw-ttd-chart-title { font-size: 10px; letter-spacing: var(--track-eyebrow);
                      text-transform: uppercase; color: var(--text-secondary);
                      font-weight: 600; margin-bottom: 6px; }
.nw-ttd-bars { display: flex; flex-direction: column; gap: 3px; }
.nw-ttd-bar-row { display: flex; align-items: center; gap: 6px;
                  font-size: 10.5px; color: var(--text-secondary); }
.nw-ttd-bar-label { flex: 0 0 32px; text-align: right;
                    font-variant-numeric: tabular-nums; }
.nw-ttd-bar-track { flex: 1; height: 7px; background: var(--surface-2);
                    border-radius: 3px; overflow: hidden; }
.nw-ttd-bar-fill  { display: block; height: 100%; border-radius: 3px;
                    background: var(--text-secondary); }
.nw-ttd-bar-val   { flex: 0 0 50px; font-variant-numeric: tabular-nums; }
/* breakdown table */
.nw-ttd-table { width: 100%; border-collapse: collapse; font-size: 11.5px;
                margin-top: 4px; }
.nw-ttd-table th { font-size: 10px; letter-spacing: var(--track-eyebrow);
                   text-transform: uppercase; color: var(--text-secondary);
                   font-weight: 600; padding: 4px 8px; border-bottom: 1px solid var(--border);
                   text-align: right; }
.nw-ttd-table th:first-child { text-align: left; }
.nw-ttd-table td { padding: 5px 8px; border-bottom: 1px solid var(--border);
                   font-variant-numeric: tabular-nums; text-align: right;
                   color: var(--text-primary); }
.nw-ttd-table td:first-child { text-align: left; color: var(--text-secondary); }
.nw-ttd-table tr:last-child td { border-bottom: none; }
@media (max-width: 640px) {
  .nw-ttd-kpi-row { grid-template-columns: repeat(2, 1fr); }
  .nw-ttd-charts  { grid-template-columns: 1fr; }
}
/* Sentence-case helper class (utility — applied selectively). */
.nw-sentence::first-letter { text-transform: uppercase; }
/* Compact dataframe borders */
[data-testid="stDataFrame"] table { border-collapse: collapse; }
[data-testid="stDataFrame"] th, [data-testid="stDataFrame"] td { border-bottom-width: 1px !important; }
/* "Prog." filler for sellerless rows */
.nw-prog { font-style: italic; color: var(--text-muted); }
/* Ordinal badge */
.nw-ord { font-size: 10px; padding: 1px 6px; border-radius: var(--radius-pill);
          background: var(--surface-2); color: var(--text-secondary);
          margin-right: 6px; font-variant-numeric: tabular-nums; }
/* Differentiator subtitle */
.nw-sub { font-size: 11px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
/* Title ink */
h1, .stMarkdown h1 { color: var(--text-primary); }
/* ── HTML tab row (replaces st.button-based nav so we get flat text tabs,
   not Streamlit's filled primary buttons). Active tab gets the 3px
   brand-red underline — sanctioned chrome. Clicks update ?view= via
   real navigation. */
.nw-tabrow { display: flex; align-items: stretch; gap: var(--space-2);
             border-bottom: 1px solid var(--border);
             margin: 8px 0 14px; font-size: 13px; }
.nw-tabrow-spacer { flex: 1; }
.nw-tab { padding: 10px 12px; color: var(--text-secondary); font-weight: 600;
          letter-spacing: 0.02em; text-decoration: none;
          border-bottom: 3px solid transparent;
          margin-bottom: -1px; transition: color 0.12s; }
.nw-tab:hover { color: var(--text-primary); }
.nw-tab-active { color: var(--text-primary);
                 border-bottom-color: var(--brand-red); }
.nw-tab-configure { border-left: 1px solid var(--border);
                    padding-left: 16px; margin-left: 6px; }
.nw-tab-configure.nw-tab-active { border-bottom-color: var(--brand-red); }
/* Header right-side cluster — timestamp only (Configure tab is the sole
   entry point into the settings view, no separate gear button). */
.nw-header-right { display: flex; align-items: center; justify-content: flex-end; }
/* ── Custom HTML table for Direct Campaigns ─────────────────────────── */
.nw-tbl-wrap { background: var(--surface-1); border-radius: var(--radius-md);
               border: 1px solid var(--border); padding: 16px 18px; margin: 8px 0; }
.nw-tbl-head { display: flex; justify-content: space-between; align-items: center;
               margin-bottom: 10px; font-size: 12px; }
.nw-tbl-title { color: var(--text-primary); font-weight: 600; }
.nw-tbl-title .nw-tbl-sub { color: var(--text-muted); font-weight: 400; margin-left: 6px; }
.nw-legend { display: flex; gap: 14px; font-size: 11px; color: var(--text-secondary);
             font-variant-numeric: tabular-nums; }
.nw-legend-dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px;
                 vertical-align: middle; }
.nw-tbl { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.nw-tbl th { text-align: left; font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
             color: var(--text-secondary); font-weight: 600; padding: 6px 10px 10px;
             border-bottom: 1px solid var(--border); }
.nw-tbl th.num { text-align: right; }
.nw-tbl td { padding: 10px; vertical-align: top; font-size: 13px; color: var(--text-primary);
             border-bottom: 1px solid var(--border); }
.nw-tbl td.num { text-align: right; }
.nw-tbl tr:last-child td { border-bottom: none; }
.li-name { font-weight: 600; color: var(--text-primary); }
.li-sub  { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.li-ord  { font-size: 10px; padding: 1px 6px; border-radius: var(--radius-pill);
           background: var(--surface-2); color: var(--text-secondary); margin-right: 6px; }
.pill { display: inline-block; padding: 3px 8px; border-radius: var(--radius-sm); font-weight: 600;
        font-size: 12px; line-height: 1.4; font-feature-settings: var(--num-feature); }
.pill-red    { background: var(--state-critical-surface); color: var(--state-critical); }
.pill-amber  { background: var(--state-warning-surface);  color: var(--state-warning); }
/* On-pace pill — boxed for cell consistency but kept in the QUIET green tier
   (muted ink-green on a faint surface) so healthy rows still recede and the
   louder amber/red exceptions own the page (green-overwhelm rule). */
.pill-green  { background: var(--state-positive-surface-quiet); color: var(--state-positive-muted); }
/* inline-block forces the colored-text spans to shrink to their content
   width, so they right-align cleanly under a grid cell with `text-align:
   right` — same behavior as .pill.

   The light-canvas state scale keeps the original asymmetric philosophy
   (tuned 2026-05-25 after the green-overwhelm audit): --state-positive
   is a quiet ink-green that recedes — "healthy" should never shout —
   while warning/critical band onto tinted surfaces so problems pop.
   Re-toned for paper 2026-06 (Newsweek rebrand). */
.txt-green   { display: inline-block; color: var(--state-positive-muted); font-weight: 600; font-size: 13px; }
.txt-amber   { display: inline-block; color: var(--state-warning);  font-weight: 600; font-size: 13px; }
.txt-red     { display: inline-block; color: var(--state-critical); font-weight: 600; font-size: 13px; }
/* Delta-row palette: worsening = critical, drifting = warning; the
   improving "up" delta stays the quiet green. Same recede-vs-pop logic
   as .txt-green above. */
.pace-delta  { font-size: 11px; margin-top: 4px; color: var(--state-critical); }
.pace-delta.up { color: var(--state-positive-muted); }
.pace-delta.amber { color: var(--state-warning); }
/* Progress cell: bar + inline % label. Wrapper puts them side-by-side
   with a small gap so the number sits flush right of the bar without
   wrapping. Track is the sunk paper tone; fills carry the state scale. */
.nw-prog-wrap { display: flex; align-items: center; gap: 8px; }
.nw-prog-bar  { flex: 1; height: 8px; background: var(--surface-2);
                border-radius: 4px; overflow: hidden; min-width: 40px; }
.nw-prog-fill { height: 100%; border-radius: 4px; }
.nw-prog-label{ font-size: 11px; color: var(--text-secondary);
                font-variant-numeric: tabular-nums; min-width: 28px;
                text-align: right; }
.prog-red   { background: var(--state-critical); }
.prog-amber { background: var(--state-warning); }
.prog-green { background: var(--state-positive-muted); }
.seller-prog { font-style: italic; color: var(--text-muted); }
.cell-dash { display: inline-block; color: var(--text-muted); }
/* Per-column alignment override — used for VCR right now (centered
   reads better when the cell has a value+delta stack and the column
   is wide enough that right-alignment leaves a big gap of empty
   space on the left).
   Selectors deliberately match the specificity of the table's existing
   `.nw-row-header .num` / `.nw-row > summary .num` `text-align: right`
   rules, which are defined further down the stylesheet and would
   otherwise win on source order if we just wrote `.num.center {}`. */
.nw-row-header .num.center,
.nw-rows .nw-row > summary .num.center,
.nw-pmp-rows .nw-row-header .num.center,
.nw-pmp-rows .nw-pmp-row .num.center { text-align: center; }
.bold-rev  { font-weight: 700; }
/* ── Grid-based row layout + native <details> drawer ─────────────── */
.nw-rows .nw-row-header,
.nw-rows .nw-row > summary {
  display: grid;
  /* Columns: Line item | Revenue | Delivered | Pace | Viewable | Attention | SIVT | GIVT | CTR | VCR | Seller | Progress
     VCR widened from 7→10fr because its cells render a 2-line
     value+delta ("68.4%" + "▲ 0.30pp") that was getting cramped
     against the Seller column. SIVT/GIVT trimmed 7→6fr because they
     just show "0.19%" / "—" — they had headroom to give. Pace
     trimmed 11→10fr (already had margin). Net width unchanged. */
  grid-template-columns:
    22fr 10fr 9fr 10fr 9fr 9fr 6fr 6fr 8fr 10fr 10fr 14fr;
  gap: 10px;
  /* align-items: start so every cell's first line (the value) sits at
     the same top edge. Was align-items: center, which works only when
     every cell has the same line count. After adding per-row deltas
     to most cells, rows mix 1-line (no delta when there's no signal)
     and 2-line cells, so centering pulled the single-line values down
     to the row midpoint while the value+delta stack stayed at the top
     — values across a row no longer aligned horizontally. */
  align-items: start;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
}
.nw-row-header {
  font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
  color: var(--text-secondary); font-weight: 600;
  border-bottom-color: var(--border);
}
.nw-row-header .num { text-align: right; }
.nw-row {
  font-variant-numeric: tabular-nums;
  border-bottom: 1px solid var(--border);
}
.nw-row > summary {
  cursor: pointer; font-size: 13px;
  color: var(--text-primary); list-style: none;
}
.nw-row > summary::-webkit-details-marker { display: none; }
.nw-row > summary::marker { content: ""; }
.nw-row > summary .num { text-align: right; }
.nw-row > summary:hover { background: var(--surface-2); }
.nw-row[open] > summary { background: var(--surface-2); }
.nw-row[open] > summary .nw-chev { transform: rotate(90deg); }
/* Solution-3 mobile row card: hidden on desktop. The ≤640 block below swaps
   each Direct-table summary from its 12-column grid to this compact card
   (name + pace bar + delivery sparkline + revenue/pace). Tapping it still
   toggles the row's <details> drawer. */
.nw-row-m { display: none; grid-template-columns: 1fr 92px auto; gap: 10px;
            align-items: center; padding: 11px 12px; }
.nw-row-m .m-name { font-weight: 700; font-size: 13px; color: var(--text-primary); line-height: 1.2; }
.nw-row-m .m-name .li-ord { color: var(--text-muted); font-weight: 700; font-size: 10px; margin-right: 3px; }
.nw-row-m .m-sub { color: var(--text-muted); font-size: 10px; margin-top: 1px; }
.nw-row-m .m-pbar-l { font-size: 8px; color: var(--text-muted); letter-spacing: .03em;
                      text-transform: uppercase; font-weight: 600; margin-top: 8px; }
.nw-row-m .m-pbar { height: 7px; background: var(--border); border-radius: 4px;
                    overflow: hidden; margin-top: 3px; max-width: 220px; }
.nw-row-m .m-pbar > i { display: block; height: 100%; border-radius: 4px; }
.nw-row-m .m-pbar > i.red     { background: var(--state-critical); }
.nw-row-m .m-pbar > i.amber   { background: var(--state-warning); }
.nw-row-m .m-pbar > i.green   { background: var(--state-positive-muted); }
.nw-row-m .m-pbar > i.neutral { background: var(--text-muted); }
.nw-row-m .m-spark { min-width: 0; }
.nw-row-m .m-spark svg { width: 100%; height: 30px; display: block; }
.nw-row-m .m-spark-l { font-size: 8px; color: var(--text-muted); text-align: center;
                       margin-top: 1px; letter-spacing: .03em; text-transform: uppercase; }
.nw-row-m .m-right { text-align: right; }
.nw-row-m .m-right .m-rev { font-family: var(--font-display); font-weight: 700; font-size: 14px; }
/* PMP mobile card — same idea as .nw-row-m but PMP deals have no daily
   series, so the visual is an eCPM-vs-floor bar (floor at the 50% tick)
   instead of a delivery sparkline. Hidden on desktop; the ≤640 block swaps
   it in for the 12-column summary. */
.nw-pmp-m { display: none; grid-template-columns: 1fr auto; gap: 12px;
            align-items: start; padding: 11px 12px; }
.nw-pmp-m .m-main { min-width: 0; }
.nw-pmp-m .m-name { font-weight: 700; font-size: 13px; color: var(--text-primary); line-height: 1.25; }
.nw-pmp-m .m-sub { color: var(--text-muted); font-size: 10px; margin-top: 2px; }
.nw-pmp-m .m-ecpm-wrap { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.nw-pmp-m .m-ecpm-bar { position: relative; flex: 1; max-width: 200px; height: 7px;
                        background: var(--border); border-radius: 4px; overflow: hidden; }
.nw-pmp-m .m-ecpm-bar > i { display: block; height: 100%; border-radius: 4px; }
.nw-pmp-m .m-ecpm-bar > i.amber   { background: var(--state-warning); }
.nw-pmp-m .m-ecpm-bar > i.green   { background: var(--state-positive-muted); }
.nw-pmp-m .m-ecpm-bar > i.neutral { background: var(--text-secondary); }
.nw-pmp-m .m-floor-tick { position: absolute; top: 0; bottom: 0; left: 50%;
                          width: 2px; background: var(--text-primary); opacity: .55; }
.nw-pmp-m .m-ecpm-lbl { font-size: 9px; color: var(--text-muted); white-space: nowrap; }
/* 7-day revenue sparkline on the PMP card (sits under the eCPM-vs-floor bar). */
.nw-pmp-m .m-spark2 { margin-top: 8px; }
/* Uniform scaling (height follows the viewBox aspect) — no preserveAspectRatio
   warp, so the end dot stays a round dot (the 2026-06-14 smeared-dot fix). */
.nw-pmp-m .m-spark2 svg { width: 100%; max-width: 220px; height: auto; display: block; }
.nw-pmp-m .m-spark-l { font-size: 8px; color: var(--text-muted); text-transform: uppercase;
                       letter-spacing: .03em; margin-top: 1px; }
.nw-pmp-m .m-right { text-align: right; min-width: 0; }
/* Deal-type pill pinned top-right (fixed spot) — not inline after the
   variable-length name, which scattered it across the column. */
.nw-pmp-m .m-right .m-dt { margin-bottom: 5px; }
.nw-pmp-m .m-right .m-rev { font-family: var(--font-display); font-weight: 700; font-size: 14px; }
.nw-pmp-m .m-right .m-ecpm { font-size: 11px; color: var(--text-secondary); margin-top: 2px; }
.nw-pmp-m .m-right .m-impr { font-size: 10px; color: var(--text-muted); margin-top: 1px; }
.nw-chev {
  display: inline-block; width: 10px;
  margin-right: 6px; color: var(--text-muted);
  transition: transform 0.15s;
}
.nw-drawer {
  padding: 16px 22px 18px;
  background: var(--surface-2);
  border-top: 1px solid var(--border);
  font-size: 12px;
}
.nw-drawer-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; }
.nw-drawer-li {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px; color: var(--text-primary);
  background: var(--surface-1); border: 1px solid var(--border);
  padding: 4px 8px;
  border-radius: var(--radius-sm); overflow-wrap: anywhere;
}
.nw-drawer-id {
  font-size: 11px; color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  user-select: all;
}
.nw-meta-grid {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 24px; margin-top: 14px;
}
.nw-meta-grid > div { line-height: 1.4; min-width: 0; }
.nw-meta-grid .lbl {
  font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
  color: var(--text-muted); display: block; margin-bottom: 2px;
}
.nw-meta-grid .val {
  color: var(--text-primary); font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
/* Consolidated LI identity + spec card (Direct drawer, 2026-06-15). Replaces
   the top raw-name box AND the old flat 9-cell meta grid — both of which
   duplicated the name (the grid's `Order` field WAS the raw name). It leads
   with the friendly "<Advertiser> — <Campaign>" title (serif), a GAM-ID chip,
   and the raw convention string as a mono caption, then hero pacing tiles
   (Goal / Delivered+progress / Remaining) over a tinted detail grid. */
.nw-li-card { margin-top: 18px; }
.nw-li-head { display: flex; justify-content: space-between; align-items: flex-start;
  gap: 16px; flex-wrap: wrap; }
.nw-li-eyebrow { font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
  color: var(--text-muted); font-weight: 600; margin-bottom: 3px; }
.nw-li-name { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px;
  font-weight: 600; color: var(--text-primary); line-height: 1.45; margin: 0;
  overflow-wrap: anywhere; }
.nw-li-gam { font-size: 11px; color: var(--text-secondary); font-variant-numeric: tabular-nums;
  white-space: nowrap; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: var(--radius-pill); padding: 3px 11px; }
.nw-li-gam a { color: var(--text-primary); font-weight: 600; text-decoration: none; }
.nw-li-gam a:hover { text-decoration: underline; }
.nw-li-hero { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }
.nw-li-hero .tile { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 10px 13px; min-width: 0; }
.nw-li-hero .k { font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
  color: var(--text-muted); display: block; margin-bottom: 4px; }
.nw-li-hero .big { font-family: var(--font-display); font-size: 20px; color: var(--text-primary);
  font-variant-numeric: tabular-nums; line-height: 1; }
.nw-li-bar { height: 6px; border-radius: 3px; background: var(--surface-2);
  overflow: hidden; margin-top: 9px; border: 1px solid var(--border); }
.nw-li-bar > i { display: block; height: 100%; background: var(--state-positive-muted); }
.nw-li-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
  gap: 10px; margin-top: 10px; }
.nw-li-grid .cell { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 8px 12px; min-width: 0; }
.nw-li-grid .k { font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
  color: var(--text-muted); display: block; margin-bottom: 2px; }
.nw-li-grid .v { color: var(--text-primary); font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere; }
.nw-warn {
  margin-top: 14px; padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--state-warning-surface);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}
.nw-warn strong {
  display: block; font-size: 11px;
  letter-spacing: 0.06em; text-transform: uppercase;
  margin-bottom: 4px; color: var(--state-warning);
}
.nw-warn.severity-red {
  background: var(--state-critical-surface);
}
.nw-warn.severity-red strong { color: var(--state-critical); }
.nw-warn.severity-info {
  background: var(--accent-info-surface);
}
.nw-warn.severity-info strong { color: var(--accent-info); }
/* Drawer status banner — thesis statement at the top. */
.nw-status-banner {
  display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap;
  padding: 10px 12px; margin-top: 12px;
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  border-left: 3px solid transparent;
  font-size: 12px; line-height: 1.4;
  color: var(--text-secondary);
}
.nw-status-banner strong {
  font-size: 11px; letter-spacing: 0.04em;
  text-transform: uppercase; font-weight: 600;
  white-space: nowrap;
}
.nw-status-banner.sev-red {
  background: var(--state-critical-surface);
  border-left-color: var(--state-critical);
}
.nw-status-banner.sev-red strong { color: var(--state-critical); }
.nw-status-banner.sev-amber {
  background: var(--state-warning-surface);
  border-left-color: var(--state-warning);
}
.nw-status-banner.sev-amber strong { color: var(--state-warning); }
.nw-status-banner.sev-ok {
  background: var(--state-positive-surface-quiet);
  border-left-color: var(--state-positive-muted);
}
.nw-status-banner.sev-ok strong { color: var(--state-positive-muted); }
/* Drawer 7-day delivery chart panel. */
.nw-drawer-chart {
  margin-top: 12px; padding: 8px 12px 10px;
  background: var(--surface-1);
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  /* Cap on the wide layout so the chart reads as a proportioned card,
     not an edge-to-edge stretched band; the date row lives inside the
     panel and caps with it, staying aligned under the 7 points. */
  max-width: 760px;
}
.nw-drawer-chart-label {
  font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-secondary); font-weight: 600; margin-bottom: 6px;
  display: flex; justify-content: space-between; align-items: baseline;
  flex-wrap: wrap; gap: 6px;
}
.nw-drawer-chart-label .legend-row { display: inline-flex; gap: 10px; }
.nw-drawer-chart-label .legend {
  font-size: 10px; color: var(--text-muted); font-weight: 400;
  text-transform: none; letter-spacing: 0;
}
/* Fill the panel width and scale uniformly (height follows the viewBox
   aspect) — true proportions at any drawer width, no horizontal warp. */
.nw-drawer-chart svg { display: block; width: 100%; height: auto; }
/* Day-of-week + date row under a 7-cell chart (drawer delivery chart). */
.nw-date-row {
  display: grid; grid-template-columns: repeat(7, 1fr);
  margin-top: 4px; font-size: 9px;
  color: var(--text-muted); font-variant-numeric: tabular-nums;
}
.nw-date-row > span { text-align: center; }
.nw-date-row .is-today {
  color: var(--text-primary); font-weight: 600;
}
.nw-date-row .is-soft { color: var(--state-warning); }
/* Compact date row under small-multiples sparklines — first/last show day +
   date, middle days are single-letter abbreviations. */
.nw-sm-dates {
  display: grid; grid-template-columns: repeat(7, 1fr);
  margin-top: 3px; font-size: 8px;
  color: var(--text-muted); font-variant-numeric: tabular-nums;
}
.nw-sm-dates > span { text-align: center; }
.nw-sm-dates .is-today {
  color: var(--text-primary); font-weight: 600;
}
/* Small multiples for viewability + CTR/VCR — compact, secondary weight. */
.nw-sm-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px;
  /* Align under the delivery-chart card and keep the sparklines from
     sprawling super-wide on the wide layout. */
  max-width: 760px;
}
.nw-sm-panel {
  padding: 8px 10px;
  background: var(--surface-1);
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
}
.nw-sm-label {
  font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-secondary); font-weight: 600; margin-bottom: 4px;
  display: flex; justify-content: space-between; align-items: baseline;
}
.nw-sm-label .latest {
  font-size: 11px; font-weight: 600; letter-spacing: 0;
  color: var(--text-primary); text-transform: none;
  font-variant-numeric: tabular-nums;
}
/* Small-multiple sparklines scale UNIFORMLY (uniform=True viewBox, no
   preserveAspectRatio="none") — height:auto keeps geometry true at any
   panel width instead of crushing the trend flat on the wide layout. */
.nw-sm-panel svg { width: 100%; height: auto; display: block; }
/* Desktop: the 7-day delivery chart spans the full drawer width, and the
   Viewability/CTR/Attention/SIVT/GIVT small-multiples sit in ONE aligned row of
   5 directly below it — so the graphs line up (Roger 2026-06-15). An earlier
   side-by-side left the short delivery chart next to a 3-row grid, which read
   ragged / unaligned. ≤1024px / mobile keeps the capped chart + 2-col grid,
   stacked as before. */
@media (min-width: 1025px) {
  .nw-drawer-charts > .nw-drawer-chart { max-width: none; }
  .nw-drawer-charts > .nw-sm-grid { grid-template-columns: repeat(5, 1fr); max-width: none; }
  /* A video line shows 6 small-multiples (Viewability · VCR · CTR · Attention ·
     SIVT · GIVT) — widen the row to 6 so they stay in one aligned row. */
  .nw-drawer-charts > .nw-sm-grid--6 { grid-template-columns: repeat(6, 1fr); }
}
/* Desktop: the PMP deal drawer's 3 trend charts read as a "headline + funnel
   row" — revenue spans the full drawer width on top, then total requests +
   bid responses sit paired in a row directly below it (Roger 2026-06-15). Same
   rhythm as the Direct drawer's full-width delivery chart + small-multiples
   row, and it kills the tall 3-high full-width stack that left the drawer's
   right half empty. The first chart (revenue) is forced full-width via
   flex-basis:100%; the rest share the next flex line. Variable count is handled
   for free: a 2-chart deal (Pubmatic — revenue + bid responses, no requests)
   shows revenue full + responses full below; a revenue-only deal shows one
   full-width chart. ≤1024px / mobile: the wrapper is a plain block, so every
   chart stacks full-width exactly as before. */
@media (min-width: 1025px) {
  .nw-pmp-charts { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 12px; }
  .nw-pmp-charts > .nw-drawer-chart { flex: 1 1 240px; max-width: none; margin-top: 0; }
  .nw-pmp-charts > .nw-drawer-chart:first-child { flex-basis: 100%; }
}
.nw-actions { margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; }
.nw-action {
  display: inline-block; padding: 6px 14px;
  border-radius: var(--radius-md);
  background: var(--surface-1);
  border: 1px solid var(--border);
  color: var(--text-primary);
  font-size: 11px; text-decoration: none;
}
.nw-action:hover { border-color: var(--text-secondary); }
.nw-action-primary {
  background: var(--accent-info-surface);
  border-color: var(--accent-info-border);
  color: var(--accent-info);
}
.nw-action.is-disabled {
  opacity: 0.45; cursor: not-allowed; pointer-events: auto;
  background: transparent; color: var(--text-muted);
}
.nw-action.is-disabled:hover { background: transparent; border-color: var(--border); }
/* Clickable GAM ID in the drawer subtitle — anchor inheriting drawer style. */
.stApp .nw-drawer-id-link,
.stApp .nw-drawer-id-link:link,
.stApp .nw-drawer-id-link:visited {
  color: var(--text-secondary) !important;
  text-decoration: none !important;
  border-bottom: 1px dotted rgba(31,30,25,0.35);
  font-variant-numeric: tabular-nums;
}
.stApp .nw-drawer-id-link:hover {
  color: var(--text-primary) !important;
  border-bottom-color: rgba(31,30,25,0.60);
}
/* ── PMP section (matches the Direct Campaigns design language) ──── */
.nw-section-div { height: 0; border: 0; border-top: 2px solid var(--border-strong);
                  background: none; margin: 28px 0 14px; }
.nw-section-eyebrow { font-size: 11px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
                      color: var(--text-secondary); font-weight: 600; }
.nw-section-h3 { font-family: var(--font-display); font-size: 18px; font-weight: 700;
                 color: var(--text-primary); margin: 2px 0 10px 0; line-height: 1.2; }
/* Deal-type pills (PG / PD / PA / PMP) — categorical chrome, tints
   derived from the viz palette (never the severity scale). */
.pill-dt { display: inline-block; padding: 2px 8px; border-radius: var(--radius-sm);
           font-size: 10px; font-weight: 600; letter-spacing: 0.04em;
           text-transform: uppercase; line-height: 1.4;
           font-variant-numeric: tabular-nums; }
.pill-dt-pg  { background: var(--cat-green-surface); color: var(--cat-green); }
.pill-dt-pd  { background: var(--accent-info-surface); color: var(--accent-info); }
.pill-dt-pa  { background: var(--surface-2); color: var(--text-secondary); }
.pill-dt-pmp { background: var(--cat-purple-surface); color: var(--cat-purple); }
/* eCPM threshold colors — under floor amber, well above green. */
.ecpm-under { background: var(--state-warning-surface); color: var(--state-warning);
              padding: 2px 8px; border-radius: var(--radius-sm); font-weight: 600; }
.ecpm-over  { color: var(--state-positive-muted); font-weight: 600; }
/* PMP table — same grid pattern as Direct but different column proportions. */
.nw-pmp-rows .nw-row-header,
.nw-pmp-rows .nw-pmp-row {
  display: grid;
  /* Columns: Deal | Type | DSP | SSP | Format | Revenue | Impressions | eCPM | Attention | SIVT | GIVT | Seller */
  grid-template-columns: 22fr 6fr 9fr 7fr 9fr 10fr 11fr 9fr 8fr 7fr 7fr 13fr;
  gap: 8px;
  /* align-items: start matches the Direct table — see same-named CSS
     rule above. Same reason: Attention/SIVT/GIVT cells now mix 1-line
     and 2-line content depending on whether a delta is present, so
     centering misaligned the values across the row. */
  align-items: start;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 13px; color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.nw-pmp-rows .nw-row-header {
  font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
  color: var(--text-secondary); font-weight: 600;
  border-bottom-color: var(--border);
}
.nw-pmp-rows .nw-row-header .num,
.nw-pmp-rows .nw-pmp-row .num { text-align: right; }

/* Sticky table headers — both Direct and PMP. Header row sticks to the
   top of the viewport while the user scrolls through the table body, so
   the column labels stay visible on long tables. Background must be
   opaque — the card surface (--surface-1) — so rows scrolling
   underneath don't show through.
   z-index 5 = above pace pills + delta rows in the body, below
   Streamlit's chrome (which uses higher z-indices). Defined LAST in
   the stylesheet so the position/background props win on source order
   over the earlier .nw-row-header / .nw-pmp-rows .nw-row-header rules
   that set typography (specificity is equal). */
.nw-row-header,
.nw-pmp-rows .nw-row-header {
  position: sticky;
  top: 0;
  z-index: 5;
  background: var(--surface-1);
}
/* Click-to-expand mechanics — each PMP row becomes <details name="pmp-cmprow">
   so it's a native exclusive-accordion (only one drawer open at a time). */
.nw-pmp-rows details > summary.nw-pmp-row {
  cursor: pointer; list-style: none;
}
.nw-pmp-rows details > summary.nw-pmp-row::-webkit-details-marker { display: none; }
.nw-pmp-rows details > summary.nw-pmp-row::marker { content: ""; }
.nw-pmp-rows details > summary.nw-pmp-row:hover {
  background: var(--surface-2);
}
.nw-pmp-rows details[open] > summary.nw-pmp-row {
  background: var(--surface-2);
}
.nw-pmp-drawer {
  padding: 14px 18px 16px;
  background: var(--surface-2);
  border-top: 1px solid var(--border);
  font-size: 12px;
}
/* Legend (small color-coded glossary in the table card header) */
.nw-legend-pill { display: flex; gap: 14px; font-size: 11px;
                  color: var(--text-secondary); align-items: center; }
.nw-legend-pill .pill-dt { font-size: 9px; padding: 1px 6px; }
/* Deal-name primary + parenthetical + subtitle */
.pmp-name-primary { font-weight: 600; color: var(--text-primary); }
.pmp-name-paren { color: var(--text-secondary); font-weight: 400; margin-left: 4px; }
.pmp-name-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px;
                font-variant-numeric: tabular-nums; }
/* ── Settings sections (Direct Campaigns redesign) ───────────────── */
.cfg-section { background: var(--surface-1); border-radius: var(--radius-md);
               border: 1px solid var(--border); padding: 16px 20px; margin: 10px 0; }
.cfg-section-head { display: flex; justify-content: space-between; align-items: baseline;
                    margin-bottom: 4px; }
.cfg-eyebrow { font-size: 10px; letter-spacing: var(--track-eyebrow); text-transform: uppercase;
               color: var(--text-secondary); font-weight: 600; }
.cfg-count   { font-size: 11px; color: var(--text-muted);
               font-variant-numeric: tabular-nums; }
.cfg-title   { font-family: var(--font-display); font-size: 18px; font-weight: 700;
               color: var(--text-primary); margin: 0 0 4px 0; }
.cfg-desc    { font-size: 12px; color: var(--text-secondary); margin-bottom: 14px;
               line-height: 1.5; }
.cfg-card    { background: var(--surface-2); border-radius: var(--radius-md);
               border: 1px solid var(--border); padding: 12px 14px; margin: 8px 0; }
.cfg-card-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.cfg-card-meta  { font-size: 11px; color: var(--text-muted);
                  margin-left: 8px; font-weight: 400; }
.cfg-mono    { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px;
               color: var(--text-secondary); }
.cfg-tertiary{ color: var(--text-muted); }
.cfg-status-enabled { display: inline-block; padding: 1px 8px; border-radius: var(--radius-sm);
                      background: var(--state-positive-surface); color: var(--state-positive);
                      font-size: 10px; font-weight: 600; letter-spacing: 0.05em; }
.cfg-status-disabled{ display: inline-block; padding: 1px 8px; border-radius: var(--radius-sm);
                      background: var(--surface-2); color: var(--text-muted);
                      font-size: 10px; font-weight: 600; letter-spacing: 0.05em; }
.cfg-computed { display: inline-block; padding: 1px 6px; border-radius: 3px;
                background: var(--accent-info-surface); color: var(--accent-info);
                font-size: 9px; font-weight: 600; letter-spacing: 0.04em; margin-left: 6px;
                vertical-align: middle; }
.cfg-suggest { background: var(--accent-info-surface); color: var(--accent-info);
               border: 1px solid var(--accent-info-border);
               border-radius: var(--radius-md); padding: 10px 14px;
               font-size: 12px; margin: 8px 0; }
.cfg-gradient { height: 12px; border-radius: 6px; margin: 6px 0;
                background: linear-gradient(to right,
                  var(--state-critical) 0%, #d6aa00 50%, var(--state-positive) 100%); position: relative; }
.cfg-gradient-marker { position: absolute; top: -3px; width: 2px; height: 18px;
                       background: var(--text-primary); border-radius: 1px; }
.cfg-gradient-axis { display: flex; justify-content: space-between; font-size: 10px;
                     color: var(--text-muted); margin-top: 2px; }
.cfg-key-row { display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 12px;
               padding: 6px 0; border-bottom: 1px solid var(--border); align-items: center; }
.cfg-key-row:last-child { border-bottom: none; }
.cfg-pill-preview { display: inline-block; padding: 2px 10px; border-radius: var(--radius-sm);
                    font-weight: 600; font-size: 12px; }
.cfg-alias { font-size: 12px; color: var(--text-secondary); padding: 2px 0 2px 18px;
             font-family: ui-monospace, Menlo, Consolas, monospace; }
.cfg-canonical { font-size: 13px; font-weight: 600; color: var(--text-primary); padding: 4px 0; }
.cfg-canonical.system { font-style: italic; color: var(--text-muted); }
/* ════════════════════════════════════════════════════════════════════
   RESPONSIVE / MOBILE. The dashboard is desktop-first; these overrides
   keep it legible on phones + tablets without touching any desktop rule
   (everything lives behind a media query, source-ordered last so equal-
   specificity rules win). Filters already reflow for free — Streamlit
   stacks st.columns on narrow viewports. Verified at 390px (iPhone) and
   360px (Android).
   ════════════════════════════════════════════════════════════════════ */
/* Tablet + large phone: the fixed 9-up KPI grid crushes tiles until the
   labels/values wrap one glyph per line. Switch to a fluid grid that
   packs as many ≥96px tiles per row as fit and wraps the rest. At desktop
   widths the base repeat(9,1fr) still applies (this only kicks in ≤1024). */
@media (max-width: 1024px) {
  .nw-kpi-row { grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); }
}
@media (max-width: 640px) {
  /* Reclaim side padding on small screens. */
  .stApp .main .block-container,
  .stApp [data-testid="stMain"] .block-container,
  .stApp [data-testid="stAppViewContainer"] .block-container {
    padding-left: 1rem !important;
    padding-right: 1rem !important;
  }
  /* KPI strips: the ≤1024 fluid auto-fit packs 4 tiles across on a phone and
     crushes them (labels wrap, values cramped). Pin the main 9-tile strip to
     3-up (clean 3×3) and the PMP 4-tile strip to 2-up (2×2). */
  .nw-kpi-row { grid-template-columns: repeat(3, 1fr); }
  .nw-kpi-row.nw-kpi-row--pmp { grid-template-columns: repeat(2, 1fr); }
  /* Exception banners: 3-up → stacked full-width (legible over cramped). */
  .nw-banner-row { grid-template-columns: 1fr; }
  /* Tab row: scroll horizontally instead of wrapping/clipping, so every
     view stays reachable by swipe. */
  .nw-tabrow { overflow-x: auto; flex-wrap: nowrap;
               -webkit-overflow-scrolling: touch; scrollbar-width: none; }
  .nw-tabrow::-webkit-scrollbar { display: none; }
  .nw-tab { white-space: nowrap; flex: 0 0 auto; }
  /* Any remaining dense grid table keeps the horizontal swipe by default. */
  .nw-tbl-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  /* Direct table → compact "graph card" rows (Solution 3): swap each row's
     12-column summary for the .nw-row-m card (name + pace bar + delivery
     sparkline + revenue/pace). No horizontal scroll. */
  .nw-tbl-wrap.nw-tbl-direct { overflow-x: visible; }
  .nw-tbl-direct .nw-rows { min-width: 0; }
  .nw-tbl-direct .nw-row-header { display: none; }
  .nw-tbl-direct .nw-row > summary { display: block; padding: 0; }
  .nw-tbl-direct .nw-row > summary > *:not(.nw-row-m) { display: none; }
  .nw-tbl-direct .nw-row-m { display: grid; }
  /* PMP table → the same graph-card treatment. PMP deals have no daily
     series, so the card's visual is an eCPM-vs-floor bar (see .nw-pmp-m).
     The summary IS .nw-pmp-row, so the grid swap targets it directly. */
  .nw-tbl-wrap.nw-tbl-pmp { overflow-x: visible; }
  .nw-tbl-pmp .nw-pmp-rows { min-width: 0; }
  .nw-tbl-pmp .nw-row-header { display: none; }
  .nw-tbl-pmp .nw-pmp-row { display: block; padding: 0; }
  .nw-tbl-pmp .nw-pmp-row > *:not(.nw-pmp-m) { display: none; }
  .nw-tbl-pmp .nw-pmp-m { display: grid; }
  /* Drawer metadata: 4-up → 2-up. */
  .nw-meta-grid { grid-template-columns: 1fr 1fr; }
  /* Needs-attention accordion: tighten the reveal on mobile. */
  .nw-na-sub { padding-left: 26px; }
}
</style>
""",
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────────────────────────────────
# Dual-entry navigation: data tabs + a Configure tab pushed to the right
# with a divider, AND a gear icon button in the header top-right. Both
# entry points route through st.session_state.active_view.
# ──────────────────────────────────────────────────────────────────────────
_VIEW_KEYS  = ("campaigns", "site", "dsp", "magnite", "pubmatic", "opensincera", "configure")
_VIEW_TITLE = {
    "campaigns":   "Overall performance",
    "site":        "By site / size",
    "dsp":         "By DSP",
    "magnite":     "Magnite deals",
    "pubmatic":    "Pubmatic deals",
    "opensincera": "OpenSincera",
    "configure":   "Configure",
}
_NAV_DATA = [
    ("campaigns",   "Campaigns"),
    ("site",        "By site / size"),
    ("dsp",         "By DSP"),
    ("magnite",     "Magnite deals"),
    ("pubmatic",    "Pubmatic deals"),
    ("opensincera", "OpenSincera"),
]

if "active_view" not in st.session_state:
    st.session_state.active_view = "campaigns"
# Honor ?view= deep-link on first load (and any rerun the user navigates with).
try:
    _qp = st.query_params.get("view")
    if isinstance(_qp, str) and _qp in _VIEW_KEYS and st.session_state.active_view != _qp:
        st.session_state.active_view = _qp
except Exception:
    pass

_load_errors: dict[str, str] = {}  # table → error message, populated by load()

# Cache TTL: 1 hour. The original 6h TTL guarded the FREE plan's 5 GB/month
# egress cap (1h ≈ 9 GB/month) and the Nano compute's daily disk-IO budget.
# The org is on Pro now (250 GB egress included) and the project runs Micro
# compute (covered by Pro's $10 compute credit), so neither constraint
# binds — and 1h means post-sweep data shows up within the hour instead of
# whenever the 6h window happened to roll. The debug "Clear cache +
# re-query" button still handles on-demand refresh.
_CACHE_TTL_SECONDS = 3600

@st.cache_data(ttl=_CACHE_TTL_SECONDS)
def load(table: str) -> pd.DataFrame:
    # ── Column projection ────────────────────────────────────────────────
    # The two big DV tables carry many columns no dashboard view reads:
    # dv_attention has 8 sibling attention indices + 2 viewability-rate columns
    # we never surface (only attention_index is used); dv_ivt ships precomputed
    # fraud_sivt_rate / givt_rate / ivt_rate that the dashboard *recomputes*
    # impression-weighted from monitored_ads, plus advertiser /
    # eligible_impressions / total_calls. Selecting only the consumed columns
    # roughly halves the cold-load wire bytes of these tables (the dominant
    # cost of the Campaigns view's first paint). The projected set must cover
    # every consumer — see _dv_attention_aggregates / _dv_ivt_aggregates and
    # the publisher-wide drawer recompute. If the projected SELECT errors (a
    # column renamed/dropped upstream), we fall back to SELECT * below, so the
    # projection is a pure optimization, never a hard dependency.
    _COL_PROJECT = {
        "dv_attention": ["line_item_id", "line_item_name", "order_name",
                         "attention_index", "date"],
        "dv_ivt":       ["line_item_id", "line_item_name", "order_name",
                         "traffic_validity", "monitored_ads", "date"],
    }
    # For time-series tables with a `date` column, cap to last N days.
    # Dashboard views never look back more than ~7 days for these; full-table
    # cold-loads of the big DV tables drove the 2026-06-06/07 disk-IO
    # incidents. (Blamed on "Nano tier" at the time — the instance is actually
    # Micro on Pro, so the budget is roomier than feared, but loading rows no
    # view can render is waste at any size.)
    #
    # Add a table here only if (a) it has a `date` column and (b) the dashboard
    # surfaces only recent rows. Metadata / lookup tables (gam_pmp_deals,
    # gam_pa_metadata, opensincera_*, pmp_last_bid_date) stay full-table because
    # they're either small or the dashboard needs the full set.
    # Verified 2026-06-07 against live schema: only dv_attention and dv_ivt are
    # large enough (now ~24k + ~44k rows over their 7-day window) for the date
    # filter to meaningfully save IO. All other dashboard tables sit at <10k
    # rows and don't justify the conditional. gam_campaigns specifically does
    # NOT have a `date` column (its time cols are `start_date`, `end_date`,
    # `report_start`) — including it here silently broke gam_campaigns loading
    # in #108.
    _DATE_CAPPED = {
        "dv_attention": 30,
        "dv_ivt":       30,
    }
    _where = ""
    if table in _DATE_CAPPED:
        _where = f"WHERE date >= CURRENT_DATE - INTERVAL '{_DATE_CAPPED[table]} days'"
    _cols = "*"
    if table in _COL_PROJECT:
        _cols = ", ".join(f'"{c}"' for c in _COL_PROJECT[table])
    try:
        with _engine().connect() as conn:
            return pd.read_sql(f'SELECT {_cols} FROM "{table}" {_where}', conn)
    except Exception as _proj_e:
        if _cols == "*":
            _load_errors[table] = str(_proj_e)
            return pd.DataFrame()
        # Projected SELECT failed (a column was renamed/dropped upstream?) —
        # degrade to the full table on a fresh connection so the view still
        # renders. Recorded under a distinct key for the debug panel.
        _load_errors[f"{table}:projection"] = str(_proj_e)
        try:
            with _engine().connect() as conn:
                return pd.read_sql(f'SELECT * FROM "{table}" {_where}', conn)
        except Exception as _e:
            _load_errors[table] = str(_e)
            return pd.DataFrame()


@st.cache_data(ttl=_CACHE_TTL_SECONDS)
def _load_li_max_duration() -> pd.DataFrame:
    """Pre-aggregated max creative duration per line item.

    Replaces the prior pattern of loading 183K gam_lica rows + 12K
    gam_creatives rows just to compute a max() per LI in pandas. Same
    output (~2K rows), tiny fraction of the bandwidth.

    Columns: line_item_id (text), _creative_max_dur (float seconds).
    Returns empty frame on any error so callers degrade to "no
    recategorization" instead of crashing.
    """
    try:
        with _engine().connect() as conn:
            return pd.read_sql(
                sqlalchemy.text(
                    """
                    SELECT l.line_item_id,
                           MAX(c.duration_seconds) AS _creative_max_dur
                    FROM gam_lica l
                    JOIN gam_creatives c ON l.creative_id = c.creative_id
                    WHERE c.duration_seconds IS NOT NULL
                    GROUP BY l.line_item_id
                    """
                ),
                conn,
            )
    except Exception as _e:
        _load_errors["_li_max_duration"] = str(_e)
        return pd.DataFrame(columns=["line_item_id", "_creative_max_dur"])


@st.cache_data(ttl=_CACHE_TTL_SECONDS)
def _load_dv_attention_agg():
    """Server-side pre-aggregation of dv_attention — replaces the raw ~24k-row
    load (the dominant cold-load cost). Returns three ``AVG(attention_index)``
    frames, each at exactly the grain a consumer reduces to, so feeding them to
    the dashboard_logic aggregators is behaviour-identical to the raw rows
    (proven on prod 2026-06-15). The per-order grain is a *separate* query, not
    derived from per-LI, so it can't become a mean-of-means if creative counts
    ever go uneven:
      by_li    — per (line_item_id, date): per-LI columns + drawer sparklines
      by_order — per (order_name, date):   PMP table's per-order columns
      by_date  — per (date):               publisher-wide Attention KPI tile
    Each WHERE mirrors the matching ``dl`` dropna. ~42% fewer rows than raw."""
    _e_li = pd.DataFrame(columns=["line_item_id", "line_item_name", "date", "attention_index"])
    _e_ord = pd.DataFrame(columns=["order_name", "date", "attention_index"])
    _e_dt = pd.DataFrame(columns=["date", "attention_index"])
    try:
        with _engine().connect() as conn:
            by_li = pd.read_sql(sqlalchemy.text("""
                SELECT line_item_id, MIN(line_item_name) AS line_item_name,
                       date, AVG(attention_index) AS attention_index
                FROM dv_attention
                WHERE attention_index IS NOT NULL AND line_item_id IS NOT NULL
                  AND date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY line_item_id, date
            """), conn)
            by_order = pd.read_sql(sqlalchemy.text("""
                SELECT order_name, date, AVG(attention_index) AS attention_index
                FROM dv_attention
                WHERE attention_index IS NOT NULL AND order_name IS NOT NULL
                  AND date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY order_name, date
            """), conn)
            by_date = pd.read_sql(sqlalchemy.text("""
                SELECT date, AVG(attention_index) AS attention_index
                FROM dv_attention
                WHERE attention_index IS NOT NULL
                  AND date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY date
            """), conn)
            return by_li, by_order, by_date
    except Exception as _e:
        _load_errors["_dv_attention_agg"] = str(_e)
        return _e_li, _e_ord, _e_dt


@st.cache_data(ttl=_CACHE_TTL_SECONDS)
def _load_dv_ivt_agg() -> pd.DataFrame:
    """Server-side pre-aggregation of dv_ivt — per (line_item_id, order_name,
    date, traffic_validity) ``SUM(monitored_ads)``. monitored_ads sums compose,
    so this ONE frame is exact for every IVT consumer (per-LI columns, per-order
    PMP columns, drawer sparklines, publisher-wide KPI) — behaviour-identical to
    the raw ~44k-row load, ~42% fewer rows."""
    _empty = pd.DataFrame(columns=["line_item_id", "line_item_name", "order_name",
                                   "date", "traffic_validity", "monitored_ads"])
    try:
        with _engine().connect() as conn:
            return pd.read_sql(sqlalchemy.text("""
                SELECT line_item_id, MIN(line_item_name) AS line_item_name,
                       order_name, date, traffic_validity,
                       SUM(monitored_ads) AS monitored_ads
                FROM dv_ivt
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY line_item_id, order_name, date, traffic_validity
            """), conn)
    except Exception as _e:
        _load_errors["_dv_ivt_agg"] = str(_e)
        return _empty


# ── Header: eyebrow + view-aware H1 + right-aligned timestamp + inline gear.
_active_view = st.session_state.active_view
_hdr_left, _hdr_right = st.columns([4, 2])
with _hdr_left:
    st.markdown('<div class="nw-eyebrow">Yield &amp; pacing</div>', unsafe_allow_html=True)
    st.markdown(
        f"<h1>{_VIEW_TITLE.get(_active_view, 'Overall performance')}</h1>",
        unsafe_allow_html=True,
    )

_header_right_slot = _hdr_right.empty()

@st.cache_data(ttl=_CACHE_TTL_SECONDS)
def _last_data_refresh_iso() -> str | None:
    """Latest _pulled_at across gam_campaigns — the canonical 'when did the
    data last update' signal for the header timestamp. Cached on the shared
    TTL to match the rest of the cache profile."""
    try:
        with _engine().connect() as _conn:
            row = _conn.execute(sqlalchemy.text(
                "SELECT MAX(_pulled_at) FROM gam_campaigns"
            )).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


@st.cache_data(ttl=_CACHE_TTL_SECONDS)
def _dv_attention_aggregates():
    """Per-LI / per-order Attention current+prior dicts + the per-LI daily
    series, **memoized**. Sourced from the SERVER-SIDE pre-aggregation
    (`_load_dv_attention_agg`): the per-(LI,date) AVG frame feeds the per-LI
    columns + sparklines, the per-(order,date) AVG frame feeds the per-order
    columns. Each AVG *is* the `dl` aggregators' first-level reduction, so the
    outputs are identical to the old raw-row path (proven on prod) at ~42% fewer
    rows fetched. Returns
    ``(li_col, by_li, prior_by_li, series_by_li, by_order, prior_by_order)``."""
    by_li_df, by_order_df, _ = _load_dv_attention_agg()
    li_col = "line_item_name"
    by_li: dict = {}; prior_by_li: dict = {}; series_by_li: dict = {}
    by_order: dict = {}; prior_by_order: dict = {}
    if not by_li_df.empty and "attention_index" in by_li_df.columns:
        li_col = dl.choose_join_col(by_li_df)
        if li_col in by_li_df.columns:
            by_li, prior_by_li = dl.attention_current_and_prior(by_li_df, li_col)
            series_by_li = dl.attention_daily_series_by_li(by_li_df, li_col)
    if not by_order_df.empty and {"order_name", "attention_index"}.issubset(by_order_df.columns):
        by_order, prior_by_order = dl.attention_current_and_prior(by_order_df, "order_name")
    # PMP fallback: some deals carry an abbreviated word in their "Deal"
    # key (programmatic_deal_name) that matches DV's Line Item, not Order
    # (e.g. _Tech_ vs _Technology_). Merge a line_item_name-keyed lookup
    # under the canonical order_name one so those deals still join.
    if not by_li_df.empty and "line_item_name" in by_li_df.columns and "attention_index" in by_li_df.columns:
        _bn, _pbn = dl.attention_current_and_prior(by_li_df, "line_item_name")
        by_order = dl.merge_lookups(by_order, _bn)
        prior_by_order = dl.merge_lookups(prior_by_order, _pbn)
    return li_col, by_li, prior_by_li, series_by_li, by_order, prior_by_order


@st.cache_data(ttl=_CACHE_TTL_SECONDS)
def _dv_ivt_aggregates():
    """Per-LI / per-order MRC impression-weighted SIVT%/GIVT% current+prior
    dicts + per-LI daily series, **memoized**. Sourced from the SERVER-SIDE
    pre-aggregation (`_load_dv_ivt_agg`): per (LI,order,date,validity)
    SUM(monitored_ads). monitored_ads sums compose, so the SUM frame is exact
    for both the per-LI and per-order paths — identical to the old raw-row path
    at ~42% fewer rows fetched."""
    ivt = _load_dv_ivt_agg()
    li_col = "line_item_name"
    sivt_by_li: dict = {}; sivt_prior_by_li: dict = {}
    givt_by_li: dict = {}; givt_prior_by_li: dict = {}
    sivt_series_by_li: dict = {}; givt_series_by_li: dict = {}
    sivt_by_order: dict = {}; sivt_prior_by_order: dict = {}
    givt_by_order: dict = {}; givt_prior_by_order: dict = {}
    if (not ivt.empty
            and {"traffic_validity", "monitored_ads", "date"}.issubset(ivt.columns)):
        li_col = dl.choose_join_col(ivt)
        if li_col in ivt.columns:
            sivt_by_li, sivt_prior_by_li = dl.ivt_share_with_prior(ivt, li_col, "Fraud/SIVT")
            givt_by_li, givt_prior_by_li = dl.ivt_share_with_prior(ivt, li_col, "Fraud/GIVT")
            sivt_series_by_li = dl.ivt_daily_series_by_li(ivt, li_col, "Fraud/SIVT")
            givt_series_by_li = dl.ivt_daily_series_by_li(ivt, li_col, "Fraud/GIVT")
        if "order_name" in ivt.columns:
            sivt_by_order, sivt_prior_by_order = dl.ivt_share_with_prior(ivt, "order_name", "Fraud/SIVT")
            givt_by_order, givt_prior_by_order = dl.ivt_share_with_prior(ivt, "order_name", "Fraud/GIVT")
        # PMP fallback by line_item_name — see _dv_attention_aggregates
        # (deals whose "Deal" key matches DV's Line Item, not Order).
        if "line_item_name" in ivt.columns:
            _sbn, _spbn = dl.ivt_share_with_prior(ivt, "line_item_name", "Fraud/SIVT")
            _gbn, _gpbn = dl.ivt_share_with_prior(ivt, "line_item_name", "Fraud/GIVT")
            sivt_by_order = dl.merge_lookups(sivt_by_order, _sbn)
            sivt_prior_by_order = dl.merge_lookups(sivt_prior_by_order, _spbn)
            givt_by_order = dl.merge_lookups(givt_by_order, _gbn)
            givt_prior_by_order = dl.merge_lookups(givt_prior_by_order, _gpbn)
    return (li_col, sivt_by_li, sivt_prior_by_li, givt_by_li, givt_prior_by_li,
            sivt_series_by_li, givt_series_by_li,
            sivt_by_order, sivt_prior_by_order, givt_by_order, givt_prior_by_order)


def _render_header_right(ts_html=None):
    """Fill the header right-side cluster: timestamp + inline gear icon.
    Default timestamp sources from gam_campaigns._pulled_at (when the data
    last refreshed) rather than wall-clock time. View-specific overrides
    can pass a richer timestamp (e.g. with line-item count)."""
    if ts_html is None:
        freshness = _fmt_header_freshness(_last_data_refresh_iso())
        if freshness:
            ts_html = f'🕐 {freshness}'
        else:
            # No cached data yet (pre-first-refresh) — fall back to wall clock.
            try:
                from zoneinfo import ZoneInfo as _ZI
                _now_edt = datetime.now(_ZI("America/New_York"))
                ts_html = f'🕐 {_now_edt.strftime("%-I:%M %p EDT")}'
            except Exception:
                ts_html = f'🕐 {datetime.now().strftime("%H:%M")}'
    _header_right_slot.markdown(
        '<div class="nw-header-right">'
        f'<div class="nw-timestamp">{ts_html}</div>'
        '</div>',
        unsafe_allow_html=True,
    )

# Default fill — Configure view shows "Last saved …", others fall back to current time.
if _active_view == "configure":
    _ts_html = None
    try:
        with _engine().connect() as _c_hdr:
            _r = _c_hdr.execute(sqlalchemy.text(
                "SELECT updated_at FROM dashboard_settings WHERE key='main'"
            )).fetchone()
        if _r and _r[0]:
            _ts = pd.to_datetime(_r[0])
            _age = pd.Timestamp.utcnow() - (_ts.tz_convert("UTC") if _ts.tzinfo else _ts)
            _h = _age.total_seconds() / 3600
            _last = f"{int(_age.total_seconds()/60)} min ago" if _h < 1 else \
                    f"{int(_h)} hours ago" if _h < 24 else f"{int(_h/24)} days ago"
            _ts_html = f"Last saved {_last} by R. Hirano"
    except Exception:
        pass
    _render_header_right(_ts_html)
else:
    _render_header_right()

# ── Tab row: HTML anchors (no st.button — Streamlit's primary-button red
# fill is unbeatable from CSS). Clicks navigate via ?view= query param.
def _tab_html(view_key, label, extra=""):
    cls = "nw-tab"
    if _active_view == view_key:
        cls += " nw-tab-active"
    if extra:
        cls += f" {extra}"
    return f'<a class="{cls}" href="?view={view_key}" target="_self">{label}</a>'

st.markdown(
    '<nav class="nw-tabrow">'
    + "".join(_tab_html(k, lbl) for k, lbl in _NAV_DATA)
    + '<span class="nw-tabrow-spacer"></span>'
    + _tab_html("configure", "⚙  Configure", extra="nw-tab-configure")
    + '</nav>',
    unsafe_allow_html=True,
)

# NOTE: existing `with tab_X:` blocks below are converted to
# `if st.session_state.active_view == "X":` conditionals in a follow-up
# edit. The aliases below let the original blocks continue to type-check
# during the transition — they're stubs that the conditional replacements
# never reach.
tab_seller = tab_site = tab_dsp = tab_deal = tab_pubmatic = tab_settings = None

if st.session_state.active_view == "site":
    df = load("magnite_site_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        dmin, dmax = df["date"].min(), df["date"].max()

        start, end = date_filter("site", dmin, dmax)

        f1, f2, f3 = st.columns(3)
        with f1:
            sites = st.multiselect("Filter sites", sorted(df["site"].dropna().unique()))
        with f2:
            sizes = st.multiselect("Filter sizes", sorted(df["size"].dropna().unique()))
        with f3:
            devices = st.multiselect("Filter device types", sorted(df["device_type_name_v1"].dropna().unique()))

        view = df[(df["date"] >= start) & (df["date"] <= end)]
        if sites:
            view = view[view["site"].isin(sites)]
        if sizes:
            view = view[view["size"].isin(sizes)]
        if devices:
            view = view[view["device_type_name_v1"].isin(devices)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Ad requests", f"{view['ad_requests'].sum():,}")
        c2.metric("Impressions", f"{view['impressions'].sum():,}")
        c3.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")

        # WoW alert
        if len(view) > 0:
            max_d = view["date"].max()
            r7 = view[view["date"] > max_d - timedelta(days=7)]["publisher_gross_revenue"].sum()
            p7 = view[(view["date"] <= max_d - timedelta(days=7)) & (view["date"] > max_d - timedelta(days=14))]["publisher_gross_revenue"].sum()
            if p7 > 0:
                pct = (r7 - p7) / p7 * 100
                if pct <= -10:
                    st.warning(f"Revenue down {abs(pct):.1f}% vs prior week (${r7:,.0f} vs ${p7:,.0f})")
                elif pct >= 10:
                    st.success(f"Revenue up {pct:.1f}% vs prior week (${r7:,.0f} vs ${p7:,.0f})")

        col_trend, col_funnel = st.columns([2, 1])
        with col_trend:
            st.subheader("Daily revenue")
            daily = view.groupby("date")["publisher_gross_revenue"].sum().rename("Revenue ($)")
            st.line_chart(daily, height=220, color="#4b62e0")  # --viz-1
        with col_funnel:
            st.subheader("Bid funnel")
            funnel = pd.Series({
                "Ad requests": view["ad_requests"].sum(),
                "Bid requests": view["bid_requests"].sum(),
                "Impressions": view["impressions"].sum(),
            })
            st.bar_chart(funnel, height=220, color="#4b62e0")  # --viz-1

        st.dataframe(
            view,
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "ad_requests": st.column_config.NumberColumn(format="localized"),
                "bid_requests": st.column_config.NumberColumn(format="localized"),
                "bid_responses": st.column_config.NumberColumn(format="localized"),
                "auctions": st.column_config.NumberColumn(format="localized"),
                "impressions": st.column_config.NumberColumn(format="localized"),
                "publisher_gross_revenue": st.column_config.NumberColumn(format="dollar"),
                "ecpm": st.column_config.NumberColumn(format="dollar"),
            },
        )

if st.session_state.active_view == "magnite":
    df = load("magnite_deal_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date

        # Revenue source from deal_id (0 = Open Market, >0 = Deal)
        df["revenue_source"] = df["deal_id"].astype(str).str.strip().apply(
            lambda x: "Open Market" if x == "0" else "Deal"
        )
        # Normalize open market deal name
        open_market_mask = df["revenue_source"] == "Open Market"
        df.loc[open_market_mask, "deal"] = "Open Market"

        # Seller AE from deal name
        df["seller_ae"] = (
            df["deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
            .map(AE_NAMES)
        )

        dmin, dmax = df["date"].min(), df["date"].max()
        start, end = date_filter("deal", dmin, dmax)

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            rev_sources = st.multiselect(
                "Revenue source",
                sorted(df["revenue_source"].dropna().unique()),
                key="deal_rev_source_filter",
            )
        with f2:
            dsps = st.multiselect(
                "DSP",
                sorted(df["partner"].dropna().unique()) if "partner" in df.columns else [],
                key="deal_dsp_filter",
            )
        with f3:
            formats = st.multiselect(
                "Format",
                sorted(df["ad_format"].dropna().unique()) if "ad_format" in df.columns else [],
                key="deal_format_filter",
            )
        with f4:
            aes = st.multiselect(
                "Filter by Seller",
                sorted(df["seller_ae"].dropna().unique()),
                key="deal_ae_filter",
            )

        deal_search = st.text_input("Search deals by name", placeholder="Type to filter…", key="deal_search")

        view = df[(df["date"] >= start) & (df["date"] <= end)]
        if rev_sources:
            view = view[view["revenue_source"].isin(rev_sources)]
        if dsps:
            view = view[view["partner"].isin(dsps)]
        if formats:
            view = view[view["ad_format"].isin(formats)]
        if aes:
            view = view[view["seller_ae"].isin(aes)]
        if deal_search:
            view = view[view["deal"].str.contains(deal_search, case=False, na=False)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Impressions", f"{view['impressions'].sum():,}")
        c2.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")
        c3.metric("Net revenue", f"${view['seller_net_revenue'].sum():,.2f}")

        # Zero-impression alert (exclude Open Market — it always has impressions)
        pmp_view = view[view["deal"] != "Open Market"]
        if len(pmp_view) > 0:
            zero_imp = pmp_view.groupby("deal")["impressions"].sum()
            zero_imp = zero_imp[zero_imp == 0]
            if not zero_imp.empty:
                st.warning(f"⚠️ {len(zero_imp)} deal(s) with 0 impressions — needs attention.")
                with st.expander("View deals"):
                    zero_df = zero_imp.reset_index()[["deal"]].rename(columns={"deal": "Deal"})
                    zero_df["Seller"] = (
                        zero_df["Deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                        .map(AE_NAMES).fillna("")
                    )
                    days_count = (
                        pmp_view[pmp_view["deal"].isin(zero_imp.index)]
                        .groupby("deal")["date"].nunique()
                    )
                    zero_df["Days with 0 impr."] = zero_df["Deal"].map(days_count).fillna(0).astype(int)
                    deal_metrics = (
                        pmp_view[pmp_view["deal"].isin(zero_imp.index)]
                        .groupby("deal")[["bid_requests", "bid_responses"]].sum()
                    )
                    zero_df["bid_requests"]  = zero_df["Deal"].map(deal_metrics["bid_requests"]).fillna(0)
                    zero_df["bid_responses"] = zero_df["Deal"].map(deal_metrics["bid_responses"]).fillna(0)

                    def _status(row):
                        if row["bid_requests"] == 0:
                            return "Deal not being sent to buyer — check trafficking"
                        if row["bid_responses"] == 0:
                            return "Buyer hasn't accepted the deal"
                        return "Accepted but not winning — check floor price or targeting"

                    zero_df["Status"] = zero_df.apply(_status, axis=1)
                    st.dataframe(
                        zero_df[["Deal", "Seller", "Days with 0 impr.", "Status"]]
                        .sort_values("Days with 0 impr.", ascending=False),
                        use_container_width=True,
                        hide_index=True,
                    )

        col_src, col_deals, col_ae = st.columns(3)
        with col_src:
            st.subheader("Revenue by source")
            src_rev = (
                view.groupby("revenue_source")["publisher_gross_revenue"]
                .sum().sort_values(ascending=True).rename("Revenue ($)")
            )
            st.bar_chart(src_rev, height=280, horizontal=True, color="#4b62e0")  # --viz-1
        pmp_view = view[view["revenue_source"] == "Deal"]
        with col_deals:
            st.subheader("Top 10 deals by revenue")
            top10_deals = (
                pmp_view.groupby("deal")["publisher_gross_revenue"]
                .sum().nlargest(10).reset_index()
                .rename(columns={"deal": "Deal", "publisher_gross_revenue": "Revenue"})
            )
            chart = alt.Chart(top10_deals).mark_bar(color="#4b62e0").encode(  # --viz-1
                x=alt.X("Revenue:Q", title="Revenue ($)"),
                y=alt.Y("Deal:N", sort="-x", title=None, axis=alt.Axis(labelLimit=500)),
                tooltip=["Deal", alt.Tooltip("Revenue:Q", format="$,.2f")],
            ).properties(height=320)
            st.altair_chart(chart, use_container_width=True)
        with col_ae:
            st.subheader("Revenue by Seller")
            ae_rev = (
                pmp_view.groupby("seller_ae")["publisher_gross_revenue"]
                .sum().sort_values(ascending=True).rename("Revenue ($)")
            )
            st.bar_chart(ae_rev, height=280, horizontal=True, color="#4b62e0")  # --viz-1

        st.dataframe(
            view.sort_values("publisher_gross_revenue", ascending=False),
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "seller_ae": None,
                "deal": st.column_config.TextColumn("Marketplace Deal Name"),
                "revenue_source": st.column_config.TextColumn("Revenue Source"),
                "partner": st.column_config.TextColumn("DSP"),
                "ad_format": st.column_config.TextColumn("Format"),
                "bid_requests": st.column_config.NumberColumn(format="localized"),
                "bid_responses": st.column_config.NumberColumn(format="localized"),
                "impressions": st.column_config.NumberColumn(format="localized"),
                "paid_impression": st.column_config.NumberColumn(format="localized"),
                "publisher_gross_revenue": st.column_config.NumberColumn(format="dollar"),
                "seller_net_revenue": st.column_config.NumberColumn(format="dollar"),
                "ecpm": st.column_config.NumberColumn(format="dollar"),
            },
        )

if st.session_state.active_view == "dsp":
    df = load("magnite_dsp_daily")
    if df.empty:
        st.info("No data yet.")
    else:
        last_pull = df["_pulled_at"].max() if "_pulled_at" in df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        dmin, dmax = df["date"].min(), df["date"].max()

        start, end = date_filter("dsp", dmin, dmax)

        f1, f2 = st.columns(2)
        with f1:
            partners = st.multiselect(
                "Filter DSPs",
                sorted(df["partner"].dropna().unique()),
                key="dsp_partner_filter",
            )
        with f2:
            sites_dsp = st.multiselect(
                "Filter sites",
                sorted(df["site"].dropna().unique()),
                key="dsp_site_filter",
            )

        view = df[(df["date"] >= start) & (df["date"] <= end)]
        if partners:
            view = view[view["partner"].isin(partners)]
        if sites_dsp:
            view = view[view["site"].isin(sites_dsp)]

        c1, c2, c3 = st.columns(3)
        c1.metric("Impressions", f"{view['impressions'].sum():,}")
        c2.metric("Gross revenue", f"${view['publisher_gross_revenue'].sum():,.2f}")
        c3.metric("Auctions won", f"{view['auctions_won'].sum():,}")

        # Low win rate alert
        if len(view) > 0:
            win_by_dsp = view.groupby("partner")["win_rate"].mean()
            low_win = win_by_dsp[win_by_dsp < 10].sort_values()
            if not low_win.empty:
                names = ", ".join([f"{p} ({v:.1f}%)" for p, v in low_win.items()])
                st.warning(f"Low win rate (<10%): {names}")

        col_top, col_issues = st.columns(2)
        with col_top:
            st.subheader("Top 10 DSPs by revenue")
            top10_rev = (view.groupby("partner")["publisher_gross_revenue"]
                         .sum().nlargest(10).sort_values(ascending=True)
                         .rename("Revenue ($)"))
            st.bar_chart(top10_rev, height=280, horizontal=True, color="#4b62e0")  # --viz-1
        with col_issues:
            st.subheader("DSPs to watch — low win rate")
            dsp_summary = (view.groupby("partner")
                           .agg(revenue=("publisher_gross_revenue", "sum"),
                                win_rate=("win_rate", "mean"))
                           .query("revenue > 0")
                           .sort_values("revenue", ascending=False)
                           .head(20))
            flagged = dsp_summary[dsp_summary["win_rate"] < 15].sort_values("revenue", ascending=False)
            if flagged.empty:
                st.success("No DSPs with revenue + low win rate issues.")
            else:
                st.dataframe(
                    flagged.reset_index().rename(columns={
                        "partner": "DSP",
                        "revenue": "Revenue ($)",
                        "win_rate": "Win Rate (%)",
                    }).style.format({"Revenue ($)": "${:,.2f}", "Win Rate (%)": "{:.1f}%"}),
                    use_container_width=True,
                    hide_index=True,
                )

        st.dataframe(
            view.sort_values("publisher_gross_revenue", ascending=False),
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "bid_requests": st.column_config.NumberColumn(format="localized"),
                "bid_responses": st.column_config.NumberColumn(format="localized"),
                "auctions_won": st.column_config.NumberColumn(format="localized"),
                "impressions": st.column_config.NumberColumn(format="localized"),
                "publisher_gross_revenue": st.column_config.NumberColumn(format="dollar"),
                "win_rate": st.column_config.NumberColumn(format="localized"),
            },
        )

if st.session_state.active_view == "pubmatic":
    try:
        pm_df = load("pubmatic_deals")
    except Exception:
        st.info("No Pubmatic data yet — run refresh_cache.py to populate pubmatic_deals.")
        pm_df = pd.DataFrame()

    if pm_df.empty:
        st.info("No Pubmatic data yet.")
    else:
        last_pull = pm_df["_pulled_at"].max() if "_pulled_at" in pm_df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        pm_df = pm_df.copy()
        pm_df["date"] = pd.to_datetime(pm_df["date"]).dt.date

        # Use publisher_deal_id as the display label when deal name is missing
        if "deal" not in pm_df.columns:
            pm_df["deal"] = None
        if "publisher_deal_id" not in pm_df.columns:
            pm_df["publisher_deal_id"] = None
        pm_df["deal_label"] = pm_df["deal"].fillna(pm_df["publisher_deal_id"]).fillna(pm_df["deal_meta_id"].astype(str))
        if "ad_format" in pm_df.columns:
            # One canonical bucket per format ("Banner"→Display,
            # "In-stream video"→Video) so the filter doesn't show two
            # names for the same thing. User format_aliases win.
            pm_df["ad_format"] = pm_df["ad_format"].map(
                lambda f: dl.canonicalize_format(f, _cfg.get("format_aliases") or {}))

        dmin, dmax = pm_df["date"].min(), pm_df["date"].max()
        start, end = date_filter("pubmatic", dmin, dmax)

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            dsp_opts = sorted(pm_df["dsp"].dropna().unique()) if "dsp" in pm_df.columns else []
            sel_dsps = st.multiselect("DSP", dsp_opts, key="pm_dsp_filter")
        with f2:
            _pm_dt_aliases = _cfg.get("deal_type_aliases", {})
            if "deal_type" in pm_df.columns:
                _pm_dt_labels = pm_df["deal_type"].dropna().replace(_pm_dt_aliases)
                deal_type_opts = sorted(_pm_dt_labels.unique().tolist())
            else:
                deal_type_opts = []
            sel_deal_types = st.multiselect("Deal type", deal_type_opts, key="pm_deal_type_filter")
        with f3:
            format_opts = sorted(pm_df["ad_format"].dropna().unique()) if "ad_format" in pm_df.columns else []
            sel_formats = st.multiselect("Format", format_opts, key="pm_format_filter")

        pm_search = st.text_input("Search deals by name", placeholder="Type to filter…", key="pm_deal_search")

        view = pm_df[(pm_df["date"] >= start) & (pm_df["date"] <= end)]
        if sel_dsps:
            view = view[view["dsp"].isin(sel_dsps)]
        if sel_deal_types and "deal_type" in view.columns:
            view = view[view["deal_type"].replace(_pm_dt_aliases).isin(sel_deal_types)]
        if sel_formats and "ad_format" in view.columns:
            view = view[view["ad_format"].isin(sel_formats)]
        if pm_search:
            view = view[view["deal_label"].str.contains(pm_search, case=False, na=False)]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Paid impressions", f"{view['paid_impressions'].sum():,.0f}")
        c2.metric("Revenue", f"${view['revenue'].sum():,.2f}")
        c3.metric("Avg eCPM", f"${view['ecpm'].mean():,.2f}" if len(view) else "—")
        c4.metric("Win rate", f"{view['win_rate'].mean():,.1f}%" if len(view) else "—")

        col_deals, col_dsps = st.columns(2)
        with col_deals:
            st.subheader("Top 10 deals by revenue")
            top_deals = (
                view.groupby("deal_label")["revenue"]
                .sum().nlargest(10).sort_values(ascending=True).reset_index()
                .rename(columns={"deal_label": "Deal", "revenue": "Revenue"})
            )
            if not top_deals.empty:
                chart = alt.Chart(top_deals).mark_bar(color="#4b62e0").encode(  # --viz-1
                    x=alt.X("Revenue:Q", title="Revenue ($)"),
                    y=alt.Y("Deal:N", sort="-x", title=None, axis=alt.Axis(labelLimit=400)),
                    tooltip=["Deal", alt.Tooltip("Revenue:Q", format="$,.2f")],
                ).properties(height=320)
                st.altair_chart(chart, use_container_width=True)

        with col_dsps:
            st.subheader("Top 10 DSPs by revenue")
            top_dsps = (
                view.groupby("dsp")["revenue"]
                .sum().nlargest(10).sort_values(ascending=True).reset_index()
                .rename(columns={"dsp": "DSP", "revenue": "Revenue"})
            ) if "dsp" in view.columns else pd.DataFrame()
            if not top_dsps.empty:
                chart_dsp = alt.Chart(top_dsps).mark_bar(color="#4b62e0").encode(  # --viz-1
                    x=alt.X("Revenue:Q", title="Revenue ($)"),
                    y=alt.Y("DSP:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
                    tooltip=["DSP", alt.Tooltip("Revenue:Q", format="$,.2f")],
                ).properties(height=320)
                st.altair_chart(chart_dsp, use_container_width=True)

        st.subheader("Daily revenue trend")
        daily_pm = view.groupby("date")["revenue"].sum().rename("Revenue ($)")
        st.line_chart(daily_pm, height=200, color="#4b62e0")  # --viz-1

        # Zero-response alert
        no_resp = (
            view.groupby("deal_label")
            .agg(paid_impressions=("paid_impressions", "sum"), responses=("non_zero_bid_responses", "sum"))
            .query("paid_impressions == 0 and responses == 0")
        ) if "non_zero_bid_responses" in view.columns else pd.DataFrame()
        if not no_resp.empty:
            st.warning(f"⚠️ {len(no_resp)} deal(s) with 0 paid impressions and 0 bid responses.")

        st.dataframe(
            view.sort_values("revenue", ascending=False),
            use_container_width=True,
            column_config={
                "_pulled_at": None,
                "source": None,
                "deal_meta_id": None,
                "dsp_id": None,
                "ad_format_id": None,
                "deal": st.column_config.TextColumn("Deal Name"),
                "deal_label": st.column_config.TextColumn("Deal"),
                "publisher_deal_id": st.column_config.TextColumn("Publisher Deal ID"),
                "dsp": st.column_config.TextColumn("DSP"),
                "paid_impressions": st.column_config.NumberColumn(format="localized"),
                "non_zero_bid_responses": st.column_config.NumberColumn("Bid Responses", format="localized"),
                "total_requests": st.column_config.NumberColumn(format="localized"),
                "revenue": st.column_config.NumberColumn(format="dollar"),
                "ecpm": st.column_config.NumberColumn(format="dollar"),
                "win_rate": st.column_config.NumberColumn("Win Rate %", format="localized"),
            },
        )

if st.session_state.active_view == "opensincera":
    # OpenSincera quality / ecosystem metadata. Refreshed by
    # refresh_cache.py into four tables: opensincera_ecosystem,
    # opensincera_publishers, opensincera_adsystems, opensincera_modules.
    eco_df  = load("opensincera_ecosystem")
    pubs_df = load("opensincera_publishers")
    sys_df  = load("opensincera_adsystems")
    mod_df  = load("opensincera_modules")

    if eco_df.empty and pubs_df.empty and sys_df.empty and mod_df.empty:
        st.info(
            "No OpenSincera data yet. Set OPENSINCERA_TOKEN and run "
            "`python refresh_cache.py` to populate the cache."
        )
    else:
        last_pull_candidates = []
        for _df in (eco_df, pubs_df, sys_df, mod_df):
            if not _df.empty and "_pulled_at" in _df.columns:
                last_pull_candidates.append(_df["_pulled_at"].max())
        if last_pull_candidates:
            st.caption(f"Last refresh: {_fmt_last_refresh(max(last_pull_candidates))}")

        # Publishers leads because that's where the Newsweek-vs-peers
        # scorecard lives — the primary view ad-ops opens this tab for.
        # Streamlit's st.tabs() shows the first label by default, so the
        # order of the list IS the default-tab order.
        sub_pubs, sub_eco, sub_sys, sub_mod = st.tabs(
            ["Publishers", "Ecosystem", "Ad systems", "Prebid modules"]
        )

        # ── Ecosystem ───────────────────────────────────────────────────
        with sub_eco:
            if eco_df.empty:
                st.info("No ecosystem snapshot yet.")
            else:
                latest = eco_df.sort_values("_pulled_at").iloc[-1]
                e1, e2, e3, e4 = st.columns(4)
                e1.metric("Publishers", f"{int(latest.get('sincera_ecosystem_size', 0)):,}"
                          if pd.notna(latest.get("sincera_ecosystem_size")) else "—")
                e2.metric("Known ad systems", f"{int(latest.get('known_adsystems', 0)):,}"
                          if pd.notna(latest.get("known_adsystems")) else "—")
                e3.metric("Global GPIDs", f"{int(latest.get('global_gpids', 0)):,}"
                          if pd.notna(latest.get("global_gpids")) else "—")
                e4.metric("Pubs with GPID", f"{int(latest.get('pubs_with_gpid', 0)):,}"
                          if pd.notna(latest.get("pubs_with_gpid")) else "—")

                f1, f2, f3, f4 = st.columns(4)
                f1.metric("Avg user modules",
                          f"{float(latest['avg_user_modules_deployed']):.2f}"
                          if pd.notna(latest.get("avg_user_modules_deployed")) else "—")
                f2.metric("Avg audience providers",
                          f"{float(latest['avg_audience_providers_deployed']):.2f}"
                          if pd.notna(latest.get("avg_audience_providers_deployed")) else "—")
                f3.metric("WebRisk-flagged", f"{int(latest.get('webrisk_flagged_publishers', 0)):,}"
                          if pd.notna(latest.get("webrisk_flagged_publishers")) else "—")
                f4.metric("Adult domains", f"{int(latest.get('adult_domains', 0)):,}"
                          if pd.notna(latest.get("adult_domains")) else "—")

                # Show breakdown JSON columns as expandable tables.
                _json_cols = (
                    ("pbjs_ad_unit_media_types", "Prebid ad-unit media types"),
                    ("pbjs_major_versions",      "Prebid major versions"),
                    ("header_wrappers",          "Header wrappers (by ad system)"),
                )
                for col, label in _json_cols:
                    raw = latest.get(col)
                    if not isinstance(raw, str) or not raw:
                        continue
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(parsed, dict) or not parsed:
                        continue
                    with st.expander(label):
                        _rows = sorted(
                            ((k, int(v) if str(v).isdigit() else v) for k, v in parsed.items()),
                            key=lambda kv: (-kv[1] if isinstance(kv[1], int) else 0, str(kv[0])),
                        )
                        st.dataframe(
                            pd.DataFrame(_rows, columns=["key", "count"]),
                            use_container_width=True, hide_index=True,
                        )

                if len(eco_df) > 1:
                    st.subheader("Ecosystem size over time")
                    _eco_trend = eco_df.copy()
                    if "date" in _eco_trend.columns:
                        _eco_trend["date"] = pd.to_datetime(_eco_trend["date"], errors="coerce")
                        _eco_trend = _eco_trend.dropna(subset=["date"]).sort_values("date")
                        _eco_trend = _eco_trend.set_index("date")[["sincera_ecosystem_size"]]
                        st.line_chart(_eco_trend, height=220, color="#4b62e0")  # --viz-1

        # ── Publishers ──────────────────────────────────────────────────
        with sub_pubs:
            if pubs_df.empty:
                st.info("No publisher records yet.")
            else:
                view = pubs_df.copy()

                # A2CR is returned as a fraction (0.2 = 20%). Surface as %.
                if "avg_ads_to_content_ratio" in view.columns:
                    view["avg_ads_to_content_ratio_pct"] = (
                        pd.to_numeric(view["avg_ads_to_content_ratio"], errors="coerce") * 100
                    )

                # ── Newsweek vs. peer-median scorecard ──────────────
                # Each metric: (column, label, lower_is_better).
                # Quality framing (Sincera's own framing) — lower A2CR /
                # ads-in-view / page weight / CPU / resellers = better;
                # higher ID-absorption = better. Ad refresh is omitted
                # from the win/loss banner because the direction depends
                # on whether you optimise for UX or for impressions.
                _scorecard_metrics = [
                    ("avg_ads_to_content_ratio_pct", "A2CR %",        True,  "%.1f%%"),
                    ("avg_ads_in_view",              "Ads in view",   True,  "%.2f"),
                    ("avg_page_weight",              "Page wt (MB)",  True,  "%.2f"),
                    ("avg_cpu",                      "CPU (s)",       True,  "%.2f"),
                    ("id_absorption_rate",           "ID absorption", False, "%.3f"),
                    ("reseller_count",               "Resellers",     True,  "%.0f"),
                ]

                _nw_row = view[view["domain"].str.contains("newsweek", case=False, na=False)]
                if _nw_row.empty:
                    st.info("Newsweek not in the watch-list — scorecard skipped.")
                else:
                    nw = _nw_row.iloc[0]
                    peers = view[~view.index.isin(_nw_row.index)]

                    st.subheader(f"Newsweek vs. {len(peers)} peers")

                    wins = losses = ties = 0
                    cols = st.columns(len(_scorecard_metrics))
                    for col, (m, label, lower_better, fmt) in zip(cols, _scorecard_metrics):
                        if m not in view.columns:
                            col.metric(label, "—")
                            continue
                        nw_val = pd.to_numeric(nw.get(m), errors="coerce")
                        peer_median = pd.to_numeric(peers[m], errors="coerce").median()
                        if pd.isna(nw_val) or pd.isna(peer_median):
                            col.metric(label, "—")
                            continue
                        delta = nw_val - peer_median
                        # st.metric "normal" colors green-up/red-down; "inverse"
                        # flips for metrics where lower is better.
                        delta_color = "inverse" if lower_better else "normal"
                        col.metric(
                            label,
                            (fmt % nw_val),
                            delta=f"{delta:+.2f} vs peer median",
                            delta_color=delta_color,
                            help=f"Peer median: {fmt % peer_median}",
                        )
                        # Tie tolerance: treat anything within 1% of the
                        # median as a tie so noise doesn't flip the count.
                        tol = abs(peer_median) * 0.01
                        if abs(delta) <= tol:
                            ties += 1
                        elif (delta < 0 and lower_better) or (delta > 0 and not lower_better):
                            wins += 1
                        else:
                            losses += 1

                    if wins + losses + ties:
                        st.caption(
                            f"Newsweek beats peer median on **{wins}** metric(s), "
                            f"loses on **{losses}**, ties on **{ties}**."
                        )

                # Pin Newsweek to the top of the table for quick scanning.
                view["_is_newsweek"] = view["domain"].str.contains("newsweek", case=False, na=False)
                view = view.sort_values(["_is_newsweek", "domain"], ascending=[False, True])

                display_cols = [
                    c for c in [
                        "name", "domain", "primary_supply_type",
                        "avg_ads_to_content_ratio_pct", "avg_ads_in_view",
                        "avg_ad_refresh", "avg_page_weight", "avg_cpu",
                        "total_unique_gpids", "id_absorption_rate",
                        "total_supply_paths", "reseller_count", "updated_at",
                    ] if c in view.columns
                ]

                st.dataframe(
                    view[display_cols] if display_cols else view,
                    use_container_width=True, hide_index=True,
                    column_config={
                        "name":                          st.column_config.TextColumn("Publisher"),
                        "domain":                        st.column_config.TextColumn("Domain"),
                        "primary_supply_type":           st.column_config.TextColumn("Supply"),
                        "avg_ads_to_content_ratio_pct":  st.column_config.NumberColumn("A2CR %", format="%.1f%%"),
                        "avg_ads_in_view":               st.column_config.NumberColumn("Ads in view", format="%.2f"),
                        "avg_ad_refresh":                st.column_config.NumberColumn("Ad refresh (s)", format="%.1f"),
                        "avg_page_weight":               st.column_config.NumberColumn("Page wt (MB)", format="%.2f"),
                        "avg_cpu":                       st.column_config.NumberColumn("CPU (s)", format="%.2f"),
                        "total_unique_gpids":            st.column_config.NumberColumn("GPIDs", format="localized"),
                        "id_absorption_rate":            st.column_config.NumberColumn("ID absorption", format="%.3f"),
                        "total_supply_paths":            st.column_config.NumberColumn("Supply paths", format="localized"),
                        "reseller_count":                st.column_config.NumberColumn("Resellers", format="localized"),
                        "updated_at":                    st.column_config.TextColumn("Updated"),
                    },
                )

                # Side-by-side ranking charts for the two most-actionable
                # metrics. Newsweek's bar is coloured distinctly so it
                # pops out of the per-publisher comparison.
                # "Us vs them" series colors. Brand red is chrome-only under
                # the Newsweek system — never a chart series — so Newsweek's
                # bar is INK (--text-primary) and peers recede in warm gray
                # (--text-muted). Literals because Vega can't read CSS vars.
                _nw_color   = "#1f1e19"
                _peer_color = "#8c887b"
                col_a2cr, col_refresh = st.columns(2)
                if "avg_ads_to_content_ratio_pct" in view.columns and not view["avg_ads_to_content_ratio_pct"].dropna().empty:
                    with col_a2cr:
                        st.subheader("A2CR by publisher (lower is better)")
                        _src = view.dropna(subset=["avg_ads_to_content_ratio_pct"]).copy()
                        chart = (
                            alt.Chart(_src)
                            .mark_bar()
                            .encode(
                                x=alt.X("avg_ads_to_content_ratio_pct:Q", title="A2CR (%)"),
                                y=alt.Y("domain:N", sort="-x", title=None,
                                        axis=alt.Axis(labelLimit=200)),
                                color=alt.condition(
                                    "datum._is_newsweek",
                                    alt.value(_nw_color),
                                    alt.value(_peer_color),
                                ),
                                tooltip=[
                                    alt.Tooltip("name:N", title="Publisher"),
                                    alt.Tooltip("domain:N", title="Domain"),
                                    alt.Tooltip("avg_ads_to_content_ratio_pct:Q",
                                                title="A2CR %", format=".2f"),
                                ],
                            ).properties(height=320)
                        )
                        st.altair_chart(chart, use_container_width=True)

                if "avg_ad_refresh" in view.columns and not view["avg_ad_refresh"].dropna().empty:
                    with col_refresh:
                        st.subheader("Ad refresh by publisher (higher is slower)")
                        _src = view.dropna(subset=["avg_ad_refresh"]).copy()
                        chart = (
                            alt.Chart(_src)
                            .mark_bar()
                            .encode(
                                x=alt.X("avg_ad_refresh:Q", title="Refresh (s)"),
                                y=alt.Y("domain:N", sort="-x", title=None,
                                        axis=alt.Axis(labelLimit=200)),
                                color=alt.condition(
                                    "datum._is_newsweek",
                                    alt.value(_nw_color),
                                    alt.value(_peer_color),
                                ),
                                tooltip=[
                                    alt.Tooltip("name:N", title="Publisher"),
                                    alt.Tooltip("domain:N", title="Domain"),
                                    alt.Tooltip("avg_ad_refresh:Q",
                                                title="Refresh (s)", format=".1f"),
                                ],
                            ).properties(height=320)
                        )
                        st.altair_chart(chart, use_container_width=True)

        # ── Ad systems ──────────────────────────────────────────────────
        with sub_sys:
            if sys_df.empty:
                st.info("No ad-system records yet.")
            else:
                st.caption(f"{len(sys_df):,} ad systems known to Sincera.")
                sys_search = st.text_input("Search ad systems", placeholder="Name or domain…",
                                           key="os_adsys_search")
                _view_sys = sys_df.copy()
                if sys_search:
                    _mask = (
                        _view_sys["name"].str.contains(sys_search, case=False, na=False)
                        | _view_sys["canonical_domain"].str.contains(sys_search, case=False, na=False)
                    )
                    _view_sys = _view_sys[_mask]

                st.dataframe(
                    _view_sys[[c for c in ["id", "name", "canonical_domain", "description",
                                           "image_url"] if c in _view_sys.columns]],
                    use_container_width=True, hide_index=True,
                    column_config={
                        "id":               st.column_config.NumberColumn("ID", format="localized"),
                        "name":             st.column_config.TextColumn("Name"),
                        "canonical_domain": st.column_config.TextColumn("Domain"),
                        "description":      st.column_config.TextColumn("Description"),
                        "image_url":        st.column_config.ImageColumn("Logo"),
                    },
                )

        # ── Prebid modules ──────────────────────────────────────────────
        with sub_mod:
            if mod_df.empty:
                st.info("No Prebid-module records yet.")
            else:
                cats = sorted(mod_df["module_category"].dropna().unique().tolist()) \
                    if "module_category" in mod_df.columns else []
                col_filter, col_search = st.columns([1, 2])
                with col_filter:
                    sel_cats = st.multiselect("Category", cats, key="os_mod_cat")
                with col_search:
                    mod_search = st.text_input("Search module name", placeholder="e.g. brightcom",
                                               key="os_mod_search")

                view_mod = mod_df.copy()
                if sel_cats:
                    view_mod = view_mod[view_mod["module_category"].isin(sel_cats)]
                if mod_search:
                    view_mod = view_mod[
                        view_mod["module_name"].str.contains(mod_search, case=False, na=False)
                    ]

                if "detected_count" in view_mod.columns:
                    view_mod = view_mod.sort_values("detected_count", ascending=False)

                st.dataframe(
                    view_mod,
                    use_container_width=True, hide_index=True,
                    column_config={
                        "id":              st.column_config.NumberColumn("ID", format="localized"),
                        "module_name":     st.column_config.TextColumn("Module"),
                        "module_category": st.column_config.TextColumn("Category"),
                        "adsystem_id":     st.column_config.NumberColumn("Ad system ID", format="localized"),
                        "detected_count":  st.column_config.NumberColumn("Detections (90d)", format="localized"),
                        "_pulled_at":      None,
                    },
                )

                if "detected_count" in view_mod.columns and not view_mod.empty:
                    st.subheader("Top 20 detected modules")
                    top = view_mod.head(20).copy()
                    chart = (
                        alt.Chart(top).mark_bar(color="#4b62e0").encode(  # --viz-1
                            x=alt.X("detected_count:Q", title="Detections (last 90d)"),
                            y=alt.Y("module_name:N", sort="-x", title=None,
                                    axis=alt.Axis(labelLimit=240)),
                            tooltip=[
                                "module_name", "module_category",
                                alt.Tooltip("detected_count:Q", format=","),
                            ],
                        ).properties(height=420)
                    )
                    st.altair_chart(chart, use_container_width=True)

if st.session_state.active_view == "campaigns":
    # ── Table 1: Direct campaigns from GAM ──────────────────────────────
    try:
        gam_df = load("gam_campaigns")
    except Exception:
        gam_df = pd.DataFrame()
        st.info("No GAM data yet. The gam_campaigns table will be created on the next scheduled refresh.")
    # Drop excluded test/QA orders at the source so nothing from them reaches
    # the Direct table, the KPI rollups, or the DV joins (per Roger 2026-06-15).
    if not gam_df.empty and "order_id" in gam_df.columns:
        gam_df = gam_df[~gam_df["order_id"].astype(str).isin(_EXCLUDED_ORDER_IDS)]

    # DV Attention — daily Pinnacle CSV emailed to newsweek@agentmail.to,
    # parsed by refresh_dv_attention() into the dv_attention table. We
    # average the Attention Index per line_item_name across whatever
    # window the latest email covered (typically last 7 days) and join
    # into gam_df for the per-row "Attention" cell. Missing lines render
    # as "—" via the _attention_html helper.
    # Aggregation + join-column choice live in dashboard_logic (tested),
    # **memoized** in _dv_attention_aggregates(). As of 2026-06-15 the source is
    # SERVER-SIDE PRE-AGGREGATED (_load_dv_attention_agg): the per-(LI,date) and
    # per-(order,date) AVG frames feed the columns + drawer sparklines; the tiny
    # per-date frame (dv_df, the publisher-wide Attention KPI tile's source
    # below) replaces the raw ~24k-row load — ~42% fewer rows, raw frame no
    # longer held. Dicts default empty (cells render "—") when DV is absent;
    # _dv_li_col falls back to the name join (choose_join_col).
    _, _, dv_df = _load_dv_attention_agg()
    (_dv_li_col, _dv_by_li, _dv_prior_by_li, _attn_series_by_li,
     _dv_by_order, _dv_prior_by_order) = _dv_attention_aggregates()

    # DV IVT — daily Pinnacle CSV emailed to newsweek@agentmail.to with
    # subject "Unified Analytics Report: IVT". Polled by refresh_dv_ivt()
    # into the dv_ivt table. As of 2026-05-24 the export includes a
    # `Monitored Ads` impression count, so we compute a TRUE
    # impression-weighted IVT% per the MRC standard:
    #
    #   IVT % = Σ Monitored Ads (Fraud rows of this type) /
    #           Σ Monitored Ads (all rows) × 100
    #
    # (Earlier versions used a day-prevalence proxy because the export
    # didn't include impression counts. Pre-history of why this matters
    # for buyer conversations is in project_yield_dashboard_dv_attention.md.)
    #
    # Split SIVT / GIVT per MRC standard:
    #   - GIVT = self-identifying invalid (declared bots, known DC IPs).
    #            Unambiguously bad.
    #   - SIVT = sophisticated (Data Center, Bot Fraud, Hijacked
    #            Devices, Emulator, App/Site Fraud, Injected Ads,
    #            Laundering). Some sub-categories can be benign — Data
    #            Center includes Alexa/Siri/SSR. The 8-way sub-category
    #            breakdown isn't in the current export.
    # MRC impression-weighted SIVT/GIVT share + join-column choice live in
    # dashboard_logic (tested), **memoized** in _dv_ivt_aggregates(). Source is
    # SERVER-SIDE PRE-AGGREGATED (_load_dv_ivt_agg): per (LI,order,date,validity)
    # SUM(monitored_ads). Sums compose, so the one frame is exact for the
    # aggregates AND the publisher-wide SIVT/GIVT KPI recompute below (ivt_df).
    # ~42% fewer rows than the raw ~44k-row load. Dicts default empty ("—") when
    # dv_ivt is absent.
    ivt_df = _load_dv_ivt_agg()
    (_ivt_li_col, _sivt_by_li, _sivt_prior_by_li, _givt_by_li, _givt_prior_by_li,
     _sivt_series_by_li, _givt_series_by_li,
     _sivt_by_order, _sivt_prior_by_order, _givt_by_order, _givt_prior_by_order) = _dv_ivt_aggregates()

    # TTD Luckyland acquisition report — polled from agentmail by refresh_ttd().
    # Degrades silently to empty frame when the table doesn't exist yet.
    try:
        _ttd_df = load("ttd_luckyland")
    except Exception:
        _ttd_df = pd.DataFrame()
    _ttd_summary = dl.ttd_cpa_summary(_ttd_df)

    if gam_df.empty:
        st.info("No GAM data yet. Run refresh_cache.py to populate gam_campaigns.")
    else:
        gam_df = gam_df.copy()

        # Source the per-LI max video creative duration (consumed further
        # down, after ad_format exists, to recategorize long preroll).
        #
        # Source priority:
        #   1. video_ad_duration column from gam_campaigns (canonical, comes
        #      straight from GAM's VIDEO_AD_DURATION report dimension).
        #   2. SOAP creative+LICA join (fallback for rows pulled before the
        #      report-API dimension landed; also a backup if VIDEO_AD_DURATION
        #      isn't populated for a given LI). In practice this is the live
        #      path: Newsweek's video is 3rd-party-served, so the report
        #      dimension comes back null across the board.
        if "video_ad_duration" in gam_df.columns:
            gam_df["_creative_max_dur"] = pd.to_numeric(
                gam_df["video_ad_duration"], errors="coerce"
            )
        # SOAP fallback — only fires when video_ad_duration is missing or null.
        # Uses the pre-aggregated _load_li_max_duration() (server-side
        # GROUP BY) instead of pulling 183K LICA + 12K creative rows
        # client-side.
        if "_creative_max_dur" not in gam_df.columns \
           or gam_df["_creative_max_dur"].isna().all():
            _max_dur = _load_li_max_duration()
            if not _max_dur.empty and "line_item_id" in gam_df.columns:
                gam_df["line_item_id"] = gam_df["line_item_id"].astype(str)
                gam_df = gam_df.merge(_max_dur, on="line_item_id", how="left",
                                      suffixes=("", "_soap"))
                if "_creative_max_dur_soap" in gam_df.columns:
                    gam_df["_creative_max_dur"] = gam_df["_creative_max_dur"].fillna(
                        gam_df["_creative_max_dur_soap"]
                    )
                    gam_df = gam_df.drop(columns=["_creative_max_dur_soap"])

        _incl_patterns = _cfg.get("included_order_patterns", ["Newsweek_Direct%"])
        _prefixes = [p.rstrip("%") for p in _incl_patterns if p]
        _order_populated = gam_df["order_name"].notna() & (gam_df["order_name"] != "")
        if _prefixes:
            _match_order = pd.Series(False, index=gam_df.index)
            _match_li = pd.Series(False, index=gam_df.index)
            for _pfx in _prefixes:
                _match_order |= _order_populated & gam_df["order_name"].str.startswith(_pfx, na=False)
                _match_li |= (~_order_populated) & gam_df["line_item_name"].str.startswith(_pfx, na=False)
            gam_df = gam_df[_match_order | _match_li]

        for datecol in ("start_date", "end_date"):
            if datecol in gam_df.columns:
                gam_df[datecol] = pd.to_datetime(gam_df[datecol], errors="coerce").dt.date

        for numcol in ("pacing_pct", "impressions_delivered", "impressions_1d", "lifetime_impressions_delivered", "impressions_goal", "cpm_rate",
                       "ad_server_cpm_and_cpc_revenue", "ad_server_ctr",
                       "ad_server_active_view_viewable_impressions_rate", "vcr",
                       "video_interaction_video_starts", "video_interaction_video_completions"):
            if numcol in gam_df.columns:
                gam_df[numcol] = pd.to_numeric(gam_df[numcol], errors="coerce")

        # Compute VCR from raw video columns (completions / starts × 100)
        if "video_interaction_video_starts" in gam_df.columns and \
                "video_interaction_video_completions" in gam_df.columns:
            _starts = gam_df["video_interaction_video_starts"]
            _completions = gam_df["video_interaction_video_completions"]
            gam_df["vcr"] = (_completions / _starts * 100).where(_starts > 0)

        # Normalize salesperson in place so "Seller" shows short name regardless of
        # which column the settings point to (salesperson or seller_ae).
        # Name-token parsing lives in dashboard_logic (tested).
        if "salesperson" in gam_df.columns:
            gam_df["salesperson"] = gam_df["salesperson"].apply(dl.parse_gam_salesperson)

        _parsed_sp = gam_df["salesperson"] if "salesperson" in gam_df.columns else pd.Series(dtype=str)
        _null_mask = _parsed_sp.isna()

        _regex_seller = (
            gam_df["order_name"].str.extract(dl.AE_TOKEN_RE, expand=False).map(AE_NAMES)
        )
        _li_seller = (
            gam_df["line_item_name"].str.extract(dl.AE_TOKEN_RE, expand=False).map(AE_NAMES)
        )
        gam_df["seller_ae"] = _parsed_sp.where(~_null_mask, _regex_seller.fillna(_li_seller))

        # Extract advertiser (index 7) and campaign (index 8) from line item name.
        # Replace hyphens with spaces so the displayed Advertiser / Campaign
        # columns read as "Ford Motor Company" / "Always On" rather than the
        # hyphenated token form used inside the line-item-name convention.
        gam_df["advertiser"]    = gam_df["line_item_name"].apply(dl.li_part, idx=7).str.replace("-", " ", regex=False)
        gam_df["campaign_name"] = gam_df["line_item_name"].apply(dl.li_part, idx=8).str.replace("-", " ", regex=False)
        # Canonical format per line (dashboard_logic.derive_format): name
        # keywords beat GAM's INVENTORY_FORMAT_NAME — the API flattens
        # interstitials / FITO / Centerstage / Apple News into "Banner" —
        # then the API value (authoritative for display/video), then the
        # position-10 name token. User format_aliases re-route any outcome;
        # junk resolves to NA (out of the filter, fallback benchmarks).
        _format_aliases = _cfg.get("format_aliases") or {}
        _api_fmt_col = (gam_df["inventory_format_name"]
                        if "inventory_format_name" in gam_df.columns
                        else pd.Series([None] * len(gam_df), index=gam_df.index))
        gam_df["ad_format"] = [
            dl.derive_format(_a, _n, _format_aliases)
            for _a, _n in zip(_api_fmt_col, gam_df["line_item_name"])
        ]
        _team_map = _cfg.get("team_names", {"USA": "USA", "INTL": "International"})
        gam_df["team"] = (
            gam_df["line_item_name"]
            .str.extract(dl.TEAM_TOKEN_RE, expand=False)
            .map(_team_map)
        )
        for _col in ("advertiser", "campaign_name", "ad_format", "seller_ae", "team"):
            if _col in gam_df.columns:
                gam_df[_col] = gam_df[_col].replace({None: pd.NA, "None": pd.NA, "": pd.NA})

        # The >30s preroll distinction is a BENCHMARK band, not a format:
        # the Format filter and columns show plain "Video" (it's just one
        # video format — Roger, 2026-06-12), while _bench_format carries
        # "Video Preroll >30s" for threshold lookups so long-form video
        # keeps grading against its own VCR line (the #156 fix). MUST run
        # after ad_format is derived — the column doesn't exist earlier.
        if "_creative_max_dur" in gam_df.columns:
            gam_df["_bench_format"] = gam_df.apply(
                lambda row: dl.bump_video_format(
                    row.get("ad_format"), row.get("_creative_max_dur")),
                axis=1,
            )
        else:
            gam_df["_bench_format"] = gam_df["ad_format"]

        # Manual long-preroll override — applied AFTER the duration-based
        # auto-detection so user-curated rules win. Useful for Newsweek's
        # 3rd-party tag setups (Innovid / DCM JS loaders) where neither
        # the GAM Creative API nor the VAST URL exposes duration.
        _lp_rules = _cfg.get("long_preroll_lines") or []
        if _lp_rules:
            _lp_mask = gam_df.apply(
                lambda row: dl.matches_long_preroll(row, _lp_rules), axis=1)
            if _lp_mask.any():
                gam_df.loc[_lp_mask, "_bench_format"] = dl.LONG_PREROLL_FORMAT

        # Load Pubmatic sellers so they appear in the shared filter
        try:
            _pmp_sellers_df = load("pubmatic_deals")
            _pmp_sellers = (
                _pmp_sellers_df["deal"]
                .str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                .map(AE_NAMES)
                .dropna()
                .unique()
            ) if not _pmp_sellers_df.empty and "deal" in _pmp_sellers_df.columns else []
        except Exception:
            _pmp_sellers = []

        all_sellers = sorted(set(gam_df["seller_ae"].dropna().unique()) | set(_pmp_sellers))

        # ── Filter row: compact, small uppercase labels above each select.
        # Account Manager filter — derives the AM for each line by looking
        # up its seller_ae in the Configure → Section 4 → Account Manager
        # mapping. Multiselect so you can scope to one AM or compare across
        # several. Lines whose AE isn't in the map fall into "Unassigned"
        # (still selectable, useful for spotting AEs missing from Configure).
        #
        # IMPORTANT: gam_df["seller_ae"] holds the FULL display name (e.g.
        # "Theresa Hern"), not the code ("THern") — see line ~1924 where it
        # gets run through AE_NAMES.map() before storage. The AM map in
        # settings is keyed by code (matching ae_names structure), so we
        # build a name-keyed lookup here for the filter join. Aliases that
        # share a full name (THern / Thern / THearn → Theresa Hern) collapse
        # into one entry — assigning ANY one alias to JC/Jen covers them all.
        _am_map = _cfg.get("account_managers", {}) or {}
        _ae_names_map = _cfg.get("ae_names", {}) or {}
        _am_by_full_name = {
            _ae_names_map.get(_code, _code): _am
            for _code, _am in _am_map.items()
            if _am
        }
        all_ams = sorted({v for v in _am_by_full_name.values() if v})

        def _apply_am_filter(df, col="seller_ae"):
            """Apply the top-of-page Account Manager multiselect to any PMP /
            Magnite / Pubmatic / Direct dataframe whose `col` holds the full
            display name (e.g. "Theresa Hern"). Returns df unchanged when no
            AMs are selected, the column is missing, or the df is empty.
            Aliases collapse via _am_by_full_name. Unmapped or null AEs map
            to "Unassigned" so they're filterable explicitly."""
            if not selected_ams or df is None or df.empty or col not in df.columns:
                return df
            _row_am = (df[col].fillna("")
                       .map(_am_by_full_name)
                       .fillna("Unassigned")
                       .replace("", "Unassigned"))
            return df[_row_am.isin(selected_ams)]
        # Detect whether any line has a seller_ae missing from the
        # full-name keyed map → expose "Unassigned" as a filter option
        # only when relevant.
        _has_unmapped = bool(
            "seller_ae" in gam_df.columns
            and gam_df["seller_ae"].dropna().apply(lambda s: s not in _am_by_full_name).any()
        )
        am_opts = all_ams + (["Unassigned"] if _has_unmapped else [])

        # ── Campaigns filters: one "Filters" popover trigger + removable
        # active-filter chips, replacing the 6-column dropdown row that buried
        # the data below the fold on mobile. The six controls live inside the
        # popover; whatever is applied surfaces as a chip beside the trigger
        # and clears on click. Widget keys are unchanged, so the filtering
        # logic below is untouched.
        advertiser_opts = sorted(gam_df["advertiser"].dropna().unique())
        format_opts = sorted(gam_df["ad_format"].dropna().unique())
        status_opts = sorted(gam_df["status"].dropna().unique()) if "status" in gam_df.columns else []
        team_opts = sorted(gam_df["team"].dropna().unique())
        _cfg_defaults = _cfg.get("default_statuses", ["Delivering", "Upcoming"])
        _status_defaults = [s for s in _cfg_defaults if s in status_opts]
        _STATUS_VER = "2"
        if st.session_state.get("_status_ver") != _STATUS_VER and _status_defaults:
            st.session_state["gam_status_filter"] = _status_defaults
            st.session_state["_status_ver"] = _STATUS_VER

        # Read current selections from state so the chips + count reflect the
        # latest run (defaults fill in on first load before the widgets exist).
        def _ms_summary(vals):
            return str(vals[0]) if len(vals) == 1 else f"{vals[0]} +{len(vals) - 1}"
        _active_chips = []  # (state_key, empty_value, chip_text)
        _sel_seller = st.session_state.get("seller_select", "All")
        if _sel_seller and _sel_seller != "All":
            _active_chips.append(("seller_select", "All", f"Seller: {_sel_seller}"))
        for _key, _lbl in (("gam_advertiser_filter", "Advertiser"),
                           ("gam_format_filter", "Format"),
                           ("gam_status_filter", "Status"),
                           ("gam_team_filter", "Team"),
                           ("gam_am_filter", "Manager")):
            _default = _status_defaults if _key == "gam_status_filter" else []
            _vals = st.session_state.get(_key, _default)
            if _vals:
                _active_chips.append((_key, [], f"{_lbl}: {_ms_summary(_vals)}"))
        _n_active = len(_active_chips)

        def _clear_filter(state_key, empty_value):
            st.session_state[state_key] = empty_value

        def _clear_all_filters():
            st.session_state["seller_select"] = "All"
            for _k in ("gam_advertiser_filter", "gam_format_filter",
                       "gam_status_filter", "gam_team_filter", "gam_am_filter"):
                st.session_state[_k] = []

        with st.container(horizontal=True, key="nw_filter_bar"):
            _filters_pop = st.popover(
                "Filters" if not _n_active else f"Filters · {_n_active}",
                icon=":material/tune:",
            )
            for _ck, _empty, _txt in _active_chips:
                st.button(_txt, key=f"nw_chip_{_ck}",
                          icon=":material/close:", icon_position="right",
                          on_click=_clear_filter, args=(_ck, _empty))

        with _filters_pop:
            st.markdown('<div class="nw-filter-label">Seller</div>', unsafe_allow_html=True)
            selected_seller = st.selectbox(
                "Seller",
                options=["All"] + all_sellers,
                key="seller_select",
                label_visibility="collapsed",
            )
            st.markdown('<div class="nw-filter-label">Advertiser</div>', unsafe_allow_html=True)
            selected_advertisers = st.multiselect(
                "Advertiser",
                options=advertiser_opts,
                key="gam_advertiser_filter",
                label_visibility="collapsed",
            )
            st.markdown('<div class="nw-filter-label">Format</div>', unsafe_allow_html=True)
            selected_formats = st.multiselect(
                "Format",
                options=format_opts,
                key="gam_format_filter",
                label_visibility="collapsed",
            )
            st.markdown('<div class="nw-filter-label">Status</div>', unsafe_allow_html=True)
            selected_statuses = st.multiselect(
                "Status",
                options=status_opts,
                default=_status_defaults,
                key="gam_status_filter",
                label_visibility="collapsed",
            )
            st.markdown('<div class="nw-filter-label">Team</div>', unsafe_allow_html=True)
            selected_teams = st.multiselect(
                "Team",
                options=team_opts,
                key="gam_team_filter",
                label_visibility="collapsed",
            )
            st.markdown('<div class="nw-filter-label">Account Manager</div>', unsafe_allow_html=True)
            selected_ams = st.multiselect(
                "Account Manager",
                options=am_opts,
                key="gam_am_filter",
                label_visibility="collapsed",
            )
            if _n_active:
                st.button("Clear all filters", key="nw_clear_all_filters",
                          type="tertiary", icon=":material/close:",
                          on_click=_clear_all_filters)

        view_gam = gam_df if selected_seller == "All" else gam_df[gam_df["seller_ae"] == selected_seller].copy()
        if selected_advertisers:
            view_gam = view_gam[view_gam["advertiser"].isin(selected_advertisers)]
        if selected_formats:
            view_gam = view_gam[view_gam["ad_format"].isin(selected_formats)]
        if selected_statuses:
            view_gam = view_gam[view_gam["status"].isin(selected_statuses)]
        if selected_teams:
            view_gam = view_gam[view_gam["team"].isin(selected_teams)]
        view_gam = _apply_am_filter(view_gam, "seller_ae")

        # Reset Direct-table pagination whenever the filter set changes, so a
        # narrower filter can't strand you on a now-out-of-range page (mirror
        # of the PMP table's _pmp_filter_sig guard).
        _direct_filter_sig = str((
            selected_seller, sorted(selected_advertisers), sorted(selected_formats),
            sorted(selected_statuses), sorted(selected_teams), sorted(selected_ams),
        ))
        if st.session_state.get("_direct_filter_sig") != _direct_filter_sig:
            st.session_state["direct_page"] = 0
            st.session_state["_direct_filter_sig"] = _direct_filter_sig

        if view_gam.empty:
            st.info("No campaigns found for the selected filters.")
        else:
            # Header timestamp reflects when gam_campaigns was last refreshed
            # (gam_df["_pulled_at"]), NOT the current wall-clock time. Falls
            # back to the cached refresh stamp when the column isn't present.
            _ts_iso = (gam_df["_pulled_at"].max()
                       if "_pulled_at" in gam_df.columns else None)
            _ts_str = _fmt_header_freshness(_ts_iso) or _fmt_header_freshness(
                _last_data_refresh_iso()
            )
            _n_lines = len(view_gam)
            if _ts_str:
                _render_header_right(f"🕐 {_ts_str} · {_n_lines:,} line items")
            else:
                _render_header_right(f"🕐 {_n_lines:,} line items")

            # ── Summary numbers (used by both banners and KPI strip).
            total_impr = view_gam["lifetime_impressions_delivered"].sum() if "lifetime_impressions_delivered" in view_gam else 0
            total_rev  = view_gam["ad_server_cpm_and_cpc_revenue"].sum() if "ad_server_cpm_and_cpc_revenue" in view_gam else 0
            avg_pacing = view_gam["pacing_pct"].mean() if "pacing_pct" in view_gam else None

            # Viewability — recompute from lifetime counts when available so it
            # matches the cell values (which were swapped to lifetime in #22).
            if "lifetime_viewable_imps" in view_gam.columns and "lifetime_measurable_imps" in view_gam.columns:
                _vw = pd.to_numeric(view_gam["lifetime_viewable_imps"], errors="coerce").sum()
                _mb = pd.to_numeric(view_gam["lifetime_measurable_imps"], errors="coerce").sum()
                avg_viewability = (_vw / _mb * 100) if _mb else None
            else:
                avg_viewability = (
                    view_gam["ad_server_active_view_viewable_impressions_rate"].mean() * 100
                    if "ad_server_active_view_viewable_impressions_rate" in view_gam else None
                )

            # VCR — recompute impression-weighted (Σ completes / Σ starts)
            # for consistency with the sparkline and the Viewability tile's
            # pattern. Was previously a per-line mean (each line weighted
            # equally), which over-weighted small lines. Falls back to the
            # per-line mean when the lifetime video columns aren't present
            # (older gam_campaigns schema before #25).
            if "lifetime_video_starts" in view_gam.columns and "lifetime_video_completes" in view_gam.columns:
                _vs = pd.to_numeric(view_gam["lifetime_video_starts"],    errors="coerce").sum()
                _vc = pd.to_numeric(view_gam["lifetime_video_completes"], errors="coerce").sum()
                avg_vcr = (_vc / _vs * 100) if _vs else None
            else:
                avg_vcr = view_gam["vcr"].mean() if "vcr" in view_gam else None
            _video_li_count = 0
            if "ad_format" in view_gam.columns:
                _video_li_count = view_gam["ad_format"].astype("string").str.lower().str.contains("video", na=False).sum()

            if "lifetime_clicks" in view_gam.columns and "lifetime_impressions_delivered" in view_gam.columns:
                _clk = pd.to_numeric(view_gam["lifetime_clicks"], errors="coerce").sum()
                _imp = pd.to_numeric(view_gam["lifetime_impressions_delivered"], errors="coerce").sum()
                avg_ctr = (_clk / _imp * 100) if _imp else None
            else:
                avg_ctr = (
                    view_gam["ad_server_ctr"].mean()
                    if "ad_server_ctr" in view_gam.columns else None
                )

            # ── Targets — all sourced from Configure → Benchmarks by format
            # (Display = viewability + CTR fallbacks; pacing_target_pct
            # drives the pacing-band ratios used by banners + drawer +
            # AirTable classification). No hardcoded percentages remain.
            _pacing_target = float(_cfg.get("pacing_target_pct", 100.0) or 100.0)
            # Pacing bands expressed as ratios of the target so changing
            # pacing_target_pct shifts them coherently. Defaults at 100%
            # target preserve the prior 75 / 90 / 110 absolute thresholds.
            _pacing_critical  = _pacing_target * 0.75
            _pacing_warn_low  = _pacing_target * 0.90
            _pacing_warn_high = _pacing_target * 1.10
            # Display viewability + CTR benchmarks for cross-cutting use
            # (KPI sparkline targets, banner anomaly, AirTable severity).
            _bench_display = (_cfg.get("benchmarks_by_format") or {}).get("Display", {}) or {}
            _view_bench    = float(_bench_display.get("viewability_pct") or 70.0)
            _ctr_bench     = (_bench_display.get("ctr_pct"))
            _ctr_bench     = float(_ctr_bench) if _ctr_bench is not None else None

            # ── Exception banners — list the specific offenders, not just counts.
            def _short_advertiser(name):
                if not isinstance(name, str): return "—"
                # Take a recognizable mid-name token (advertiser slot, position 7).
                parts = name.split("_")
                for idx in (7, 6, 8, 2):
                    if len(parts) > idx and parts[idx] and parts[idx] not in ("NA", "N/A"):
                        return parts[idx].replace("-", " ")
                return parts[0]

            _under_rows  = (view_gam[view_gam["pacing_pct"] < _pacing_critical][["line_item_name", "pacing_pct"]].head(4)
                            if "pacing_pct" in view_gam.columns else pd.DataFrame())
            _over_rows   = (view_gam[view_gam["pacing_pct"] > _pacing_warn_high][["line_item_name", "pacing_pct"]].head(6)
                            if "pacing_pct" in view_gam.columns else pd.DataFrame())
            # Viewability anomaly threshold sources from the configured
            # benchmark (Configure → Section 3 → Benchmarks by format →
            # Display viewability). Previously hardcoded at 40 — confusing
            # when users set the benchmark to 70 and wondered why the
            # banner referenced 40.
            _vw_target = _view_bench
            _vw_anom_rows = pd.DataFrame()
            if "lifetime_viewable_imps" in view_gam.columns and "lifetime_measurable_imps" in view_gam.columns:
                _v_rate = pd.to_numeric(view_gam["lifetime_viewable_imps"], errors="coerce") / \
                          pd.to_numeric(view_gam["lifetime_measurable_imps"], errors="coerce") * 100
                _vw_anom_rows = (view_gam.assign(_v=_v_rate)
                                 .loc[_v_rate < _vw_target, ["line_item_name", "_v"]].head(4))

            def _under_detail(rows):
                if rows.empty: return f"All line items at or above {_pacing_critical:g}% pacing"
                advs = rows["line_item_name"].apply(_short_advertiser).unique().tolist()
                paces = " &amp; ".join(f"{p:.0f}%" for p in rows["pacing_pct"].head(2))
                return f"{advs[0]} · {paces} pace" if len(advs) == 1 else f"{', '.join(advs[:3])}"
            def _over_detail(rows):
                if rows.empty: return "No overpacers"
                advs = rows["line_item_name"].apply(_short_advertiser).unique().tolist()
                return ", ".join(advs[:4])
            def _vw_detail(rows):
                if rows.empty:
                    return f"All line items at or above {_vw_target:g}% viewability"
                first = rows.iloc[0]
                return f"{_short_advertiser(first['line_item_name'])} · {first['_v']:.1f}% viewable"

            def _na_esc(s):
                return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            # ── "Ending soon · at risk" card — lines whose flight ends within
            # the configured window AND that are projected, at the current
            # daily pace, to finish under goal. Decision logic + tests live in
            # dashboard_logic (landing_projection / landing_at_risk); this
            # block only builds the rows. Sits above Needs-attention so a
            # looming under-delivery (the Cartier problem) is the first thing
            # seen. Worst projected % first.
            _lr_window = int(_cfg.get("landing_window_days", 7) or 7)
            _lr_thresh = float(_cfg.get("landing_threshold_pct", 100.0) or 100.0)
            _lr_today = date.today()
            _lr_items = []
            if {"impressions_goal", "lifetime_impressions_delivered", "end_date"}.issubset(view_gam.columns):
                for _, _lr in view_gam.iterrows():
                    _ed = pd.to_datetime(_lr.get("end_date"), errors="coerce")
                    if pd.isna(_ed):
                        continue
                    _days = (_ed.date() - _lr_today).days
                    _proj = dl.landing_projection(
                        _lr.get("impressions_goal"),
                        _lr.get("lifetime_impressions_delivered"),
                        _lr.get("impressions_1d"),
                        _days)
                    if _proj is None or not dl.landing_at_risk(
                            _days, _proj["projected_pct"], _lr_window, _lr_thresh):
                        continue
                    _goal = float(_lr.get("impressions_goal"))
                    _deliv = pd.to_numeric(_lr.get("lifetime_impressions_delivered"), errors="coerce")
                    _lr_items.append({
                        "name": _lr.get("line_item_name"),
                        "days": _days,
                        "end": _ed.date(),
                        "pct_done": (float(_deliv) / _goal * 100) if pd.notna(_deliv) and _goal else 0.0,
                        "proj_pct": _proj["projected_pct"],
                        "short": _proj["short"],
                    })
            _lr_items.sort(key=lambda d: d["proj_pct"])

            def _lr_short_fmt(n):
                n = float(n)
                if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
                if n >= 1_000:     return f"{n/1_000:.0f}k"
                return f"{int(n):,}"

            def _lr_rows_html(items):
                cells = []
                for it in items:
                    sev = "sev-red" if it["proj_pct"] < 90 else "sev-amber"
                    full = dl.line_item_display_name(it["name"])
                    adv, camp = (full.split(" — ", 1) + [""])[:2] if " — " in full else (full, "")
                    camp_html = f'<span class="camp">{_na_esc(camp)}</span>' if camp else ""
                    proj = max(min(it["proj_pct"], 100.0), 0.0)
                    done = max(min(it["pct_done"], 100.0), 0.0)
                    end_s = it["end"].strftime("%b %-d")
                    days_s = "ends today" if it["days"] == 0 else (
                        "1 day left" if it["days"] == 1 else f'{it["days"]} days left')
                    cells.append(
                        f'<div class="nw-na-srow {sev}">'
                        f'<span class="nm"><span class="adv">{_na_esc(adv)}</span>{camp_html}'
                        f'<span class="nw-lr-meta">{days_s} · ends {end_s} · {it["pct_done"]:.0f}% delivered</span>'
                        f'<span class="nw-lr-bar"><span class="nw-lr-proj" style="width:{proj:.0f}%"></span>'
                        f'<span class="nw-lr-done" style="width:{done:.0f}%"></span></span>'
                        f'</span>'
                        f'<span class="pct">{it["proj_pct"]:.0f}%'
                        f'<span class="nw-lr-short">~{_lr_short_fmt(it["short"])} short</span></span>'
                        f'</div>')
                return "".join(cells)

            # Ending-soon is folded into the Needs-attention card as its
            # first (most severe) category band — revenue/time risk leads.
            # _lr_n / _lr_worst / _lr_subrows feed the _na_row build below.
            _lr_n = len(_lr_items)
            _lr_worst = ("sev-red" if any(i["proj_pct"] < 90 for i in _lr_items)
                         else "sev-amber") if _lr_items else "sev-amber"
            _lr_subrows = _lr_rows_html(_lr_items) if _lr_items else ""
            def _lr_detail(items):
                if not items:
                    return "All current lines on track to finish"
                w = items[0]  # already sorted worst projected-% first
                adv = dl.line_item_display_name(w["name"]).split(" — ", 1)[0]
                return f"{_na_esc(adv)} · proj {w['proj_pct']:.0f}%"

            # ── "Needs attention" panel: one card, a row per alert category.
            # Categories with offenders render as a native <details> accordion
            # — tap the row to reveal the specific line items inline (worst
            # first, severity-tinted bar + value); browser-native toggle, no
            # Streamlit rerun. Clear categories render as a static sev-ok row.
            # Counts keep the existing head(4)/head(6) display cap.
            # (_na_esc defined above, with the landing-risk card.)

            def _na_subrows(rows, sev, metric_col, fmt):
                # Two-tier identifiable label: advertiser (bold) over the muted
                # campaign — the same `Advertiser — Campaign` name the table
                # shows, split so sibling LIs (3 Pateks, 4 GMCs) read distinctly
                # and you can find the exact row. Metric is colored text, right.
                cells = []
                for _, _r in rows.iterrows():
                    _val = float(_r[metric_col])
                    _full = dl.line_item_display_name(_r["line_item_name"])
                    _adv, _camp = (_full.split(" — ", 1) + [""])[:2] if " — " in _full else (_full, "")
                    _camp_html = f'<span class="camp">{_na_esc(_camp)}</span>' if _camp else ""
                    cells.append(
                        f'<div class="nw-na-srow {sev}">'
                        f'<span class="nm"><span class="adv">{_na_esc(_adv)}</span>{_camp_html}</span>'
                        f'<span class="pct">{fmt(_val)}</span></div>'
                    )
                return "".join(cells)

            def _na_row(n, sev, label, detail, subrows_html):
                if not n:
                    return ('<div class="nw-na-row sev-ok"><div class="nw-na-static">'
                            '<span class="nw-na-dot"></span><span class="nw-na-n">✓</span>'
                            f'<span class="nw-na-l">{label}</span>'
                            f'<span class="nw-na-d">{detail}</span></div></div>')
                return (f'<details class="nw-na-row {sev}">'
                        '<summary><span class="nw-na-dot"></span>'
                        f'<span class="nw-na-n">{n}</span>'
                        f'<span class="nw-na-l">{label}</span>'
                        f'<span class="nw-na-d">{detail}</span>'
                        '<span class="nw-na-chev">&rsaquo;</span></summary>'
                        f'<div class="nw-na-sub">{subrows_html}</div></details>')

            _u_n, _o_n, _v_n = len(_under_rows), len(_over_rows), len(_vw_anom_rows)
            _na_total = _lr_n + _u_n + _o_n + _v_n
            _under_sub = _na_subrows(
                _under_rows.sort_values("pacing_pct"), "sev-red", "pacing_pct",
                lambda v: f"{v:.0f}%") if _u_n else ""
            _over_sub = _na_subrows(
                _over_rows.sort_values("pacing_pct", ascending=False), "sev-amber", "pacing_pct",
                lambda v: f"{v:.0f}%") if _o_n else ""
            _view_sub = _na_subrows(
                _vw_anom_rows.sort_values("_v"), "sev-amber", "_v",
                lambda v: f"{v:.1f}%") if _v_n else ""

            _na_head_cnt = f"{_na_total} flagged" if _na_total else "All clear"
            # One unified card. Ending soon (revenue/time risk) leads as the
            # first, most-severe band, then the pacing/viewability quality
            # flags. The ending-soon band's subrows carry the landing detail
            # (projected-vs-goal bar + days left + ~Nk short) via _lr_rows_html;
            # it's only emitted when there are at-risk lines (no ✓ row for it).
            _na_cats = (
                (_na_row(_lr_n, _lr_worst, "Ending soon", _lr_detail(_lr_items), _lr_subrows)
                 if _lr_items else "")
                + _na_row(_u_n, "sev-red", "Underpacing", _under_detail(_under_rows), _under_sub)
                + _na_row(_o_n, "sev-amber", "Overpacing", _over_detail(_over_rows), _over_sub)
                + _na_row(_v_n, "sev-amber", "Viewability", _vw_detail(_vw_anom_rows), _view_sub)
            )
            # ── Cockpit (WIP): pin the unified triage card as a sticky right rail
            # on desktop (≥1025px). The main `.block-container` is capped at
            # max-width:1600px elsewhere; here we shrink it and reserve a fixed
            # right gutter (margin-right) so the fixed rail doesn't overlap it.
            # Reuses that rule's exact 3-selector group so this (emitted later)
            # wins on source order. ≤1024px nothing applies — the container stays
            # in normal flow above the KPIs, unchanged.
            st.markdown(
                "<style>@media (min-width:1025px){"
                ".stApp .main .block-container,"
                '.stApp [data-testid="stMain"] .block-container,'
                '.stApp [data-testid="stAppViewContainer"] .block-container{'
                "max-width:min(1320px,calc(100vw - 380px))!important;"
                "margin-left:auto!important;margin-right:360px!important;}"
                ".st-key-nw_campaigns_rail{position:fixed;top:120px;right:20px;width:320px;"
                "max-height:calc(100vh - 140px);overflow-y:auto;z-index:6;}"
                "}</style>",
                unsafe_allow_html=True,
            )
            _rail = st.container(key="nw_campaigns_rail")
            if _na_total:
                # Forced-open at ALL widths via `nw-na--always` (Roger, 2026-06):
                # the triage categories stay visible even on mobile rather than
                # collapsing to a header line (reverses the old mobile collapse).
                # The open card is the ~4 category rows, not a screenful, because
                # the per-category offender lists are auto-opened inline on
                # desktop/tablet (the `@media min-width:641` rules above) and
                # stay tap-to-expand on mobile. Chevron hidden + header
                # non-interactive (the `open` attribute + the `--always` CSS rule
                # keep it expanded).
                _rail.markdown(
                    '<details class="nw-na nw-na--always" open>'
                    '<summary class="nw-na-head"><span>Needs attention</span>'
                    f'<span class="cnt">{_na_head_cnt}</span>'
                    '<span class="nw-na-h-chev">&rsaquo;</span></summary>'
                    f'<div class="nw-na-body">{_na_cats}</div></details>',
                    unsafe_allow_html=True,
                )
            else:
                # All clear — static ✓ rows; nothing to collapse.
                _rail.markdown(
                    '<div class="nw-na">'
                    '<div class="nw-na-head"><span>Needs attention</span>'
                    f'<span class="cnt">{_na_head_cnt}</span></div>'
                    + _na_cats + '</div>',
                    unsafe_allow_html=True,
                )

            # ── KPI strip: nine tiles — serif number, target subtitle where
            # applicable, neutral full-width sparkline (Newsweek anatomy).
            def _fmt_money(v):
                if pd.isna(v): return "—"
                if abs(v) >= 1_000_000: return f"${v/1_000_000:.2f}M"
                if abs(v) >= 1_000:     return f"${v/1_000:.1f}K"
                return f"${v:,.2f}"
            def _fmt_count(v):
                if pd.isna(v) or v == 0: return "—" if pd.isna(v) else "0"
                if abs(v) >= 1_000_000: return f"{v/1_000_000:.2f}M"
                if abs(v) >= 1_000:     return f"{v/1_000:.1f}K"
                return f"{int(v):,}"

            # ── Sparkline helpers ─────────────────────────────────────────
            def _sparkline_svg(values, target=None, color="neutral", klass="kpi-spark",
                               uniform=False):
                """SVG sparkline. `values` is the 7-day series (oldest first).
                `target` optionally draws a dashed reference line. `color` keys
                into the token palette — per the Newsweek handoff, trend lines
                default to NEUTRAL ink (state lives in the delta text and the
                banded cells, never in the sparkline stroke). Colors ride on
                style= because SVG presentation attributes can't read var().
                `klass` controls outer sizing (default `kpi-spark` stretches
                across the tile; pass `""` for parent-controlled).

                Two geometry regimes (CLAUDE.md): default stretches to fill
                width (preserveAspectRatio="none") — right for the compact
                ~130px KPI tiles, where filling width edge-to-edge is the
                point. `uniform=True` scales PROPORTIONALLY (plain viewBox, no
                preserveAspectRatio; the caller's CSS sets width:100% +
                height:auto) for the drawer small multiples, whose ~370px
                panels otherwise crush the trend flat — the delivery-chart
                distortion in miniature. A wide viewBox keeps the rendered
                height compact under uniform scaling."""
                if not values:
                    return ""
                clean = [float(v) if (v is not None and not pd.isna(v)) else None for v in values]
                non_null = [v for v in clean if v is not None]
                if len(non_null) < 2:
                    return ""
                pool = non_null + ([float(target)] if target is not None else [])
                vmin, vmax = min(pool), max(pool)
                if vmax == vmin:
                    vmax = vmin + 1
                # Uniform mode: wide viewBox (height stays compact when it
                # scales proportionally) + an x-inset so the end dot doesn't
                # clip at the panel edge. Stretch mode keeps the flush 56×20.
                if uniform:
                    W, H, PAD, XPAD = 300, 34, 5, 6
                else:
                    W, H, PAD, XPAD = 56, 20, 2, 0
                n = len(clean)
                def _x(i): return (XPAD + i / (n - 1) * (W - 2 * XPAD)) if n > 1 else W / 2
                def _y(v): return H - PAD - (v - vmin) / (vmax - vmin) * (H - 2 * PAD)
                pts = " ".join(f"{_x(i):.1f},{_y(v):.1f}"
                               for i, v in enumerate(clean) if v is not None)
                palette = {
                    "neutral": "var(--text-secondary)",
                    "green":   "var(--state-positive)",
                    "amber":   "var(--state-warning)",
                    "red":     "var(--state-critical)",
                }
                stroke = palette.get(color, palette["neutral"])
                tline = ""
                if target is not None:
                    ty = _y(float(target))
                    tline = (f'<line x1="{XPAD}" y1="{ty:.1f}" x2="{W - XPAD}" y2="{ty:.1f}" '
                             f'style="stroke:var(--spark-ref)" stroke-width="0.75" '
                             f'stroke-dasharray="2 2" vector-effect="non-scaling-stroke"/>')
                last_i = max(i for i, v in enumerate(clean) if v is not None)
                # End marker is a zero-length round-capped stroke, NOT a <circle>:
                # in the stretch regime preserveAspectRatio="none" warps a
                # <circle> into a smeared ellipse. A non-scaling round cap stays
                # a true round dot in either regime, at any container width.
                dot = (f'<path d="M{_x(last_i):.1f} {_y(clean[last_i]):.1f}h0" '
                       f'fill="none" style="stroke:{stroke}" stroke-width="4" '
                       f'stroke-linecap="round" vector-effect="non-scaling-stroke"/>')
                class_attr = f' class="{klass}"' if klass else ""
                par = "" if uniform else ' preserveAspectRatio="none"'
                # Stretch regime pins the end dot at x=W (flush, XPAD=0), so its
                # round cap (4px non-scaling) pokes ~2px past the viewBox edge and
                # the SVG viewport clips it to a half-dot — the "cut-off dot" on the
                # Direct mobile card. Keep the line flush (the tiles want edge-to-
                # edge) but let the cap render into the adjacent margin. Uniform
                # already insets with XPAD, so it needs no overflow.
                ov = "" if uniform else ' style="overflow:visible"'
                return (f'<svg{class_attr} viewBox="0 0 {W} {H}"{par}{ov} '
                        f'xmlns="http://www.w3.org/2000/svg">{tline}'
                        f'<polyline points="{pts}" fill="none" style="stroke:{stroke}" '
                        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" '
                        f'vector-effect="non-scaling-stroke"/>'
                        f'{dot}</svg>')

            def _series_sum(prefix):
                """Sum per-day metric across LIs → [day7..day1] series.
                Returns None when all 7 cols aren't present yet."""
                cols = [f"{prefix}_{i}d" for i in range(7, 0, -1)]
                if not all(c in view_gam.columns for c in cols):
                    return None
                out = []
                for c in cols:
                    s = pd.to_numeric(view_gam[c], errors="coerce")
                    out.append(float(s.sum()) if pd.notna(s.sum()) else None)
                return out

            def _ratio_series(num_prefix, denom_prefix, scale=100.0):
                """Ratio of two daily sums × scale → series."""
                ns, ds = _series_sum(num_prefix), _series_sum(denom_prefix)
                if ns is None or ds is None:
                    return None
                return [(n / d * scale) if (d and n is not None) else None
                        for n, d in zip(ns, ds)]

            def _pacing_series():
                """Synthetic aggregate pacing trend: cumulative delivered (rolled
                back from lifetime by subtracting subsequent days) divided by
                expected cumulative (goal × elapsed-fraction at that day)."""
                if not all(f"impressions_{i}d" in view_gam.columns for i in range(1, 8)):
                    return None
                if "impressions_goal" not in view_gam.columns or \
                   "start_date" not in view_gam.columns or \
                   "end_date" not in view_gam.columns:
                    return None
                today = date.today()
                lifetime = pd.to_numeric(view_gam.get("lifetime_impressions_delivered",
                                                      view_gam.get("ad_server_impressions")),
                                          errors="coerce").fillna(0)
                goal = pd.to_numeric(view_gam["impressions_goal"], errors="coerce")
                start = pd.to_datetime(view_gam["start_date"], errors="coerce")
                end = pd.to_datetime(view_gam["end_date"], errors="coerce")
                total_days = (end - start).dt.days.clip(lower=1)
                series = []
                for n in range(7, 0, -1):  # n=7 oldest, n=1 most recent
                    rolled = pd.Series(0.0, index=view_gam.index)
                    for i in range(1, n):
                        rolled = rolled + pd.to_numeric(
                            view_gam.get(f"impressions_{i}d", 0), errors="coerce"
                        ).fillna(0)
                    cumulative = (lifetime - rolled).clip(lower=0)
                    as_of = pd.Timestamp(today - timedelta(days=n))
                    elapsed = (as_of - start).dt.days.clip(lower=0)
                    elapsed = pd.Series(
                        [min(e, t) for e, t in zip(elapsed, total_days)],
                        index=view_gam.index,
                    )
                    expected = goal * (elapsed / total_days)
                    mask = (goal > 0) & (expected > 0) & cumulative.notna()
                    if not mask.any():
                        series.append(None)
                        continue
                    sum_c = float(cumulative[mask].sum())
                    sum_e = float(expected[mask].sum())
                    series.append((sum_c / sum_e * 100) if sum_e else None)
                return series

            def _trend_delta_label(values, fmt="pct", suffix_target=None):
                """Compare latest to 7-day average. Returns (text, class).
                fmt: 'pct' for relative %, 'pp' for percentage-point delta.
                Threshold tiers (apply to both formats):
                  |d| < 0.5  → "flat" in neutral text (noise band)
                  |d| < 2    → arrow + value in secondary text
                  |d| < 5 & worsening → amber
                  |d| ≥ 5 & worsening → red
                Up-arrows always neutral/positive (no warning color on growth)."""
                if not values:
                    return (None, "")
                non = [v for v in values if v is not None and not pd.isna(v)]
                if len(non) < 2:
                    return (None, "")
                latest = non[-1]
                prior_avg = sum(non[:-1]) / len(non[:-1])
                if prior_avg == 0:
                    return (None, "")
                if fmt == "pct":
                    d = (latest - prior_avg) / abs(prior_avg) * 100
                    unit = "%"
                    suffix = " vs 7-day avg"
                else:
                    d = latest - prior_avg
                    unit = "pp"
                    suffix = ""
                ABS = abs(d)
                if ABS < 0.5:
                    cls = "kpi-delta-flat"
                    body = "• flat"
                else:
                    arrow = "▲" if d > 0 else "▼"
                    if d > 0:
                        cls = "kpi-delta-neutral"
                    elif ABS < 2:
                        cls = "kpi-delta-neutral"
                    elif ABS < 5:
                        cls = "kpi-delta-amber"
                    else:
                        cls = "kpi-delta-down"
                    body = f"{arrow} {ABS:.1f}{unit}"
                txt = f'<span class="{cls}">{body}</span>{suffix}'
                if suffix_target is not None:
                    txt += f' · target {suffix_target}'
                return (txt, cls)

            def _render_ttd_cpa(summary: dict) -> None:
                """Render the TTD Luckyland CPA accordion below the Direct KPI strip.

                Uses the .nw-na shell (collapsible on mobile, force-open on tablet+)
                with 5 KPI tiles, two mini bar charts (daily conversions / daily CPA),
                and a media-type breakdown table.
                """
                def _fmt_money_cpa(v):
                    if v is None or pd.isna(v): return "—"
                    return f"${v:,.2f}"

                def _fmt_int(v):
                    if not v: return "—"
                    v = int(v)
                    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
                    if v >= 1_000:     return f"{v/1_000:.1f}K"
                    return str(v)

                def _fmt_pct(v, dp=2):
                    if v is None or pd.isna(v): return "—"
                    return f"{v:.{dp}f}%"

                def _delta_html(v, invert=False, fmt="pct"):
                    """Colored delta text — green = improving (lower CPA = good,
                    hence `invert=True` for CPA). fmt='pct' → '±X%', 'abs' → '±$X'."""
                    if v is None or pd.isna(v): return ""
                    improving = (v < 0) if invert else (v > 0)
                    cls = "kpi-delta-up" if improving else "kpi-delta-down"
                    arrow = "▲" if v > 0 else "▼"
                    if fmt == "abs":
                        body = f"{arrow} ${abs(v):.2f}"
                    else:
                        body = f"{arrow} {abs(v):.1f}%"
                    return f' <span class="{cls}">{body}</span>'

                def _bar_chart_html(series, title, fmt_val, color_css="var(--text-secondary)"):
                    """Mini horizontal bar chart for a daily series [(date, val), ...]."""
                    if not series:
                        return ""
                    vals = [v for _, v in series]
                    max_v = max(vals) if vals else 1
                    if max_v == 0:
                        max_v = 1
                    bars = ""
                    for d, v in series:
                        pct = v / max_v * 100
                        label = d.strftime("%m/%d") if hasattr(d, "strftime") else str(d)
                        bars += (
                            f'<div class="nw-ttd-bar-row">'
                            f'<span class="nw-ttd-bar-label">{label}</span>'
                            f'<span class="nw-ttd-bar-track">'
                            f'<span class="nw-ttd-bar-fill" style="width:{pct:.1f}%;background:{color_css}"></span>'
                            f'</span>'
                            f'<span class="nw-ttd-bar-val">{fmt_val(v)}</span>'
                            f'</div>'
                        )
                    return (
                        f'<div class="nw-ttd-chart">'
                        f'<div class="nw-ttd-chart-title">{title}</div>'
                        f'<div class="nw-ttd-bars">{bars}</div>'
                        f'</div>'
                    )

                s = summary
                empty = not s["impressions"] and not s["conversions"]

                # Date range eyebrow
                date_label = ""
                if s["date_min"] and s["date_max"]:
                    d0 = s["date_min"]
                    d1 = s["date_max"]
                    if hasattr(d0, "strftime"):
                        date_label = f'{d0.strftime("%b %d")} – {d1.strftime("%b %d, %Y")}'
                    else:
                        date_label = f'{d0} – {d1}'

                # 5 KPI tiles
                cpa_sub = ""
                if s["delta_cpa"] is not None:
                    cpa_sub = _delta_html(s["delta_cpa"], invert=True, fmt="abs")
                conv_sub = ""
                if s["delta_conversions"] is not None:
                    conv_sub = _delta_html(s["delta_conversions"], fmt="pct")
                spend_sub = ""
                if s["delta_spend"] is not None:
                    spend_sub = _delta_html(s["delta_spend"], fmt="pct")

                tiles_html = (
                    f'<div class="kpi-tile">'
                    f'<div class="kpi-label">CPA</div>'
                    f'<div class="kpi-value">{_fmt_money_cpa(s["cpa"])}</div>'
                    f'<div class="kpi-target">{cpa_sub}</div>'
                    f'</div>'
                    f'<div class="kpi-tile">'
                    f'<div class="kpi-label">Conversions</div>'
                    f'<div class="kpi-value">{_fmt_int(s["conversions"])}</div>'
                    f'<div class="kpi-target">{conv_sub}</div>'
                    f'</div>'
                    f'<div class="kpi-tile">'
                    f'<div class="kpi-label">Spend</div>'
                    f'<div class="kpi-value">{_fmt_money_cpa(s["spend_usd"]) if s["spend_usd"] else "—"}</div>'
                    f'<div class="kpi-target">{spend_sub}</div>'
                    f'</div>'
                    f'<div class="kpi-tile">'
                    f'<div class="kpi-label">Conv. Rate</div>'
                    f'<div class="kpi-value">{_fmt_pct(s["conv_rate"], dp=3)}</div>'
                    f'</div>'
                    f'<div class="kpi-tile">'
                    f'<div class="kpi-label">Clicks</div>'
                    f'<div class="kpi-value">{_fmt_int(s["clicks"])}</div>'
                    f'</div>'
                )

                # Bar charts (show last 14 days max to keep bars readable)
                daily_convs = s["daily_conversions"][-14:]
                daily_cpa   = s["daily_cpa"][-14:]
                charts_html = ""
                if daily_convs or daily_cpa:
                    conv_chart = _bar_chart_html(
                        daily_convs, "Daily Conversions",
                        lambda v: str(int(v)),
                        "var(--text-secondary)",
                    )
                    cpa_chart = _bar_chart_html(
                        daily_cpa, "Daily CPA",
                        lambda v: f"${v:.0f}",
                        "var(--text-muted)",
                    )
                    if conv_chart or cpa_chart:
                        charts_html = (
                            f'<div class="nw-ttd-charts">'
                            f'{conv_chart}{cpa_chart}'
                            f'</div>'
                        )

                # Media type breakdown table
                table_html = ""
                if s["by_media_type"]:
                    rows = ""
                    for row in s["by_media_type"]:
                        cpa_cell  = _fmt_money_cpa(row["cpa"])
                        cvr_cell  = _fmt_pct(row["conv_rate"], dp=3)
                        rows += (
                            f'<tr>'
                            f'<td>{row["media_type"]}</td>'
                            f'<td>{_fmt_int(row["impressions"])}</td>'
                            f'<td>{_fmt_int(row["clicks"])}</td>'
                            f'<td>{_fmt_int(row["conversions"])}</td>'
                            f'<td>{_fmt_money_cpa(row["spend_usd"])}</td>'
                            f'<td>{cpa_cell}</td>'
                            f'<td>{cvr_cell}</td>'
                            f'</tr>'
                        )
                    table_html = (
                        f'<table class="nw-ttd-table">'
                        f'<thead><tr>'
                        f'<th>Format</th><th>Impr.</th><th>Clicks</th>'
                        f'<th>Conv.</th><th>Spend</th><th>CPA</th><th>CVR</th>'
                        f'</tr></thead>'
                        f'<tbody>{rows}</tbody>'
                        f'</table>'
                    )

                # Accordion body
                if empty:
                    body_inner = '<div style="padding:12px 13px;font-size:12px;color:var(--text-muted)">No TTD data yet — run <code>refresh_cache.py --mode=ttd</code> to populate.</div>'
                else:
                    body_inner = (
                        (f'<div class="nw-ttd-date">{date_label}</div>' if date_label else "")
                        + f'<div class="nw-ttd-kpi-row">{tiles_html}</div>'
                        + charts_html
                        + table_html
                    )

                count_label = f"{s['conversions']:,} conv." if s["conversions"] else "no data"
                st.markdown(
                    f'<div class="nw-ttd-wrap">'
                    f'<details class="nw-na" open>'
                    f'<summary class="nw-na-head">'
                    f'Luckyland Casino · TTD Acquisition'
                    f'<span class="nw-na-h-chev">›</span>'
                    f'<span class="cnt">{count_label}</span>'
                    f'</summary>'
                    f'<div class="nw-na-body" style="padding:12px 13px 4px">'
                    f'{body_inner}'
                    f'</div>'
                    f'</details>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            def _kpi_tile(label, value, target=None, spark=None):
                """Render one KPI card: label / serif number / target subtitle,
                with the (neutral) sparkline running full-width underneath —
                the Newsweek tile anatomy. `target` is the subtitle text.
                `spark` is the pre-rendered SVG markup (or '' for text-only)."""
                target_html = f'<div class="kpi-target">{target}</div>' if target else ""
                spark_html = spark or ""
                return (
                    f'<div class="kpi-tile">'
                    f'<div class="kpi-label">{label}</div>'
                    f'<div class="kpi-value">{value}</div>'
                    f'{target_html}'
                    f'{spark_html}'
                    f'</div>'
                )

            # ── Compute the sparkline series. ────────────────────────────
            _rev_series  = _series_sum("revenue")
            _impr_series = _series_sum("impressions")
            _ctr_series  = _ratio_series("clicks", "impressions")
            _view_series = _ratio_series("viewable_imps", "measurable_imps")
            _pace_series = _pacing_series()
            # VCR daily series = Σ video_completes per day / Σ video_starts per day.
            # `_ratio_series` already multiplies by 100 (default scale=100.0).
            _vcr_series  = _ratio_series("video_completes", "video_starts")

            # ── DV Attention Index — publisher-wide daily mean over the
            # report window. Sparkline target line is at 100 (DV's industry
            # median). Falls back gracefully when dv_df is empty (DV email
            # not yet polled / agentmail creds missing).
            _attn_series: list = []
            _attn_total = None
            if (not dv_df.empty
                    and "attention_index" in dv_df.columns
                    and "date" in dv_df.columns):
                _attn_daily = (dv_df.dropna(subset=["attention_index"])
                                   .groupby("date")["attention_index"]
                                   .mean()
                                   .sort_index())
                if not _attn_daily.empty:
                    _attn_series = _attn_daily.tail(7).tolist()
                    _attn_total = float(_attn_daily.mean())

            # ── DV SIVT and GIVT — publisher-wide impression-weighted %
            # per day (Σ Monitored Ads of that Fraud bucket / Σ all
            # Monitored Ads). Both target 1% (industry-standard tolerance);
            # lower is better, opposite of Attention/Viewability — handled
            # below by the spark_color call passing `lower_is_worse=False`.
            _sivt_series: list = []; _sivt_total = None
            _givt_series: list = []; _givt_total = None
            if (not ivt_df.empty
                    and {"traffic_validity", "monitored_ads", "date"}.issubset(ivt_df.columns)):
                _ads_all = pd.to_numeric(ivt_df["monitored_ads"], errors="coerce").fillna(0)
                _val_str = ivt_df["traffic_validity"].astype(str)

                def _ivt_daily_pct(label: str) -> list:
                    """Per-date impression-weighted % for one Fraud bucket."""
                    tot_by_day = _ads_all.groupby(ivt_df["date"]).sum()
                    mask = _val_str == label
                    frd_by_day = _ads_all[mask].groupby(ivt_df["date"][mask]).sum()
                    joined = pd.DataFrame({"total": tot_by_day, "fraud": frd_by_day}).fillna(0)
                    joined["pct"] = (joined["fraud"] / joined["total"] * 100).where(joined["total"] > 0)
                    return joined["pct"].dropna().sort_index().tail(7).tolist()

                def _ivt_overall_pct(label: str) -> float | None:
                    """Single publisher-wide % over the whole window."""
                    tot = _ads_all.sum()
                    if not tot:
                        return None
                    frd = _ads_all[_val_str == label].sum()
                    return float(frd / tot * 100)

                _sivt_series = _ivt_daily_pct("Fraud/SIVT")
                _givt_series = _ivt_daily_pct("Fraud/GIVT")
                _sivt_total  = _ivt_overall_pct("Fraud/SIVT")
                _givt_total  = _ivt_overall_pct("Fraud/GIVT")

            # All sparklines render in neutral ink per the Newsweek handoff —
            # state never rides the trend stroke. Polarity/health still
            # surfaces in the delta subtitle (_trend_delta_label) and the
            # dashed target reference line drawn by _sparkline_svg.
            _rev_spark  = _sparkline_svg(_rev_series)  if _rev_series  else ""
            _impr_spark = _sparkline_svg(_impr_series) if _impr_series else ""
            _pace_spark = _sparkline_svg(
                _pace_series, target=float(_pacing_target),
            ) if _pace_series else ""
            # Viewability + CTR targets source from Configure → Benchmarks by
            # format → Display, so changing them in the Settings tab updates
            # both the sparkline reference line and the subtitle string.
            _view_target = _view_bench
            _view_spark = _sparkline_svg(
                _view_series, target=_view_target,
            ) if _view_series else ""
            _ctr_spark = _sparkline_svg(_ctr_series) if _ctr_series else ""

            # Attention sparkline + subtitle. Target = 100 (DV's industry
            # median, the same value used for the per-row column's color
            # bands). "pp" label is technically "points off the 100 index"
            # but reads correctly as e.g. "▲ 2.1pp · target 100".
            _attn_target = 100.0
            _attn_spark = _sparkline_svg(
                _attn_series, target=_attn_target,
            ) if _attn_series else ""

            # SIVT + GIVT sparklines. Both target = 1% (industry tolerance,
            # top of green band) — drawn as the reference line.
            _ivt_target = 1.0
            _sivt_spark = _sparkline_svg(
                _sivt_series, target=_ivt_target,
            ) if _sivt_series else ""
            _givt_spark = _sparkline_svg(
                _givt_series, target=_ivt_target,
            ) if _givt_series else ""

            # VCR sparkline. Target sources from Configure → Benchmarks by
            # format → Video → VCR%; falls back to 70 (the standard benchmark
            # for in-stream video).
            _vcr_bench = ((_cfg.get("benchmarks_by_format") or {})
                          .get("Video", {}) or {}).get("vcr_pct")
            _vcr_target = float(_vcr_bench) if _vcr_bench is not None else 70.0
            _vcr_spark = _sparkline_svg(
                _vcr_series, target=_vcr_target,
            ) if _vcr_series else ""

            _view_target_str = f"{_view_target:g}%"
            _ctr_bench_str   = f"{_ctr_bench:g}%" if _ctr_bench is not None else None
            _rev_sub  = _trend_delta_label(_rev_series,  "pct")[0]
            _impr_sub = _trend_delta_label(_impr_series, "pct")[0]
            _pace_sub = _trend_delta_label(_pace_series, "pp", suffix_target=f"{int(_pacing_target)}%")[0] \
                        if _pace_series else f"Target {int(_pacing_target)}%"
            _view_sub = _trend_delta_label(_view_series, "pp", suffix_target=_view_target_str)[0] \
                        if _view_series else f"Target {_view_target_str}"
            _attn_sub = _trend_delta_label(_attn_series, "pp", suffix_target=f"{int(_attn_target)}")[0] \
                        if _attn_series else f"Target {int(_attn_target)}"
            # IVT subtitles. Target "≤1%" wording communicates the
            # ceiling-not-floor semantics. _trend_delta_label's existing
            # arrow polarity ("▲" = neutral) is technically backwards for
            # IVT (rising IVT is bad) but at sub-1% baseline values the
            # arrow movement is tiny — flagging in dv_attention memory
            # note as a follow-up if it becomes a real problem.
            _sivt_sub = _trend_delta_label(_sivt_series, "pp", suffix_target=f"≤{_ivt_target:g}%")[0] \
                        if _sivt_series else f"Target ≤{_ivt_target:g}%"
            _givt_sub = _trend_delta_label(_givt_series, "pp", suffix_target=f"≤{_ivt_target:g}%")[0] \
                        if _givt_series else f"Target ≤{_ivt_target:g}%"
            if _ctr_bench_str:
                _ctr_sub = _trend_delta_label(_ctr_series, "pp", suffix_target=_ctr_bench_str)[0] \
                           if _ctr_series else f"Benchmark {_ctr_bench_str}"
            else:
                _ctr_sub = _trend_delta_label(_ctr_series, "pp")[0] if _ctr_series else "—"

            if _video_li_count > 0 and pd.notna(avg_vcr):
                _vcr_val = f"{avg_vcr:.1f}%"
                # Match the Viewability/Attention/SIVT/GIVT subtitle pattern:
                # trend-vs-prior-avg + target. Keep the video-line count too
                # so the user knows the average is across N lines, not 1 —
                # rendered as a parenthetical so the trend stays prominent.
                _vcr_target_str = f"{_vcr_target:g}%"
                _lines_bit = f"{int(_video_li_count)} video line{'s' if _video_li_count != 1 else ''}"
                _vcr_trend = _trend_delta_label(
                    _vcr_series, "pp",
                    suffix_target=f"{_vcr_target_str} · {_lines_bit}",
                )[0] if _vcr_series else f"Target {_vcr_target_str} · {_lines_bit}"
                _vcr_sub = _vcr_trend
            else:
                _vcr_val = "—"
                _vcr_sub = "No video"
                _vcr_spark = ""  # no sparkline when no video data
            # Single grid container so all nine tiles stretch to equal
            # height. Quality metrics (Viewability, Attention, SIVT, GIVT)
            # cluster in the middle so the eye can compare them in one
            # sweep. SIVT and GIVT use 2 decimals because Newsweek's
            # publisher-wide values run sub-1%; integer formatting would
            # show "1%" for both 0.52% and 1.49% — losing the meaningful
            # signal of "are we beating or breaking the 1% target".
            _attn_disp = f"{_attn_total:.0f}" if _attn_total is not None else "—"
            def _ivt_disp(v):
                if v is None or pd.isna(v): return "—"
                v = float(v)
                if v == 0:    return "0%"
                if v < 1:     return f"{v:.2f}%"
                if v < 10:    return f"{v:.1f}%"
                return f"{v:.0f}%"
            st.markdown(
                '<div class="nw-kpi-row">'
                + _kpi_tile("Revenue", _fmt_money(total_rev), _rev_sub or None, _rev_spark)
                + _kpi_tile("Impressions", _fmt_count(total_impr), _impr_sub or None, _impr_spark)
                + _kpi_tile("Avg pacing",
                            f"{avg_pacing:.1f}%" if pd.notna(avg_pacing) else "—",
                            _pace_sub, _pace_spark)
                + _kpi_tile("Viewability",
                            f"{avg_viewability:.1f}%" if pd.notna(avg_viewability) else "—",
                            _view_sub, _view_spark)
                + _kpi_tile("Attention", _attn_disp, _attn_sub, _attn_spark)
                + _kpi_tile("SIVT", _ivt_disp(_sivt_total), _sivt_sub, _sivt_spark)
                + _kpi_tile("GIVT", _ivt_disp(_givt_total), _givt_sub, _givt_spark)
                + _kpi_tile("VCR", _vcr_val, _vcr_sub, _vcr_spark)
                + _kpi_tile("CTR",
                            f"{avg_ctr:.2f}%" if pd.notna(avg_ctr) else "—",
                            _ctr_sub, _ctr_spark)
                + '</div>',
                unsafe_allow_html=True,
            )

            # ---------- TTD Luckyland CPA accordion ----------
            _render_ttd_cpa(_ttd_summary)

            # ---------- Campaign table ----------
            # Remaining impressions (None when no goal is set)
            if "impressions_goal" in view_gam.columns and "lifetime_impressions_delivered" in view_gam.columns:
                view_gam = view_gam.copy()
                view_gam["remaining_impressions"] = view_gam.apply(
                    lambda r: max(r["impressions_goal"] - r["lifetime_impressions_delivered"], 0)
                    if pd.notna(r["impressions_goal"]) and pd.notna(r["lifetime_impressions_delivered"])
                    else None,
                    axis=1,
                )

            # Override the displayed Clicks / Revenue / Viewability % / CTR %
            # cells with lifetime values (computed from the lifetime_* columns
            # added by run_lifetime_delivery). Previously these reflected only
            # the most recent 7 days from the windowed delivery report, which
            # is misleading for long-running campaigns.
            view_gam = view_gam.copy()
            if "lifetime_clicks" in view_gam.columns:
                view_gam["ad_server_clicks"] = pd.to_numeric(view_gam["lifetime_clicks"], errors="coerce")
            if "lifetime_revenue" in view_gam.columns:
                view_gam["ad_server_cpm_and_cpc_revenue"] = pd.to_numeric(view_gam["lifetime_revenue"], errors="coerce")
            if "lifetime_viewable_imps" in view_gam.columns and "lifetime_measurable_imps" in view_gam.columns:
                _viewable_lt   = pd.to_numeric(view_gam["lifetime_viewable_imps"],   errors="coerce")
                _measurable_lt = pd.to_numeric(view_gam["lifetime_measurable_imps"], errors="coerce")
                view_gam["ad_server_active_view_viewable_impressions_rate"] = (
                    (_viewable_lt / _measurable_lt).where(_measurable_lt > 0, other=None) * 100
                )
            elif "ad_server_active_view_viewable_impressions_rate" in view_gam.columns:
                # Fallback when lifetime columns aren't populated yet (between
                # deploy and the next refresh). API column is a 0-1 ratio.
                view_gam["ad_server_active_view_viewable_impressions_rate"] = (
                    pd.to_numeric(view_gam["ad_server_active_view_viewable_impressions_rate"], errors="coerce") * 100
                )
            if "lifetime_clicks" in view_gam.columns and "lifetime_impressions_delivered" in view_gam.columns:
                _clicks_lt = pd.to_numeric(view_gam["lifetime_clicks"], errors="coerce")
                _imps_lt   = pd.to_numeric(view_gam["lifetime_impressions_delivered"], errors="coerce")
                view_gam["ad_server_ctr"] = (
                    (_clicks_lt / _imps_lt).where(_imps_lt > 0, other=None) * 100
                )
            elif "ad_server_ctr" in view_gam.columns:
                # Fallback when lifetime columns aren't populated yet.
                view_gam["ad_server_ctr"] = pd.to_numeric(view_gam["ad_server_ctr"], errors="coerce") * 100

            # ── Per-LI delta annotations for impressions / clicks / pacing / viewability ──
            # Renders cells like "12,345 (▲ +500)" using the latest day
            # snapshot vs the day before. Falls back to plain value when 2d data is
            # missing (first deploy before refresh repopulates the new columns).
            # Sort BEFORE the string conversion — once columns are strings, header-
            # click sorts are lexicographic and meaningless.
            if "pacing_pct" in view_gam.columns:
                view_gam = view_gam.sort_values("pacing_pct", na_position="last").copy()
            else:
                view_gam = view_gam.copy()

            def _arrow(d):
                if pd.isna(d): return ""
                return "▲" if d >= 0 else "▼"

            # Per-day viewability rate from the new viewable/measurable counts.
            # Vectorized (column math, not a per-row apply) — mirrors the
            # lifetime-rate pattern above. `.where(denom > 0, None)` yields NaN
            # exactly where the old apply returned None; the only consumer
            # (_fmt_pct_annot) guards both with pd.isna, so the rendered cell
            # and its color band are behaviour-identical.
            for _suf in ("1d", "2d"):
                _viewable  = f"viewable_imps_{_suf}"
                _measurable = f"measurable_imps_{_suf}"
                if _viewable in view_gam.columns and _measurable in view_gam.columns:
                    _v = pd.to_numeric(view_gam[_viewable], errors="coerce")
                    _m = pd.to_numeric(view_gam[_measurable], errors="coerce")
                    view_gam[f"viewability_rate_{_suf}"] = (
                        (_v / _m).where(_m > 0, other=None) * 100
                    )

            # Prior-day pacing — re-compute from lifetime minus 1d-impressions over a
            # goal pro-rated to one day earlier. No new refresh data needed for this.
            def _prior_pacing(row):
                # Date math + pro-rating live in dashboard_logic (tested).
                return dl.prior_pacing(
                    row.get("impressions_goal"),
                    row.get("lifetime_impressions_delivered"),
                    row.get("impressions_1d"),
                    row.get("start_date"),
                    row.get("end_date"),
                    pd.Timestamp(date.today()),
                )

            view_gam["pacing_prior_pct"] = view_gam.apply(_prior_pacing, axis=1)

            # Build annotated strings — overwrite the numeric columns the table
            # already references so the existing display_cols mapping picks them up.
            # The PRIMARY value displayed in each cell is unchanged from before
            # (Impressions: 1d, Clicks: 7-day sum, Pacing: cumulative, Viewability:
            # 7-day mean rate). The parenthetical annotation adds a yesterday-vs-
            # day-before trend indicator for visual context only.
            def _fmt_count_annot(primary, v1, v2):
                """Cell value = `primary`; annotation = delta of v1 vs v2 (omitted when 0)."""
                if pd.isna(primary): return ""
                base = f"{int(primary):,}"
                if pd.isna(v1) or pd.isna(v2): return base
                d = int(v1) - int(v2)
                if d == 0: return base
                sign = "+" if d > 0 else ""
                return f"{base} ({_arrow(d)} {sign}{d:,})"

            def _fmt_pct_annot(primary, v1, v2):
                """Cell value = `primary` (already 0-100 percent); annotation = pp delta of v1 vs v2 (omitted when 0)."""
                if pd.isna(primary): return ""
                base = f"{primary:.1f}%"
                if pd.isna(v1) or pd.isna(v2): return base
                d = v1 - v2
                if abs(d) < 0.05: return base  # rounds to "0.0pp" — suppress
                sign = "+" if d > 0 else ""
                return f"{base} ({_arrow(d)} {sign}{d:.1f}pp)"

            if "ad_server_clicks" in view_gam.columns:
                # Primary stays = ad_server_clicks (7-day sum, what it was before).
                # Annotation = 1d - 2d delta (daily trend indicator).
                view_gam["ad_server_clicks"] = view_gam.apply(
                    lambda r: _fmt_count_annot(r.get("ad_server_clicks"),
                                                r.get("clicks_1d"),
                                                r.get("clicks_2d")),
                    axis=1,
                )
            if "pacing_pct" in view_gam.columns:
                _pacing_numeric = pd.to_numeric(view_gam["pacing_pct"], errors="coerce")
                view_gam["pacing_pct"] = _pacing_numeric
                def _pace_delta(row):
                    v1 = _pacing_numeric.loc[row.name]
                    v2 = row.get("pacing_prior_pct")
                    if pd.isna(v1) or pd.isna(v2): return ""
                    d = v1 - v2
                    if abs(d) < 0.05: return ""
                    arrow = "▲" if d >= 0 else "▼"
                    sign  = "+"  if d > 0  else ""
                    return f"{arrow} {sign}{d:.1f}pp"
                view_gam["pacing_delta"] = view_gam.apply(_pace_delta, axis=1)
            if "ad_server_active_view_viewable_impressions_rate" in view_gam.columns:
                # Primary stays = the 7-day mean viewability rate (already 0-100).
                # Annotation = 1d rate - 2d rate pp delta. Below-70 is conveyed
                # by the column's red→green color ramp, no text qualifier needed.
                view_gam["ad_server_active_view_viewable_impressions_rate"] = view_gam.apply(
                    lambda r: _fmt_pct_annot(
                        r.get("ad_server_active_view_viewable_impressions_rate"),
                        r.get("viewability_rate_1d"),
                        r.get("viewability_rate_2d"),
                    ),
                    axis=1,
                )

            # Per-day CTR (clicks / impressions) for the annotation delta.
            # Vectorized (see the viewability note above — same NaN-vs-None
            # equivalence through _fmt_pct_annot's pd.isna guard).
            for _suf in ("1d", "2d"):
                _cl = f"clicks_{_suf}"
                _im = f"impressions_{_suf}"
                if _cl in view_gam.columns and _im in view_gam.columns:
                    _c = pd.to_numeric(view_gam[_cl], errors="coerce")
                    _i = pd.to_numeric(view_gam[_im], errors="coerce")
                    view_gam[f"ctr_rate_{_suf}"] = (
                        (_c / _i).where(_i > 0, other=None) * 100
                    )
            if "ad_server_ctr" in view_gam.columns:
                # Primary stays = lifetime CTR (already 0-100 from the earlier
                # override). Annotation = 1d CTR rate - 2d CTR rate pp delta.
                view_gam["ad_server_ctr"] = view_gam.apply(
                    lambda r: _fmt_pct_annot(
                        r.get("ad_server_ctr"),
                        r.get("ctr_rate_1d"),
                        r.get("ctr_rate_2d"),
                    ),
                    axis=1,
                )

            # Per-day VCR (completes / starts) for the annotation delta.
            # Vectorized (same NaN-vs-None equivalence as the rates above).
            for _suf in ("1d", "2d"):
                _vs = f"video_starts_{_suf}"
                _vc = f"video_completes_{_suf}"
                if _vs in view_gam.columns and _vc in view_gam.columns:
                    _s = pd.to_numeric(view_gam[_vs], errors="coerce")
                    _c = pd.to_numeric(view_gam[_vc], errors="coerce")
                    view_gam[f"vcr_rate_{_suf}"] = (
                        (_c / _s).where(_s > 0, other=None) * 100
                    )
            if "vcr" in view_gam.columns:
                # Primary stays = lifetime VCR. Annotation = 1d - 2d pp delta.
                # Non-video LIs (ad_format doesn't contain 'video') render 'N/A'
                # so the column is never blank — clear visual signal that VCR
                # doesn't apply.
                def _fmt_vcr(row):
                    fmt = row.get("ad_format")
                    if not isinstance(fmt, str) or "video" not in fmt.lower():
                        return "N/A"
                    return _fmt_pct_annot(row.get("vcr"),
                                           row.get("vcr_rate_1d"),
                                           row.get("vcr_rate_2d"))
                view_gam["vcr"] = view_gam.apply(_fmt_vcr, axis=1)

            # VCR column always shown now (non-video shows 'N/A').
            has_vcr = "vcr" in view_gam.columns

            _direct_src = next(
                (s for s in _cfg.get("direct_sources", []) if s.get("name") == "GAM Direct"),
                None,
            )
            _direct_col_map = _direct_src.get("columns", {}) if _direct_src else {}
            if _direct_col_map:
                # Build source_col → display_name from settings (preserving order)
                display_cols = {
                    src: name
                    for name, src in _direct_col_map.items()
                    if src and src not in ("N/A", "")
                }
            else:
                display_cols = {
                    "seller_ae": "Seller", "advertiser": "Advertiser",
                    "campaign_name": "Campaign", "line_item_name": "Line Item",
                    "ad_format": "Format", "start_date": "Start Date",
                    "end_date": "End Date", "impressions_goal": "Goal",
                    "cpm_rate": "CPM Rate",
                    "lifetime_impressions_delivered": "Delivered",
                    "remaining_impressions": "Remaining",
                    "ad_server_clicks": "Clicks",
                    "pacing_pct":   "Pace",
                    "pacing_delta": "Δ",
                    "ad_server_active_view_viewable_impressions_rate": "Viewability %",
                    "ad_server_ctr": "CTR %",
                    "vcr": "VCR %",
                    "ad_server_cpm_and_cpc_revenue": "Revenue",
                }
            if "vcr" not in display_cols:
                # Always include — non-video LIs render as 'N/A', video LIs get the rate.
                display_cols["vcr"] = "VCR %"

            # ── Progress column: delivered/goal, capped at 1.0. None for
            # goal-less line items (sponsorships, house, etc.).
            if "impressions_goal" in view_gam.columns and "lifetime_impressions_delivered" in view_gam.columns:
                view_gam["progress_pct"] = view_gam.apply(
                    lambda r: (min(r["lifetime_impressions_delivered"] / r["impressions_goal"], 1.0)
                               if pd.notna(r["impressions_goal"]) and r["impressions_goal"] > 0
                               and pd.notna(r["lifetime_impressions_delivered"]) else None),
                    axis=1,
                )

            # ── Alphabetical sort + ordinal badge. Sort A–Z by the displayed
            # Advertiser — Campaign name (the same derivation each row renders),
            # with line_item_id as the tiebreak. Then number **#1, #2, … per GAM
            # order, assigned in the displayed (alphabetical) order** — so every
            # line of a multi-line order carries a badge and they read low→high
            # down the order's block (single-line orders show none). (Per Roger
            # 2026-06-15: restart per order.) An order is one advertiser, so its
            # lines sit together in the A–Z display; the cumcount runs AFTER the
            # sort, so it follows campaign-alphabetical order — *not*
            # line_item_id — which is what fixes the old #6-above-#3 scatter.
            if "line_item_name" in view_gam.columns:
                _disp = view_gam["line_item_name"].map(dl.line_item_display_name)
                view_gam = view_gam.assign(_disp_key=_disp.str.casefold())
                _sort_cols = ["_disp_key"]
                if "line_item_id" in view_gam.columns:
                    _sort_cols.append("line_item_id")
                view_gam = view_gam.sort_values(_sort_cols, kind="stable", na_position="last")
                _ord_col = ("order_id" if "order_id" in view_gam.columns
                            else "order_name" if "order_name" in view_gam.columns else None)
                if _ord_col is not None:
                    view_gam["_ordinal"] = view_gam.groupby(_ord_col, dropna=False).cumcount() + 1
                    _ord_max = view_gam.groupby(_ord_col, dropna=False)["_ordinal"].transform("max")
                    # Only show #N when the order has >1 LI.
                    view_gam["line_item_name"] = view_gam.apply(
                        lambda r: (f"#{int(r['_ordinal'])}  {r['line_item_name']}"
                                   if pd.notna(r['line_item_name']) and _ord_max.loc[r.name] > 1
                                   else r['line_item_name']),
                        axis=1,
                    )
                view_gam = view_gam.drop(columns=["_disp_key"])

            # (The pre-redesign st.dataframe path — table_df + pandas-Styler
            # color maps — was built here but never rendered after the custom
            # HTML table below replaced it. Deleted 2026-06-12 per the dead-code
            # note from #200 instead of re-pointing it at the new tokens.)

            # Cells like "61% (▲2)" carry annotations — extract the leading
            # numeric percent so color coding still applies; tolerate the
            # pre-refresh numeric fallback too.
            def _parse_leading_pct(v):
                if isinstance(v, (int, float)) and pd.notna(v):
                    return float(v)
                if isinstance(v, str):
                    m = re.match(r"\s*([+-]?\d+(?:\.\d+)?)\s*%", v)
                    if m:
                        return float(m.group(1))
                return None

            # ── Custom HTML table — multi-line cells and proper typography
            # require this; st.dataframe can't render LI name + subtitle,
            # Pace pill + variance below, or color-coded progress bars per row.
            def _esc(s):
                if s is None: return ""
                s = str(s)
                return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

            def _subtitle(li_name, ad_format, cpm_rate):
                """Extract 'Format · $CPM' from the line-item name. The client
                lives in the main display name now (e.g. 'Cartier UK -
                Uniscroller' on the row above), so the subtitle stays
                advertiser-free to avoid the repetition."""
                parts = (li_name or "").split("_") if isinstance(li_name, str) else []
                fmt = ad_format if (isinstance(ad_format, str) and ad_format) else (parts[10] if len(parts) > 10 else "")
                cpm_str = ""
                try:
                    if cpm_rate is not None and not (isinstance(cpm_rate, float) and pd.isna(cpm_rate)):
                        cpm_str = f"${float(cpm_rate):g} CPM"
                except Exception:
                    pass
                bits = [b for b in (fmt, cpm_str) if b]
                return " · ".join(bits)

            def _pace_html(p, p_prior, is_new=False):
                """Pace cell: a banded pill in every state + variance below.
                On-pace uses the quiet green tier (pill-green) so the cell is
                boxed consistently while healthy still recedes. "new line item"
                is shown when the line didn't exist the prior day (`is_new`,
                from `dl.is_new_line_item`) — not from a large pace swing — so
                an established line with a real >100pp jump shows the actual Δ.
                Banding + delta decisions live in dashboard_logic."""
                p = pd.to_numeric(p, errors="coerce")
                p_prior = pd.to_numeric(p_prior, errors="coerce")
                if pd.isna(p):
                    return '<div class="cell-dash">—</div>'
                pct_int = int(round(p))
                _b = dl.pace_band(p, _pacing_target)
                if _b == "red":
                    cell = f'<div class="pill pill-red">{pct_int}%</div>'
                elif _b == "green":
                    cell = f'<div class="pill pill-green">{pct_int}%</div>'
                else:  # "amber" (underpacing) and "over" (overpacing) render alike
                    cell = f'<div class="pill pill-amber">{pct_int}%</div>'
                if is_new:
                    cell += '<div class="pace-delta" style="font-style:italic">new line item</div>'
                elif pd.notna(p_prior):
                    d = p - p_prior
                    # new_line_threshold=None — "new" is existence-based now, so
                    # a big swing on an established line renders as a real Δ.
                    verdict = dl.classify_delta(d, new_line_threshold=None)
                    if verdict is not None:
                        arrow, is_improvement = verdict
                        cls = "pace-delta up" if is_improvement else "pace-delta"
                        cell += f'<div class="{cls}">{arrow} {abs(d):.1f}pp</div>'
                return cell

            # Per-format viewability + CTR + VCR thresholds from settings.
            #   green  ≥ target
            #   amber  red_cut ≤ p < target
            #   red    p < red_cut
            #
            # `target` reads from benchmarks_by_format.<fmt>.<key>_pct
            # (e.g. `viewability_pct`). `red_cut` reads from the matching
            # `<key>_red_below` field; if that's null/missing it falls back
            # to `target * 0.85` — the original implicit band. So existing
            # settings keep their old visuals until a user configures an
            # explicit red threshold in Configure → Section 3.
            # Threshold resolution + banding live in dashboard_logic (pure,
            # tested); these wrappers just bind the session's settings dict.
            _bench_cfg = _cfg.get("benchmarks_by_format") or {}
            def _bench_target(fmt, key, fallback_key=None, fallback=None):
                return dl.bench_target(_bench_cfg, fmt, key, fallback_key, fallback)

            def _bench_red_cut(fmt, key, target, fallback_key=None):
                return dl.bench_red_cut(_bench_cfg, fmt, key, target, fallback_key)

            def _viewability_html(p, fmt=None, p_prior=None):
                p = _parse_leading_pct(p)
                if p is None: return '<div class="cell-dash">—</div>'
                target = _bench_target(fmt, "viewability_pct",
                                       fallback_key="Display", fallback=70.0)
                red_cut = _bench_red_cut(fmt, "viewability", target,
                                         fallback_key="Display")
                _b = dl.band(p, target, red_cut)
                if _b == "red":
                    cell = f'<div class="pill pill-red">{p:.1f}%</div>'
                elif _b == "amber":
                    cell = f'<div class="txt-amber">{p:.1f}%</div>'
                else:
                    cell = f'<div class="txt-green">{p:.1f}%</div>'
                if p_prior is not None and not pd.isna(p_prior):
                    cell += _delta_below_html(p - float(p_prior), lower_is_worse=True)
                return cell

            # _attention_html now lives at module level so the PMP table
            # can use it too (different lexical scope). Kept the reference
            # site here unchanged.

            def _ctr_html(p, fmt=None, p_prior=None):
                # p is already numeric (computed from lifetime clicks/imps *100).
                if p is None or pd.isna(p):
                    return '<span class="cell-dash">—</span>'
                target = _bench_target(fmt, "ctr_pct",
                                       fallback_key="Display", fallback=None)
                if target is None or target <= 0:
                    cell = f"{p:.2f}%"
                else:
                    red_cut = _bench_red_cut(fmt, "ctr", target,
                                             fallback_key="Display")
                    _b = dl.band(p, target, red_cut)
                    if _b == "red":
                        cell = f'<span class="pill pill-red">{p:.2f}%</span>'
                    elif _b == "amber":
                        cell = f'<span class="txt-amber">{p:.2f}%</span>'
                    else:
                        cell = f'<span class="txt-green">{p:.2f}%</span>'
                if p_prior is not None and not pd.isna(p_prior):
                    cell += _delta_below_html(p - float(p_prior), lower_is_worse=True)
                return cell

            def _vcr_html(p, is_video, fmt=None, p_prior=None):
                if not is_video:
                    return '<div class="cell-dash">—</div>'
                p = _parse_leading_pct(p)
                if p is None: return '<div class="cell-dash">—</div>'
                target = _bench_target(fmt, "vcr_pct",
                                       fallback_key="Video", fallback=60.0)
                red_cut = _bench_red_cut(fmt, "vcr", target,
                                         fallback_key="Video")
                _b = dl.band(p, target, red_cut)
                if _b == "red":
                    cell = f'<div class="pill pill-red">{p:.1f}%</div>'
                elif _b == "amber":
                    cell = f'<div class="txt-amber">{p:.1f}%</div>'
                else:
                    cell = f'<div class="txt-green">{p:.1f}%</div>'
                if p_prior is not None and not pd.isna(p_prior):
                    cell += _delta_below_html(p - float(p_prior), lower_is_worse=True)
                return cell

            def _delivered_html(v):
                if pd.isna(v): return '<div class="cell-dash">—</div>'
                a = abs(v)
                if a >= 1_000_000: return f"{v/1_000_000:.2f}M"
                if a >= 1_000:     return f"{v/1_000:.1f}K"
                return f"{int(v):,}"

            def _revenue_html(v):
                if pd.isna(v): return '<div class="cell-dash">$0</div>'
                cls = "bold-rev" if v > 10_000 else ""
                if v >= 1000:
                    return f'<span class="{cls}">${v:,.0f}</span>'
                return f'<span class="{cls}">${v:,.0f}</span>'

            def _progress_html(p):
                if pd.isna(p): return ""
                pct = max(0.0, min(1.0, p)) * 100
                # Bar color is muted gray-green (the existing pace pill
                # already communicates red/amber/green by-band; making the
                # bar match the pace band too would be visual repetition).
                # Inline % label sits flush-right of the bar.
                cls = "prog-green"
                return (
                    '<div class="nw-prog-wrap">'
                    f'<div class="nw-prog-bar"><div class="nw-prog-fill {cls}" style="width:{pct:.0f}%"></div></div>'
                    f'<span class="nw-prog-label">{pct:.0f}%</span>'
                    '</div>'
                )

            # ── Drawer helpers (data + warning matcher + URL builder).
            # Single source of truth for the GAM Network ID — Configure setting
            # wins, falling back to env var for backwards compat.
            _gam_network_id = (
                (_cfg.get("gam_network_id") or "").strip()
                or os.environ.get("GAM_NETWORK_ID", "").strip()
            )
            _warnings_cfg = _cfg.get("line_item_warnings") or []

            def _warnings_for(row):
                out = []
                for rule in _warnings_cfg:
                    field = rule.get("match_field")
                    sub = (rule.get("match_substring") or "").lower()
                    if not field or not sub:
                        continue
                    val = row.get(field)
                    if isinstance(val, str) and sub in val.lower():
                        out.append(rule)
                return out

            def _gam_li_url(li_id):
                if not _gam_network_id or li_id is None:
                    return None
                if isinstance(li_id, float) and pd.isna(li_id):
                    return None
                try:
                    li_int = int(li_id)
                except (TypeError, ValueError):
                    return None
                return (f"https://admanager.google.com/{_gam_network_id}"
                        f"#delivery/line_item/detail/line_item_id={li_int}")

            # ── AirTable ticket helpers ──────────────────────────────────────
            _at_base    = (_cfg.get("airtable_base_id") or "").strip()
            _at_form    = (_cfg.get("airtable_form_id") or "").strip()
            _at_fields  = _cfg.get("airtable_field_names") or {}
            _at_routes  = {r["context"]: r["request_type"]
                           for r in (_cfg.get("airtable_request_type_routing") or [])
                           if isinstance(r, dict) and r.get("context") and r.get("request_type")}
            _at_reporter = (_cfg.get("airtable_reporter") or "").strip()

            def _direct_request_type(row):
                """Classify a Direct/PG row → AirTable Request Type via the
                routing table. Falls back to 'Direct Campaign - Troubleshooting'
                when no rule matches (the docx-recommended default)."""
                order = row.get("order_name") or ""
                if isinstance(order, str) and order.startswith("Newsweek_PG"):
                    return _at_routes.get("PMP deal · any issue", "PMP - Adjust")
                # Viewability anomaly = strongest visual signal → Screenshot.
                # Threshold = Configure → Benchmarks by format → Display.
                _v_num = pd.to_numeric(row.get("ad_server_active_view_viewable_impressions_rate"),
                                       errors="coerce")
                if pd.notna(_v_num) and _v_num < _view_bench:
                    return _at_routes.get("Direct line · viewability anomaly",
                                          "Direct Campaign - Screenshot")
                # Healthy near end-of-flight → IO Review (closeout reporting).
                p = pd.to_numeric(row.get("pacing_pct"), errors="coerce")
                start = pd.to_datetime(row.get("start_date"), errors="coerce")
                end   = pd.to_datetime(row.get("end_date"),   errors="coerce")
                if pd.notna(p) and _pacing_warn_low <= p <= _pacing_warn_high \
                   and pd.notna(start) and pd.notna(end):
                    total = max((end - start).days, 1)
                    elapsed = max((pd.Timestamp(date.today()) - start).days, 0)
                    if total > 0 and elapsed / total >= 0.9:
                        return _at_routes.get("Direct line · healthy end-of-flight",
                                              "Direct Campaign - IO Review")
                # Default: delivery problem.
                return _at_routes.get("Direct line · delivery problem",
                                      "Direct Campaign - Troubleshooting")

            def _drawer_severity(row):
                """'Critical' / 'Warning' / 'Info' — mirrors the status banner."""
                lit = (row.get("line_item_type") or "").upper()
                if lit == "SPONSORSHIP":
                    return "Info"
                p = pd.to_numeric(row.get("pacing_pct"), errors="coerce")
                if pd.isna(p):
                    return "Info"
                if p < _pacing_critical:    return "Critical"
                if p < _pacing_warn_low or p > _pacing_warn_high: return "Warning"
                return "Info"

            def _drawer_thesis(row):
                """One-line thesis statement matching the banner text — used
                as the AirTable Notes prefill."""
                lit = (row.get("line_item_type") or "").upper()
                p = pd.to_numeric(row.get("pacing_pct"), errors="coerce")
                start = pd.to_datetime(row.get("start_date"), errors="coerce")
                end   = pd.to_datetime(row.get("end_date"),   errors="coerce")
                flight_bit = ""
                if pd.notna(start) and pd.notna(end):
                    total = max((end - start).days, 1)
                    elapsed = max((pd.Timestamp(date.today()) - start).days, 0)
                    elapsed = min(elapsed, total)
                    flight_bit = f" on day {elapsed} of {total}"
                if lit == "SPONSORSHIP":
                    return f"Sponsorship line — 100% by definition{flight_bit}."
                if pd.isna(p):
                    return f"Direct line · review{flight_bit}."
                if p < _pacing_critical:  return f"Pacing critical: {p:.1f}%{flight_bit}. Delivery well below expected."
                if p < _pacing_warn_low:  return f"Underpacing: {p:.1f}%{flight_bit}. Tracking behind expected pace."
                if p > _pacing_warn_high: return f"Overpacing: {p:.1f}%{flight_bit}. Will exhaust goal before flight ends."
                return f"On track: {p:.1f}% pacing{flight_bit}."

            def _airtable_url(request_type, *, line_item="", gam_id="",
                              severity="", seller="", notes=""):
                """Build an AirTable prefilled-form URL. Returns None when
                Base ID or Form ID isn't configured."""
                if not _at_base or not _at_form or not request_type:
                    return None
                fields = {
                    "Request Type": request_type,
                    "Line Item":    line_item,
                    "GAM ID":       gam_id,
                    "Severity":     severity,
                    "Seller":       seller,
                    "Reporter":     _at_reporter,
                    "Notes":        notes,
                }
                parts = []
                for canonical, value in fields.items():
                    if value is None or str(value).strip() == "":
                        continue
                    name = _at_fields.get(canonical, canonical)
                    parts.append(f"prefill_{quote_plus(name)}={quote_plus(str(value))}")
                if not parts:
                    return None
                return f"https://airtable.com/{_at_base}/{_at_form}/form?{'&'.join(parts)}"

            def _fmt_int_cell(v):
                v = pd.to_numeric(v, errors="coerce")
                return "—" if pd.isna(v) else f"{int(v):,}"

            def _kmb(v):
                # Compact K/M for the drawer hero tiles — matches the table's
                # DELIVERED formatting (`_delivered_html`): 1,875,000 → "1.88M".
                v = pd.to_numeric(v, errors="coerce")
                if pd.isna(v):
                    return "—"
                a = abs(v)
                if a >= 1_000_000:
                    return f"{v/1_000_000:.2f}M"
                if a >= 1_000:
                    return f"{v/1_000:.1f}K"
                return f"{int(v):,}"

            def _fmt_date_cell(v):
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return "—"
                s = str(v)
                return s.split(" ")[0] if " " in s else s

            def _pretty_date_cell(v):
                # "Jun 3" — friendlier than ISO for the spec card's Flight field.
                d = pd.to_datetime(v, errors="coerce")
                return d.strftime("%b %d").replace(" 0", " ") if pd.notna(d) else "—"

            def _drawer_status_banner(row):
                """Rule-based thesis statement (severity-colored). Flight ref
                includes the calendar date so 'day 17 of 30' is anchored as
                'Sun May 18 · day 17 of 30' — no mental translation needed."""
                p = pd.to_numeric(row.get("pacing_pct"), errors="coerce")
                if pd.isna(p):
                    return ""
                start = pd.to_datetime(row.get("start_date"), errors="coerce")
                end = pd.to_datetime(row.get("end_date"), errors="coerce")
                flight_bit = ""
                if pd.notna(start) and pd.notna(end):
                    total = max((end - start).days, 1)
                    today_dt = pd.Timestamp(date.today())
                    elapsed = max((today_dt - start).days, 0)
                    elapsed = min(elapsed, total)
                    flight_bit = (f" · {today_dt.strftime('%a %b %d').replace(' 0', ' ')}"
                                  f" · day {elapsed} of {total}")
                # Sponsorship line items get forced to 100% upstream — call it out.
                lit = (row.get("line_item_type") or "").upper()
                if lit == "SPONSORSHIP":
                    return ('<div class="nw-status-banner sev-ok">'
                            '<strong>✓ Sponsorship</strong>'
                            f'<div>Pacing is 100% by definition{flight_bit}.</div>'
                            '</div>')
                if p < _pacing_critical:
                    return ('<div class="nw-status-banner sev-red">'
                            '<strong>⚠ Pacing critical</strong>'
                            f'<div>{p:.1f}%{flight_bit}. Delivery well below expected.</div>'
                            '</div>')
                if p < _pacing_warn_low:
                    return ('<div class="nw-status-banner sev-amber">'
                            '<strong>⚠ Underpacing</strong>'
                            f'<div>{p:.1f}%{flight_bit}. Tracking behind expected pace.</div>'
                            '</div>')
                if p <= _pacing_warn_high:
                    return ('<div class="nw-status-banner sev-ok">'
                            '<strong>✓ On track</strong>'
                            f'<div>{p:.1f}% pacing{flight_bit}.</div>'
                            '</div>')
                return ('<div class="nw-status-banner sev-amber">'
                        '<strong>⚠ Overpacing</strong>'
                        f'<div>{p:.1f}%{flight_bit}. Will exhaust goal before flight ends.</div>'
                        '</div>')

            def _row_daily_imp_series(row):
                cols = [f"impressions_{i}d" for i in range(7, 0, -1)]
                if not all(c in row.index for c in cols):
                    return None
                out = []
                for c in cols:
                    v = pd.to_numeric(row.get(c), errors="coerce")
                    out.append(float(v) if pd.notna(v) else None)
                return out if any(v is not None for v in out) else None

            def _row_view_series(row):
                cv = [f"viewable_imps_{i}d"   for i in range(7, 0, -1)]
                cm = [f"measurable_imps_{i}d" for i in range(7, 0, -1)]
                if not all(c in row.index for c in cv + cm):
                    return None
                out = []
                for a, b in zip(cv, cm):
                    v = pd.to_numeric(row.get(a), errors="coerce")
                    m = pd.to_numeric(row.get(b), errors="coerce")
                    out.append(float(v / m * 100) if pd.notna(v) and pd.notna(m) and m > 0 else None)
                return out if any(v is not None for v in out) else None

            def _row_ctr_series(row):
                cc = [f"clicks_{i}d"      for i in range(7, 0, -1)]
                ci = [f"impressions_{i}d" for i in range(7, 0, -1)]
                if not all(c in row.index for c in cc + ci):
                    return None
                out = []
                for a, b in zip(cc, ci):
                    c = pd.to_numeric(row.get(a), errors="coerce")
                    i = pd.to_numeric(row.get(b), errors="coerce")
                    out.append(float(c / i * 100) if pd.notna(c) and pd.notna(i) and i > 0 else None)
                return out if any(v is not None for v in out) else None

            def _row_vcr_series(row):
                cs = [f"video_starts_{i}d"    for i in range(7, 0, -1)]
                cc = [f"video_completes_{i}d" for i in range(7, 0, -1)]
                if not all(c in row.index for c in cs + cc):
                    return None
                out = []
                for a, b in zip(cs, cc):
                    s = pd.to_numeric(row.get(a), errors="coerce")
                    c = pd.to_numeric(row.get(b), errors="coerce")
                    out.append(float(c / s * 100) if pd.notna(c) and pd.notna(s) and s > 0 else None)
                return out if any(v is not None for v in out) else None

            # DV trust-metric series come from the precomputed per-LI dicts
            # (dv_attention / dv_ivt) keyed the same way the table cells join.
            def _row_dv_key(row, join_col):
                if join_col == "line_item_id":
                    return str(row.get("line_item_id") or "")
                return re.sub(r"^#\d+\s+", "", str(row.get("line_item_name") or ""))

            def _row_attn_series(row):
                return _attn_series_by_li.get(_row_dv_key(row, _dv_li_col), [])

            def _row_sivt_series(row):
                return _sivt_series_by_li.get(_row_dv_key(row, _ivt_li_col), [])

            def _row_givt_series(row):
                return _givt_series_by_li.get(_row_dv_key(row, _ivt_li_col), [])

            def _date_row_html(actuals, expected=None):
                """7-cell row of 'Mon 12'-style labels under the delivery chart.
                Marks today (rightmost) and below-expected days for visual
                context — answers 'which day was the dip?' without a calendar."""
                today_d = date.today()
                cells = []
                soft_threshold = (expected * 0.75) if expected else None
                for i in range(7, 0, -1):
                    d = today_d - timedelta(days=i - 1)
                    label = f"{d.strftime('%a')} {d.day}"
                    classes = []
                    if i == 1:
                        classes.append("is-today")
                        label += " · today"
                    # Mark soft days (delivery < 75% of expected) with ↓.
                    if soft_threshold is not None:
                        idx = 7 - i  # actuals are oldest-first; i=7 → idx 0, i=1 → idx 6
                        v = actuals[idx] if 0 <= idx < len(actuals) else None
                        if v is not None and v < soft_threshold:
                            classes.append("is-soft")
                            label += " ↓"
                    cls = (" ".join(classes)) if classes else ""
                    cells.append(f'<span class="{cls}">{label}</span>')
                return f'<div class="nw-date-row">{"".join(cells)}</div>'

            def _sm_date_row_html():
                """Compact 7-cell date row under the small-multiples sparklines.
                Endpoints show 'M 12' / 'S 18'; middle days are single letters."""
                today_d = date.today()
                cells = []
                for i in range(7, 0, -1):
                    d = today_d - timedelta(days=i - 1)
                    letter = d.strftime("%a")[0]
                    is_endpoint = (i == 7 or i == 1)
                    text = f"{letter} {d.day}" if is_endpoint else letter
                    cls = "is-today" if i == 1 else ""
                    cells.append(f'<span class="{cls}">{text}</span>')
                return f'<div class="nw-sm-dates">{"".join(cells)}</div>'

            def _drawer_delivery_chart(row):
                """7-day daily delivery — actual line scaled to its own range so
                day-to-day shape is visible even when expected dwarfs actuals.
                Dashed reference line shows the expected daily rate; if it
                exceeds the actual range it clips to the chart's top edge
                so it still reads as a horizon line."""
                actuals = _row_daily_imp_series(row)
                if actuals is None:
                    return ""
                non_null = [a for a in actuals if a is not None]
                if not non_null:
                    return ""
                goal = pd.to_numeric(row.get("impressions_goal"), errors="coerce")
                start = pd.to_datetime(row.get("start_date"), errors="coerce")
                end = pd.to_datetime(row.get("end_date"), errors="coerce")
                expected = None
                if pd.notna(goal) and goal > 0 and pd.notna(start) and pd.notna(end):
                    total = max((end - start).days, 1)
                    expected = float(goal) / total
                # viewBox sized to a ~5.4:1 chart aspect; the SVG scales
                # UNIFORMLY (CSS width:100% + height:auto, no
                # preserveAspectRatio="none") so the geometry is never warped.
                # The panel caps at max-width so on the wide layout the chart
                # stays a proportioned card instead of a stretched-flat band —
                # and the date row (inside the panel) caps with it, staying
                # aligned under the 7 points.
                W, H, PAD = 600, 112, 16
                # Scale Y axis to actuals only — keeps day-to-day shape visible.
                vmax = max(non_null) * 1.2 if max(non_null) > 0 else 1
                n = len(actuals)
                base_y = H - PAD
                def _x(i): return PAD + i / (n - 1) * (W - 2 * PAD) if n > 1 else W / 2
                def _y(v): return base_y - v / vmax * (H - 2 * PAD)
                idx_pts = [(i, v) for i, v in enumerate(actuals) if v is not None]
                pts = " ".join(f"{_x(i):.1f},{_y(v):.1f}" for i, v in idx_pts)
                first_i, last_i = idx_pts[0][0], idx_pts[-1][0]
                # Color: green when on track, amber when off — never red on
                # data lines (state red is reserved for severity indicators,
                # brand red for chrome). Tokens ride on style= because SVG
                # presentation attributes can't read var().
                avg = sum(non_null) / len(non_null) if non_null else 0
                if expected and expected > 0:
                    ratio = avg / expected
                    stroke = "var(--state-positive-muted)" if ratio >= 0.9 else "var(--state-warning)"
                else:
                    stroke = "var(--state-positive-muted)"
                # Faint area under the actual line, same muted state color. The
                # drawer delivery chart is the one sanctioned state-colored line
                # (a pace-health signal), so the low-opacity wash reinforces it
                # without introducing a new loud element.
                area_pts = (f"{pts} {_x(last_i):.1f},{base_y:.1f} "
                            f"{_x(first_i):.1f},{base_y:.1f}")
                area = (f'<polygon points="{area_pts}" style="fill:{stroke}" '
                        f'fill-opacity="0.10" stroke="none"/>')
                # Baseline hairline grounds the trend at the chart floor.
                baseline = (f'<line x1="{PAD}" y1="{base_y:.1f}" x2="{W-PAD}" '
                            f'y2="{base_y:.1f}" style="stroke:var(--border)" '
                            f'stroke-width="1" vector-effect="non-scaling-stroke"/>')
                # Expected reference line — clip to chart top if above vmax.
                exp_line = ""
                if expected and expected > 0:
                    raw_ey = _y(expected)
                    ey = max(raw_ey, PAD)  # don't escape the top edge
                    exp_line = (
                        f'<line x1="{PAD}" y1="{ey:.1f}" x2="{W-PAD}" y2="{ey:.1f}" '
                        f'style="stroke:var(--spark-ref)" stroke-width="1" '
                        f'stroke-dasharray="5 3" vector-effect="non-scaling-stroke"/>'
                    )
                # End marker: paper halo + state dot, both zero-length round
                # caps with non-scaling stroke — a consistent few-px dot at any
                # rendered width (the halo lifts it off the line + area wash).
                dx, dy = _x(last_i), _y(actuals[last_i])
                dot = (
                    f'<path d="M{dx:.1f} {dy:.1f}h0" fill="none" '
                    f'style="stroke:var(--surface-1)" stroke-width="7.5" '
                    f'stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
                    f'<path d="M{dx:.1f} {dy:.1f}h0" fill="none" style="stroke:{stroke}" '
                    f'stroke-width="5" stroke-linecap="round" '
                    f'vector-effect="non-scaling-stroke"/>'
                )

                def _fmt_per_day(v):
                    if v >= 1_000_000: return f"{v/1_000_000:.2f}M/day"
                    if v >= 1_000:     return f"{v/1_000:.1f}K/day"
                    return f"{int(v):,}/day"
                legend_bits = ['<span class="legend">— actual</span>']
                if expected:
                    legend_bits.append(
                        f'<span class="legend">- - expected {_fmt_per_day(expected)}</span>'
                    )
                return (
                    '<div class="nw-drawer-chart">'
                    '<div class="nw-drawer-chart-label">'
                    '<span>7-day daily delivery</span>'
                    f'<span class="legend-row">{"".join(legend_bits)}</span>'
                    '</div>'
                    f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">'
                    f'{area}{baseline}{exp_line}'
                    f'<polyline points="{pts}" fill="none" style="stroke:{stroke}" '
                    f'stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round" '
                    f'vector-effect="non-scaling-stroke"/>'
                    f'{dot}</svg>'
                    + _date_row_html(actuals, expected) +
                    '</div>'
                )

            # Per-format benchmark lookup — pulls from settings so the
            # "Video Preroll >30s" entry actually shapes the chart targets
            # for long-preroll lines (50% VCR instead of 70%).
            _benchmarks_cfg = _cfg.get("benchmarks_by_format") or {}
            def _row_bench(fmt, key):
                if isinstance(fmt, str) and fmt in _benchmarks_cfg:
                    v = _benchmarks_cfg[fmt].get(key)
                    return float(v) if v is not None else None
                # Fall back to the generic Video / Display entries.
                fallback = "Video" if (isinstance(fmt, str) and "video" in fmt.lower()) else "Display"
                v = _benchmarks_cfg.get(fallback, {}).get(key)
                return float(v) if v is not None else None

            def _drawer_small_multiples(row):
                # Benchmark band, not the filter-facing format — the
                # "Video Preroll >30s" settings row shapes these targets.
                fmt = row.get("_bench_format") or row.get("ad_format")
                is_video = isinstance(fmt, str) and "video" in fmt.lower()
                view = _row_view_series(row)
                # Targets sourced from settings.benchmarks_by_format keyed on
                # the row's (possibly re-categorized) ad_format.
                view_target = _row_bench(fmt, "viewability_pct") or 70.0
                panels = []
                sm_dates = _sm_date_row_html()
                # Rate panels: Viewability always; VCR for video (completion
                # rate); CTR always — so a VIDEO line shows BOTH VCR and CTR
                # (Roger 2026-06-15), while non-video shows CTR only. Decimals:
                # CTR 2dp, viewability/VCR 1dp.
                rate_specs = [("Viewability", view, view_target, 1)]
                if is_video:
                    rate_specs.append(
                        ("VCR", _row_vcr_series(row), _row_bench(fmt, "vcr_pct"), 1))
                rate_specs.append(
                    ("CTR", _row_ctr_series(row), _row_bench(fmt, "ctr_pct"), 2))
                for _r_label, _r_series, _r_target, _r_dp in rate_specs:
                    if _r_series is None:
                        continue
                    latest = next((v for v in reversed(_r_series) if v is not None), None)
                    latest_html = (f'<span class="latest">{latest:.{_r_dp}f}%</span>'
                                   if latest is not None else '')
                    panels.append(
                        '<div class="nw-sm-panel">'
                        f'<div class="nw-sm-label"><span>{_r_label}</span>{latest_html}</div>'
                        f'{_sparkline_svg(_r_series, target=_r_target, klass="", uniform=True)}'
                        f'{sm_dates}'
                        '</div>'
                    )
                # DV trust-metric trends (Attention / SIVT / GIVT) — per-LI
                # daily series. Attention target = 100 (DV median); SIVT/GIVT
                # target = 1% (industry tolerance). Skipped when a line has
                # fewer than two days of DV coverage.
                for _dv_label, _dv_series, _dv_target, _dv_fmt in (
                    ("Attention", _row_attn_series(row), 100.0, lambda v: f"{v:.0f}"),
                    ("SIVT",      _row_sivt_series(row), 1.0,   lambda v: f"{v:.2f}%"),
                    ("GIVT",      _row_givt_series(row), 1.0,   lambda v: f"{v:.2f}%"),
                ):
                    _dv_pts = [v for v in (_dv_series or []) if v is not None]
                    if len(_dv_pts) < 2:
                        continue
                    _dv_latest = next((v for v in reversed(_dv_series) if v is not None), None)
                    _dv_lh = (f'<span class="latest">{_dv_fmt(_dv_latest)}</span>'
                              if _dv_latest is not None else '')
                    panels.append(
                        '<div class="nw-sm-panel">'
                        f'<div class="nw-sm-label"><span>{_dv_label}</span>{_dv_lh}</div>'
                        f'{_sparkline_svg(_dv_series, target=_dv_target, klass="", uniform=True)}'
                        f'{sm_dates}'
                        '</div>'
                    )
                if not panels:
                    return ""
                # A video line carries 6 cards (Viewability·VCR·CTR·Attention·
                # SIVT·GIVT); widen the desktop row from 5 to 6 so they stay in
                # one aligned row (the --6 modifier; mobile stays 2-col).
                _grid_cls = "nw-sm-grid nw-sm-grid--6" if len(panels) == 6 else "nw-sm-grid"
                return f'<div class="{_grid_cls}">' + "".join(panels) + '</div>'

            def _drawer_html(row):
                _raw_unesc = re.sub(r"^#\d+\s+", "", str(row.get("line_item_name") or ""))
                # The card titles with the FULL GAM line-item name (Roger's call),
                # not the friendly "<Advertiser> — <Campaign>" derivation — the
                # detail view shows the real, complete GAM name. Rendered mono
                # (`.nw-li-name`) since it's a structured technical identifier; the
                # table ROWS still use the friendly name (scannable + sort key).
                full_li = _esc(_raw_unesc)
                li_id = row.get("line_item_id")
                li_id_str = ""
                if li_id is not None and not (isinstance(li_id, float) and pd.isna(li_id)):
                    try:
                        li_id_str = str(int(li_id))
                    except (TypeError, ValueError):
                        li_id_str = str(li_id)
                gam_link = _gam_li_url(li_id)

                cpm = row.get("cpm_rate")
                cpm_s = "—"
                try:
                    if cpm is not None and not (isinstance(cpm, float) and pd.isna(cpm)):
                        cpm_s = f"${float(cpm):g}"
                except Exception:
                    pass

                clicks_raw = row.get("lifetime_clicks")
                if clicks_raw is None or (isinstance(clicks_raw, float) and pd.isna(clicks_raw)):
                    clicks_raw = row.get("ad_server_clicks")

                # Max creative duration from the gam_lica↔gam_creatives join.
                # "—" → no duration could be resolved → line keeps its original
                # ad_format for benchmark purposes (no recategorization).
                _cdur = row.get("_creative_max_dur")
                if _cdur is None or (isinstance(_cdur, float) and pd.isna(_cdur)):
                    _cdur_str = "—"
                else:
                    try:
                        _cdur_str = f"{float(_cdur):.0f}s"
                    except (TypeError, ValueError):
                        _cdur_str = "—"
                # Creative duration is only meaningful for video — show the cell
                # on video lines only (Roger 2026-06-15).
                _fmt_dur = row.get("_bench_format") or row.get("ad_format")
                _is_video = isinstance(_fmt_dur, str) and "video" in _fmt_dur.lower()
                _cdur_cell = (
                    f'<div class="cell"><span class="k">Creative duration</span>'
                    f'<span class="v">{_cdur_str}</span></div>'
                    if _is_video else ""
                )

                warn_html = ""
                for w in _warnings_for(row):
                    sev = (w.get("severity") or "amber").lower()
                    cls = "severity-red" if sev == "red" else ("severity-info" if sev == "info" else "")
                    warn_html += (
                        f'<div class="nw-warn {cls}">'
                        f'<strong>⚠ {_esc(w.get("title") or "Warning")}</strong>'
                        f'<div>{_esc(w.get("body") or "")}</div>'
                        f'</div>'
                    )

                # Context-aware action row — buttons surface based on the
                # line's state. Email/AirTable use href="#" placeholders for
                # now (wire to real URLs once configured in settings).
                action_buttons = []
                if gam_link:
                    action_buttons.append(
                        f'<a class="nw-action nw-action-primary" href="{gam_link}" '
                        f'target="_blank" rel="noopener noreferrer" '
                        f'title="Opens line item {_esc(li_id_str)} in GAM (new tab)">'
                        f'↗ Open in GAM</a>'
                    )
                elif _gam_network_id and not li_id_str:
                    # Network configured but row has no LI ID (rare — aggregated rows).
                    action_buttons.append(
                        '<a class="nw-action is-disabled" '
                        'title="No line item ID available for this row" '
                        'aria-disabled="true">↗ Open in GAM</a>'
                    )
                elif not _gam_network_id:
                    # Settings field empty — disable with a hint pointing back to Configure.
                    action_buttons.append(
                        '<a class="nw-action is-disabled" '
                        'title="Configure GAM Network ID in Settings to enable" '
                        'aria-disabled="true">↗ Open in GAM</a>'
                    )
                _p_num = pd.to_numeric(row.get("pacing_pct"), errors="coerce")
                if pd.notna(_p_num) and _p_num < _pacing_warn_low:
                    action_buttons.append(
                        '<a class="nw-action" href="#" '
                        'onclick="return false;">⚡ Boost priority</a>'
                    )
                # AirTable ticket — only when there's a meaningful issue.
                # Thresholds source from Configure (pacing_target_pct + the
                # 75/90/110 band ratios; Display viewability benchmark).
                _show_at = (pd.notna(_p_num) and (_p_num < _pacing_warn_low or _p_num > _pacing_warn_high))
                # Also show for low-viewability lines (Screenshot ticket).
                _v_at = pd.to_numeric(row.get("ad_server_active_view_viewable_impressions_rate"),
                                       errors="coerce")
                if pd.notna(_v_at) and _v_at < _view_bench:
                    _show_at = True
                if _show_at:
                    _at_rt = _direct_request_type(row)
                    _at_li = re.sub(r"^#\d+\s+", "", str(row.get("line_item_name") or ""))
                    _at_sev = _drawer_severity(row)
                    _at_seller = row.get("seller_ae") or ""
                    _at_notes = _drawer_thesis(row)
                    _at_url = _airtable_url(
                        _at_rt, line_item=_at_li, gam_id=li_id_str,
                        severity=_at_sev, seller=_at_seller, notes=_at_notes,
                    )
                    if _at_url:
                        action_buttons.append(
                            f'<a class="nw-action" href="{_at_url}" '
                            f'target="_blank" rel="noopener noreferrer" '
                            f'title="File AirTable ticket · {_esc(_at_rt)}">'
                            f'🎫 AirTable ticket</a>'
                        )
                    else:
                        action_buttons.append(
                            '<a class="nw-action is-disabled" '
                            'title="Configure AirTable Base ID and Form ID in Settings to enable" '
                            'aria-disabled="true">🎫 AirTable ticket</a>'
                        )
                _seller_name = row.get("seller_ae")
                if isinstance(_seller_name, str) and _seller_name.strip() \
                   and _seller_name.strip().lower() != "house":
                    action_buttons.append(
                        f'<a class="nw-action" href="#" onclick="return false;">'
                        f'📧 Notify {_esc(_seller_name)}</a>'
                    )
                actions = (f'<div class="nw-actions">{"".join(action_buttons)}</div>'
                           if action_buttons else "")

                # GAM ID chip — a right-clickable deep link (Copy Link Address
                # gets the full URL; Streamlit blocks JS so navigator.clipboard
                # isn't available, but the browser's native context menu is).
                if li_id_str:
                    if gam_link:
                        _gam_chip = (
                            f'<span class="nw-li-gam">GAM ID · '
                            f'<a href="{gam_link}" target="_blank" rel="noopener noreferrer" '
                            f'title="Click to open in GAM · right-click to copy link">'
                            f'{_esc(li_id_str)} ↗</a></span>'
                        )
                    else:
                        _gam_chip = f'<span class="nw-li-gam">GAM ID · {_esc(li_id_str)}</span>'
                else:
                    _gam_chip = ''

                # Spec-card values.
                _rev_v = pd.to_numeric(row.get("lifetime_revenue"), errors="coerce")
                if pd.isna(_rev_v):
                    _rev_v = pd.to_numeric(row.get("ad_server_cpm_and_cpc_revenue"), errors="coerce")
                _rev_s = f"${_rev_v:,.0f}" if pd.notna(_rev_v) else "—"
                _prog = pd.to_numeric(row.get("progress_pct"), errors="coerce")
                _bar_html = ""
                if pd.notna(_prog):
                    _bar_html = (f'<div class="nw-li-bar"><i style="width:'
                                 f'{max(0.0, min(100.0, float(_prog))):.0f}%"></i></div>')
                _seller_v = row.get("seller_ae")
                _seller_s = (_esc(_seller_v) if isinstance(_seller_v, str) and _seller_v.strip()
                             else "Programmatic")
                _flight_s = (f'{_pretty_date_cell(row.get("start_date"))} → '
                             f'{_pretty_date_cell(row.get("end_date"))}')

                status_html = _drawer_status_banner(row)
                chart_html = _drawer_delivery_chart(row)
                sm_html = _drawer_small_multiples(row)
                # Consolidated identity + spec card (Option C) — leads with the
                # friendly name, GAM-ID chip, and the raw convention string, then
                # hero pacing tiles + a tinted detail grid. The name + raw string
                # used to sit in a box at the *top*; the metadata was a flat grid
                # dumped at the bottom whose `Order` field repeated the raw name.
                return (
                    '<div class="nw-drawer">'
                    f'{status_html}'
                    f'{warn_html}'
                    f'<div class="nw-drawer-charts">{chart_html}{sm_html}</div>'
                    '<div class="nw-li-card">'
                    '<div class="nw-li-head">'
                    '<div><div class="nw-li-eyebrow">Line item</div>'
                    f'<div class="nw-li-name">{full_li or "—"}</div></div>'
                    f'{_gam_chip}'
                    '</div>'
                    '<div class="nw-li-hero">'
                    f'<div class="tile"><span class="k">Goal</span>'
                    f'<span class="big">{_kmb(row.get("impressions_goal"))}</span></div>'
                    f'<div class="tile"><span class="k">Delivered</span>'
                    f'<span class="big">{_kmb(row.get("lifetime_impressions_delivered"))}</span>'
                    f'{_bar_html}</div>'
                    f'<div class="tile"><span class="k">Remaining</span>'
                    f'<span class="big">{_kmb(row.get("remaining_impressions"))}</span></div>'
                    '</div>'
                    '<div class="nw-li-grid">'
                    f'<div class="cell"><span class="k">Flight</span><span class="v">{_flight_s}</span></div>'
                    f'<div class="cell"><span class="k">Format</span><span class="v">{_esc(row.get("ad_format") or "—")}</span></div>'
                    f'<div class="cell"><span class="k">CPM</span><span class="v">{cpm_s}</span></div>'
                    f'<div class="cell"><span class="k">Revenue</span><span class="v">{_rev_s}</span></div>'
                    f'<div class="cell"><span class="k">Clicks</span><span class="v">{_fmt_int_cell(clicks_raw)}</span></div>'
                    f'<div class="cell"><span class="k">Seller</span><span class="v">{_seller_s}</span></div>'
                    f'{_cdur_cell}'
                    '</div>'
                    '</div>'
                    f'{actions}'
                    '</div>'
                )

            # ── Build the HTML table row by row.
            _rows_html = []
            # Pre-compute viewability and CTR per row from lifetime counts.
            _vw_rate = None; _ctr_rate = None
            if "lifetime_viewable_imps" in view_gam.columns and "lifetime_measurable_imps" in view_gam.columns:
                _viewable  = pd.to_numeric(view_gam["lifetime_viewable_imps"], errors="coerce")
                _measurable = pd.to_numeric(view_gam["lifetime_measurable_imps"], errors="coerce")
                _vw_rate = (_viewable / _measurable * 100).where(_measurable > 0, other=None)
            if "lifetime_clicks" in view_gam.columns and "lifetime_impressions_delivered" in view_gam.columns:
                _clk = pd.to_numeric(view_gam["lifetime_clicks"], errors="coerce")
                _imp = pd.to_numeric(view_gam["lifetime_impressions_delivered"], errors="coerce")
                _ctr_rate = (_clk / _imp * 100).where(_imp > 0, other=None)

            # ── Pagination (mirrors the PMP table) ──────────────────────────
            # The Direct list renders into a single custom HTML grid; at a few
            # hundred filtered LIs that's a lot of DOM built per rerun. Page it
            # at 25 like the PMP table, with the same ← Prev / Page X of N /
            # Next → control above and below. view_gam is already sorted A–Z;
            # slicing by position preserves the index labels, so the per-row
            # _vw_rate / _ctr_rate lookups (by index.get_loc) still resolve.
            _DIRECT_PAGE_SIZE = 25
            _direct_count = len(view_gam)
            _direct_total_pages = max(1, math.ceil(_direct_count / _DIRECT_PAGE_SIZE))
            _direct_cur_page = max(0, min(int(st.session_state.get("direct_page", 0)),
                                          _direct_total_pages - 1))

            def _direct_go_prev():
                st.session_state["direct_page"] = max(0, _direct_cur_page - 1)

            def _direct_go_next():
                st.session_state["direct_page"] = min(_direct_total_pages - 1, _direct_cur_page + 1)

            _direct_page_slice = view_gam.iloc[
                _direct_cur_page * _DIRECT_PAGE_SIZE : (_direct_cur_page + 1) * _DIRECT_PAGE_SIZE
            ]

            # Iterate the current page; view_gam is already sorted A–Z by name.
            for _i, (_, row) in enumerate(_direct_page_slice.iterrows()):
                _li_name = row.get("line_item_name") or "—"
                _li_clean = re.sub(r"^#\d+\s+", "", str(_li_name))
                _ord_match = re.match(r"^(#\d+)\s+", str(_li_name))
                _ord_html = f'<span class="li-ord">{_ord_match.group(1)}</span>' if _ord_match else ""
                _sub = _subtitle(_li_clean, row.get("ad_format"), row.get("cpm_rate"))

                _rev = pd.to_numeric(row.get("lifetime_revenue"), errors="coerce") if "lifetime_revenue" in row else float("nan")
                if pd.isna(_rev) and "ad_server_cpm_and_cpc_revenue" in row:
                    _rev = pd.to_numeric(row.get("ad_server_cpm_and_cpc_revenue"), errors="coerce")
                _delivered = pd.to_numeric(row.get("lifetime_impressions_delivered"), errors="coerce") if "lifetime_impressions_delivered" in row else float("nan")
                _pace = row.get("pacing_pct")
                _pace_prior = row.get("pacing_prior_pct")
                # "new line item" = no delivery before the latest day (didn't
                # exist the prior day), so there's no real pace trend to show.
                _pace_is_new = dl.is_new_line_item(
                    row.get("lifetime_impressions_delivered"), row.get("impressions_1d"))
                _vw = _vw_rate.iloc[view_gam.index.get_loc(row.name)] if _vw_rate is not None else None
                _ctr = _ctr_rate.iloc[view_gam.index.get_loc(row.name)] if _ctr_rate is not None else None
                _vcr_val = row.get("vcr")
                # Benchmark band (e.g. "Video Preroll >30s"), not the
                # filter-facing format — thresholds key on the band.
                _fmt_str = row.get("_bench_format") or row.get("ad_format")
                _is_video = isinstance(_fmt_str, str) and "video" in _fmt_str.lower()
                _seller = row.get("seller_ae")
                _seller_html = (f'<span class="seller-prog">Prog.</span>'
                                if not (isinstance(_seller, str) and _seller.strip())
                                else _esc(_seller))
                _progress = row.get("progress_pct")

                # Display name = "<Advertiser> — <Campaign>" from the 14-field
                # LI naming convention (advertiser = token[7]; campaign =
                # token[8]). The campaign carries the placement/product
                # (Newsmakers-Centerstage, Qx65-Homepage-Takeover, Apple-News,
                # Custom-Audience-Pre-roll, …), so sibling LIs read distinctly
                # instead of collapsing to one "Infiniti - Display" (the
                # format, token[10], is redundant with — and often contradicts
                # — the canonical chip below, so it's intentionally dropped from
                # the name). See `project_gam_line_item_naming_convention.md`
                # for the full SOP; dl.line_item_display_name falls back
                # gracefully when the campaign token is missing.
                _display_name = dl.line_item_display_name(_li_clean)
                # DV Attention + SIVT + GIVT (current values + priors for
                # the Δ row below each cell). Lookups built once at view
                # load from dv_attention / dv_ivt tables. Rows with no DV
                # coverage get None → em-dash via the respective formatters.
                # Key matches how _dv_by_li / _sivt_by_li were indexed above.
                _dv_key  = str(row.get("line_item_id") or "") if _dv_li_col  == "line_item_id" else _li_clean
                _ivt_key = str(row.get("line_item_id") or "") if _ivt_li_col == "line_item_id" else _li_clean
                _attn       = _dv_by_li.get(_dv_key)          if _dv_by_li       else None
                _attn_prior = _dv_prior_by_li.get(_dv_key)    if _dv_prior_by_li else None
                _sivt       = _sivt_by_li.get(_ivt_key)       if _sivt_by_li     else None
                _sivt_prior = _sivt_prior_by_li.get(_ivt_key) if _sivt_prior_by_li else None
                _givt       = _givt_by_li.get(_ivt_key)       if _givt_by_li     else None
                _givt_prior = _givt_prior_by_li.get(_ivt_key) if _givt_prior_by_li else None

                # Viewability + CTR + VCR priors — same "lifetime minus 1d"
                # pattern Pace uses (see _prior_pacing). Computed inline so
                # we don't have to round-trip through a separate lookup;
                # numerators/denominators are right here on the row.
                def _lt_minus_1d_ratio(lt_num_col, d1_num_col, lt_den_col, d1_den_col, scale=100.0):
                    if not all(c in row.index for c in (lt_num_col, d1_num_col, lt_den_col, d1_den_col)):
                        return None
                    return dl.lt_minus_1d_ratio(
                        pd.to_numeric(row.get(lt_num_col),  errors="coerce"),
                        pd.to_numeric(row.get(d1_num_col),  errors="coerce"),
                        pd.to_numeric(row.get(lt_den_col),  errors="coerce"),
                        pd.to_numeric(row.get(d1_den_col),  errors="coerce"),
                        scale=scale,
                    )
                _vw_prior  = _lt_minus_1d_ratio("lifetime_viewable_imps",   "viewable_imps_1d",
                                                "lifetime_measurable_imps", "measurable_imps_1d")
                _ctr_prior = _lt_minus_1d_ratio("lifetime_clicks",          "clicks_1d",
                                                "lifetime_impressions_delivered", "impressions_1d")
                _vcr_prior = _lt_minus_1d_ratio("lifetime_video_completes", "video_completes_1d",
                                                "lifetime_video_starts",    "video_starts_1d")

                # Revenue + Impressions deltas — % change of latest day's
                # cumulative vs "everything before yesterday". Different
                # math than the ratios above: volumes need % change, not
                # pp. `new_line_threshold=None` because a doubling on
                # these columns is real signal, not a "new line item" flag.
                def _volume_pct_delta(lifetime_col: str, day_col: str):
                    if lifetime_col not in row.index or day_col not in row.index:
                        return None
                    return dl.volume_pct_delta(
                        pd.to_numeric(row.get(lifetime_col), errors="coerce"),
                        pd.to_numeric(row.get(day_col),      errors="coerce"),
                    )
                _rev_pct   = _volume_pct_delta("lifetime_revenue", "revenue_1d")
                _imp_pct   = _volume_pct_delta("lifetime_impressions_delivered", "impressions_1d")
                _rev_delta = _delta_below_html(_rev_pct, lower_is_worse=True,
                                                unit="%", new_line_threshold=None)
                _imp_delta = _delta_below_html(_imp_pct, lower_is_worse=True,
                                                unit="%", new_line_threshold=None)

                # Solution-3 mobile card (rendered into every summary; CSS shows
                # it only ≤640px and hides the 12 grid cells). Reuses the row's
                # pace banding and the 7-day delivery series.
                _pace_num_m = pd.to_numeric(_pace, errors="coerce")
                if pd.isna(_pace_num_m):
                    _m_psev, _m_pw = "neutral", 0.0
                else:
                    _pn_m = float(_pace_num_m)
                    if _pn_m < _pacing_critical:
                        _m_psev = "red"
                    elif _pn_m < _pacing_warn_low or _pn_m > _pacing_warn_high:
                        _m_psev = "amber"
                    else:
                        _m_psev = "green"
                    _m_pw = max(0.0, min(_pn_m, 100.0))
                _deliv_series_m = _row_daily_imp_series(row)
                _deliv_spark_m = _sparkline_svg(_deliv_series_m, klass="") if _deliv_series_m else ""
                _spark_block_m = (f'<div class="m-spark">{_deliv_spark_m}'
                                  '<div class="m-spark-l">delivery 7d</div></div>'
                                  if _deliv_spark_m else '<div class="m-spark"></div>')
                _row_m = (
                    '<div class="nw-row-m">'
                    f'<div class="m-main"><div class="m-name">{_ord_html}{_esc(_display_name)}</div>'
                    f'<div class="m-sub">{_esc(_sub) or "—"}</div>'
                    '<div class="m-pbar-l">pace</div>'
                    f'<div class="m-pbar"><i class="{_m_psev}" style="width:{_m_pw:.0f}%"></i></div></div>'
                    f'{_spark_block_m}'
                    f'<div class="m-right"><div class="m-rev">{_revenue_html(_rev)}</div>'
                    f'{_pace_html(_pace, _pace_prior, _pace_is_new)}</div>'
                    '</div>'
                )

                _rows_html.append(
                    '<details class="nw-row" name="cmprow">'
                    '<summary>'
                    f'<div><div class="li-name"><span class="nw-chev">›</span>{_ord_html}{_esc(_display_name)}</div>'
                    f'<div class="li-sub">{_esc(_sub) or "—"}</div></div>'
                    f'<div class="num">{_revenue_html(_rev)}{_rev_delta}</div>'
                    f'<div class="num">{_delivered_html(_delivered)}{_imp_delta}</div>'
                    f'<div class="num center">{_pace_html(_pace, _pace_prior, _pace_is_new)}</div>'
                    f'<div class="num">{_viewability_html(_vw, _fmt_str, p_prior=_vw_prior)}</div>'
                    f'<div class="num center">{_attention_html(_attn, prior=_attn_prior)}</div>'
                    f'<div class="num center">{_ivt_html(_sivt, prior=_sivt_prior)}</div>'
                    f'<div class="num center">{_ivt_html(_givt, prior=_givt_prior)}</div>'
                    f'<div class="num center">{_ctr_html(_ctr, _fmt_str, p_prior=_ctr_prior)}</div>'
                    f'<div class="num center">{_vcr_html(_vcr_val, _is_video, _fmt_str, p_prior=_vcr_prior)}</div>'
                    f'<div>{_seller_html}</div>'
                    f'<div>{_progress_html(_progress)}</div>'
                    + _row_m +
                    '</summary>'
                    + _drawer_html(row) +
                    '</details>'
                )

            _direct_tbl_sub = (
                f"· {_direct_count} line item{'s' if _direct_count != 1 else ''} · sorted A–Z"
            )
            if _direct_total_pages > 1:
                _direct_tbl_sub += f" · page {_direct_cur_page + 1}/{_direct_total_pages}"
            _table_html = (
                '<div class="nw-tbl-wrap nw-tbl-direct">'
                '<div class="nw-tbl-head">'
                '<div class="nw-tbl-title">Direct campaigns'
                f'<span class="nw-tbl-sub">{_direct_tbl_sub}</span></div>'
                '<div class="nw-legend">'
                '<span><span class="nw-legend-dot" style="background:var(--state-critical)"></span>under</span>'
                '<span><span class="nw-legend-dot" style="background:var(--state-warning)"></span>off-target</span>'
                '<span><span class="nw-legend-dot" style="background:var(--state-positive-muted)"></span>healthy</span>'
                '<span>— = N/A</span>'
                '</div>'
                '</div>'
                '<div class="nw-rows">'
                '<div class="nw-row-header">'
                '<div>Line item</div>'
                '<div class="num">Revenue</div>'
                '<div class="num">Delivered</div>'
                '<div class="num center">Pace</div>'
                '<div class="num">Viewable</div>'
                '<div class="num center" title="DV Attention Index — 100 = industry median">Attention</div>'
                '<div class="num center" title="Sophisticated Invalid Traffic — impression-weighted: Σ SIVT Monitored Ads / Σ all Monitored Ads. Industry tolerance ≤ 3%. SIVT includes data center, bot fraud, hijacked devices, emulators, app/site fraud, injected ads, laundering.">SIVT</div>'
                '<div class="num center" title="General Invalid Traffic — impression-weighted: Σ GIVT Monitored Ads / Σ all Monitored Ads. Industry tolerance ≤ 3%. GIVT is self-identifying invalid: declared bots, known crawlers, etc.">GIVT</div>'
                '<div class="num center">CTR</div>'
                '<div class="num center">VCR</div>'
                '<div>Seller</div>'
                '<div>Progress</div>'
                '</div>'
                + "".join(_rows_html) +
                '</div>'
                '</div>'
            )
            # Compact one-row pager above + below the table (see _compact_pager).
            _direct_pg_main = f"Page {_direct_cur_page + 1} of {_direct_total_pages}"
            _direct_pg_sub = f"{len(_direct_page_slice)} of {_direct_count} shown"
            _compact_pager("direct_top", _direct_cur_page, _direct_total_pages,
                           _direct_go_prev, _direct_go_next, _direct_pg_main, _direct_pg_sub)

            st.markdown(_table_html, unsafe_allow_html=True)

            _compact_pager("direct_bot", _direct_cur_page, _direct_total_pages,
                           _direct_go_prev, _direct_go_next, _direct_pg_main)

    # ── Spend momentum ───────────────────────────────────────────────────────
    # Three PMP sub-sections (GAM, Magnite, Pubmatic — PD + PA only) plus
    # Direct campaigns. Each compares revenue over the most-recent 3 dates
    # vs the prior 3 dates, sorted by delta descending. Quick reference for
    # the weekly programmatic email.

    def _sp_esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _sp_dollar(v):
        return f"${v:,.0f}" if pd.notna(v) and abs(v) >= 0.5 else "—"

    def _sp_rows_for(summary_df, name_col, wrap=None):
        rows = []
        for _, _r in summary_df.iterrows():
            _dlt  = _r["_delta"]
            _pct  = _r["_pct"]
            _sign = "+" if _dlt > 0 else ""
            _dlt_s = f"{_sign}${abs(_dlt):,.0f}" if abs(_dlt) >= 0.5 else "—"
            _pct_s = f" ({_sign}{_pct:.0f}%)" if pd.notna(_pct) else ""
            # Newsweek state tokens: gaining = positive, losing = critical,
            # flat = muted ink.
            _cls = ("up" if _dlt > 0 else ("down" if _dlt < -0.5 else "flat"))
            # Identifiable two-tier label (advertiser bold over muted campaign)
            # from the same Advertiser — Campaign name the PMP table shows, so
            # the row isn't a clipped "N…". Direct's advertiser-only group key
            # falls through pmp_deal_display_name as just the advertiser.
            _full = dl.pmp_deal_display_name(_r[name_col] or "")[0]
            _adv, _camp = (_full.split(" — ", 1) + [""])[:2] if " — " in _full else (_full, "")
            _camp_html = f'<span class="sp-camp"> — {_sp_esc(_camp)}</span>' if _camp else ""
            _sp_html = (
                '<div class="sp-row">'
                f'<div class="sp-nm"><span class="sp-adv">{_sp_esc(_adv)}</span>{_camp_html}</div>'
                '<div class="sp-met">'
                f'<span class="sp-flow">${_r["_prior_rev"]:,.0f} → ${_r["_recent_rev"]:,.0f}</span>'
                f'<span class="sp-dlt {_cls}">{_dlt_s}<span class="sp-pct">{_pct_s}</span></span>'
                '</div></div>'
            )
            rows.append(wrap(_sp_html, _r[name_col]) if wrap else _sp_html)
        return rows

    # Spend-momentum windowing/aggregation is decision logic — it lives in
    # dashboard_logic.spend_momentum (adaptive last-N-vs-prior-N, tested). It
    # grades 7-vs-7 once the daily pulls carry 14 days and degrades to the old
    # 3-vs-3 on the ~7-day cache. _sp_rows_for below is the render half.

    _PA_PD = {"Private Auction", "Preferred Deal", "PA", "PD"}

    # ── PMP spend momentum (PD + PA only) ─────────────────────────────────────
    # GAM + Magnite + Pubmatic in ONE combined list — no per-SSP buckets, no
    # Direct campaigns (PMP-only, per Roger 2026-06). Each source is normalized
    # to (deal, _date, _rev), concatenated, then run through one momentum pass.
    _pmp_parts = []

    try:
        _gam_pmp_mom = load("gam_pmp_deals").copy()
    except Exception:
        _gam_pmp_mom = pd.DataFrame()
    if (not _gam_pmp_mom.empty and "date" in _gam_pmp_mom.columns
            and "ad_server_cpm_and_cpc_revenue" in _gam_pmp_mom.columns):
        _gam_ch = next((c for c in _gam_pmp_mom.columns if "channel" in c.lower()), None)
        if _gam_ch:
            _gam_pmp_mom = _gam_pmp_mom[_gam_pmp_mom[_gam_ch].isin(_PA_PD)]
        _gam_dcol = next((c for c in _gam_pmp_mom.columns if "deal_name" in c), "order_name")
        _gam_pmp_mom = _gam_pmp_mom.dropna(subset=[_gam_dcol])
        if not _gam_pmp_mom.empty:
            _pmp_parts.append(pd.DataFrame({
                "deal":  _gam_pmp_mom[_gam_dcol].astype(str),
                "_date": _gam_pmp_mom["date"],
                "_rev":  pd.to_numeric(_gam_pmp_mom["ad_server_cpm_and_cpc_revenue"], errors="coerce"),
            }))

    try:
        _mag_mom = load("magnite_deal_daily").copy()
    except Exception:
        _mag_mom = pd.DataFrame()
    if (not _mag_mom.empty and "date" in _mag_mom.columns and "deal" in _mag_mom.columns
            and "publisher_gross_revenue" in _mag_mom.columns):
        _mag_mom["_deal_type"] = _mag_mom["deal"].apply(
            lambda d: _parse_deal(str(d) if pd.notna(d) else "")["deal_type_label"])
        _mag_mom = _mag_mom[_mag_mom["_deal_type"].isin(_PA_PD)].dropna(subset=["deal"])
        if not _mag_mom.empty:
            _pmp_parts.append(pd.DataFrame({
                "deal":  _mag_mom["deal"].astype(str),
                "_date": _mag_mom["date"],
                "_rev":  pd.to_numeric(_mag_mom["publisher_gross_revenue"], errors="coerce"),
            }))

    try:
        _pub_mom = load("pubmatic_deals").copy()
    except Exception:
        _pub_mom = pd.DataFrame()
    if not _pub_mom.empty and "date" in _pub_mom.columns and "revenue" in _pub_mom.columns:
        _pub_mom["deal_label"] = (
            _pub_mom["deal"].fillna(_pub_mom.get("publisher_deal_id"))
            if "deal" in _pub_mom.columns else _pub_mom.get("publisher_deal_id"))
        if "deal_type" in _pub_mom.columns:
            _pub_mom["_deal_type"] = _pub_mom["deal_type"].map(
                lambda v: _cfg.get("deal_type_aliases", {}).get(v, v) if pd.notna(v) else None)
            _fb = _pub_mom["_deal_type"].isna() | ~_pub_mom["_deal_type"].isin(_PA_PD)
            _pub_mom.loc[_fb, "_deal_type"] = _pub_mom.loc[_fb, "deal"].apply(
                lambda d: _parse_deal(str(d) if pd.notna(d) else "")["deal_type_label"])
        else:
            _pub_mom["_deal_type"] = _pub_mom["deal"].apply(
                lambda d: _parse_deal(str(d) if pd.notna(d) else "")["deal_type_label"])
        _pub_mom = _pub_mom[_pub_mom["_deal_type"].isin(_PA_PD)].dropna(subset=["deal_label"])
        if not _pub_mom.empty:
            _pmp_parts.append(pd.DataFrame({
                "deal":  _pub_mom["deal_label"].astype(str),
                "_date": _pub_mom["date"],
                "_rev":  pd.to_numeric(_pub_mom["revenue"], errors="coerce"),
            }))

    _pmp_mom_rows = []
    _pmp_summ = pd.DataFrame()
    _total_gaining = 0
    _total_losing  = 0
    if _pmp_parts:
        _pmp_combined = pd.concat(_pmp_parts, ignore_index=True)
        _pmp_summ, _total_gaining, _total_losing = dl.spend_momentum(_pmp_combined, "deal", "_rev")
        if not _pmp_summ.empty:
            _pmp_mom_rows = _sp_rows_for(_pmp_summ, "deal")

    # Spend-momentum rows render inside the "PMP signals" accordion under the
    # PMP section below (moved there 2026-06, Option 2). _sp_css is defined here
    # next to the row builder and consumed by that accordion's momentum row.
    _sp_css = (
        '<style>'
        '.sp-row{padding:8px 6px;border-bottom:1px solid var(--border)}'
        '.sp-nm{font-size:13px;line-height:1.25}'
        '.sp-adv{font-weight:700;color:var(--text-primary)}'
        '.sp-camp{color:var(--text-muted)}'
        '.sp-met{display:flex;justify-content:space-between;align-items:baseline;'
        'gap:10px;margin-top:2px}'
        '.sp-flow{font-size:11px;color:var(--text-muted);font-variant-numeric:tabular-nums}'
        '.sp-dlt{font-weight:700;font-size:12.5px;font-variant-numeric:tabular-nums}'
        '.sp-dlt.up{color:var(--state-positive-muted)}'
        '.sp-dlt.down{color:var(--state-critical)}'
        '.sp-dlt.flat{color:var(--text-muted)}'
        '.sp-pct{opacity:.7;font-weight:400;font-size:11px}'
        '</style>'
    )

    # ── Section 2: PMP deals ─────────────────────────────────────────────
    # Small section header (eyebrow + 18px h3 — never bigger than the page H1).
    st.markdown(
        '<hr class="nw-section-div"/>'
        '<div class="nw-section-eyebrow">Programmatic</div>'
        '<div class="nw-section-h3">PMP deals</div>',
        unsafe_allow_html=True,
    )

    # Build filter controls unconditionally — they must render even when Pubmatic is absent.
    _pmp_ssps_available = [s["name"] for s in _cfg["ssps"] if s.get("enabled", True)]
    _pmp_deal_types_available = sorted(set(
        dt for s in _cfg["ssps"] if s.get("enabled", True) for dt in s.get("deal_types", [])
    ))
    # DSP / Format / Deal Source / Team options come from the previous render via session_state (two-pass pattern).
    _pmp_dsps_opts        = st.session_state.get("_pmp_dsps_opts", [])
    _pmp_formats_opts     = st.session_state.get("_pmp_formats_opts", [])
    _pmp_deal_sources_opts = st.session_state.get("_pmp_deal_sources_opts", [])
    _pmp_teams_opts        = st.session_state.get("_pmp_teams_opts", [])
    # Filters as a "Filters" popover + removable chips (same treatment as the
    # Direct section above). The six controls live in the popover; applied
    # filters surface as chips beside the trigger and clear on click. Widget
    # keys are unchanged, so the PMP filtering below is untouched.
    def _pmp_ms_summary(vals):
        return str(vals[0]) if len(vals) == 1 else f"{vals[0]} +{len(vals) - 1}"
    _pmp_filter_specs = [
        ("campaigns_pmp_deal_type_filter",   "Deal type"),
        ("campaigns_pmp_ssp_filter",         "SSP"),
        ("campaigns_pmp_dsp_filter",         "DSP"),
        ("campaigns_pmp_format_filter",      "Format"),
        ("campaigns_pmp_deal_source_filter", "Deal source"),
        ("campaigns_pmp_team_filter",        "Team"),
    ]
    # Deal Type defaults to PA / PD / PMP (Programmatic Guaranteed excluded) when
    # the table first loads. Seeded once into the multiselect's session_state so
    # it picks the default up without a default= arg; clearing the Deal type chip
    # (or "Clear all filters") restores All, PG included.
    if "campaigns_pmp_deal_type_filter" not in st.session_state:
        st.session_state["campaigns_pmp_deal_type_filter"] = [
            t for t in ("Private Auction", "Preferred Deal", "Private Marketplace")
            if t in _pmp_deal_types_available
        ]
    _pmp_chips = []
    for _pk, _plbl in _pmp_filter_specs:
        _pv = st.session_state.get(_pk, [])
        if _pv:
            _pmp_chips.append((_pk, f"{_plbl}: {_pmp_ms_summary(_pv)}"))
    _pmp_n_active = len(_pmp_chips)

    def _pmp_clear_filter(state_key):
        st.session_state[state_key] = []

    def _pmp_clear_all_filters():
        for _ck, _ in _pmp_filter_specs:
            st.session_state[_ck] = []

    with st.container(horizontal=True, key="nw_pmp_filter_bar"):
        _pmp_pop = st.popover(
            "Filters" if not _pmp_n_active else f"Filters · {_pmp_n_active}",
            icon=":material/tune:",
        )
        for _ck, _txt in _pmp_chips:
            st.button(_txt, key=f"nw_pmp_chip_{_ck}",
                      icon=":material/close:", icon_position="right",
                      on_click=_pmp_clear_filter, args=(_ck,))

    with _pmp_pop:
        st.markdown('<div class="nw-filter-label">Deal Type</div>', unsafe_allow_html=True)
        sel_pmp_deal_types = st.multiselect(
            "Deal Type", _pmp_deal_types_available,
            key="campaigns_pmp_deal_type_filter",
            label_visibility="collapsed", placeholder="All",
        )
        st.markdown('<div class="nw-filter-label">SSP</div>', unsafe_allow_html=True)
        sel_pmp_ssps = st.multiselect(
            "SSP", _pmp_ssps_available,
            key="campaigns_pmp_ssp_filter",
            label_visibility="collapsed", placeholder="All",
        )
        st.markdown('<div class="nw-filter-label">DSP</div>', unsafe_allow_html=True)
        sel_pmp_dsps = st.multiselect(
            "DSP", _pmp_dsps_opts,
            key="campaigns_pmp_dsp_filter",
            label_visibility="collapsed", placeholder="All",
        )
        st.markdown('<div class="nw-filter-label">Format</div>', unsafe_allow_html=True)
        sel_pmp_formats = st.multiselect(
            "Format", _pmp_formats_opts,
            key="campaigns_pmp_format_filter",
            label_visibility="collapsed", placeholder="All",
        )
        st.markdown('<div class="nw-filter-label">Deal Source</div>', unsafe_allow_html=True)
        sel_pmp_deal_sources = st.multiselect(
            "Deal Source", _pmp_deal_sources_opts,
            key="campaigns_pmp_deal_source_filter",
            label_visibility="collapsed", placeholder="All",
        )
        st.markdown('<div class="nw-filter-label">Team</div>', unsafe_allow_html=True)
        sel_pmp_teams = st.multiselect(
            "Team", _pmp_teams_opts,
            key="campaigns_pmp_team_filter",
            label_visibility="collapsed", placeholder="All",
        )
        st.markdown('<div class="nw-filter-label">Options</div>', unsafe_allow_html=True)
        # Default off — read later to filter the table view (_pmp_display).
        _show_low_rev = st.checkbox(
            "Show deals under $100/day",
            value=False,
            key="pmp_show_low_rev",
        )
        if _pmp_n_active:
            st.button("Clear all filters", key="nw_pmp_clear_all_filters",
                      type="tertiary", icon=":material/close:",
                      on_click=_pmp_clear_all_filters)

    # ── Pubmatic ──────────────────────────────────────────────────────────
    pmp_summary = pd.DataFrame()
    try:
        pmp_df = load("pubmatic_deals") if _ssp_enabled.get("Pubmatic", True) else pd.DataFrame()
    except Exception:
        pmp_df = pd.DataFrame()
    if pmp_df.empty:
        if _ssp_enabled.get("Pubmatic", True):
            st.info("No Pubmatic PMP data yet — run refresh_cache.py to populate pubmatic_deals.")
    else:
        pmp_df = pmp_df.copy()
        pmp_df["date"] = pd.to_datetime(pmp_df["date"]).dt.date
        # pubmatic_deals now retains 14 days (week-vs-week momentum), but the PMP
        # summary stays a fixed 7-day view so its totals don't move.
        pmp_df = dl.window_last_n_days(pmp_df, n=7)
        if "deal" not in pmp_df.columns:
            pmp_df["deal"] = None
        if "publisher_deal_id" not in pmp_df.columns:
            pmp_df["publisher_deal_id"] = None
        pmp_df["deal_label"] = pmp_df["deal"].fillna(pmp_df["publisher_deal_id"]).fillna(pmp_df["deal_meta_id"].astype(str))
        pmp_df["seller_ae"] = (
            pmp_df["deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
            .map(AE_NAMES)
        )
        if selected_seller != "All":
            pmp_df = pmp_df[pmp_df["seller_ae"] == selected_seller]
        pmp_df = _apply_am_filter(pmp_df, "seller_ae")
        # _parse_deal() is primary — it reads the type code from the deal name (PD_, PA_, PG_).
        # channelTypeId is fallback only for deals whose names have no recognizable type code.
        _dt_aliases = _cfg.get("deal_type_aliases", {})
        pmp_df["deal_type_label"] = pmp_df["deal"].apply(lambda d: _parse_deal(d)["deal_type_label"])
        if "deal_type" in pmp_df.columns:
            _fb = pmp_df["deal_type_label"].isna()
            pmp_df.loc[_fb, "deal_type_label"] = pmp_df.loc[_fb, "deal_type"].map(
                lambda v: _dt_aliases.get(v, v) if pd.notna(v) and str(v).strip() else None
            )
        if sel_pmp_deal_types:
            pmp_df = pmp_df[pmp_df["deal_type_label"].isin(sel_pmp_deal_types)]
        pmp_df["ssp"] = "Pubmatic"
        _pub_grp = ["ssp", "deal_label", "deal_type_label", "ad_format", "dsp", "seller_ae"]
        if "deal_source" in pmp_df.columns:
            _pub_grp.append("deal_source")
        pmp_summary = (
            pmp_df.groupby(_pub_grp, dropna=False)
            .agg(
                paid_impressions=("paid_impressions", "sum"),
                revenue=("revenue", "sum"),
                ecpm=("ecpm", "mean"),
                win_rate=("win_rate", "mean"),
                total_requests=("total_requests", "sum"),
                non_zero_bid_responses=("non_zero_bid_responses", "sum"),
            )
            .reset_index()
            .rename(columns={
                "ssp": "SSP", "seller_ae": "Seller", "deal_label": "Deal",
                "deal_type_label": "Deal Type", "ad_format": "Format", "dsp": "DSP",
                "deal_source": "Deal Source",
                "paid_impressions": "Paid Impressions", "revenue": "Revenue",
                "ecpm": "eCPM", "win_rate": "Win Rate %",
                "total_requests": "Total Requests", "non_zero_bid_responses": "Bid Responses",
            })
        )

    # Add GAM PA / PD / PG deals from the dedicated gam_pmp_deals table
    _gam_summary = pd.DataFrame()
    _gam_ssp_cfg  = next((s for s in _cfg["ssps"] if s["name"] == "GAM"), {})
    _gam_col_map  = _gam_ssp_cfg.get("columns", {})
    _gam_cfg_deal_types = _gam_ssp_cfg.get("deal_types", [])
    _gam_deal_types = [t for t in (sel_pmp_deal_types or _gam_cfg_deal_types) if t in _gam_cfg_deal_types]
    if _gam_deal_types and _ssp_enabled.get("GAM", True):
        try:
            _gam_raw = load("gam_pmp_deals").copy()
            _deal_col = next((c for c in _gam_raw.columns if "deal_name" in c or c == "deal"), None)
            if not _gam_raw.empty and _deal_col:
                _gam_raw = _gam_raw.rename(columns={_deal_col: "deal_name"})

                # Deal Type: use channel column mapped through settings aliases; fall back to _parse_deal()
                _ch_col = next((c for c in _gam_raw.columns if "channel" in c.lower()), None)
                _canonical_types = set(_cfg.get("deal_type_codes", {}).values())
                _channel_map = {dt: dt for dt in _canonical_types}
                _channel_map.update(_cfg.get("deal_type_aliases", {}))
                if _ch_col:
                    _gam_raw["deal_type_label"] = (
                        _gam_raw[_ch_col].map(_channel_map)
                        .fillna(_gam_raw["deal_name"].apply(
                            lambda d: _parse_deal(str(d) if pd.notna(d) else "")["deal_type_label"]
                        ))
                    )
                else:
                    _gam_raw["deal_type_label"] = _gam_raw["deal_name"].apply(
                        lambda d: _parse_deal(str(d) if pd.notna(d) else "")["deal_type_label"]
                    )

                def _with_fallback(df, col_cfg, parse_key):
                    """Use settings-mapped column when valid; fall back to _parse_deal()."""
                    _auto = df["deal_name"].apply(
                        lambda d: _parse_deal(str(d) if pd.notna(d) else "")[parse_key]
                    )
                    if col_cfg in ("[auto]", "N/A", "", None) or col_cfg not in df.columns:
                        return _auto
                    _from_api = df[col_cfg].str.strip().replace("", None)
                    return _from_api.where(_from_api.notna(), _auto)

                _gam_raw["ad_format"] = _with_fallback(_gam_raw, _gam_col_map.get("Format", "[auto]"), "ad_format")
                _gam_raw["dsp"]       = _with_fallback(_gam_raw, _gam_col_map.get("DSP", ""),       "dsp")

                # Build order_name → salesperson lookup from gam_campaigns (Order/User API data)
                try:
                    _sp_df = load("gam_campaigns")[["order_name", "salesperson"]].dropna(subset=["order_name", "salesperson"])
                    _sp_df = _sp_df.drop_duplicates("order_name").copy()
                    _sp_df["salesperson"] = _sp_df["salesperson"].apply(_parse_gam_salesperson)
                    _gam_sp_map = dict(zip(_sp_df["order_name"], _sp_df["salesperson"]))
                except Exception:
                    _gam_sp_map = {}

                _seller_cfg = _gam_col_map.get("Seller", "[auto]")
                if _seller_cfg not in ("[auto]", "N/A", "", None) and _seller_cfg in _gam_raw.columns:
                    _gam_raw["seller_ae"] = _gam_raw[_seller_cfg].map(AE_NAMES)
                else:
                    # PD/PG: use GAM order.salesperson (API is source of truth).
                    # PA: regex only — PA orders run through Ad Exchange backstop with no AE assigned.
                    _api_seller = (
                        _gam_raw["order_name"].map(_gam_sp_map)
                        if "order_name" in _gam_raw.columns and _gam_sp_map
                        else pd.Series([None] * len(_gam_raw), index=_gam_raw.index)
                    )
                    _ae_regex = dl.AE_TOKEN_RE
                    _regex_from_deal = _gam_raw["deal_name"].str.extract(_ae_regex, expand=False).map(AE_NAMES)
                    _regex_from_order = (
                        _gam_raw["order_name"].str.extract(_ae_regex, expand=False).map(AE_NAMES)
                        if "order_name" in _gam_raw.columns else pd.Series([None] * len(_gam_raw), index=_gam_raw.index)
                    )
                    _regex_seller = _regex_from_deal.fillna(_regex_from_order)
                    _is_pa = _gam_raw["deal_type_label"] == "Private Auction"
                    _gam_raw["seller_ae"] = _api_seller.where(~_is_pa & _api_seller.notna(), _regex_seller)

                _gam_deals = _gam_raw[_gam_raw["deal_type_label"].isin(_gam_deal_types)].copy()
                if selected_seller != "All":
                    _gam_deals = _gam_deals[_gam_deals["seller_ae"] == selected_seller]
                _gam_deals = _apply_am_filter(_gam_deals, "seller_ae")
                if sel_pmp_deal_types:
                    _gam_deals = _gam_deals[_gam_deals["deal_type_label"].isin(sel_pmp_deal_types)]
                # gam_pmp_deals now retains 14 days (week-vs-week momentum); the
                # summary totals stay a fixed 7-day view.
                _gam_deals = dl.window_last_n_days(_gam_deals, n=7)
                if not _gam_deals.empty:
                    _rev_col  = next((c for c in (_gam_col_map.get("Revenue", ""), "ad_server_cpm_and_cpc_revenue", "revenue") if c and c in _gam_deals.columns), None)
                    _imp_col  = next((c for c in (_gam_col_map.get("Paid Impressions", ""), "ad_server_impressions", "impressions") if c and c in _gam_deals.columns), None)
                    _ecpm_col = next((c for c in (_gam_col_map.get("eCPM", ""), "ad_server_average_ecpm", "ecpm") if c and c in _gam_deals.columns), None)
                    for _c in (_rev_col, _imp_col, _ecpm_col):
                        if _c:
                            _gam_deals[_c] = pd.to_numeric(_gam_deals[_c], errors="coerce")
                    _agg_kwargs = {}
                    if _imp_col:  _agg_kwargs["paid_impressions"] = (_imp_col, "sum")
                    if _rev_col:  _agg_kwargs["revenue"]          = (_rev_col, "sum")
                    if _ecpm_col: _agg_kwargs["ecpm"]             = (_ecpm_col, "mean")
                    _gam_agg = (
                        _gam_deals.groupby(["deal_name", "deal_type_label", "ad_format", "dsp", "seller_ae"], dropna=False)
                        .agg(**_agg_kwargs)
                        .reset_index()
                    )
                    # Enrich with PA deal metadata (floor price, status) from gam_pa_metadata
                    try:
                        _pa_meta = load("gam_pa_metadata")
                        if not _pa_meta.empty and "deal_name" in _pa_meta.columns:
                            _pa_lookup = (
                                _pa_meta[["deal_name", "floor_price_usd", "deal_status"]]
                                .drop_duplicates("deal_name")
                            )
                            _gam_agg = _gam_agg.merge(_pa_lookup, on="deal_name", how="left")
                    except Exception:
                        pass
                    _gam_agg["SSP"] = "GAM"
                    _gam_agg["Win Rate %"] = None
                    _gam_agg["Total Requests"] = None
                    _gam_agg["Bid Responses"] = None
                    _gam_summary = _gam_agg.rename(columns={
                        "seller_ae": "Seller", "deal_name": "Deal",
                        "deal_type_label": "Deal Type", "ad_format": "Format", "dsp": "DSP",
                        "paid_impressions": "Paid Impressions", "revenue": "Revenue", "ecpm": "eCPM",
                        "floor_price_usd": "Floor CPM", "deal_status": "Deal Status",
                    })
        except Exception:
            pass

    # Add Magnite PA / PD / PMP deals (PG only comes from GAM)
    _magnite_summary = pd.DataFrame()
    _mag_cfg_deal_types = next((s["deal_types"] for s in _cfg["ssps"] if s["name"] == "Magnite"), [])
    _mag_types = [t for t in (sel_pmp_deal_types or _mag_cfg_deal_types) if t in _mag_cfg_deal_types]
    if _mag_types and _ssp_enabled.get("Magnite", True):
        try:
            _mag_df = load("magnite_deal_daily").copy()
            # Merge demand fields from the separate report (demand_type_ad_resp and
            # revenue_source can't be fetched alongside auction metrics in the same call).
            _mag_demand = load("magnite_deal_demand")
            if not _mag_demand.empty and "deal_id" in _mag_demand.columns and "deal_id" in _mag_df.columns:
                _demand_cols = [c for c in ["deal_id", "demand_type_ad_resp", "revenue_source"] if c in _mag_demand.columns]
                _demand_lookup = (
                    _mag_demand[_demand_cols]
                    .drop_duplicates(subset=["deal_id"])
                )
                _mag_df = _mag_df.merge(_demand_lookup, on="deal_id", how="left")
            # Deals with no match in magnite_deal_demand (typically zero-impression
            # rows where Magnite tracks the deal_id in daily but didn't return demand
            # metadata) default to "Publisher Deals". The previous fallback tried to
            # derive from deal-name position-3 == "Magnite", but position-3 in
            # Newsweek's naming is the SSP (Magnite), not the deal source — all of
            # Newsweek's PA traffic is publisher-sourced regardless of which SSP
            # routes it. If a deal is genuinely Magnite-sourced, the API returns
            # that explicitly and this fallback doesn't apply.
            if "revenue_source" in _mag_df.columns:
                _mag_df["revenue_source"] = _mag_df["revenue_source"].fillna("Publisher Deals")
            if not _mag_df.empty and "deal" in _mag_df.columns:
                _dt_aliases = _cfg.get("deal_type_aliases", {})
                # _parse_deal() is primary; demand_type_ad_resp is fallback for unrecognized deal names.
                _mag_df["deal_type_label"] = _mag_df["deal"].apply(lambda d: _parse_deal(d)["deal_type_label"])
                if "demand_type_ad_resp" in _mag_df.columns:
                    _fb = _mag_df["deal_type_label"].isna()
                    _mag_df.loc[_fb, "deal_type_label"] = _mag_df.loc[_fb, "demand_type_ad_resp"].map(
                        lambda v: _dt_aliases.get(v, v) if pd.notna(v) and str(v).strip() not in ("", "-N/A-") else None
                    )
                _mag_df = _mag_df[_mag_df["deal_type_label"].isin(_mag_types)]
                _mag_df["ssp"] = "Magnite"
                _mag_df["seller_ae"] = (
                    _mag_df["deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                    .map(AE_NAMES)
                )
                # Magnite deal API doesn't return partner/ad_format — derive from deal name.
                if "ad_format" not in _mag_df.columns:
                    _mag_df["ad_format"] = _mag_df["deal"].apply(lambda d: _parse_deal(d)["ad_format"])
                if "partner" not in _mag_df.columns:
                    _mag_df["partner"] = _mag_df["deal"].apply(lambda d: _parse_deal(d)["dsp"])
                if selected_seller != "All":
                    _mag_df = _mag_df[_mag_df["seller_ae"] == selected_seller]
                _mag_df = _apply_am_filter(_mag_df, "seller_ae")
                if not _mag_df.empty:
                    # magnite_deal_daily now retains 14 days (week-vs-week
                    # momentum); the summary totals stay a fixed 7-day view.
                    _mag_df = dl.window_last_n_days(_mag_df, n=7)
                    _mag_grp = ["ssp", "deal", "deal_type_label", "ad_format", "partner", "seller_ae"]
                    if "revenue_source" in _mag_df.columns:
                        _mag_grp.append("revenue_source")
                    _mag_agg = (
                        _mag_df.groupby(_mag_grp, dropna=False)
                        .agg(
                            paid_impressions=("paid_impression", "sum"),
                            revenue=("publisher_gross_revenue", "sum"),
                            ecpm=("ecpm", "mean"),
                            total_requests=("bid_requests", "sum"),
                            non_zero_bid_responses=("bid_responses", "sum"),
                        )
                        .reset_index()
                    )
                    _mag_agg["Win Rate %"] = (
                        (_mag_agg["paid_impressions"] / _mag_agg["total_requests"] * 100)
                        .where(_mag_agg["total_requests"] > 0)
                    )
                    _magnite_summary = _mag_agg.rename(columns={
                        "ssp": "SSP",
                        "seller_ae": "Seller",
                        "deal": "Deal",
                        "deal_type_label": "Deal Type",
                        "ad_format": "Format",
                        "partner": "DSP",
                        "paid_impressions": "Paid Impressions",
                        "revenue": "Revenue",
                        "ecpm": "eCPM",
                        "total_requests": "Total Requests",
                        "non_zero_bid_responses": "Bid Responses",
                        "revenue_source": "Deal Source",
                    })
        except Exception as _mag_exc:
            st.warning(f"Magnite PMP load error: {_mag_exc}")

    # Generic loader for any custom SSP added via the Settings tab
    _custom_summaries = []
    _builtin_ssps = {"GAM", "Magnite", "Pubmatic"}
    for _ssp_cfg in _cfg["ssps"]:
        _ssp_name = _ssp_cfg["name"]
        if _ssp_name in _builtin_ssps or not _ssp_cfg.get("enabled", True):
            continue
        try:
            _custom_df = load(_ssp_cfg["table"]).copy()
            if _custom_df.empty:
                continue
            _col_map = _ssp_cfg.get("columns", {})
            _rename = {}
            _field_to_internal = {
                "Deal": "Deal", "Deal Type": "Deal Type", "DSP": "DSP",
                "Format": "Format", "Seller": "Seller",
                "Paid Impressions": "paid_imp_raw", "Revenue": "rev_raw",
                "eCPM": "ecpm_raw", "Win Rate %": "wr_raw",
                "Total Requests": "tr_raw", "Bid Responses": "br_raw",
            }
            for field, src in _col_map.items():
                if src and src not in ("[auto]", "") and not src.startswith("[computed") and src in _custom_df.columns:
                    _rename[src] = _field_to_internal.get(field, field)
            _custom_df = _custom_df.rename(columns=_rename)
            _custom_df["SSP"] = _ssp_name
            if _col_map.get("Deal Type") == "[auto]" and "Deal" in _custom_df.columns:
                _custom_df["Deal Type"] = _custom_df["Deal"].apply(lambda d: _parse_deal(d)["deal_type_label"])
            if _col_map.get("Format") == "[auto]" and "Deal" in _custom_df.columns:
                _custom_df["Format"] = _custom_df["Deal"].apply(lambda d: _parse_deal(d)["ad_format"])
            if _col_map.get("Seller") == "[auto]" and "Deal" in _custom_df.columns:
                _custom_df["Seller"] = (
                    _custom_df["Deal"].str.extract(r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False).map(AE_NAMES)
                )
            _active_types = sel_pmp_deal_types or _ssp_cfg.get("deal_types", [])
            if _active_types and "Deal Type" in _custom_df.columns:
                _custom_df = _custom_df[_custom_df["Deal Type"].isin(_active_types)]
            if selected_seller != "All" and "Seller" in _custom_df.columns:
                _custom_df = _custom_df[_custom_df["Seller"] == selected_seller]
            _custom_df = _apply_am_filter(_custom_df, "Seller")
            if _custom_df.empty:
                continue
            _grp_cols = [c for c in ["SSP", "Deal", "Deal Type", "Format", "DSP", "Seller"] if c in _custom_df.columns]
            _agg_spec = {}
            for _metric, _raw, _how in [
                ("Paid Impressions", "paid_imp_raw", "sum"), ("Revenue", "rev_raw", "sum"),
                ("Total Requests", "tr_raw", "sum"), ("Bid Responses", "br_raw", "sum"),
                ("eCPM", "ecpm_raw", "mean"), ("Win Rate %", "wr_raw", "mean"),
            ]:
                if _raw in _custom_df.columns:
                    _custom_df[_raw] = pd.to_numeric(_custom_df[_raw], errors="coerce")
                    _agg_spec[_metric] = (_raw, _how)
            if _agg_spec:
                _custom_summaries.append(
                    _custom_df.groupby(_grp_cols, dropna=False).agg(**_agg_spec).reset_index()
                )
        except Exception:
            pass

    _parts = [df for df in [pmp_summary, _magnite_summary, _gam_summary] + _custom_summaries if not df.empty]
    if _parts:
        combined_pmp = pd.concat(_parts, ignore_index=True).sort_values("Revenue", ascending=False)
    else:
        combined_pmp = pd.DataFrame(columns=["SSP", "Deal", "Deal Type", "Format", "DSP", "Seller",
                                              "Deal Source", "Paid Impressions", "Revenue", "eCPM",
                                              "Win Rate %", "Total Requests", "Bid Responses"])

    # Normalize DSP and Format names across all SSPs using the settings alias maps
    _dsp_aliases = _cfg.get("dsp_aliases", {})
    if _dsp_aliases and "DSP" in combined_pmp.columns:
        combined_pmp["DSP"] = combined_pmp["DSP"].replace(_dsp_aliases)

    _format_aliases = _cfg.get("format_aliases", {})
    if "Format" in combined_pmp.columns:
        # Canonicalize (aliases + family rules) rather than alias-replace
        # only — keeps the PMP Format filter on the same one-name-per-thing
        # buckets as the Direct tab.
        combined_pmp["Format"] = combined_pmp["Format"].map(
            lambda f: dl.canonicalize_format(f, _format_aliases))

    _deal_source_aliases = _cfg.get("deal_source_aliases", {})
    if _deal_source_aliases and "Deal Source" in combined_pmp.columns:
        combined_pmp["Deal Source"] = combined_pmp["Deal Source"].replace(_deal_source_aliases)

    # Fill missing Deal Source with the per-SSP default configured in Settings -> PMP Data Sources.
    _ssp_ds_defaults = {s["name"]: s.get("deal_source_default", "") for s in _cfg.get("ssps", [])}
    if any(_ssp_ds_defaults.values()):
        if "Deal Source" not in combined_pmp.columns:
            combined_pmp["Deal Source"] = None
        _ds_blank = combined_pmp["Deal Source"].isna() | (combined_pmp["Deal Source"].astype(str).str.strip() == "")
        _ds_fill = combined_pmp.loc[_ds_blank, "SSP"].map(_ssp_ds_defaults).replace("", None)
        combined_pmp.loc[_ds_blank, "Deal Source"] = _ds_fill

    # Derive Team from the deal name (Team-USA / Team-INTL → display labels from team_names).
    # Rows whose deal name doesn't carry the Team marker get NaN and are excluded when a Team filter is active.
    _team_map = _cfg.get("team_names", {"USA": "USA", "INTL": "International"})
    combined_pmp["Team"] = (
        combined_pmp["Deal"].str.extract(dl.TEAM_TOKEN_RE, expand=False).map(_team_map)
        if "Deal" in combined_pmp.columns else None
    )

    # Persist DSP / Format / Deal Source / Team options for next render (two-pass pattern — filters are rendered above).
    st.session_state["_pmp_dsps_opts"]         = sorted(combined_pmp["DSP"].dropna().unique().tolist())
    st.session_state["_pmp_formats_opts"]      = sorted(combined_pmp["Format"].dropna().unique().tolist())
    st.session_state["_pmp_deal_sources_opts"] = sorted(combined_pmp["Deal Source"].dropna().unique().tolist()) if "Deal Source" in combined_pmp.columns else []
    st.session_state["_pmp_teams_opts"]        = sorted(combined_pmp["Team"].dropna().unique().tolist()) if "Team" in combined_pmp.columns else []

    _combined_prefilter = combined_pmp.copy()

    if sel_pmp_ssps:
        combined_pmp = combined_pmp[combined_pmp["SSP"].isin(sel_pmp_ssps)]
    if sel_pmp_dsps:
        combined_pmp = combined_pmp[combined_pmp["DSP"].isin(sel_pmp_dsps)]
    if sel_pmp_formats:
        combined_pmp = combined_pmp[combined_pmp["Format"].isin(sel_pmp_formats)]
    if sel_pmp_deal_sources and "Deal Source" in combined_pmp.columns:
        combined_pmp = combined_pmp[combined_pmp["Deal Source"].isin(sel_pmp_deal_sources)]
    if sel_pmp_teams and "Team" in combined_pmp.columns:
        combined_pmp = combined_pmp[combined_pmp["Team"].isin(sel_pmp_teams)]

    # Reset pagination when any filter changes (incl. the low-rev toggle).
    _pmp_filter_sig = str((
        sorted(sel_pmp_deal_types), sorted(sel_pmp_ssps), sorted(sel_pmp_dsps),
        sorted(sel_pmp_formats), sorted(sel_pmp_deal_sources), sorted(sel_pmp_teams),
        bool(_show_low_rev),
    ))
    if st.session_state.get("_pmp_filter_sig") != _pmp_filter_sig:
        st.session_state["pmp_page"] = 0
        st.session_state["_pmp_filter_sig"] = _pmp_filter_sig

    if combined_pmp.empty:
        # Give a specific reason when we can detect it.
        if not _combined_prefilter.empty and sel_pmp_ssps:
            # Data exists but the SSP filter excluded it — name which SSPs have matching data.
            _has_data = _combined_prefilter.copy()
            if sel_pmp_deal_types:
                _has_data = _has_data[_has_data["Deal Type"].isin(sel_pmp_deal_types)]
            _ssps_with_data = sorted(_has_data["SSP"].dropna().unique().tolist())
            _msg = (
                f"No data for SSP = **{', '.join(sel_pmp_ssps)}**"
                + (f" + Deal Type = **{', '.join(sel_pmp_deal_types)}**" if sel_pmp_deal_types else "")
                + f". Try selecting: **{', '.join(_ssps_with_data)}**." if _ssps_with_data
                else ". No matching data exists for any SSP with the current filters."
            )
            st.warning(_msg)
        elif not _combined_prefilter.empty:
            st.warning("Filters returned no rows — try clearing the Deal Type, DSP, or Format filter.")
        else:
            st.info("No PMP deal data found. Run a data refresh to populate.")
    else:
        # ── Local helpers / formatters (scope-isolated from Direct section).
        def _pmp_esc(s):
            if s is None: return ""
            s = str(s)
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def _pmp_fmt_money(v):
            if pd.isna(v): return "—"
            v = float(v)
            if abs(v) >= 1_000_000: return f"${v/1_000_000:.2f}M"
            if abs(v) >= 1_000:     return f"${v/1_000:.1f}K"
            return f"${v:,.2f}"

        def _pmp_fmt_count(v):
            if pd.isna(v) or v == 0: return "—" if pd.isna(v) else "0"
            v = float(v)
            if abs(v) >= 1_000_000: return f"{v/1_000_000:.2f}M"
            if abs(v) >= 1_000:     return f"{v/1_000:.1f}K"
            return f"{int(v):,}"

        def _pmp_tile(label, value, sub=None):
            sub_html = f'<div class="kpi-target">{_pmp_esc(sub)}</div>' if sub else ''
            return (f'<div class="kpi-tile">'
                    f'<div class="kpi-label">{_pmp_esc(label)}</div>'
                    f'<div class="kpi-value">{value}</div>'
                    f'{sub_html}'
                    f'</div>')

        def _dt_pill(dt):
            code_map = {
                "Programmatic Guaranteed": ("PG", "pill-dt-pg"),
                "Preferred Deal":          ("PD", "pill-dt-pd"),
                "Private Auction":         ("PA", "pill-dt-pa"),
                "Private Marketplace":     ("PMP", "pill-dt-pmp"),
            }
            if not isinstance(dt, str): return ""
            code, cls = code_map.get(dt, (dt[:3].upper(), "pill-dt-pa"))
            return f'<span class="pill-dt {cls}">{code}</span>'

        def _ecpm_cell(ecpm, floor):
            if ecpm is None or pd.isna(ecpm):
                return '<span class="cell-dash">—</span>'
            if floor is not None and not pd.isna(floor):
                if ecpm < floor:
                    return f'<span class="ecpm-under">${ecpm:.2f}</span>'
                if ecpm >= floor * 2:
                    return f'<span class="ecpm-over">${ecpm:.2f}</span>'
            return f"${ecpm:.2f}"

        def _rev_cell(v):
            if pd.isna(v): return '<span class="cell-dash">$0</span>'
            cls = "bold-rev" if v > 10_000 else ""
            return f'<span class="{cls}">${v:,.0f}</span>'

        def _impr_cell(v):
            if pd.isna(v): return '<span class="cell-dash">—</span>'
            a = abs(v)
            if a >= 1_000_000: return f"{v/1_000_000:.2f}M"
            if a >= 1_000:     return f"{v/1_000:.1f}K"
            return f"{int(v):,}"

        # ── Top-line numbers + deal-type mix ──
        _pmp_rev = float(combined_pmp["Revenue"].sum()) if "Revenue" in combined_pmp.columns else 0.0
        _pmp_impr = float(combined_pmp["Paid Impressions"].sum()) if "Paid Impressions" in combined_pmp.columns else 0.0
        _pmp_ecpm = (_pmp_rev / _pmp_impr * 1000) if _pmp_impr else None
        _pmp_count = len(combined_pmp)
        _type_counts = (combined_pmp["Deal Type"].value_counts()
                        if "Deal Type" in combined_pmp.columns else pd.Series(dtype=int))
        _mix_parts = []
        for _lbl, _key in (("PG", "Programmatic Guaranteed"), ("PD", "Preferred Deal"),
                            ("PA", "Private Auction"), ("PMP", "Private Marketplace")):
            _n = int(_type_counts.get(_key, 0))
            if _n: _mix_parts.append(f"{_lbl} {_n}")
        _mix_sub = " · ".join(_mix_parts)

        # Direct totals for comparison sublines (may not exist when gam_df is empty).
        try:
            _d_rev = float(total_rev)
            _d_impr = float(total_impr)
            _d_ecpm = (_d_rev / _d_impr * 1000) if _d_impr else None
        except NameError:
            _d_rev = _d_impr = _d_ecpm = None

        # ── Exception banners ──
        _floors = _cfg.get("pmp_floors_by_deal_type", {}) or {}

        def _deal_floor(row):
            # Per-deal configured floor: the $<floor> token Newsweek embeds in
            # the deal name (cross-SSP — the SSP delivery feeds carry no floor),
            # falling back to the per-deal-type floor from settings when a deal
            # isn't convention-named.
            _f = dl.pmp_deal_floor(row.get("Deal"))
            if _f is None:
                _dt = row.get("Deal Type") or ""
                _f = _floors.get(_dt) if _dt else None
            return _f

        _breach_rows = pd.DataFrame()
        if "eCPM" in combined_pmp.columns and "Deal" in combined_pmp.columns:
            _df_b = combined_pmp.copy()
            # Per-deal floor from the name; per-type settings floor as fallback.
            _df_b["_floor"] = _df_b["Deal"].map(dl.pmp_deal_floor)
            if _floors and "Deal Type" in _df_b.columns:
                _df_b["_floor"] = _df_b["_floor"].fillna(_df_b["Deal Type"].map(_floors))
            _df_b["_floor"] = pd.to_numeric(_df_b["_floor"], errors="coerce")
            _ecpm_num = pd.to_numeric(_df_b["eCPM"], errors="coerce")
            _breach_rows = _df_b[_df_b["_floor"].notna() & _ecpm_num.notna() & (_ecpm_num < _df_b["_floor"])]

        _pa_no_delivery = 0
        try:
            _pa_inv = load("gam_pa_metadata")
            # "No delivery" = PA deals NOT winning impressions. Drop the few that
            # are actually delivering (present in gam_pmp_deals with impressions),
            # so the list is genuinely non-delivering inventory; the rest stay,
            # grouped by status below.
            if not _pa_inv.empty and "auction_name" in _pa_inv.columns:
                try:
                    _gpd_d = load("gam_pmp_deals")
                    _dn_col = next((c for c in _gpd_d.columns if "deal_name" in c), None)
                    _im_col = next((c for c in _gpd_d.columns if "impression" in c.lower()), None)
                    if _dn_col and _im_col:
                        _gd = _gpd_d[[_dn_col, _im_col]].copy()
                        _gd[_im_col] = pd.to_numeric(_gd[_im_col], errors="coerce").fillna(0)
                        _delivering = set(_gd.groupby(_dn_col)[_im_col].sum()
                                          .loc[lambda s: s > 0].index.astype(str))
                    elif _dn_col:
                        _delivering = set(_gpd_d[_dn_col].dropna().astype(str))
                    else:
                        _delivering = set()
                except Exception:
                    _delivering = set()
                if _delivering:
                    _pa_inv = _pa_inv[~_pa_inv["auction_name"].astype(str).isin(_delivering)]
            # Canceled deals are dead by intent — not shown in the no-delivery list.
            if not _pa_inv.empty and "deal_status" in _pa_inv.columns:
                _pa_inv = _pa_inv[_pa_inv["deal_status"].astype(str).str.upper() != "CANCELED"]
            # Open-auction backstop deals (AE token "OpenAuction" — Google
            # demand facilitation, not AE-managed) are excluded; the list is
            # seller-owned PA inventory only.
            if not _pa_inv.empty and "auction_name" in _pa_inv.columns:
                _pa_ae_tok = _pa_inv["auction_name"].astype(str).str.extract(
                    r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                _pa_inv = _pa_inv[_pa_ae_tok != "OpenAuction"]
            _pa_no_delivery = len(_pa_inv) if not _pa_inv.empty else 0
        except Exception:
            _pa_inv = pd.DataFrame()

        _banners = []
        if not _breach_rows.empty:
            _n_breach = len(_breach_rows)
            _ex = _breach_rows.iloc[0]
            _ex_primary = dl.pmp_deal_display_name(_ex.get("Deal") or "")[0]
            _ex_ecpm = float(_ex.get("eCPM")) if pd.notna(_ex.get("eCPM")) else 0.0
            _ex_floor = float(_ex.get("_floor")) if pd.notna(_ex.get("_floor")) else 0.0
            _ex_dt_code = {"Programmatic Guaranteed": "PG", "Preferred Deal": "PD",
                           "Private Auction": "PA", "Private Marketplace": "PMP"
                           }.get(_ex.get("Deal Type"), "")
            _hd = f"{_n_breach} {_ex_dt_code} deal{'s' if _n_breach != 1 else ''} below floor eCPM".strip()
            _banners.append(
                f'<div class="nw-banner sev-amber">'
                f'<div class="nw-banner-head">⚠ {_hd}</div>'
                f'<div>{_pmp_esc(_ex_primary)} · ${_ex_ecpm:.2f} vs ${_ex_floor:.2f} floor</div>'
                f'</div>'
            )
        # No-delivery is folded into the "PMP signals" accordion below the KPIs.
        if _banners:
            st.markdown(
                '<div class="nw-banner-row" style="grid-template-columns: repeat(' + str(len(_banners)) + ', 1fr);">'
                + "".join(_banners) + '</div>',
                unsafe_allow_html=True,
            )

        # ── KPI strip — 4 tiles (4-column grid override). ──
        _rev_sub  = f"vs ${_d_rev/1000:,.1f}K direct" if _d_rev else None
        _ecpm_sub = f"vs ${_d_ecpm:.2f} direct" if _d_ecpm else None
        st.markdown(
            '<div class="nw-kpi-row nw-kpi-row--pmp">'
            + _pmp_tile("Revenue", _pmp_fmt_money(_pmp_rev), _rev_sub)
            + _pmp_tile("Paid impressions", _pmp_fmt_count(_pmp_impr))
            + _pmp_tile("Avg eCPM", f"${_pmp_ecpm:.2f}" if _pmp_ecpm else "—", _ecpm_sub)
            + _pmp_tile("Active deals", f"{_pmp_count:,}", _mix_sub or None)
            + '</div>',
            unsafe_allow_html=True,
        )

        # ── PMP signals accordion (Option 2): one card, a row per signal —
        # Spend momentum + No delivery + Stale deals — each expands inline.
        # Reuses the Needs-attention accordion CSS, so it collapses to one line
        # on mobile and stays open on desktop. Stale deals folded in read-only
        # 2026-06 (archive removed).
        # Renders into this SLOT, which lives **in the cockpit rail**
        # (`_rail`, defined in the Direct section above) so the rail carries all
        # triage — Needs-attention on top, PMP signals below — as one pinned
        # column on desktop. ≤1024px the rail is normal flow, so PMP signals just
        # stack under the Needs-attention card as before. It is BUILT by
        # _render_pmp_signals() — called AFTER the drawer machinery is defined
        # below — so each flagged deal can **expand to the SAME _pmp_drawer_html
        # the main table row opens** (Roger 2026-06-14: "see the PMP details on
        # the signals card"). Deals present in the delivery frame get the full
        # performance drawer (revenue · eCPM · 7-day trend · metadata);
        # no-delivery / long-stale deals expand to a name-only note.
        _pmp_sig_slot = _rail.empty()

        def _render_pmp_signals():
            # Deal name → its row in the UNFILTERED combined frame, so a flagged
            # deal shows its full drawer regardless of the table's active
            # filters. First (highest-revenue) row wins if a name spans SSPs.
            _pmp_rowlut = {}
            if not _combined_prefilter.empty:
                for _, _cr in _combined_prefilter.iterrows():
                    _pmp_rowlut.setdefault(str(_cr.get("Deal")), _cr)

            def _sig_deal_details(_deal_name, _setup_html=""):
                # Full drawer when the deal is delivering (in the frame); else
                # the deal's setup details (no-delivery / long-stale have no
                # performance data, so there's no revenue / eCPM / trend).
                _mr = _pmp_rowlut.get(str(_deal_name))
                if _mr is not None:
                    return _pmp_drawer_html(_mr)
                return (
                    '<div class="nw-pmp-drawer">'
                    '<div class="nw-drawer-head">'
                    f'<span class="nw-drawer-li">{_pmp_esc(str(_deal_name) or "—")}</span>'
                    '</div>'
                    + (_setup_html or '<div class="nw-sig-nodata">No recent '
                       'delivery — nothing to chart.</div>')
                    + '</div>'
                )

            def _sig_deal_wrap(_html, _deal_name, _setup_html=""):
                # Wrap a signal deal row as a <details> that expands to its
                # drawer. Plain row when there's no deal identity to look up.
                if not _deal_name:
                    return _html
                return ('<details class="nw-sig-deal"><summary>' + _html
                        + '</summary>' + _sig_deal_details(_deal_name, _setup_html)
                        + '</details>')

            _sig_rows = []
            if _pmp_mom_rows and not _pmp_summ.empty:
                _mom_wrapped = _sp_rows_for(_pmp_summ, "deal", wrap=_sig_deal_wrap)
                _sig_rows.append(
                    '<details class="nw-na-row sev-info">'
                    '<summary><span class="nw-na-dot"></span>'
                    f'<span class="nw-na-n">{_total_gaining + _total_losing}</span>'
                    '<span class="nw-na-l">Spend momentum</span>'
                    f'<span class="nw-na-d">{_total_gaining} gaining · {_total_losing} losing</span>'
                    '<span class="nw-na-chev">&rsaquo;</span></summary>'
                    f'<div class="nw-na-sub nw-sig-sub">{"".join(_mom_wrapped)}</div></details>'
                )
            if _pa_no_delivery > 0 and not _pa_inv.empty:
                # Group non-delivering PA deals by SELLER (the AE from the deal name,
                # resolved through settings.json ae_names), busiest seller first.
                # Each card: readable Advertiser — Campaign name, a deal-type pill
                # (PA/PD) top-right, and a meta line of status · days-inactive
                # (colored by idle age) + floor. "Inactive" = days since the deal
                # last won a bid (pmp_last_bid_date.last_bid_date) or, for deals that
                # never bid, since it was set up (create_time). Most-inactive first
                # within each seller. Canceled / delivering / open-auction excluded
                # upstream.
                _today = datetime.now(timezone.utc).date()
                try:
                    _lbd_t = load("pmp_last_bid_date")
                    _lastbid_map = (dict(zip(_lbd_t["deal_key"].astype(str),
                                             _lbd_t["last_bid_date"].astype(str)))
                                    if not _lbd_t.empty and "deal_key" in _lbd_t.columns else {})
                except Exception:
                    _lastbid_map = {}
                _dt_full = {"PA": "Private Auction", "PD": "Preferred Deal",
                            "PG": "Programmatic Guaranteed", "PMP": "Private Marketplace"}
                _nd = _pa_inv.copy()
                _nd["_st"] = (_nd["deal_status"].astype(str).str.upper()
                              if "deal_status" in _nd.columns else "OTHER")
                _nd_active_ct = int((_nd["_st"] == "ACTIVE").sum())
                _nd_ae = _nd["auction_name"].astype(str).str.extract(
                    r"Team-(?:USA|INTL)_([A-Za-z]+)", expand=False)
                _nd["_seller"] = _nd_ae.map(AE_NAMES).fillna(_nd_ae).fillna("Unassigned")

                def _nd_idle(_r):
                    # last bid when known (true inactivity); else days since set up.
                    _ct = _r.get("create_time")
                    _ctd = str(_ct)[:10] if pd.notna(_ct) else None
                    return dl.idle_days(_lastbid_map.get(str(_r.get("auction_name") or "")), _ctd, _today)
                _nd["_idle"] = _nd.apply(_nd_idle, axis=1)
                _seller_n = _nd["_seller"].value_counts()
                _nd_groups = []
                for _seller in sorted(_nd["_seller"].unique(), key=lambda s: (-int(_seller_n[s]), s)):
                    _grp = _nd[_nd["_seller"] == _seller].sort_values("_idle", ascending=False)
                    _drows = []
                    for _, _ri in _grp.iterrows():
                        _primary = dl.pmp_deal_display_name(_ri.get("auction_name") or "")[0]
                        _adv, _camp = ((_primary.split(" — ", 1) + [""])[:2]
                                       if " — " in _primary else (_primary, ""))
                        _camp_html = f'<span class="sp-camp"> — {_pmp_esc(_camp)}</span>' if _camp else ""
                        _fv = _ri.get("floor_price_usd")
                        _fs = f"${float(_fv):.2f} floor" if pd.notna(_fv) else "no floor"
                        _st_cls = "nd-st nd-pending" if _ri["_st"] == "PENDING" else "nd-st"
                        _tok = (str(_ri.get("auction_name") or "").split("_") + ["", ""])[1]
                        _pill = _dt_pill(_dt_full.get(_tok, _tok))
                        _idle = int(_ri["_idle"])
                        _ib = dl.idle_band(_idle)
                        _nd_html = (
                            '<div class="sp-row">'
                            '<div class="nd-top">'
                            f'<div class="sp-nm"><span class="sp-adv">{_pmp_esc(_adv)}</span>{_camp_html}</div>'
                            f'{_pill}</div>'
                            '<div class="sp-met"><span>'
                            f'<span class="{_st_cls}">{_pmp_esc(_ri["_st"].title())}</span>'
                            f' · <span class="nd-idle idle-{_ib}">{_idle}d inactive</span></span>'
                            f'<span class="sp-flow">{_pmp_esc(_fs)}</span></div>'
                            '</div>'
                        )
                        _fval = f"${float(_fv):.2f}" if pd.notna(_fv) else "—"
                        _ct_disp = str(_ri.get("create_time"))[:10] if pd.notna(_ri.get("create_time")) else "—"
                        _nd_setup = (
                            '<div class="nw-sig-nodata" style="margin-bottom:8px">'
                            'Set up but not winning impressions — no delivery to chart.</div>'
                            '<div class="nw-meta-grid">'
                            f'<div><span class="lbl">Status</span><span class="val">{_pmp_esc(_ri["_st"].title())}</span></div>'
                            f'<div><span class="lbl">Deal type</span><span class="val">{_pmp_esc(_dt_full.get(_tok, _tok))}</span></div>'
                            f'<div><span class="lbl">Floor</span><span class="val">{_fval}</span></div>'
                            f'<div><span class="lbl">Days inactive</span><span class="val">{_idle}d</span></div>'
                            f'<div><span class="lbl">Set up</span><span class="val">{_pmp_esc(_ct_disp)}</span></div>'
                            '</div>'
                        )
                        _drows.append(_sig_deal_wrap(_nd_html, _ri.get("auction_name"), _nd_setup))
                    _worst = int(_grp["_idle"].max())
                    _sp = _seller.split()
                    _init = (_sp[0][0] + _sp[-1][0]).upper() if len(_sp) >= 2 else _seller[:2].upper()
                    _nd_groups.append(
                        '<details class="nd-sg"><summary>'
                        f'<span class="nd-av">{_pmp_esc(_init)}</span>'
                        f'<span class="nd-sname">{_pmp_esc(_seller)}</span>'
                        f'<span class="nd-scount">{len(_grp)} · worst {_worst}d</span>'
                        '<span class="nd-schev">&rsaquo;</span></summary>'
                        + "".join(_drows) + '</details>'
                    )
                _sig_rows.append(
                    '<details class="nw-na-row sev-red">'
                    '<summary><span class="nw-na-dot"></span>'
                    f'<span class="nw-na-n">{_pa_no_delivery}</span>'
                    '<span class="nw-na-l">No delivery</span>'
                    f'<span class="nw-na-d">{_nd_active_ct} active</span>'
                    '<span class="nw-na-chev">&rsaquo;</span></summary>'
                    f'<div class="nw-na-sub nw-sig-sub">{"".join(_nd_groups)}</div></details>'
                )

            # ── Stale deals row — no bid responses for 90+ days, still seen in the
            # source (paused/removed deals already dropped by recently_seen_mask).
            # Read-only (archive removed 2026-06): deal · SSP · last bid · idle age.
            try:
                _lbd_stale = load("pmp_last_bid_date")
            except Exception:
                _lbd_stale = pd.DataFrame()
            if not _lbd_stale.empty:
                _stale_today = datetime.now(timezone.utc).date()
                # Two distinct cutoffs: stale = no winning bid in 90+ days;
                # "still live" = appeared in the GAM feed within the (short)
                # seen-window, so a paused deal drops out within ~a week
                # instead of lingering for the full 90.
                _stale_cut = (_stale_today - timedelta(days=90)).isoformat()
                _seen_window = int(_cfg.get("stale_seen_window_days", 7) or 7)
                _seen_cut = (_stale_today - timedelta(days=_seen_window)).isoformat()
                _lbd_stale = _lbd_stale.copy()
                _lbd_stale["last_bid_date"]   = _lbd_stale["last_bid_date"].astype(str).replace({"None": pd.NA, "nan": pd.NA, "": pd.NA})
                _lbd_stale["first_seen_date"] = _lbd_stale["first_seen_date"].astype(str).replace({"None": pd.NA, "nan": pd.NA, "": pd.NA})
                if "last_seen_date" in _lbd_stale.columns:
                    _lbd_stale["last_seen_date"] = _lbd_stale["last_seen_date"].astype(str).replace({"None": pd.NA, "nan": pd.NA, "": pd.NA})
                _stale = _lbd_stale[dl.stale_deal_mask(_lbd_stale, _stale_cut)
                                    & dl.recently_seen_mask(_lbd_stale, _seen_cut)].copy()
                if not _stale.empty:
                    _stale["_idle"] = _stale.apply(
                        lambda r: dl.idle_days(r.get("last_bid_date"),
                                               r.get("first_seen_date"), _stale_today), axis=1)
                    _stale = _stale.sort_values("_idle", ascending=False)
                    _st_rows = []
                    for _, _sr in _stale.iterrows():
                        _primary = dl.pmp_deal_display_name(str(_sr.get("deal_key") or ""))[0]
                        _adv, _camp = ((_primary.split(" — ", 1) + [""])[:2]
                                       if " — " in _primary else (_primary, ""))
                        _camp_html = f'<span class="sp-camp"> — {_pmp_esc(_camp)}</span>' if _camp else ""
                        _lbd_disp = str(_sr.get("last_bid_date") or "")
                        if not _lbd_disp or _lbd_disp in ("None", "nan", "<NA>"):
                            _lbd_disp = "never"
                        _sidle = int(_sr["_idle"])
                        _sib = dl.idle_band(_sidle)
                        _idle_html = f'<span class="nd-idle idle-{_sib}">{_sidle}d idle</span>'
                        _st_html = (
                            '<div class="sp-row">'
                            f'<div class="sp-nm"><span class="sp-adv">{_pmp_esc(_adv)}</span>{_camp_html}</div>'
                            f'<div class="sp-met"><span class="sp-flow">{_pmp_esc(str(_sr.get("ssp") or ""))}'
                            f' · last bid {_pmp_esc(_lbd_disp)} · {_idle_html}</span></div>'
                            '</div>'
                        )
                        _fsd = str(_sr.get("first_seen_date") or "")
                        if not _fsd or _fsd in ("None", "nan", "<NA>"):
                            _fsd = "—"
                        _st_setup = (
                            '<div class="nw-sig-nodata" style="margin-bottom:8px">'
                            'No bid responses in 90+ days — no recent delivery to chart.</div>'
                            '<div class="nw-meta-grid">'
                            f'<div><span class="lbl">SSP</span><span class="val">{_pmp_esc(str(_sr.get("ssp") or "—"))}</span></div>'
                            f'<div><span class="lbl">Last bid</span><span class="val">{_pmp_esc(_lbd_disp)}</span></div>'
                            f'<div><span class="lbl">First seen</span><span class="val">{_pmp_esc(_fsd)}</span></div>'
                            f'<div><span class="lbl">Idle</span><span class="val">{_sidle}d</span></div>'
                            '</div>'
                        )
                        _st_rows.append(_sig_deal_wrap(_st_html, _sr.get("deal_key"), _st_setup))
                    _sig_rows.append(
                        '<details class="nw-na-row sev-amber">'
                        '<summary><span class="nw-na-dot"></span>'
                        f'<span class="nw-na-n">{len(_stale)}</span>'
                        '<span class="nw-na-l">Stale deals</span>'
                        '<span class="nw-na-d">no bids 90+ days</span>'
                        '<span class="nw-na-chev">&rsaquo;</span></summary>'
                        f'<div class="nw-na-sub nw-sig-sub">{"".join(_st_rows)}</div></details>'
                    )
            if _sig_rows:
                _pmp_sig_slot.markdown(
                    _sp_css +
                    '<style>'
                    '.nw-na-row.sev-info .nw-na-dot{background:var(--text-muted)}'
                    '.nw-na-row.sev-info .nw-na-n{color:var(--text-secondary);font-size:14px}'
                    '.nw-sig-sub .sp-row:last-child{border-bottom:none}'
                    '.nw-sig-scroll{overflow-x:auto}'
                    # No-delivery: each seller is a collapsible row (initials avatar
                    # + name + count · worst-idle); deals nest inside, expand on tap.
                    '.nd-sg{border-top:1px solid var(--border)}'
                    '.nd-sg:first-child{border-top:none}'
                    '.nd-sg>summary{list-style:none;display:flex;align-items:center;gap:10px;'
                    'padding:10px 12px;cursor:pointer;background:var(--surface-1)}'
                    '.nd-sg>summary::-webkit-details-marker{display:none}'
                    '.nd-sg>summary::marker{content:""}'
                    '.nd-av{width:26px;height:26px;border-radius:50%;background:var(--text-primary);'
                    'color:var(--surface-1);font-size:10px;font-weight:700;display:flex;'
                    'align-items:center;justify-content:center;flex:0 0 auto}'
                    '.nd-sname{font-weight:700;font-size:14px;color:var(--text-primary)}'
                    '.nd-scount{margin-left:auto;font-size:11px;color:var(--text-muted)}'
                    '.nd-schev{color:var(--text-muted);font-size:13px;transition:transform .15s ease}'
                    '.nd-sg[open]>summary .nd-schev{transform:rotate(90deg)}'
                    '.nd-st{font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;'
                    'font-weight:700;color:var(--text-muted)}'
                    '.nd-st.nd-pending{color:var(--state-warning)}'
                    # Name + deal-type pill on one row (pill pinned top-right);
                    # days-inactive colored by idle age (amber 90+, red 180+).
                    '.nd-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}'
                    '.nd-top .sp-nm{flex:1 1 auto;min-width:0}'
                    '.nd-top .pill-dt{flex:0 0 auto}'
                    '.nd-idle{font-weight:700;color:var(--text-secondary)}'
                    '.nd-idle.idle-amber{color:var(--state-warning)}'
                    '.nd-idle.idle-red{color:var(--state-critical)}'
                    # Expandable signal deal: the row stays as the <summary>;
                    # tapping reveals the same drawer the main PMP table opens.
                    '.nw-sig-deal>summary{list-style:none;cursor:pointer}'
                    '.nw-sig-deal>summary::-webkit-details-marker{display:none}'
                    '.nw-sig-deal>summary::marker{content:""}'
                    '.nw-sig-deal>summary:hover{background:var(--surface-2)}'
                    '.nw-sig-deal[open]>summary{background:var(--surface-2)}'
                    '.nw-sig-deal .nw-pmp-drawer{margin:0 2px 8px;background:var(--surface-1);'
                    'border:1px solid var(--border);border-radius:var(--radius-md);padding:10px 12px}'
                    '.nw-sig-nodata{font-size:11.5px;color:var(--text-muted);line-height:1.45}'
                    '</style>'
                    # Default-open (incl. on mobile) so the signals are visible on
                    # load; the per-row accordions inside still expand on tap. The
                    # separate Needs-attention alerts card keeps its collapsed-on-
                    # mobile default (open is per-<details>).
                    '<details class="nw-na nw-pmp-sig" open>'
                    '<summary class="nw-na-head"><span>PMP signals</span>'
                    f'<span class="cnt">{len(_sig_rows)} signal{"s" if len(_sig_rows) != 1 else ""}</span>'
                    '<span class="nw-na-h-chev">&rsaquo;</span></summary>'
                    '<div class="nw-na-body">' + "".join(_sig_rows) + '</div></details>',
                    unsafe_allow_html=True,
                )

        # ── AirTable helpers (PMP scope — Direct scope has its own copies). ──
        _pmp_at_base = (_cfg.get("airtable_base_id") or "").strip()
        _pmp_at_form = (_cfg.get("airtable_form_id") or "").strip()
        _pmp_at_fields = _cfg.get("airtable_field_names") or {}
        _pmp_at_routes = {r["context"]: r["request_type"]
                          for r in (_cfg.get("airtable_request_type_routing") or [])
                          if isinstance(r, dict) and r.get("context") and r.get("request_type")}
        _pmp_at_reporter = (_cfg.get("airtable_reporter") or "").strip()

        def _pmp_airtable_url(row):
            """Build the AirTable prefilled-form URL for a PMP row. PMP always
            routes to one Request Type per the docx spec."""
            if not _pmp_at_base or not _pmp_at_form:
                return None
            rt = _pmp_at_routes.get("PMP deal · any issue", "PMP - Adjust")
            # Severity from eCPM vs floor.
            _ecpm = pd.to_numeric(row.get("eCPM"), errors="coerce")
            _dt = row.get("Deal Type") or ""
            _floor = _deal_floor(row)
            severity = "Info"
            if pd.notna(_ecpm) and _floor:
                if _ecpm < _floor * 0.8: severity = "Critical"
                elif _ecpm < _floor:     severity = "Warning"
            # Thesis statement.
            notes = ""
            if pd.notna(_ecpm) and _floor:
                if _ecpm < _floor:
                    pct = (_floor - _ecpm) / _floor * 100
                    notes = f"eCPM ${_ecpm:.2f} clearing vs ${_floor:.2f} {_dt} floor — {pct:.0f}% below committed rate."
                elif _ecpm >= _floor * 2:
                    pct = (_ecpm - _floor) / _floor * 100
                    notes = f"Strong yield: ${_ecpm:.2f} clearing — {pct:.0f}% above the ${_floor:.2f} {_dt} floor."
            fields = {
                "Request Type": rt,
                "Line Item":    row.get("Deal") or "",
                "GAM ID":       "",  # PMP rows don't have a line item ID
                "Severity":     severity,
                "Seller":       row.get("Seller") or "",
                "Reporter":     _pmp_at_reporter,
                "Notes":        notes,
            }
            parts = []
            for canonical, value in fields.items():
                if value is None or str(value).strip() == "":
                    continue
                name = _pmp_at_fields.get(canonical, canonical)
                parts.append(f"prefill_{quote_plus(name)}={quote_plus(str(value))}")
            if not parts:
                return None
            return f"https://airtable.com/{_pmp_at_base}/{_pmp_at_form}/form?{'&'.join(parts)}"

        # ── Per-row drawer helper. ───────────────────────────────────────
        # ── 7-day per-deal revenue series (drawer chart + mobile-card spark).
        # Rebuilt from the same daily source tables the summary aggregates away,
        # keyed by (SSP, Deal) to match each combined_pmp row. Pubmatic's row key
        # is deal_label (deal → publisher_deal_id → deal_meta_id), GAM's is
        # programmatic_deal_name, Magnite's is deal — mirror each exactly so the
        # lookup hits. Built once; looked up per row.
        _pmp_daily_parts = []
        try:
            _gpd = load("gam_pmp_deals")
            _gdc = (next((c for c in _gpd.columns if "deal_name" in c or c == "deal"), None)
                    if not _gpd.empty else None)
            if _gdc:
                _pmp_daily_parts.append(pd.DataFrame({
                    "ssp": "GAM", "deal": _gpd[_gdc].astype(str), "date": _gpd.get("date"),
                    "revenue": pd.to_numeric(_gpd.get("ad_server_cpm_and_cpc_revenue"), errors="coerce"),
                }))
        except Exception:
            pass
        try:
            # GAM's bid funnel per deal lives in a SEPARATE table from delivery
            # (gam_pmp_deals has impressions/revenue only). Keyed by
            # programmatic_deal_name = the same Deal key combined_pmp uses, so it
            # merges with the GAM revenue rows above on (ssp, deal, date) in the
            # per-column groupby-sum. deals_bid_requests = ad requests (callouts),
            # deals_bids = bid responses.
            _gbd = load("gam_deal_bid_daily")
            if not _gbd.empty and "programmatic_deal_name" in _gbd.columns:
                _pmp_daily_parts.append(pd.DataFrame({
                    "ssp": "GAM", "deal": _gbd["programmatic_deal_name"].astype(str),
                    "date": _gbd.get("date"),
                    "total_requests": pd.to_numeric(_gbd.get("deals_bid_requests"), errors="coerce"),
                    "bid_responses": pd.to_numeric(_gbd.get("deals_bids"), errors="coerce"),
                }))
        except Exception:
            pass
        try:
            _mdd = load("magnite_deal_daily")
            if not _mdd.empty and "deal" in _mdd.columns:
                _pmp_daily_parts.append(pd.DataFrame({
                    "ssp": "Magnite", "deal": _mdd["deal"].astype(str), "date": _mdd.get("date"),
                    "revenue": pd.to_numeric(_mdd.get("publisher_gross_revenue"), errors="coerce"),
                    "total_requests": (pd.to_numeric(_mdd["bid_requests"], errors="coerce")
                                       if "bid_requests" in _mdd.columns else pd.NA),
                    "bid_responses": (pd.to_numeric(_mdd["bid_responses"], errors="coerce")
                                      if "bid_responses" in _mdd.columns else pd.NA),
                }))
        except Exception:
            pass
        try:
            _pud = load("pubmatic_deals")
            if not _pud.empty:
                _plabel = _pud.get("deal")
                if _plabel is None:
                    _plabel = pd.Series([None] * len(_pud), index=_pud.index)
                if "publisher_deal_id" in _pud.columns:
                    _plabel = _plabel.fillna(_pud["publisher_deal_id"])
                if "deal_meta_id" in _pud.columns:
                    _plabel = _plabel.fillna(_pud["deal_meta_id"].astype(str))
                _pmp_daily_parts.append(pd.DataFrame({
                    "ssp": "Pubmatic", "deal": _plabel.astype(str), "date": _pud.get("date"),
                    "revenue": pd.to_numeric(_pud.get("revenue"), errors="coerce"),
                    "total_requests": (pd.to_numeric(_pud["total_requests"], errors="coerce")
                                       if "total_requests" in _pud.columns else pd.NA),
                    "bid_responses": (pd.to_numeric(_pud["non_zero_bid_responses"], errors="coerce")
                                      if "non_zero_bid_responses" in _pud.columns else pd.NA),
                }))
        except Exception:
            pass
        _pmp_daily = (pd.concat(_pmp_daily_parts, ignore_index=True)
                      if _pmp_daily_parts else pd.DataFrame(
                          columns=["ssp", "deal", "date", "revenue",
                                   "total_requests", "bid_responses"]))
        # Per-deal 7-day trend series for the drawer charts. Revenue covers all
        # three SSPs; total_requests / bid_responses are the bid funnel, reported
        # by all three: GAM (gam_deal_bid_daily: deals_bid_requests/deals_bids),
        # Magnite (bid_requests/bid_responses), Pubmatic (total_requests/
        # non_zero_bid_responses). Same (SSP, Deal) keying as revenue; a deal with
        # no funnel rows gets an empty series and skips those two charts.
        _pmp_rev_series_by_deal, _pmp_rev_dates = dl.revenue_daily_series_by_deal(_pmp_daily)
        _pmp_req_series_by_deal, _ = dl.daily_series_by_deal(_pmp_daily, "total_requests")
        _pmp_resp_series_by_deal, _ = dl.daily_series_by_deal(_pmp_daily, "bid_responses")

        def _pmp_rev_series_for(row):
            return _pmp_rev_series_by_deal.get((str(row.get("SSP")), str(row.get("Deal"))))

        def _pmp_req_series_for(row):
            return _pmp_req_series_by_deal.get((str(row.get("SSP")), str(row.get("Deal"))))

        def _pmp_resp_series_for(row):
            return _pmp_resp_series_by_deal.get((str(row.get("SSP")), str(row.get("Deal"))))

        def _pmp_spark_svg(values):
            """Compact 7-day revenue sparkline for the mobile PMP card. NEUTRAL
            (trend shape only — the eCPM-vs-floor banding owns severity). Scales
            **UNIFORMLY** (wide viewBox + CSS width:100%/height:auto, *no*
            preserveAspectRatio="none") so the aspect ratio is always preserved
            and the round-cap end dot can't smear into a bar. The card's spark
            box is ~9:1 — far from a 56×20 viewBox — and under that anisotropic
            stretch iOS Safari distorts a non-scaling round cap into a horizontal
            blob (the 2026-06-14 "graphs look off" bug; the Direct card escapes
            it only because its box is near-square). An XPAD inset keeps the end
            dot off the edge. Mirrors the drawer small-multiples' uniform regime;
            kept local because the Direct _sparkline_svg sits behind the
            `if gam_df.empty` else-branch and isn't reachable here."""
            vals = [float(v) for v in values if v is not None] if values else []
            if len(vals) < 2:
                return ""
            W, H, XPAD, PAD = 300, 34, 6, 4
            vmin, vmax = min(vals), max(vals)
            if vmax == vmin:
                vmax = vmin + 1
            n = len(vals)
            def _x(i): return XPAD + i / (n - 1) * (W - 2 * XPAD)
            def _y(v): return H - PAD - (v - vmin) / (vmax - vmin) * (H - 2 * PAD)
            pts = " ".join(f"{_x(i):.1f},{_y(v):.1f}" for i, v in enumerate(vals))
            li = n - 1
            dot = (f'<path d="M{_x(li):.1f} {_y(vals[li]):.1f}h0" fill="none" '
                   f'style="stroke:var(--text-secondary)" stroke-width="3.5" '
                   f'stroke-linecap="round" vector-effect="non-scaling-stroke"/>')
            return (f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">'
                    f'<polyline points="{pts}" fill="none" style="stroke:var(--text-secondary)" '
                    f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" '
                    f'vector-effect="non-scaling-stroke"/>{dot}</svg>')

        def _pmp_drawer_trend_chart(series, dates, label="7-day revenue", money=True):
            """7-day daily trend for a PMP deal drawer metric — revenue,
            total ad requests, or bid responses. Same area-wash + baseline +
            end-dot language as the Direct drawer delivery chart, but NEUTRAL (a
            trend is shape, not a pace-health signal; the eCPM-vs-floor banding
            owns severity). Scales UNIFORMLY (plain viewBox + CSS
            width:100%/height:auto) so geometry never warps. `money` toggles the
            `$` prefix on the total/latest legend (counts vs revenue). Skipped
            when the metric has no positive value in the window — so a GAM deal
            (no bid funnel) renders no requests/responses chart."""
            if not series:
                return ""
            nn = [v for v in series if v is not None]
            if not nn or sum(nn) <= 0:
                return ""
            W, H, PAD = 600, 112, 16
            mx = max(nn)
            vmax = mx * 1.2 if mx > 0 else 1
            n = len(series)
            base_y = H - PAD
            def _cx(i): return PAD + i / (n - 1) * (W - 2 * PAD) if n > 1 else W / 2
            def _cy(v): return base_y - (v or 0) / vmax * (H - 2 * PAD)
            pts = " ".join(f"{_cx(i):.1f},{_cy(v):.1f}" for i, v in enumerate(series))
            li = n - 1
            stroke = "var(--text-secondary)"
            area = (f'<polygon points="{pts} {_cx(li):.1f},{base_y:.1f} {_cx(0):.1f},{base_y:.1f}" '
                    f'style="fill:{stroke}" fill-opacity="0.10" stroke="none"/>')
            baseline = (f'<line x1="{PAD}" y1="{base_y:.1f}" x2="{W-PAD}" y2="{base_y:.1f}" '
                        f'style="stroke:var(--border)" stroke-width="1" vector-effect="non-scaling-stroke"/>')
            dx, dy = _cx(li), _cy(series[li])
            dot = (f'<path d="M{dx:.1f} {dy:.1f}h0" fill="none" style="stroke:var(--surface-1)" '
                   f'stroke-width="7.5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
                   f'<path d="M{dx:.1f} {dy:.1f}h0" fill="none" style="stroke:{stroke}" '
                   f'stroke-width="5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>')
            def _fmt(v):
                _p = "$" if money else ""
                if v >= 1_000_000: return f"{_p}{v/1_000_000:.1f}M"
                if v >= 1000:      return f"{_p}{v/1000:.1f}K"
                return f"{_p}{v:,.0f}"
            total = sum(nn)
            _tot = _fmt(total)
            _last = series[li] or 0
            _lat = _fmt(_last)
            _cells = ""
            for i, d in enumerate(dates or []):
                _lab = f"{d.strftime('%a')} {d.day}" if d else ""
                _cls = "is-today" if i == len(dates) - 1 else ""
                _cells += f'<span class="{_cls}">{_pmp_esc(_lab)}</span>'
            _date_row = f'<div class="nw-date-row">{_cells}</div>' if _cells else ""
            return (
                '<div class="nw-drawer-chart">'
                f'<div class="nw-drawer-chart-label"><span>{label}</span>'
                f'<span class="legend-row"><span class="legend">{_tot} total · {_lat}/day latest</span></span></div>'
                f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{area}{baseline}'
                f'<polyline points="{pts}" fill="none" style="stroke:{stroke}" stroke-width="1.75" '
                f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>{dot}</svg>'
                f'{_date_row}</div>'
            )

        def _pmp_drawer_html(row):
            _full = _pmp_esc(row.get("Deal") or "")
            _dt = row.get("Deal Type") or ""
            _floor = _deal_floor(row)
            _ecpm_v = pd.to_numeric(row.get("eCPM"), errors="coerce")
            _rev_chart  = _pmp_drawer_trend_chart(_pmp_rev_series_for(row), _pmp_rev_dates,
                                                  "7-day revenue", money=True)
            # Bid-funnel trends (Magnite / Pubmatic only; GAM deals skip them).
            _req_chart  = _pmp_drawer_trend_chart(_pmp_req_series_for(row), _pmp_rev_dates,
                                                  "7-day total requests", money=False)
            _resp_chart = _pmp_drawer_trend_chart(_pmp_resp_series_for(row), _pmp_rev_dates,
                                                  "7-day bid responses", money=False)

            # Status banner: eCPM vs floor thesis.
            status_html = ""
            if pd.notna(_ecpm_v) and _floor:
                if _ecpm_v < _floor:
                    pct_below = (_floor - _ecpm_v) / _floor * 100
                    status_html = (
                        '<div class="nw-status-banner sev-amber">'
                        '<strong>⚠ eCPM below floor</strong>'
                        f'<div>${_ecpm_v:.2f} clearing vs ${_floor:.2f} '
                        f'{_pmp_esc(_dt)} floor — {pct_below:.0f}% below committed rate.</div>'
                        '</div>'
                    )
                elif _ecpm_v >= _floor * 2:
                    pct_above = (_ecpm_v - _floor) / _floor * 100
                    status_html = (
                        '<div class="nw-status-banner sev-ok">'
                        '<strong>✓ Strong yield</strong>'
                        f'<div>${_ecpm_v:.2f} clearing — {pct_above:.0f}% above '
                        f'the ${_floor:.2f} {_pmp_esc(_dt)} floor.</div>'
                        '</div>'
                    )

            # Bid metrics inline (only when source SSP reports them).
            wr_num = pd.to_numeric(row.get("Win Rate %"), errors="coerce")
            tr_num = pd.to_numeric(row.get("Total Requests"), errors="coerce")
            br_num = pd.to_numeric(row.get("Bid Responses"), errors="coerce")
            bid_html = ""
            if pd.notna(wr_num) or pd.notna(tr_num) or pd.notna(br_num):
                cells = []
                if pd.notna(wr_num):
                    cells.append(
                        f'<div><span class="lbl">Win rate</span>'
                        f'<span class="val">{wr_num:.1f}%</span></div>'
                    )
                if pd.notna(tr_num):
                    cells.append(
                        f'<div><span class="lbl">Total requests</span>'
                        f'<span class="val">{_pmp_fmt_count(tr_num)}</span></div>'
                    )
                if pd.notna(br_num):
                    cells.append(
                        f'<div><span class="lbl">Bid responses</span>'
                        f'<span class="val">{_pmp_fmt_count(br_num)}</span></div>'
                    )
                if pd.notna(tr_num) and pd.notna(br_num) and tr_num > 0:
                    resp_rate = br_num / tr_num * 100
                    cells.append(
                        f'<div><span class="lbl">Response rate</span>'
                        f'<span class="val">{resp_rate:.1f}%</span></div>'
                    )
                if cells:
                    bid_html = f'<div class="nw-meta-grid">{"".join(cells)}</div>'

            # Full metadata grid.
            _floor_str = f"${_floor:.2f}" if _floor else "—"
            meta_html = (
                '<div class="nw-meta-grid">'
                f'<div><span class="lbl">SSP</span>'
                f'<span class="val">{_pmp_esc(row.get("SSP") or "—")}</span></div>'
                f'<div><span class="lbl">Deal type</span>'
                f'<span class="val">{_pmp_esc(_dt or "—")}</span></div>'
                f'<div><span class="lbl">DSP</span>'
                f'<span class="val">{_pmp_esc(row.get("DSP") or "—")}</span></div>'
                f'<div><span class="lbl">Format</span>'
                f'<span class="val">{_pmp_esc(row.get("Format") or "—")}</span></div>'
                f'<div><span class="lbl">Seller</span>'
                f'<span class="val">{_pmp_esc(row.get("Seller") or "—")}</span></div>'
                f'<div><span class="lbl">Deal source</span>'
                f'<span class="val">{_pmp_esc(row.get("Deal Source") or "—")}</span></div>'
                f'<div><span class="lbl">Team</span>'
                f'<span class="val">{_pmp_esc(row.get("Team") or "—")}</span></div>'
                f'<div><span class="lbl">Configured floor</span>'
                f'<span class="val">{_floor_str}</span></div>'
                '</div>'
            )

            # Action row — surface AirTable ticket when there's a meaningful
            # issue (eCPM under floor or significantly above). Healthy in-band
            # rows skip the button to avoid noise.
            _action_html = ""
            _show_at_pmp = pd.notna(_ecpm_v) and _floor and (
                _ecpm_v < _floor or _ecpm_v >= _floor * 2
            )
            if _show_at_pmp:
                _at_url = _pmp_airtable_url(row)
                if _at_url:
                    _action_html = (
                        '<div class="nw-actions">'
                        f'<a class="nw-action" href="{_at_url}" '
                        'target="_blank" rel="noopener noreferrer" '
                        f'title="File AirTable ticket · PMP - Adjust">'
                        '🎫 AirTable ticket</a>'
                        '</div>'
                    )
                else:
                    _action_html = (
                        '<div class="nw-actions">'
                        '<a class="nw-action is-disabled" '
                        'title="Configure AirTable Base ID and Form ID in Settings to enable" '
                        'aria-disabled="true">🎫 AirTable ticket</a>'
                        '</div>'
                    )

            return (
                '<div class="nw-pmp-drawer">'
                '<div class="nw-drawer-head">'
                f'<span class="nw-drawer-li">{_full or "—"}</span>'
                '</div>'
                f'{status_html}'
                f'<div class="nw-pmp-charts">{_rev_chart}{_req_chart}{_resp_chart}</div>'
                f'{bid_html}'
                f'{meta_html}'
                f'{_action_html}'
                '</div>'
            )

        # The PMP signals card (placeholder created under the KPI strip above)
        # renders now — _pmp_drawer_html and the revenue series it needs are
        # defined, so each flagged deal can expand to its full drawer.
        _render_pmp_signals()

        # ── Revenue threshold + pagination ──
        # "Show deals under $100/day" + "Exclude PG" now live in the Filters
        # popover; _show_low_rev was captured there and page reset is handled by
        # the filter signature above.
        _REV_MIN = 100.0 * 7           # $100/day × 7-day cache window

        _pmp_display = combined_pmp.copy()
        if not _show_low_rev and "Revenue" in _pmp_display.columns:
            _pmp_display = _pmp_display[_pmp_display["Revenue"].fillna(0) >= _REV_MIN]

        _PAGE_SIZE = 25
        _pmp_display_count = len(_pmp_display)
        _pmp_total_pages = max(1, math.ceil(_pmp_display_count / _PAGE_SIZE))
        _cur_page = max(0, min(int(st.session_state.get("pmp_page", 0)), _pmp_total_pages - 1))

        def _pmp_go_prev():
            st.session_state["pmp_page"] = max(0, _cur_page - 1)

        def _pmp_go_next():
            st.session_state["pmp_page"] = min(_pmp_total_pages - 1, _cur_page + 1)

        _pmp_page_slice = _pmp_display.iloc[_cur_page * _PAGE_SIZE : (_cur_page + 1) * _PAGE_SIZE]

        _pmp_pg_main = f"Page {_cur_page + 1} of {_pmp_total_pages}"
        _pmp_pg_sub = (f"{_pmp_display_count} of {_pmp_count} deals shown"
                       if _pmp_display_count < _pmp_count else "")
        _compact_pager("pmp_top", _cur_page, _pmp_total_pages,
                       _pmp_go_prev, _pmp_go_next, _pmp_pg_main, _pmp_pg_sub)

        # ── Table — custom HTML grid matching Direct campaigns design. ──
        _pmp_rows_html = []
        for _, row in _pmp_page_slice.iterrows():
            _primary, _sub = dl.pmp_deal_display_name(row.get("Deal") or "")
            _dt = row.get("Deal Type") or ""
            _floor_val = _deal_floor(row)
            _seller = row.get("Seller")
            if not isinstance(_seller, str) or not _seller.strip():
                _seller_html = '<span class="seller-prog">—</span>'
            else:
                # Render "Firstname I." → "F. Lastname"-style abbreviation.
                _parts = _seller.strip().split(" ")
                _seller_html = (f"{_parts[0][0]}. {_parts[-1]}" if len(_parts) >= 2 else _seller)
                _seller_html = _pmp_esc(_seller_html)

            _name_html = f'<span class="pmp-name-primary">{_pmp_esc(_primary)}</span>'
            if _sub:
                _name_html += f'<div class="pmp-name-sub">{_pmp_esc(_sub)}</div>'

            # DV Attention + SIVT + GIVT for PMP — joined by exact
            # deal_name (== Order in the DV CSV). GAM PMP rows are the
            # only ones that get DV coverage; Magnite/Pubmatic-only
            # deals fall through to "—". Priors give us the per-row Δ
            # underneath each cell (same look as Pace).
            _deal_key = row.get("Deal")
            _pmp_attn       = _dv_by_order.get(_deal_key)         if _dv_by_order         else None
            _pmp_attn_prior = _dv_prior_by_order.get(_deal_key)   if _dv_prior_by_order   else None
            _pmp_sivt       = _sivt_by_order.get(_deal_key)       if _sivt_by_order       else None
            _pmp_sivt_prior = _sivt_prior_by_order.get(_deal_key) if _sivt_prior_by_order else None
            _pmp_givt       = _givt_by_order.get(_deal_key)       if _givt_by_order       else None
            _pmp_givt_prior = _givt_prior_by_order.get(_deal_key) if _givt_prior_by_order else None

            # PMP mobile card (shown only ≤640px; CSS hides the 12 grid cells).
            # These deals have no daily series, so the visual is an eCPM-vs-floor
            # bar — floor at the 50% tick, banded like the eCPM cell (under floor
            # amber, ≥2× floor green, otherwise neutral).
            _m_ecpm_num = pd.to_numeric(row.get("eCPM"), errors="coerce")
            _m_ecpm_bar = ""
            if (_floor_val is not None and not pd.isna(_floor_val)
                    and float(_floor_val) > 0 and pd.notna(_m_ecpm_num)):
                _m_fl = float(_floor_val); _m_ec = float(_m_ecpm_num)
                if _m_ec < _m_fl:        _m_esev = "amber"
                elif _m_ec >= _m_fl * 2: _m_esev = "green"
                else:                    _m_esev = "neutral"
                _m_ew = max(0.0, min(_m_ec / (_m_fl * 2) * 100, 100.0))
                _m_ecpm_bar = (
                    '<div class="m-ecpm-wrap"><div class="m-ecpm-bar">'
                    f'<i class="{_m_esev}" style="width:{_m_ew:.0f}%"></i>'
                    '<span class="m-floor-tick"></span></div>'
                    f'<span class="m-ecpm-lbl">floor ${_m_fl:.2f}</span></div>'
                )
            # 7-day revenue sparkline (Option 3: shown alongside the eCPM bar).
            _rev_series_m = _pmp_rev_series_for(row)
            _rev_spark_m = ""
            if _rev_series_m and sum(v for v in _rev_series_m if v is not None) > 0:
                _sv = _pmp_spark_svg(_rev_series_m)
                if _sv:
                    _rev_spark_m = (f'<div class="m-spark2">{_sv}'
                                    '<div class="m-spark-l">revenue 7d</div></div>')
            # Deal-type pill lives at a fixed spot (top-right, above revenue),
            # NOT inline after the name — name lengths vary (and wrap), which
            # scattered the pill all over the column.
            _row_m_pmp = (
                '<div class="nw-pmp-m"><div class="m-main">'
                f'<div class="m-name">{_pmp_esc(_primary)}</div>'
                f'<div class="m-sub">{_pmp_esc(row.get("DSP") or "—")} · '
                f'{_pmp_esc(row.get("SSP") or "—")} · {_pmp_esc(row.get("Format") or "—")}</div>'
                f'{_m_ecpm_bar}{_rev_spark_m}</div>'
                '<div class="m-right">'
                f'<div class="m-dt">{_dt_pill(_dt)}</div>'
                f'<div class="m-rev">{_rev_cell(row.get("Revenue"))}</div>'
                f'<div class="m-ecpm">{_ecpm_cell(row.get("eCPM"), _floor_val)} eCPM</div>'
                f'<div class="m-impr">{_impr_cell(row.get("Paid Impressions"))} impr</div>'
                '</div></div>'
            )
            _pmp_rows_html.append(
                '<details name="pmp-cmprow">'
                '<summary class="nw-pmp-row">'
                f'<div>{_name_html}</div>'
                f'<div>{_dt_pill(_dt)}</div>'
                f'<div>{_pmp_esc(row.get("DSP") or "—")}</div>'
                f'<div>{_pmp_esc(row.get("SSP") or "—")}</div>'
                f'<div>{_pmp_esc(row.get("Format") or "—")}</div>'
                f'<div class="num">{_rev_cell(row.get("Revenue"))}</div>'
                f'<div class="num">{_impr_cell(row.get("Paid Impressions"))}</div>'
                f'<div class="num">{_ecpm_cell(row.get("eCPM"), _floor_val)}</div>'
                f'<div class="num center">{_attention_html(_pmp_attn, prior=_pmp_attn_prior)}</div>'
                f'<div class="num center">{_ivt_html(_pmp_sivt, prior=_pmp_sivt_prior)}</div>'
                f'<div class="num center">{_ivt_html(_pmp_givt, prior=_pmp_givt_prior)}</div>'
                f'<div>{_seller_html}</div>'
                + _row_m_pmp +
                '</summary>'
                + _pmp_drawer_html(row) +
                '</details>'
            )

        _pmp_hidden = _pmp_count - _pmp_display_count
        _pmp_tbl_sub = (
            f"· {_pmp_display_count} of {_pmp_count} shown · "
            f"{_pmp_hidden} under $100/day hidden · sorted by revenue"
            if _pmp_display_count < _pmp_count
            else f"· {_pmp_count} active · sorted by revenue"
        )
        if _pmp_total_pages > 1:
            _pmp_tbl_sub += f" · page {_cur_page + 1}/{_pmp_total_pages}"

        st.markdown(
            '<div class="nw-tbl-wrap nw-tbl-pmp">'
            '<div class="nw-tbl-head">'
            f'<div class="nw-tbl-title">PMP deals'
            f'<span class="nw-tbl-sub">{_pmp_tbl_sub}</span></div>'
            '<div class="nw-legend-pill">'
            '<span><span class="pill-dt pill-dt-pg">PG</span> Programmatic guaranteed</span>'
            '<span><span class="pill-dt pill-dt-pd">PD</span> Preferred deal</span>'
            '<span><span class="pill-dt pill-dt-pa">PA</span> Private auction</span>'
            '</div>'
            '</div>'
            '<div class="nw-pmp-rows">'
            '<div class="nw-row-header">'
            '<div>Deal</div><div>Type</div><div>DSP</div><div>SSP</div><div>Format</div>'
            '<div class="num">Revenue</div><div class="num">Impressions</div>'
            '<div class="num">eCPM</div>'
            '<div class="num center" title="DV Attention Index — 100 = industry median. GAM PMP rows only.">Attention</div>'
            '<div class="num center" title="Sophisticated Invalid Traffic — impression-weighted: Σ SIVT Monitored Ads / Σ all Monitored Ads. Industry tolerance ≤ 3%. GAM PMP rows only.">SIVT</div>'
            '<div class="num center" title="General Invalid Traffic — impression-weighted: Σ GIVT Monitored Ads / Σ all Monitored Ads. Industry tolerance ≤ 3%. GAM PMP rows only.">GIVT</div>'
            '<div>Seller</div>'
            '</div>'
            + "".join(_pmp_rows_html) +
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        _compact_pager("pmp_bot", _cur_page, _pmp_total_pages,
                       _pmp_go_prev, _pmp_go_next,
                       f"Page {_cur_page + 1} of {_pmp_total_pages}")

        # GAM private-auction "No delivery" and "Stale deals" both render
        # inside the "PMP signals" accordion above (folded in 2026-06); the
        # standalone cards + the archive UI were removed when they moved up.

# ── Settings tab ─────────────────────────────────────────────────────────────

if st.session_state.active_view == "configure":
    _s = _load_settings()  # fresh read so edits are based on current file

    _CANONICAL_FIELDS = [
        "Deal", "Deal Type", "DSP", "Format", "Seller",
        "Paid Impressions", "Revenue", "eCPM",
        "Win Rate %", "Total Requests", "Bid Responses",
    ]
    _ALL_DEAL_TYPES = [
        "Private Auction", "Preferred Deal",
        "Programmatic Guaranteed", "Private Marketplace",
    ]

    # ── Page header — eyebrow + Configure + Last saved (relative).
    _last_saved_disp = "—"
    try:
        with _engine().connect() as _conn_s:
            _row = _conn_s.execute(sqlalchemy.text(
                "SELECT updated_at FROM dashboard_settings WHERE key='main'"
            )).fetchone()
            if _row and _row[0]:
                _ts = pd.to_datetime(_row[0])
                _age = pd.Timestamp.now(tz="UTC") - _ts.tz_convert("UTC") if _ts.tzinfo else \
                       pd.Timestamp.utcnow() - _ts
                _hours = _age.total_seconds() / 3600
                if _hours < 1:
                    _last_saved_disp = f"{int(_age.total_seconds()/60)} min ago"
                elif _hours < 24:
                    _last_saved_disp = f"{int(_hours)} hours ago"
                else:
                    _last_saved_disp = f"{int(_hours/24)} days ago"
    except Exception:
        pass
    # Best-effort user attribution — first AE in the mapping (no real auth here).
    _by_user = "R. Hirano"
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'margin:6px 0 4px 0;">'
        f'<div><div class="nw-eyebrow">Yield &amp; pacing</div>'
        f'<div style="font-family:var(--font-display);font-size:22px;font-weight:700;color:var(--text-primary);">Configure</div></div>'
        f'<div style="font-size:11px;color:var(--text-muted);">'
        f'Last saved {_last_saved_disp} by {_by_user}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Unmapped-values detection — compute counts per category.
    def _query_distinct(sql):
        try:
            with _engine().connect() as _c:
                return set(str(r[0]).strip() for r in _c.execute(sqlalchemy.text(sql)).fetchall()
                           if r[0] is not None and str(r[0]).strip()
                           and str(r[0]).strip() not in ("(Not applicable)",))
        except Exception:
            return set()

    _live_dsps = (
        _query_distinct("SELECT DISTINCT dsp FROM gam_pmp_deals")
        | _query_distinct("SELECT DISTINCT dsp FROM pubmatic_deals")
    )
    _mapped_dsps = set((_s.get("dsp_aliases") or {}).keys())
    _unmapped_dsps = sorted(_live_dsps - _mapped_dsps - {""})

    _live_formats = (
        _query_distinct("SELECT DISTINCT ad_format FROM gam_pmp_deals")
        | _query_distinct("SELECT DISTINCT ad_format FROM pubmatic_deals")
    )
    _mapped_formats = set((_s.get("format_aliases") or {}).keys()) | {"Display", "Video", "Native", "Multi", "Interstitial", "Banner"}
    _unmapped_formats = sorted(_live_formats - _mapped_formats - {""})

    _live_dt = _query_distinct("SELECT DISTINCT deal_type FROM pubmatic_deals")
    _mapped_dt = set((_s.get("deal_type_aliases") or {}).keys()) | set((_s.get("deal_type_codes") or {}).values())
    _unmapped_dt = sorted(_live_dt - _mapped_dt - {""})

    _live_ds = _query_distinct("SELECT DISTINCT deal_source FROM pubmatic_deals")
    _mapped_ds = set((_s.get("deal_source_aliases") or {}).keys()) | {"Publisher", "Magnite"}
    _unmapped_ds = sorted(_live_ds - _mapped_ds - {""})

    # Seller codes from line item names / order names.
    _ae_codes_mapped = set((_s.get("ae_names") or {}).keys())
    _unmapped_codes = set()
    try:
        with _engine().connect() as _c:
            _names = _c.execute(sqlalchemy.text(
                "SELECT line_item_name FROM gam_campaigns "
                "WHERE line_item_name LIKE '%Team-USA_%' OR line_item_name LIKE '%Team-INTL_%' "
                "LIMIT 5000"
            )).fetchall()
        import re as _re_sc
        _ae_re = _re_sc.compile(r"Team-(?:USA|INTL)_([A-Za-z]+)")
        for (_n,) in _names:
            if not _n: continue
            _m = _ae_re.search(str(_n))
            if _m and _m.group(1) not in _ae_codes_mapped:
                _unmapped_codes.add(_m.group(1))
    except Exception:
        pass
    _unmapped_codes = sorted(_unmapped_codes)

    _pmp_unmapped_total = len(_unmapped_dsps) + len(_unmapped_formats) + len(_unmapped_dt) + len(_unmapped_ds)
    _direct_unmapped_total = len(_unmapped_codes)
    _total_unmapped = _pmp_unmapped_total + _direct_unmapped_total

    if _total_unmapped > 0:
        _bits = []
        if _unmapped_dsps:    _bits.append(f"DSP: '{_unmapped_dsps[0]}'")
        if _unmapped_formats: _bits.append(f"Format: '{_unmapped_formats[0]}'")
        if _unmapped_dt:      _bits.append(f"Deal type: '{_unmapped_dt[0]}'")
        if _unmapped_ds:      _bits.append(f"Deal source: '{_unmapped_ds[0]}'")
        if _unmapped_codes:   _bits.append(f"Seller code: '{_unmapped_codes[0]}'")
        _detail = " · ".join(_bits)
        if _total_unmapped > len(_bits):
            _detail += f" ({_total_unmapped - len(_bits)} more)"
        st.markdown(
            f'<div class="cfg-unmapped-banner">'
            f'<div class="cfg-unmapped-head">⚠ {_total_unmapped} unmapped value{"s" if _total_unmapped != 1 else ""} detected since last save</div>'
            f'<div>{_detail} — see the highlighted rows below</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Sub-tab labels include unmapped count badges.
    _pmp_tab_label    = f"PMP deals  {_pmp_unmapped_total}" if _pmp_unmapped_total > 0 else "PMP deals"
    _direct_tab_label = f"Direct campaigns  {_direct_unmapped_total}" if _direct_unmapped_total > 0 else "Direct campaigns"
    # Wrap the entire Configure body in a form so cell edits across the
    # sub-tabs (PMP / Direct) and every data_editor don't trigger a page
    # rerun on each keystroke. All edits batch until the user clicks the
    # form submit button at the bottom. Reactive elements like the
    # unmapped-values banner and tab-label counts reflect the LAST SAVED
    # state, not in-progress edits — that's the tradeoff for a stable
    # editing experience.
    with st.form("configure_form", clear_on_submit=False):
        _settings_pmp_tab, _settings_direct_tab = st.tabs([_pmp_tab_label, _direct_tab_label])

        with _settings_pmp_tab:
            # ────────────────────────────────────────────────────────────────────
            # SECTION 1 — Sources
            # ────────────────────────────────────────────────────────────────────
            _n_pmp_enabled = sum(1 for s in _s.get("ssps", []) if s.get("enabled", True))
            st.markdown(
                f'<div class="cfg-section-head" style="margin-top:8px">'
                f'<span class="cfg-eyebrow">Section 1 — Sources</span>'
                f'<span class="cfg-count">{_n_pmp_enabled} active</span></div>'
                f'<div class="cfg-desc">Each row is one SSP feeding the PMP table. '
                f'Disabling an SSP hides it everywhere downstream.</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="cfg-card-title">PMP data sources '
                f'<span class="cfg-card-meta">· {_n_pmp_enabled} active</span></div>',
                unsafe_allow_html=True,
            )

            _ssp_rows = [
                {
                    "SSP Name":            s["name"],
                    "Enabled":             s.get("enabled", True),
                    "Database Table":      s["table"],
                    "Deal Types":          ", ".join(s.get("deal_types", [])),
                    "Default Deal Source": s.get("deal_source_default", ""),
                }
                for s in _s["ssps"]
            ]
            _ssp_edit = st.data_editor(
                pd.DataFrame(_ssp_rows),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_ssps",
                column_config={
                    "SSP Name":       st.column_config.TextColumn("SSP Name", required=True),
                    "Enabled":        st.column_config.CheckboxColumn("Enabled"),
                    "Database Table": st.column_config.TextColumn(
                        "Database Table",
                        help="SQLite/Postgres table populated by refresh_cache.py",
                    ),
                    "Deal Types": st.column_config.TextColumn(
                        "Deal Types",
                        help="Comma-separated list — e.g. Private Auction, Preferred Deal",
                    ),
                    "Default Deal Source": st.column_config.TextColumn(
                        "Default Deal Source",
                        help="Fills the Deal Source column for rows where the SSP's data has none. "
                             "Example: GAM has no deal_source column, so set this to 'Publisher'.",
                    ),
                },
            )

            # ────────────────────────────────────────────────────────────────────
            # SECTION 2 — Field mapping
            # ────────────────────────────────────────────────────────────────────
            _n_canonical = len(_CANONICAL_FIELDS)
            st.markdown(
                f'<div class="cfg-section-head" style="margin-top:24px">'
                f'<span class="cfg-eyebrow">Section 2 — Field mapping</span>'
                f'<span class="cfg-count">{_n_canonical} fields mapped</span></div>'
                f'<div class="cfg-desc">Map each canonical display field to the source column in each SSP. '
                f'<span class="cfg-pill-info">auto</span> = parsed from deal name. '
                f'<i><span class="cfg-na">N/A</span></i> = not available from that SSP.</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="cfg-card-title">Metrics &amp; dimensions '
                f'<span class="cfg-card-meta">· {_n_canonical} fields mapped</span></div>',
                unsafe_allow_html=True,
            )

            _edited_ssp_names = [
                str(row["SSP Name"]).strip()
                for _, row in _ssp_edit.iterrows()
                if pd.notna(row["SSP Name"]) and str(row["SSP Name"]).strip()
            ]
            _existing_col_maps = {s["name"]: s.get("columns", {}) for s in _s["ssps"]}
            _ssp_table_map = {
                str(row["SSP Name"]).strip(): str(row.get("Database Table", "")).strip()
                for _, row in _ssp_edit.iterrows()
                if pd.notna(row["SSP Name"]) and str(row["SSP Name"]).strip()
            }

            # Fetch the actual column names from each SSP's table so dropdowns show real options
            _SPECIAL_OPTS = ["N/A", "[auto]"]

            def _table_cols(table: str) -> list:
                try:
                    with _engine().connect() as _c:
                        _r = _c.execute(sqlalchemy.text(f'SELECT * FROM "{table}" LIMIT 0'))
                        return [col for col in _r.keys() if not col.startswith("_")]
                except Exception:
                    return []

            # Build options list per SSP: special values + real columns + any currently-stored custom values
            _ssp_opts: dict = {}
            for _sn in _edited_ssp_names:
                _tbl = _ssp_table_map.get(_sn, "")
                _real_cols = _table_cols(_tbl)
                _opts = _SPECIAL_OPTS + [c for c in _real_cols if c not in _SPECIAL_OPTS]
                # Ensure any currently-stored value is always a valid option
                for _f in _CANONICAL_FIELDS:
                    _cur = _existing_col_maps.get(_sn, {}).get(_f, "")
                    if _cur and _cur not in _opts:
                        _opts.append(_cur)
                _ssp_opts[_sn] = _opts

            # Build matrix DataFrame
            _map_rows = []
            for _f in _CANONICAL_FIELDS:
                _row: dict = {"Field": _f}
                for _sn in _edited_ssp_names:
                    _raw = _existing_col_maps.get(_sn, {}).get(_f, "") or ""
                    _row[_sn] = _raw if _raw else "N/A"
                _map_rows.append(_row)
            _map_df = pd.DataFrame(_map_rows)

            _map_col_cfg: dict = {
                "Field": st.column_config.TextColumn("Field", disabled=True, width="small"),
            }
            for _sn in _edited_ssp_names:
                _tbl = _ssp_table_map.get(_sn, "")
                _map_col_cfg[_sn] = st.column_config.SelectboxColumn(
                    _sn,
                    options=_ssp_opts[_sn],
                    width="medium",
                    help=f"Source column from `{_tbl}`. N/A = not available. [auto] = parsed from deal name.",
                    required=False,
                )

            _map_edit = st.data_editor(
                _map_df,
                use_container_width=True,
                hide_index=True,
                key="settings_colmap",
                column_config=_map_col_cfg,
                disabled=["Field"],
            )

            # ────────────────────────────────────────────────────────────────────
            # SECTION 3 — Value normalization
            # ────────────────────────────────────────────────────────────────────
            _n_dt_aliases  = len(_s.get("deal_type_aliases", {}) or {})
            _n_dsp_aliases = len(_s.get("dsp_aliases", {}) or {})
            _n_fmt_aliases = len(_s.get("format_aliases", {}) or {})
            _n_ds_aliases  = len(_s.get("deal_source_aliases", {}) or {})
            _total_aliases = _n_dt_aliases + _n_dsp_aliases + _n_fmt_aliases + _n_ds_aliases
            st.markdown(
                f'<div class="cfg-section-head" style="margin-top:24px">'
                f'<span class="cfg-eyebrow">Section 3 — Value normalization</span>'
                f'<span class="cfg-count">{_total_aliases} aliases · {_total_unmapped if _total_unmapped else 0} unmapped</span></div>'
                f'<div class="cfg-desc">Map raw values returned by each SSP to your canonical labels. '
                f'Applied globally after combining all SSP data.</div>',
                unsafe_allow_html=True,
            )

            # ── 3a: Deal Type Mapping (canonical labels — kept here as related).
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:8px">Deal type codes '
                f'<span class="cfg-card-meta">· {len(_s.get("deal_type_codes", {}) or {})} mapped</span></div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'Short codes used inside deal/order names → canonical labels.</div>',
                unsafe_allow_html=True,
            )

            _dt_rows = [{"Code": k, "Label": v} for k, v in sorted(_s["deal_type_codes"].items())]
            _dt_edit = st.data_editor(
                pd.DataFrame(_dt_rows) if _dt_rows else pd.DataFrame(columns=["Code", "Label"]),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_dt",
                column_config={
                    "Code":  st.column_config.TextColumn("Code", required=True, help="e.g. PA, PD, PG, PMP"),
                    "Label": st.column_config.TextColumn("Label", required=True, help="e.g. Private Auction"),
                },
            )

            # ── Section 4: Deal Type Value Aliases ──────────────────────────────
            _unm_dt_html = (f' · <span class="cfg-warn-count">{len(_unmapped_dt)} unmapped</span>'
                            if _unmapped_dt else "")
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Deal type aliases '
                f'<span class="cfg-card-meta">· {_n_dt_aliases} mapped{_unm_dt_html}</span></div>',
                unsafe_allow_html=True,
            )
            # placeholder so the existing st.markdown("#### Deal Type Value Aliases") gets replaced
            _placeholder_dt_alias = None  # noqa: F841
            st.caption(
                "Map raw values returned by SSP APIs to canonical deal type labels. "
                "For example, GAM's REST API returns \"Preferred Deals\" (plural) — alias it to "
                "\"Preferred Deal\" so it matches the canonical label used across all SSPs."
            )
            _alias_rows = [
                {"Raw Value": k, "Canonical Deal Type": v}
                for k, v in _s.get("deal_type_aliases", {}).items()
            ]
            _alias_edit = st.data_editor(
                pd.DataFrame(_alias_rows) if _alias_rows else pd.DataFrame(
                    columns=["Raw Value", "Canonical Deal Type"]
                ),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_deal_type_aliases",
                column_config={
                    "Raw Value": st.column_config.TextColumn(
                        "Raw Value", help="Exact string returned by the SSP API", required=True
                    ),
                    "Canonical Deal Type": st.column_config.SelectboxColumn(
                        "Canonical Deal Type",
                        options=list(_s.get("deal_type_codes", {}).values()),
                        help="Canonical label used in the dashboard",
                        required=True,
                    ),
                },
            )

            # ── Section 4: DSP Name Aliases ─────────────────────────────────────
            _unm_dsp_html = (f' · <span class="cfg-warn-count">{len(_unmapped_dsps)} unmapped</span>'
                             if _unmapped_dsps else "")
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">DSP name aliases '
                f'<span class="cfg-card-meta">· {_n_dsp_aliases} mapped{_unm_dsp_html}</span></div>',
                unsafe_allow_html=True,
            )
            _placeholder_dsp_alias = None  # noqa: F841
            st.caption(
                "Normalize DSP names that appear under multiple spellings across SSPs. "
                "Applied globally after combining Magnite, GAM, and Pubmatic data."
            )
            _dsp_alias_rows = [
                {"Raw Value": k, "Canonical DSP Name": v}
                for k, v in _s.get("dsp_aliases", {}).items()
            ]
            _dsp_alias_edit = st.data_editor(
                pd.DataFrame(_dsp_alias_rows) if _dsp_alias_rows else pd.DataFrame(
                    columns=["Raw Value", "Canonical DSP Name"]
                ),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_dsp_aliases",
                column_config={
                    "Raw Value":         st.column_config.TextColumn("Raw Value", help="Exact string as it appears in the data", required=True),
                    "Canonical DSP Name": st.column_config.TextColumn("Canonical DSP Name", help="Preferred display name", required=True),
                },
            )

            # ── Section 5: Format Name Aliases ──────────────────────────────────
            _unm_fmt_html = (f' · <span class="cfg-warn-count">{len(_unmapped_formats)} unmapped</span>'
                             if _unmapped_formats else "")
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Format aliases '
                f'<span class="cfg-card-meta">· {_n_fmt_aliases} mapped{_unm_fmt_html}</span></div>',
                unsafe_allow_html=True,
            )
            _placeholder_fmt_alias = None  # noqa: F841
            st.caption(
                "Normalize Format names that appear under multiple spellings across SSPs. "
                "Applied globally after combining Magnite, GAM, and Pubmatic data."
            )
            _format_alias_rows = [
                {"Raw Value": k, "Canonical Format Name": v}
                for k, v in _s.get("format_aliases", {}).items()
            ]
            _format_alias_edit = st.data_editor(
                pd.DataFrame(_format_alias_rows) if _format_alias_rows else pd.DataFrame(
                    columns=["Raw Value", "Canonical Format Name"]
                ),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_format_aliases",
                column_config={
                    "Raw Value":            st.column_config.TextColumn("Raw Value", help="Exact string as it appears in the data", required=True),
                    "Canonical Format Name": st.column_config.TextColumn("Canonical Format Name", help="Preferred display name", required=True),
                },
            )

            # ── Section 6: Deal Source Aliases ──────────────────────────────────
            _unm_ds_html = (f' · <span class="cfg-warn-count">{len(_unmapped_ds)} unmapped</span>'
                            if _unmapped_ds else "")
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Deal source aliases '
                f'<span class="cfg-card-meta">· {_n_ds_aliases} mapped{_unm_ds_html}</span></div>',
                unsafe_allow_html=True,
            )
            _placeholder_ds_alias = None  # noqa: F841
            st.caption(
                "Normalize Deal Source names that differ across SSPs (e.g. Magnite's 'Publisher Deals' → 'Publisher'). "
                "Applied globally after combining all SSP data."
            )
            _deal_source_alias_rows = [
                {"Raw Value": k, "Canonical Deal Source Name": v}
                for k, v in _s.get("deal_source_aliases", {}).items()
            ]
            _deal_source_alias_edit = st.data_editor(
                pd.DataFrame(_deal_source_alias_rows) if _deal_source_alias_rows else pd.DataFrame(
                    columns=["Raw Value", "Canonical Deal Source Name"]
                ),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_deal_source_aliases",
                column_config={
                    "Raw Value":                 st.column_config.TextColumn("Raw Value", help="Exact string as it appears in the data", required=True),
                    "Canonical Deal Source Name": st.column_config.TextColumn("Canonical Deal Source Name", help="Preferred display name", required=True),
                },
            )


        with _settings_direct_tab:
            # ────────────────────────────────────────────────────────────────────
            # Header — title + "Last saved" stamp.
            # ────────────────────────────────────────────────────────────────────
            _last_saved_label = "—"
            try:
                with _engine().connect() as _conn_s:
                    _row = _conn_s.execute(sqlalchemy.text(
                        "SELECT updated_at FROM dashboard_settings WHERE key='main'"
                    )).fetchone()
                    if _row and _row[0]:
                        _last_saved_label = pd.to_datetime(_row[0]).strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                pass

            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin:6px 0 14px 0;">'
                f'<div><div class="nw-eyebrow">Yield &amp; pacing</div>'
                f'<div style="font-family:var(--font-display);font-size:22px;font-weight:700;color:var(--text-primary);">Configure</div></div>'
                f'<div style="font-size:11px;color:var(--text-muted);">Last saved: {_last_saved_label}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # ── Pre-compute everything used by counts and previews ──────────
            _gam_for_counts = None
            try:
                _gam_for_counts = load("gam_campaigns")
            except Exception:
                _gam_for_counts = pd.DataFrame()

            # Match counts per Included Order Pattern.
            def _matches_for_pattern(pat):
                if not isinstance(pat, str) or not pat.strip() or _gam_for_counts is None or _gam_for_counts.empty:
                    return 0
                prefix = pat.replace("%", "")
                if "order_name" not in _gam_for_counts.columns:
                    return 0
                distinct_orders = _gam_for_counts.loc[
                    _gam_for_counts["order_name"].fillna("").str.startswith(prefix), "order_name"
                ].nunique()
                return int(distinct_orders)

            # Line item count per ad_format — mirrors the runtime logic that
            # the Direct Campaigns table actually uses, so the Benchmarks
            # editor's "Applies to" column matches reality:
            #   1. dashboard_logic.derive_format with the saved aliases
            #      (same call as the campaigns view).
            #   2. The >30s preroll recategorization (max creative duration
            #      per LI via gam_lica + gam_creatives, bump long Video to
            #      "Video Preroll >30s").

            _format_counts = {}
            if (_gam_for_counts is not None
                and not _gam_for_counts.empty
                and ("inventory_format_name" in _gam_for_counts.columns
                     or "line_item_name" in _gam_for_counts.columns)):
                # Mirror the runtime pipeline exactly: derive_format (name
                # keywords beat the API value, then position-10 token),
                # canonicalized with the same aliases.
                _aliases = _s.get("format_aliases") or {}
                _api_col = (_gam_for_counts["inventory_format_name"]
                            if "inventory_format_name" in _gam_for_counts.columns
                            else pd.Series([None] * len(_gam_for_counts), index=_gam_for_counts.index))
                _name_col = (_gam_for_counts["line_item_name"]
                             if "line_item_name" in _gam_for_counts.columns
                             else pd.Series([None] * len(_gam_for_counts), index=_gam_for_counts.index))
                _fmt_series = pd.Series(
                    [dl.derive_format(_a, _n, _aliases) or ""
                     for _a, _n in zip(_api_col, _name_col)],
                    index=_gam_for_counts.index).astype("string")
                # Recategorize >30s preroll using the pre-aggregated SQL
                # GROUP BY (same data as the campaigns view, same cache).
                try:
                    _max_dur = _load_li_max_duration()
                    if (not _max_dur.empty
                        and "line_item_id" in _gam_for_counts.columns):
                        _li_to_dur = dict(zip(_max_dur["line_item_id"].astype(str),
                                              _max_dur["_creative_max_dur"]))
                        _li_ids = _gam_for_counts["line_item_id"].astype(str)
                        _durs = _li_ids.map(_li_to_dur)
                        _is_video = _fmt_series.str.lower().str.contains("video", na=False)
                        _is_long = _durs.fillna(0).astype(float) > 30
                        _fmt_series = _fmt_series.where(~(_is_video & _is_long),
                                                       "Video Preroll >30s")
                except Exception:
                    pass
                _format_counts = _fmt_series.value_counts().to_dict()
            def _format_count(fmt):
                if not isinstance(fmt, str) or not fmt: return 0
                # Direct match (post-alias + recategorization) — fast path.
                if fmt in _format_counts:
                    return int(_format_counts[fmt])
                # Case-insensitive direct match — handles minor casing drift
                # between the benchmarks dict key and the stored value.
                fmt_lower = fmt.lower()
                for k, v in _format_counts.items():
                    if isinstance(k, str) and k.lower() == fmt_lower:
                        return int(v)
                # Substring fallback for column values that include extra
                # qualifiers (e.g. "Video Spectacular" → counts under "Video").
                # Only fires for the more specific benchmark name, not the
                # generic one — "Video Preroll >30s" matches "Video Preroll
                # 60s" but plain "Video" doesn't slurp up every video subtype.
                if len(fmt_lower) >= 6:  # avoid 3-char generic names
                    return int(sum(v for k, v in _format_counts.items()
                                  if isinstance(k, str) and fmt_lower in k.lower()))
                return 0

            # Debug: trace exactly why "Applies to" might be 0 across all rows.
            # Reports the state of _gam_for_counts (None / empty / columns) plus
            # any captured load error, then shows raw vs aliased distributions
            # when data is available. Also offers an explicit cache-clear button.
            with st.expander("ad_format distribution (debug)", expanded=False):
                # st.button is forbidden inside an st.form context, which
                # is why this raised "Missing Submit Button" / StreamlitAPIException
                # in the live app. Use st.form_submit_button instead — it
                # works inside forms and still returns True on click, so
                # the cache.clear() + rerun() side effects fire as before.
                if st.form_submit_button("Clear cache + re-query gam_campaigns"):
                    st.cache_data.clear()
                    st.rerun()
                _g = _gam_for_counts
                if _g is None:
                    st.error("`_gam_for_counts` is None — load() never returned.")
                elif _g.empty and len(_g.columns) == 0:
                    st.error("`gam_campaigns` load returned an empty DataFrame "
                             "with no columns (likely a connection or query error).")
                elif _g.empty:
                    st.warning(f"`gam_campaigns` has 0 rows but columns present: "
                               f"{list(_g.columns)[:15]}")
                elif "ad_format" not in _g.columns:
                    st.warning(
                        "`gam_campaigns` has rows but **no `ad_format` column**. "
                        "Available columns: " + ", ".join(list(_g.columns)[:25])
                    )
                else:
                    st.success(f"`gam_campaigns` loaded — {len(_g):,} rows, "
                               f"ad_format present.")
                    _raw_counts = (_g["ad_format"].fillna("(null)")
                                   .value_counts().head(25))
                    st.markdown("**Raw `gam_campaigns.ad_format` (top 25):**")
                    st.dataframe(_raw_counts, use_container_width=True)
                    if _format_counts:
                        st.markdown("**After format_aliases + Video Preroll >30s recategorization:**")
                        st.dataframe(
                            pd.Series(_format_counts).rename("count")
                              .sort_values(ascending=False).head(25),
                            use_container_width=True,
                        )
                # Surface load errors captured by load() itself.
                if "gam_campaigns" in _load_errors:
                    st.code(f"load_errors['gam_campaigns']: {_load_errors['gam_campaigns']}",
                            language="text")

            # Seller usage (used by Seller Colors "Currently used in table").
            _seller_usage = {}
            if _gam_for_counts is not None and "salesperson" in _gam_for_counts.columns:
                _sp_norm = _gam_for_counts["salesperson"].apply(_parse_gam_salesperson)
                _seller_usage = _sp_norm.dropna().value_counts().to_dict()
            def _seller_count(name):
                if not isinstance(name, str) or not name: return 0
                return int(_seller_usage.get(name, 0))

            # ────────────────────────────────────────────────────────────────────
            # SECTION 1 — Scope & sources
            # ────────────────────────────────────────────────────────────────────
            _n_sources  = sum(1 for s in _s.get("direct_sources", []) if s.get("enabled", True))
            _n_patterns = len(_s.get("included_order_patterns", []) or [])
            st.markdown(
                f'<div class="cfg-section-head" style="margin-top:8px">'
                f'<span class="cfg-eyebrow">Section 1 — Scope &amp; sources</span>'
                f'<span class="cfg-count">{_n_sources} source · {_n_patterns} patterns</span></div>'
                f'<div class="cfg-desc">Which data sources feed the Direct Campaigns table, '
                f'which orders are included, and what\'s pre-selected on load.</div>',
                unsafe_allow_html=True,
            )

            # ── 1a: Direct Campaign Sources
            st.markdown(
                f'<div class="cfg-card-title">Direct campaign sources '
                f'<span class="cfg-card-meta">· {_n_sources} active</span></div>',
                unsafe_allow_html=True,
            )
            _direct_rows = [
                {
                    "Source Name":    s["name"],
                    "Enabled":        s.get("enabled", True),
                    "Database Table": s["table"],
                }
                for s in _s.get("direct_sources", [])
            ]
            _direct_edit = st.data_editor(
                pd.DataFrame(_direct_rows) if _direct_rows else pd.DataFrame(
                    columns=["Source Name", "Enabled", "Database Table"]
                ),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_direct_sources_v2",
                column_config={
                    "Source Name":    st.column_config.TextColumn("Source name", required=True),
                    "Enabled":        st.column_config.CheckboxColumn("Enabled"),
                    "Database Table": st.column_config.TextColumn(
                        "Database table",
                        help="Table populated by refresh_cache.py (e.g. gam_campaigns)",
                    ),
                },
            )

            # ── 1b: Included Order Patterns — with live match counts.
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Included order patterns '
                f'<span class="cfg-card-meta">· {_n_patterns} patterns · use % as wildcard</span></div>',
                unsafe_allow_html=True,
            )
            _incl_rows = [
                {"Pattern": p, "Currently matches": f"~{_matches_for_pattern(p)} orders"}
                for p in _s.get("included_order_patterns", ["Newsweek_Direct%"])
            ]
            _incl_edit = st.data_editor(
                pd.DataFrame(_incl_rows) if _incl_rows else pd.DataFrame(columns=["Pattern", "Currently matches"]),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_included_order_patterns",
                column_config={
                    "Pattern": st.column_config.TextColumn("Pattern",
                        help="Order name prefix, use % as wildcard (e.g. Newsweek_Direct%)"),
                    "Currently matches": st.column_config.TextColumn("Currently matches", disabled=True),
                },
                disabled=["Currently matches"],
            )

            # ── 1c: Default Status Filter.
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Default status filter</div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'Pre-selected when the table first loads.</div>',
                unsafe_allow_html=True,
            )
            _all_known_statuses = ["Delivering", "Upcoming", "Completed", "Paused", "Paused inventory released", "Inactive"]
            _default_statuses_edit = st.multiselect(
                "Default statuses",
                options=_all_known_statuses,
                default=_s.get("default_statuses", ["Delivering", "Upcoming"]),
                key="settings_default_statuses",
                label_visibility="collapsed",
            )

            # ── 1d: GAM Network ID — powers "Open in GAM" deep links from the drawer.
            _existing_net_id = (_s.get("gam_network_id") or "").strip()
            _env_net_id = os.environ.get("GAM_NETWORK_ID", "").strip()
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">GAM integration</div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'Network ID used to build the deep link in every drawer\'s '
                f'<span style="color:var(--text-primary)">Open in GAM ↗</span> button. '
                f'Find it in any GAM URL after <code>admanager.google.com/</code>.</div>',
                unsafe_allow_html=True,
            )
            _net_col_in, _net_col_hint = st.columns([2, 4])
            with _net_col_in:
                _gam_network_id_edit = st.text_input(
                    "GAM Network ID",
                    value=_existing_net_id,
                    placeholder=_env_net_id or "e.g. 1234567",
                    key="settings_gam_network_id",
                    label_visibility="collapsed",
                )
            with _net_col_hint:
                if (_gam_network_id_edit or _existing_net_id):
                    _eff = (_gam_network_id_edit or _existing_net_id).strip()
                    st.markdown(
                        f'<div style="font-size:11px;color:var(--text-secondary);padding-top:6px">'
                        f'Sample link: <span style="font-family:ui-monospace,Menlo,monospace;color:var(--text-secondary)">'
                        f'admanager.google.com/{_eff}#delivery/line_item/detail/line_item_id=…</span></div>',
                        unsafe_allow_html=True,
                    )
                elif _env_net_id:
                    st.markdown(
                        f'<div style="font-size:11px;color:var(--text-secondary);padding-top:6px">'
                        f'Currently falling back to <code>GAM_NETWORK_ID</code> env var '
                        f'(<span style="font-family:ui-monospace,Menlo,monospace;color:var(--text-secondary)">{_env_net_id}</span>). '
                        f'Set above to override.</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="font-size:11px;color:var(--state-warning);padding-top:6px">'
                        f'⚠ Not set — drawer "Open in GAM" buttons will be disabled.</div>',
                        unsafe_allow_html=True,
                    )

            # ── 1e: Manual long-preroll override ──
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Long preroll override '
                f'<span class="cfg-card-meta">· manual flag for &gt;30s preroll lines</span></div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'Force matching lines into the '
                f'<span style="color:var(--text-primary)">Video Preroll &gt;30s</span> '
                f'benchmark. Use this when Newsweek\'s 3rd-party video tags '
                f'(Innovid / DCM) hide creative duration behind JS so neither '
                f'the GAM API nor VAST parse can detect it. '
                f'<i>Match field</i>: order_name (substring), line_item_name (substring), '
                f'or line_item_id (exact).</div>',
                unsafe_allow_html=True,
            )
            _lp_rows = list(_s.get("long_preroll_lines") or [])
            _lp_edit = st.data_editor(
                pd.DataFrame(_lp_rows) if _lp_rows
                else pd.DataFrame(columns=["match_field", "match_value"]),
                use_container_width=True, hide_index=True, num_rows="dynamic",
                key="settings_long_preroll_lines",
                column_config={
                    "match_field": st.column_config.SelectboxColumn(
                        "Match field",
                        options=["order_name", "line_item_name", "line_item_id"],
                        required=True,
                        help="What to match against in the line item row.",
                    ),
                    "match_value": st.column_config.TextColumn(
                        "Match value",
                        required=True,
                        help="Substring (case-insensitive) for order_name / line_item_name. "
                             "Exact match for line_item_id.",
                    ),
                },
            )

            # ── 1f: AirTable ticket integration ──
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">AirTable integration</div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'Powers the drawer\'s '
                f'<span style="color:var(--text-primary)">🎫 AirTable ticket</span> button. '
                f'Routes Request Type automatically based on the drawer\'s state.</div>',
                unsafe_allow_html=True,
            )
            _at_b_col, _at_f_col, _at_r_col = st.columns(3)
            with _at_b_col:
                st.markdown('<div class="nw-filter-label">Base ID</div>', unsafe_allow_html=True)
                _at_base_edit = st.text_input(
                    "Base ID",
                    value=(_s.get("airtable_base_id") or "").strip(),
                    placeholder="appX7xp1veDq9ndUe",
                    key="settings_airtable_base_id",
                    label_visibility="collapsed",
                )
            with _at_f_col:
                st.markdown('<div class="nw-filter-label">Form ID</div>', unsafe_allow_html=True)
                _at_form_edit = st.text_input(
                    "Form ID",
                    value=(_s.get("airtable_form_id") or "").strip(),
                    placeholder="pagN88p2kwQBcjqZf",
                    key="settings_airtable_form_id",
                    label_visibility="collapsed",
                )
            with _at_r_col:
                st.markdown('<div class="nw-filter-label">Reporter</div>', unsafe_allow_html=True)
                _at_reporter_edit = st.text_input(
                    "Reporter",
                    value=(_s.get("airtable_reporter") or "").strip(),
                    placeholder="Roger Hirano",
                    key="settings_airtable_reporter",
                    label_visibility="collapsed",
                )

            # Request Type routing table — drawer context → AirTable enum value.
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:12px">Request Type routing '
                f'<span class="cfg-card-meta">· drawer context → AirTable enum</span></div>',
                unsafe_allow_html=True,
            )
            _at_routing_rows = _s.get("airtable_request_type_routing") or []
            _at_routing_edit = st.data_editor(
                pd.DataFrame(_at_routing_rows) if _at_routing_rows else pd.DataFrame(
                    columns=["context", "request_type"]
                ),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_airtable_routing",
                column_config={
                    "context": st.column_config.TextColumn(
                        "Drawer context",
                        help="When this state matches, route to the Request Type below.",
                    ),
                    "request_type": st.column_config.TextColumn(
                        "Request Type",
                        help="Must match an AirTable enum value exactly "
                             "(e.g. 'Direct Campaign - Troubleshooting', spaces around the hyphen).",
                    ),
                },
            )

            # Field name mapping — canonical name → AirTable form's actual field name.
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:12px">Field name mapping '
                f'<span class="cfg-card-meta">· canonical → AirTable form\'s actual field name</span></div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'AirTable\'s prefill URL parameters must match the form\'s actual field names. '
                f'Verify via AirTable → Share → Copy prefilled link.</div>',
                unsafe_allow_html=True,
            )
            _at_field_dict = _s.get("airtable_field_names") or {}
            _at_field_rows = [
                {"canonical": k, "airtable_field_name": v}
                for k, v in _at_field_dict.items()
            ]
            _at_fields_edit = st.data_editor(
                pd.DataFrame(_at_field_rows) if _at_field_rows else pd.DataFrame(
                    columns=["canonical", "airtable_field_name"]
                ),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_airtable_fields",
                column_config={
                    "canonical": st.column_config.TextColumn(
                        "Canonical name", help="Internal field name the dashboard uses.",
                    ),
                    "airtable_field_name": st.column_config.TextColumn(
                        "AirTable field name", help="Exact name as it appears in the AirTable form.",
                    ),
                },
            )

            # ────────────────────────────────────────────────────────────────────
            # SECTION 2 — Field mapping
            # ────────────────────────────────────────────────────────────────────
            _DIRECT_FIELDS = [
                "Seller", "Advertiser", "Campaign", "Line Item", "Format", "Status",
                "Start Date", "End Date", "Goal", "CPM Rate",
                "Delivered", "Remaining", "Clicks",
                "Pace", "Δ", "Viewability %", "CTR %", "VCR %", "Revenue",
            ]
            _DIRECT_COMPUTED = ["seller_ae", "salesperson", "advertiser", "campaign_name", "ad_format", "remaining_impressions", "pacing_delta"]

            _existing_direct_maps = {s["name"]: s.get("columns", {}) for s in _s.get("direct_sources", [])}
            _direct_src_names = [
                str(r["Source Name"]).strip()
                for _, r in _direct_edit.iterrows()
                if pd.notna(r["Source Name"]) and str(r["Source Name"]).strip()
            ]
            _direct_table_map = {
                str(r["Source Name"]).strip(): str(r.get("Database Table", "")).strip()
                for _, r in _direct_edit.iterrows()
                if pd.notna(r["Source Name"]) and str(r["Source Name"]).strip()
            }

            st.markdown(
                f'<div class="cfg-section-head" style="margin-top:24px">'
                f'<span class="cfg-eyebrow">Section 2 — Field mapping</span>'
                f'<span class="cfg-count">{len(_DIRECT_FIELDS)} fields mapped</span></div>'
                f'<div class="cfg-desc">Map each canonical display field to the source column. '
                f'<span class="cfg-computed">computed</span> = derived by the dashboard from raw fields.</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="cfg-card-title">Metrics &amp; dimensions '
                f'<span class="cfg-card-meta">· {len(_DIRECT_FIELDS)} fields mapped</span></div>',
                unsafe_allow_html=True,
            )

            _direct_ssp_opts: dict = {}
            for _dsn in _direct_src_names:
                _dtbl = _direct_table_map.get(_dsn, "")
                _dreal = _table_cols(_dtbl)
                _dopts = ["N/A"] + _DIRECT_COMPUTED + [c for c in _dreal if c not in (["N/A"] + _DIRECT_COMPUTED)]
                for _f in _DIRECT_FIELDS:
                    _cur = _existing_direct_maps.get(_dsn, {}).get(_f, "")
                    if _cur and _cur not in _dopts:
                        _dopts.append(_cur)
                _direct_ssp_opts[_dsn] = _dopts

            _direct_map_rows = []
            for _f in _DIRECT_FIELDS:
                _row2: dict = {"Field": _f}
                for _dsn in _direct_src_names:
                    _raw = _existing_direct_maps.get(_dsn, {}).get(_f, "") or ""
                    _row2[_dsn] = _raw if _raw else "N/A"
                _direct_map_rows.append(_row2)

            _direct_map_col_cfg: dict = {
                "Field": st.column_config.TextColumn("Canonical field", disabled=True, width="small"),
            }
            for _dsn in _direct_src_names:
                _dtbl = _direct_table_map.get(_dsn, "")
                _direct_map_col_cfg[_dsn] = st.column_config.SelectboxColumn(
                    f"{_dsn} source column",
                    options=_direct_ssp_opts[_dsn],
                    width="medium",
                    help=f"Source column from `{_dtbl}`. Computed columns: seller_ae, advertiser, campaign_name, ad_format.",
                    required=False,
                )

            _direct_map_edit = st.data_editor(
                pd.DataFrame(_direct_map_rows) if _direct_map_rows else pd.DataFrame(columns=["Field"]),
                use_container_width=True,
                hide_index=True,
                key="settings_direct_colmap",
                column_config=_direct_map_col_cfg,
                disabled=["Field"],
            )

            # ────────────────────────────────────────────────────────────────────
            # SECTION 3 — Performance benchmarks
            # ────────────────────────────────────────────────────────────────────
            _benchmarks_default = _s.get("benchmarks_by_format", {}) or {}
            _n_benchmark_formats = len(_benchmarks_default)
            st.markdown(
                f'<div class="cfg-section-head" style="margin-top:24px">'
                f'<span class="cfg-eyebrow">Section 3 — Performance benchmarks</span>'
                f'<span class="cfg-count">{_n_benchmark_formats} formats configured</span></div>'
                f'<div class="cfg-desc">Threshold values that drive cell coloring on the Direct Campaigns table. '
                f'Below benchmark → red→green gradient. Blank = no coloring for that metric.</div>',
                unsafe_allow_html=True,
            )

            # ── 3a: Pacing Target with inline gradient preview.
            _pacing_target_existing = float(_s.get("pacing_target_pct", 100.0))
            st.markdown(
                f'<div class="cfg-card-title">Pacing target</div>'
                f'<div style="font-size:11px;color:var(--text-secondary);">'
                f'Solid green at or above target.</div>',
                unsafe_allow_html=True,
            )
            _pt1, _pt2 = st.columns([1, 5])
            with _pt1:
                _pacing_target_edit = st.number_input(
                    "Target pacing %",
                    value=_pacing_target_existing,
                    min_value=0.0,
                    step=1.0,
                    format="%.1f",
                    key="settings_pacing_target",
                    label_visibility="collapsed",
                )
            with _pt2:
                _tgt_pct = max(0.0, min(100.0, _pacing_target_edit))
                st.markdown(
                    f'<div class="cfg-gradient">'
                    f'<div class="cfg-gradient-marker" style="left:{_tgt_pct:.1f}%;"></div></div>'
                    f'<div class="cfg-gradient-axis"><span>0%</span><span>→ 100%</span></div>',
                    unsafe_allow_html=True,
                )

            # ── 3b: Benchmarks by Format — with usage count and explicit blanks.
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Benchmarks by format '
                f'<span class="cfg-card-meta">· {_n_benchmark_formats} formats</span></div>',
                unsafe_allow_html=True,
            )

            # Detect "mostly blank" — most cells null → suggest enabling.
            _bench_blanks = 0
            _bench_total = 0
            for _fmt, _vals in _benchmarks_default.items():
                for _k in ("viewability_pct", "ctr_pct", "vcr_pct"):
                    _bench_total += 1
                    if _vals.get(_k) is None:
                        _bench_blanks += 1
            if _bench_total > 0 and _bench_blanks / _bench_total > 0.5:
                st.markdown(
                    '<div class="cfg-suggest">💡 <b>Suggested:</b> enable CTR &amp; VCR benchmarks. '
                    'All formats currently have CTR and VCR blank — those columns will render '
                    'uncolored on the table. Industry defaults: Display CTR 0.08%, Video VCR 65%.</div>',
                    unsafe_allow_html=True,
                )

            # Color bands: each metric has a green floor (the "%" column) and an
            # optional red ceiling (the "red <" column). Anything ≥ green is
            # green; anything below "red <" is red; in between is amber. Leave
            # "red <" blank to keep the implicit fallback (target × 0.85).
            st.markdown(
                '<div class="cfg-helper" style="font-size:12px;color:var(--text-secondary);'
                'margin:-4px 0 6px 0">'
                'Color bands: cell is <span style="color:var(--state-positive)">green</span> ≥ target, '
                '<span style="color:var(--state-warning)">amber</span> between target and red threshold, '
                '<span style="color:var(--state-critical)">red</span> below threshold. '
                'Leave “red &lt;” blank to default to 85% of target.'
                '</div>',
                unsafe_allow_html=True,
            )
            _bench_rows = [
                {"Format": fmt,
                 "Viewability %":     vals.get("viewability_pct"),
                 "Viewability red <": vals.get("viewability_red_below"),
                 "CTR %":             vals.get("ctr_pct"),
                 "CTR red <":         vals.get("ctr_red_below"),
                 "VCR %":             vals.get("vcr_pct"),
                 "VCR red <":         vals.get("vcr_red_below"),
                 "Applies to":        f"~{_format_count(fmt)} line items"}
                for fmt, vals in sorted(_benchmarks_default.items())
            ]
            _bench_edit = st.data_editor(
                pd.DataFrame(_bench_rows) if _bench_rows else pd.DataFrame(
                    columns=["Format",
                             "Viewability %", "Viewability red <",
                             "CTR %", "CTR red <",
                             "VCR %", "VCR red <",
                             "Applies to"]
                ),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_benchmarks_by_format",
                column_config={
                    "Format":            st.column_config.TextColumn("Format", required=True),
                    "Viewability %":     st.column_config.NumberColumn(
                        "Viewability %", format="%.1f",
                        help="Green floor — values at or above this render green."),
                    "Viewability red <": st.column_config.NumberColumn(
                        "Viewability red <", format="%.1f",
                        help="Red ceiling — values below this render red. Blank = 85% of target."),
                    "CTR %":             st.column_config.NumberColumn(
                        "CTR %", format="%.2f",
                        help="Green floor — values at or above this render green."),
                    "CTR red <":         st.column_config.NumberColumn(
                        "CTR red <", format="%.2f",
                        help="Red ceiling — values below this render red. Blank = 85% of target."),
                    "VCR %":             st.column_config.NumberColumn(
                        "VCR %", format="%.1f",
                        help="Green floor — values at or above this render green."),
                    "VCR red <":         st.column_config.NumberColumn(
                        "VCR red <", format="%.1f",
                        help="Red ceiling — values below this render red. Blank = 85% of target."),
                    "Applies to":        st.column_config.TextColumn("Applies to", disabled=True),
                },
                disabled=["Applies to"],
            )

            # ────────────────────────────────────────────────────────────────────
            # SECTION 4 — Identity & theming
            # ────────────────────────────────────────────────────────────────────
            _n_ae   = len(_s.get("ae_names", {}))
            _n_aes_distinct = len(set(_s.get("ae_names", {}).values()))
            st.markdown(
                f'<div class="cfg-section-head" style="margin-top:24px">'
                f'<span class="cfg-eyebrow">Section 4 — Identity &amp; theming</span>'
                f'<span class="cfg-count">{_n_aes_distinct} AEs · {_n_ae} code aliases</span></div>'
                f'<div class="cfg-desc">Normalize AE names and team codes; assign colors to statuses and '
                f'sellers for consistent visual identity across the dashboard.</div>',
                unsafe_allow_html=True,
            )

            # ── 4a: Seller Mapping with grouped preview.
            st.markdown(
                f'<div class="cfg-card-title">Seller mapping '
                f'<span class="cfg-card-meta">· {_n_aes_distinct} AEs · {_n_ae} code aliases</span></div>',
                unsafe_allow_html=True,
            )
            _ae_rows = [{"Code": k, "Full Name": v} for k, v in sorted(_s["ae_names"].items())]
            _ae_edit = st.data_editor(
                pd.DataFrame(_ae_rows) if _ae_rows else pd.DataFrame(columns=["Code", "Full Name"]),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_ae",
                column_config={
                    "Code":      st.column_config.TextColumn("Code in order/line item name", required=True),
                    "Full Name": st.column_config.TextColumn("Display name", required=True),
                },
            )

            # ── 4b: Team Mapping.
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Team mapping</div>',
                unsafe_allow_html=True,
            )
            _team_rows = [{"Code": k, "Label": v} for k, v in sorted(_s.get("team_names", {}).items())]
            _team_edit = st.data_editor(
                pd.DataFrame(_team_rows) if _team_rows else pd.DataFrame(columns=["Code", "Label"]),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_team",
                column_config={
                    "Code":  st.column_config.TextColumn("Code in line item name", required=True),
                    "Label": st.column_config.TextColumn("Display label", required=True),
                },
            )

            # ── 4c: Account Manager mapping.
            # Maps an AE code (Seller / field 14 of the LI name) to the Account
            # Manager who operates the campaign. The Direct campaigns view in
            # Overall Performance uses this map for the "Account Manager"
            # filter dropdown — each line's AM is looked up from its seller_ae.
            # AE codes not assigned to an AM fall into the "Unassigned" bucket
            # in the filter.
            #
            # AM is constrained to a small allowlist via a Selectbox column.
            # When the team grows past these two, just extend _AM_CHOICES.
            _AM_CHOICES = ["JC", "Jen"]
            _am_map = _s.get("account_managers", {}) or {}
            _n_assigned    = sum(1 for v in _am_map.values() if v)
            _n_unassigned  = sum(1 for v in _am_map.values() if not v)
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Account Manager mapping '
                f'<span class="cfg-card-meta">· {_n_assigned} assigned · {_n_unassigned} blank</span></div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'Each AE (Seller) can be paired with one of the Account Managers below. '
                f'Surfaces as the Account Manager filter on Direct campaigns. '
                f'Leave blank to keep the AE in the "Unassigned" bucket.</div>',
                unsafe_allow_html=True,
            )
            # Coerce stored values into the allowlist or None so the Selectbox
            # column doesn't reject pre-existing free-text values (e.g. from a
            # prior schema where AM was a TextColumn). Anything not in
            # _AM_CHOICES becomes None (blank) for the editor.
            _am_rows = [
                {"AE Code": k,
                 "Account Manager": (v if v in _AM_CHOICES else None)}
                for k, v in sorted(_am_map.items())
            ]
            _am_edit = st.data_editor(
                pd.DataFrame(_am_rows) if _am_rows
                else pd.DataFrame(columns=["AE Code", "Account Manager"]),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_account_managers",
                column_config={
                    "AE Code":         st.column_config.TextColumn(
                        "AE Code (matches Seller mapping above)", required=True,
                        help="The AE code as it appears in field 14 of the line item name "
                             "(e.g. AShah, JMakin). Must match a Code in the Seller mapping above."),
                    "Account Manager": st.column_config.SelectboxColumn(
                        "Account Manager",
                        options=_AM_CHOICES,
                        required=False,
                        help="Pick the AM paired with this AE. Leave blank to keep the AE unassigned."),
                },
            )

            # ── 4d: Status Colors + live preview.
            _status_color_rows = _s.get("status_colors", []) or []
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Status colors</div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'First substring match wins.</div>',
                unsafe_allow_html=True,
            )
            _status_color_editor = st.data_editor(
                pd.DataFrame(_status_color_rows) if _status_color_rows
                else pd.DataFrame(columns=["keyword", "color"]),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_status_colors",
                column_config={
                    "keyword": st.column_config.TextColumn("Status keyword", required=True),
                    "color":   st.column_config.TextColumn("Color", required=True,
                                  help="Hex like #2E7D32 or any CSS color"),
                },
            )
            # Live preview pills underneath.
            if _status_color_rows:
                def _cfg_esc(s):
                    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                            .replace(">", "&gt;").replace('"', "&quot;"))
                _pills = "".join(
                    f'<span class="cfg-pill-preview" style="background:{_cfg_esc(r.get("color",""))};'
                    f'color:#fff;margin-right:8px;opacity:0.95;">{_cfg_esc(r.get("keyword",""))}</span>'
                    for r in _status_color_rows
                    if r.get("keyword") and r.get("color")
                )
                st.markdown(
                    f'<div style="margin:8px 0 0 0;font-size:10px;color:var(--text-muted);'
                    f'letter-spacing:0.08em;text-transform:uppercase">Preview</div>'
                    f'<div style="margin-top:4px">{_pills}</div>',
                    unsafe_allow_html=True,
                )

            # ── 4d: Seller Colors — usage count + hash fallback + Show all toggle.
            _existing_seller_colors = _s.get("seller_colors", {}) or {}
            _known_ae_names = sorted(set(_s.get("ae_names", {}).values()))

            _seller_card_meta = "stable hash fallback when blank"
            st.markdown(
                f'<div class="cfg-card-title" style="margin-top:14px">Seller colors '
                f'<span class="cfg-card-meta">· {_seller_card_meta}</span></div>'
                f'<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">'
                f'Used in tables, filters, and chart legends.</div>',
                unsafe_allow_html=True,
            )
            _show_all_sellers = st.toggle(
                "Show all sellers (default: only those active in the table)",
                value=False,
                key="settings_show_all_sellers",
            )

            def _hash_fallback_color(name):
                import hashlib as _hashlib
                h = int(_hashlib.md5(name.encode("utf-8")).hexdigest()[:6], 16)
                return f"hsl({h % 360}, 55%, 38%)"

            _seller_color_rows = []
            for _name in _known_ae_names:
                _used = _seller_count(_name)
                if not _show_all_sellers and _used == 0:
                    continue
                _override = _existing_seller_colors.get(_name, "")
                _used_str = (f"{_used} line item{'s' if _used != 1 else ''}"
                             if _used > 0 else "— no active lines")
                _seller_color_rows.append({
                    "seller": _name,
                    "color":  _override,
                    "Used in table": _used_str,
                })
            # Also surface seller_colors keys not in ae_names (so user can remove them).
            for _extra in sorted(set(_existing_seller_colors.keys()) - set(_known_ae_names)):
                _seller_color_rows.append({
                    "seller": _extra,
                    "color":  _existing_seller_colors[_extra],
                    "Used in table": "— not in AE mapping",
                })
            _seller_color_editor = st.data_editor(
                pd.DataFrame(_seller_color_rows) if _seller_color_rows
                else pd.DataFrame(columns=["seller", "color", "Used in table"]),
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="settings_seller_colors",
                column_config={
                    "seller": st.column_config.TextColumn("Seller", required=True),
                    "color":  st.column_config.TextColumn("Override color",
                                  help="Hex like #1976D2; leave blank to use hash fallback"),
                    "Used in table": st.column_config.TextColumn("Used in table", disabled=True),
                },
                disabled=["Used in table"],
            )
            # Hash-fallback explainer for rows with blank overrides.
            _hash_fb_rows = [r for r in _seller_color_rows
                             if not (r.get("color") and str(r["color"]).strip())]
            if _hash_fb_rows:
                _swatches = "".join(
                    f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:14px;'
                    f'font-size:11px;color:var(--text-secondary)">'
                    f'<span style="width:10px;height:10px;border-radius:2px;'
                    f'background:{_hash_fallback_color(r["seller"])}"></span>'
                    f'{r["seller"]} <span class="cfg-tertiary">(hash fallback)</span>'
                    f'</span>'
                    for r in _hash_fb_rows[:8]
                )
                st.markdown(
                    f'<div style="margin-top:6px;font-size:11px;color:var(--text-muted);'
                    f'letter-spacing:0.05em">{_swatches}</div>',
                    unsafe_allow_html=True,
                )


        # ── Save ─────────────────────────────────────────────────────────────
        st.divider()
        if st.form_submit_button("💾  Save Settings", type="primary"):
            try:
                _new_ssps = []
                for _, _row in _ssp_edit.iterrows():
                    _ssp_name = str(_row.get("SSP Name", "")).strip()
                    if not _ssp_name:
                        continue
                    if _ssp_name in _map_edit.columns:
                        _col_map_new = {}
                        for _, _mr in _map_edit.iterrows():
                            _src = str(_mr[_ssp_name]).strip() if pd.notna(_mr[_ssp_name]) else "N/A"
                            _col_map_new[str(_mr["Field"])] = "" if _src == "N/A" else _src
                    else:
                        _col_map_new = _existing_col_maps.get(_ssp_name, {})
                    _dt_raw = str(_row.get("Deal Types", "")).strip()
                    _ds_default = str(_row.get("Default Deal Source", "") or "").strip()
                    _new_ssp_entry = {
                        "name":       _ssp_name,
                        "enabled":    bool(_row.get("Enabled", True)),
                        "table":      str(_row.get("Database Table", "")).strip(),
                        "deal_types": [t.strip() for t in _dt_raw.split(",") if t.strip()],
                        "columns":    _col_map_new,
                    }
                    if _ds_default:
                        _new_ssp_entry["deal_source_default"] = _ds_default
                    _new_ssps.append(_new_ssp_entry)

                _new_ae = {
                    str(r["Code"]).strip(): str(r["Full Name"]).strip()
                    for _, r in _ae_edit.iterrows()
                    if pd.notna(r.get("Code")) and str(r["Code"]).strip()
                }
                _new_team = {
                    str(r["Code"]).strip(): str(r["Label"]).strip()
                    for _, r in _team_edit.iterrows()
                    if pd.notna(r.get("Code")) and str(r["Code"]).strip()
                }
                # Preserve every AE Code, even when the Account Manager column is
                # blank. The Configure table is pre-populated with all known AE
                # codes so the user can fill in AMs incrementally — dropping
                # blank-AM rows on save would lose that scaffolding the first
                # time someone saves before completing the assignments. Blank
                # AMs are treated as "Unassigned" by the dashboard filter, so
                # behavior stays consistent whether the row is missing or
                # blank-valued.
                _new_account_managers = {
                    str(r["AE Code"]).strip(): (
                        str(r["Account Manager"]).strip()
                        if pd.notna(r.get("Account Manager")) else ""
                    )
                    for _, r in _am_edit.iterrows()
                    if pd.notna(r.get("AE Code")) and str(r["AE Code"]).strip()
                }
                _new_dt = {
                    str(r["Code"]).strip(): str(r["Label"]).strip()
                    for _, r in _dt_edit.iterrows()
                    if pd.notna(r.get("Code")) and str(r["Code"]).strip()
                }
                def _bench_val(v):
                    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
                        return None
                    try:
                        return float(v)
                    except Exception:
                        return None
                _new_benchmarks = {}
                for _, r in _bench_edit.iterrows():
                    _fmt = str(r.get("Format", "")).strip()
                    if not _fmt:
                        continue
                    _new_benchmarks[_fmt] = {
                        "viewability_pct":       _bench_val(r.get("Viewability %")),
                        "viewability_red_below": _bench_val(r.get("Viewability red <")),
                        "ctr_pct":               _bench_val(r.get("CTR %")),
                        "ctr_red_below":         _bench_val(r.get("CTR red <")),
                        "vcr_pct":               _bench_val(r.get("VCR %")),
                        "vcr_red_below":         _bench_val(r.get("VCR red <")),
                    }

                _new_pacing_target = float(_pacing_target_edit) if _pacing_target_edit is not None else 100.0

                _new_status_colors = []
                for _, r in _status_color_editor.iterrows():
                    _kw  = str(r.get("keyword", "")).strip()
                    _col = str(r.get("color", "")).strip()
                    if _kw and _col:
                        _new_status_colors.append({"keyword": _kw, "color": _col})

                _new_seller_colors = {}
                for _, r in _seller_color_editor.iterrows():
                    _name = str(r.get("seller", "")).strip()
                    _col  = str(r.get("color", "")).strip()
                    if _name and _col:
                        _new_seller_colors[_name] = _col

                _new_direct = []
                for _, _row in _direct_edit.iterrows():
                    _dsrc_name = str(_row.get("Source Name", "")).strip()
                    if not _dsrc_name:
                        continue
                    if _dsrc_name in _direct_map_edit.columns:
                        _dcol_map = {}
                        for _, _mr in _direct_map_edit.iterrows():
                            _src = str(_mr[_dsrc_name]).strip() if pd.notna(_mr[_dsrc_name]) else "N/A"
                            _dcol_map[str(_mr["Field"])] = "" if _src == "N/A" else _src
                    else:
                        _dcol_map = _existing_direct_maps.get(_dsrc_name, {})
                    _new_direct.append({
                        "name":             _dsrc_name,
                        "enabled":          bool(_row.get("Enabled", True)),
                        "table":            str(_row.get("Database Table", "")).strip(),

                        "columns":          _dcol_map,
                    })

                _new_aliases = {
                    str(r["Raw Value"]).strip(): str(r["Canonical Deal Type"]).strip()
                    for _, r in _alias_edit.iterrows()
                    if pd.notna(r.get("Raw Value")) and str(r["Raw Value"]).strip()
                    and pd.notna(r.get("Canonical Deal Type")) and str(r["Canonical Deal Type"]).strip()
                }
                _new_dsp_aliases = {
                    str(r["Raw Value"]).strip(): str(r["Canonical DSP Name"]).strip()
                    for _, r in _dsp_alias_edit.iterrows()
                    if pd.notna(r.get("Raw Value")) and str(r["Raw Value"]).strip()
                    and pd.notna(r.get("Canonical DSP Name")) and str(r["Canonical DSP Name"]).strip()
                }
                _new_format_aliases = {
                    str(r["Raw Value"]).strip(): str(r["Canonical Format Name"]).strip()
                    for _, r in _format_alias_edit.iterrows()
                    if pd.notna(r.get("Raw Value")) and str(r["Raw Value"]).strip()
                    and pd.notna(r.get("Canonical Format Name")) and str(r["Canonical Format Name"]).strip()
                }
                _new_deal_source_aliases = {
                    str(r["Raw Value"]).strip(): str(r["Canonical Deal Source Name"]).strip()
                    for _, r in _deal_source_alias_edit.iterrows()
                    if pd.notna(r.get("Raw Value")) and str(r["Raw Value"]).strip()
                    and pd.notna(r.get("Canonical Deal Source Name")) and str(r["Canonical Deal Source Name"]).strip()
                }

                _new_incl_patterns = [
                    str(r["Pattern"]).strip()
                    for _, r in _incl_edit.iterrows()
                    if pd.notna(r.get("Pattern")) and str(r["Pattern"]).strip()
                ]
                # AirTable routing + field name mapping from data editors.
                _new_at_routing = [
                    {"context": str(r.get("context") or "").strip(),
                     "request_type": str(r.get("request_type") or "").strip()}
                    for _, r in _at_routing_edit.iterrows()
                    if str(r.get("context") or "").strip()
                    and str(r.get("request_type") or "").strip()
                ]
                _new_at_fields = {
                    str(r.get("canonical") or "").strip():
                        str(r.get("airtable_field_name") or "").strip()
                    for _, r in _at_fields_edit.iterrows()
                    if str(r.get("canonical") or "").strip()
                    and str(r.get("airtable_field_name") or "").strip()
                }
                _save_settings({
                    "ssps": _new_ssps, "ae_names": _new_ae, "team_names": _new_team,
                    "account_managers": _new_account_managers,
                    "deal_type_codes": _new_dt, "deal_type_aliases": _new_aliases,
                    "dsp_aliases": _new_dsp_aliases, "format_aliases": _new_format_aliases,
                    "deal_source_aliases": _new_deal_source_aliases,
                    "included_order_patterns": _new_incl_patterns,
                    "default_statuses": list(_default_statuses_edit),
                    "direct_sources": _new_direct,
                    "benchmarks_by_format": _new_benchmarks,
                    "pacing_target_pct":   _new_pacing_target,
                    "status_colors":       _new_status_colors,
                    "seller_colors":       _new_seller_colors,
                    "gam_network_id":      (_gam_network_id_edit or "").strip(),
                    "long_preroll_lines": [
                        {"match_field": str(r.get("match_field") or "").strip(),
                         "match_value": str(r.get("match_value") or "").strip()}
                        for _, r in _lp_edit.iterrows()
                        if str(r.get("match_field") or "").strip()
                        and str(r.get("match_value") or "").strip()
                    ],
                    "airtable_base_id":    (_at_base_edit or "").strip(),
                    "airtable_form_id":    (_at_form_edit or "").strip(),
                    "airtable_reporter":   (_at_reporter_edit or "").strip(),
                    "airtable_request_type_routing": _new_at_routing,
                    "airtable_field_names": _new_at_fields,
                })
                st.cache_data.clear()
                st.success("Settings saved — reloading dashboard…")
                st.rerun()
            except Exception as _e:
                st.error(f"Failed to save: {_e}")
