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
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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

import altair as alt
import pandas as pd
import sqlalchemy
import streamlit as st

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


def _engine() -> sqlalchemy.Engine:
    try:
        url = st.secrets["DATABASE_URL"]
    except Exception:
        url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env or Streamlit secrets.")
    return sqlalchemy.create_engine(url)


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
    "benchmarks_by_format": {
        "Display":      {"viewability_pct": 70.0, "ctr_pct": 0.30, "vcr_pct": None},
        "Video":        {"viewability_pct": 70.0, "ctr_pct": 0.30, "vcr_pct": 70.0},
        "Native":       {"viewability_pct": 70.0, "ctr_pct": 0.30, "vcr_pct": None},
        "Multi":        {"viewability_pct": 70.0, "ctr_pct": 0.30, "vcr_pct": 70.0},
        "Interstitial": {"viewability_pct": 70.0, "ctr_pct": 0.30, "vcr_pct": None},
    },
    "pacing_target_pct": 100.0,
    "status_colors": [
        {"keyword": "Delivering", "color": "#2E7D32"},  # green
        {"keyword": "Paused",     "color": "#F9A825"},  # amber
        {"keyword": "Completed",  "color": "#5D4037"},  # brown
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
        """Return loaded settings with any missing top-level keys filled from _DEFAULT_SETTINGS."""
        result = {**_DEFAULT_SETTINGS, **loaded}
        # Deep-merge ae_names and team_names so new default entries flow through even when DB has existing settings.
        result["ae_names"] = {**_DEFAULT_SETTINGS.get("ae_names", {}), **loaded.get("ae_names", {})}
        result["team_names"] = {**_DEFAULT_SETTINGS.get("team_names", {}), **loaded.get("team_names", {})}
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


def _parse_gam_salesperson(val):
    """Extract the short name from GAM's User.display_name.

    GAM returns values like "Newsweek - Sales - Theresa Hern" or
    "Newsweek - Sales- Jeremy Makin (jmakin@newsweek.com)" — strip the
    "Newsweek - Sales[-] " prefix and any trailing email parenthetical.
    Returns None for empty / non-string inputs.
    """
    if not isinstance(val, str) or not val.strip():
        return None
    m = re.search(r"-\s*([^-(]+?)\s*(?:\(|$)", val)
    return m.group(1).strip() if m else val.strip()


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


def _parse_deal(deal: str) -> pd.Series:
    """Extract fields from Newsweek structured deal name.

    Format: Newsweek_TYPE_VERTICAL_PLATFORM_DSP_..._FORMAT_$PRICE_Team-X_AE
    """
    empty = pd.Series({
        "revenue_source": "Open Market",
        "deal_type_label": None,
        "dsp": None,
        "ad_format": None,
        "floor_price": None,
    })
    raw = str(deal).strip() if deal else ""
    if not raw or raw.upper().replace("-", "").replace("/", "") in ("NA", "0"):
        return empty

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

    return pd.Series({
        "revenue_source":  revenue_source,
        "deal_type_label": deal_type_label,
        "dsp":             dsp,
        "ad_format":       ad_format,
        "floor_price":     floor_price,
    })

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
:root {
  --border-radius-md: 8px;
  --border-radius-lg: 12px;
}
/* H1 sizing per spec — Streamlit's default is much larger. */
h1, .stMarkdown h1 { font-size: 22px !important; font-weight: 600; margin: 0 0 4px 0; line-height: 1.2; }
/* Tabular numbers across every cell + KPI value. */
[data-testid="stMetricValue"], [data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th,
.kpi-value, .kpi-target, .nw-num { font-variant-numeric: tabular-nums; }
/* Active tab underline — replace Streamlit's default red highlight with the
   standard text color (red is reserved for severity). */
.stTabs [aria-selected="true"] { border-bottom: 2px solid var(--text-color) !important; color: var(--text-color) !important; }
.stTabs [data-baseweb="tab-highlight"] { background-color: var(--text-color) !important; }
/* Eyebrow label */
.nw-eyebrow { font-size: 10px; letter-spacing: 0.10em; text-transform: uppercase;
              color: rgba(250,250,250,0.55); font-weight: 500; }
.nw-timestamp { font-size: 12px; color: rgba(250,250,250,0.55); text-align: right;
                font-variant-numeric: tabular-nums; }
/* Small uppercase filter labels (used above each select). */
.nw-filter-label { font-size: 10px; letter-spacing: 0.10em; text-transform: uppercase;
                   color: rgba(250,250,250,0.55); font-weight: 500; margin-bottom: 2px; }
/* Exception banners */
.nw-banner { border-radius: var(--border-radius-md); padding: 10px 14px; margin: 2px 0;
             border: 0.5px solid rgba(255,255,255,0.08); font-size: 12px; line-height: 1.35; }
.nw-banner .nw-banner-head { font-size: 10px; letter-spacing: 0.10em; text-transform: uppercase;
                             font-weight: 600; margin-bottom: 2px; }
.nw-banner.sev-red    { background: rgba(244, 67, 54, 0.12); color: hsl(0, 80%, 80%);   border-color: rgba(244, 67, 54, 0.35); }
.nw-banner.sev-amber  { background: rgba(255, 167, 38, 0.10); color: hsl(35, 75%, 75%); border-color: rgba(255, 167, 38, 0.30); }
.nw-banner.sev-ok     { background: rgba(76, 175, 80, 0.08);  color: hsl(120, 35%, 75%); border-color: rgba(76, 175, 80, 0.25); }
/* KPI tile — quieter than st.metric's default display sizing. */
.kpi-tile  { padding: 12px 14px; border-radius: var(--border-radius-lg);
             background: rgba(255,255,255,0.03); border: 0.5px solid rgba(255,255,255,0.08); }
.kpi-label { font-size: 10px; letter-spacing: 0.10em; text-transform: uppercase;
             color: rgba(250,250,250,0.55); font-weight: 500; margin-bottom: 4px; }
.kpi-value { font-size: 18px; font-weight: 500; line-height: 1.2; }
.kpi-target{ font-size: 11px; color: rgba(250,250,250,0.55); margin-top: 2px; }
/* Sentence-case helper class (utility — applied selectively). */
.nw-sentence::first-letter { text-transform: uppercase; }
/* Compact dataframe borders */
[data-testid="stDataFrame"] table { border-collapse: collapse; }
[data-testid="stDataFrame"] th, [data-testid="stDataFrame"] td { border-bottom-width: 0.5px !important; }
/* "Prog." filler for sellerless rows */
.nw-prog { font-style: italic; color: rgba(250,250,250,0.55); }
/* Ordinal badge */
.nw-ord { font-size: 10px; padding: 1px 6px; border-radius: 999px;
          background: rgba(255,255,255,0.06); color: rgba(250,250,250,0.65);
          margin-right: 6px; font-variant-numeric: tabular-nums; }
/* Differentiator subtitle */
.nw-sub { font-size: 11px; color: rgba(250,250,250,0.50); font-variant-numeric: tabular-nums; }
/* Title muted */
h1, .stMarkdown h1 { color: rgba(250,250,250,0.60); }
/* Tab inactive labels muted */
.stTabs button[aria-selected="false"] { color: rgba(250,250,250,0.45) !important; }
/* ── Custom HTML table for Direct Campaigns ─────────────────────────── */
.nw-tbl-wrap { background: rgba(255,255,255,0.03); border-radius: var(--border-radius-lg);
               border: 0.5px solid rgba(255,255,255,0.08); padding: 16px 18px; margin: 8px 0; }
.nw-tbl-head { display: flex; justify-content: space-between; align-items: center;
               margin-bottom: 10px; font-size: 12px; }
.nw-tbl-title { color: rgba(250,250,250,0.85); font-weight: 500; }
.nw-tbl-title .nw-tbl-sub { color: rgba(250,250,250,0.45); font-weight: 400; margin-left: 6px; }
.nw-legend { display: flex; gap: 14px; font-size: 11px; color: rgba(250,250,250,0.55);
             font-variant-numeric: tabular-nums; }
.nw-legend-dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px;
                 vertical-align: middle; }
.nw-tbl { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.nw-tbl th { text-align: left; font-size: 10px; letter-spacing: 0.10em; text-transform: uppercase;
             color: rgba(250,250,250,0.45); font-weight: 500; padding: 6px 10px 10px;
             border-bottom: 0.5px solid rgba(255,255,255,0.08); }
.nw-tbl th.num { text-align: right; }
.nw-tbl td { padding: 10px; vertical-align: top; font-size: 13px; color: rgba(250,250,250,0.85);
             border-bottom: 0.5px solid rgba(255,255,255,0.05); }
.nw-tbl td.num { text-align: right; }
.nw-tbl tr:last-child td { border-bottom: none; }
.li-name { font-weight: 500; color: rgba(250,250,250,0.92); }
.li-sub  { font-size: 11px; color: rgba(250,250,250,0.45); margin-top: 2px; }
.li-ord  { font-size: 10px; padding: 1px 6px; border-radius: 999px;
           background: rgba(255,255,255,0.06); color: rgba(250,250,250,0.55); margin-right: 6px; }
.pill { display: inline-block; padding: 2px 10px; border-radius: 6px; font-weight: 600;
        font-size: 12px; line-height: 1.4; }
.pill-red    { background: hsl(0, 35%, 22%);  color: hsl(0, 30%, 80%); }
.pill-amber  { background: hsl(40, 45%, 22%); color: hsl(40, 35%, 78%); }
.txt-green   { color: hsl(120, 50%, 65%); font-weight: 600; font-size: 13px; }
.txt-amber   { color: hsl(40, 70%, 65%); font-weight: 500; font-size: 13px; }
.txt-red     { color: hsl(0, 60%, 70%); font-weight: 500; font-size: 13px; }
.pace-delta  { font-size: 11px; margin-top: 4px; color: hsl(0, 50%, 70%); }
.pace-delta.up { color: hsl(120, 40%, 70%); }
.pace-delta.amber { color: hsl(40, 60%, 70%); }
.nw-prog-bar { width: 100%; height: 8px; background: rgba(255,255,255,0.06); border-radius: 4px;
               overflow: hidden; }
.nw-prog-fill { height: 100%; border-radius: 4px; }
.prog-red   { background: hsl(0, 50%, 55%); }
.prog-amber { background: hsl(40, 60%, 50%); }
.prog-green { background: hsl(120, 40%, 50%); }
.seller-prog { font-style: italic; color: rgba(250,250,250,0.45); }
.cell-dash { color: rgba(250,250,250,0.30); }
.bold-rev  { font-weight: 700; }
/* ── Settings sections (Direct Campaigns redesign) ───────────────── */
.cfg-section { background: rgba(255,255,255,0.02); border-radius: var(--border-radius-lg);
               border: 0.5px solid rgba(255,255,255,0.08); padding: 16px 20px; margin: 10px 0; }
.cfg-section-head { display: flex; justify-content: space-between; align-items: baseline;
                    margin-bottom: 4px; }
.cfg-eyebrow { font-size: 10px; letter-spacing: 0.10em; text-transform: uppercase;
               color: rgba(250,250,250,0.45); font-weight: 500; }
.cfg-count   { font-size: 11px; color: rgba(250,250,250,0.45);
               font-variant-numeric: tabular-nums; }
.cfg-title   { font-size: 18px; font-weight: 600; color: rgba(250,250,250,0.92);
               margin: 0 0 4px 0; }
.cfg-desc    { font-size: 12px; color: rgba(250,250,250,0.55); margin-bottom: 14px;
               line-height: 1.5; }
.cfg-card    { background: rgba(255,255,255,0.025); border-radius: var(--border-radius-md);
               border: 0.5px solid rgba(255,255,255,0.06); padding: 12px 14px; margin: 8px 0; }
.cfg-card-title { font-size: 13px; font-weight: 500; margin-bottom: 6px; }
.cfg-card-meta  { font-size: 11px; color: rgba(250,250,250,0.45);
                  margin-left: 8px; font-weight: 400; }
.cfg-mono    { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px;
               color: rgba(250,250,250,0.80); }
.cfg-tertiary{ color: rgba(250,250,250,0.45); }
.cfg-status-enabled { display: inline-block; padding: 1px 8px; border-radius: 4px;
                      background: hsl(120, 40%, 22%); color: hsl(120, 30%, 78%);
                      font-size: 10px; font-weight: 600; letter-spacing: 0.05em; }
.cfg-status-disabled{ display: inline-block; padding: 1px 8px; border-radius: 4px;
                      background: rgba(255,255,255,0.06); color: rgba(250,250,250,0.45);
                      font-size: 10px; font-weight: 600; letter-spacing: 0.05em; }
.cfg-computed { display: inline-block; padding: 1px 6px; border-radius: 3px;
                background: rgba(33, 150, 243, 0.18); color: hsl(207, 70%, 78%);
                font-size: 9px; font-weight: 600; letter-spacing: 0.04em; margin-left: 6px;
                vertical-align: middle; }
.cfg-suggest { background: rgba(33, 150, 243, 0.10); color: hsl(207, 70%, 80%);
               border: 0.5px solid rgba(33, 150, 243, 0.30);
               border-radius: var(--border-radius-md); padding: 10px 14px;
               font-size: 12px; margin: 8px 0; }
.cfg-gradient { height: 12px; border-radius: 6px; margin: 6px 0;
                background: linear-gradient(to right,
                  hsl(0, 60%, 50%) 0%, hsl(35, 70%, 50%) 50%, hsl(120, 50%, 50%) 100%); position: relative; }
.cfg-gradient-marker { position: absolute; top: -3px; width: 2px; height: 18px;
                       background: rgba(255,255,255,0.85); border-radius: 1px; }
.cfg-gradient-axis { display: flex; justify-content: space-between; font-size: 10px;
                     color: rgba(250,250,250,0.45); margin-top: 2px; }
.cfg-key-row { display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 12px;
               padding: 6px 0; border-bottom: 0.5px solid rgba(255,255,255,0.04); align-items: center; }
.cfg-key-row:last-child { border-bottom: none; }
.cfg-pill-preview { display: inline-block; padding: 2px 10px; border-radius: 6px;
                    font-weight: 600; font-size: 12px; }
.cfg-alias { font-size: 12px; color: rgba(250,250,250,0.70); padding: 2px 0 2px 18px;
             font-family: ui-monospace, Menlo, Consolas, monospace; }
.cfg-canonical { font-size: 13px; font-weight: 500; color: rgba(250,250,250,0.90); padding: 4px 0; }
.cfg-canonical.system { font-style: italic; color: rgba(250,250,250,0.55); }
</style>
""",
    unsafe_allow_html=True,
)

# ── Header block: eyebrow / H1 / right-aligned timestamp + line-item count.
# Line-item count is computed below the load() helper; we render a placeholder
# header here and overwrite the timestamp slot once the count is known.
_hdr_left, _hdr_right = st.columns([3, 2])
with _hdr_left:
    st.markdown('<div class="nw-eyebrow">Yield &amp; pacing</div>', unsafe_allow_html=True)
    st.markdown("# Newsweek overall performance")
_header_timestamp_slot = _hdr_right.empty()


_load_errors: dict[str, str] = {}  # table → error message, populated by load()


@st.cache_data(ttl=300)
def load(table: str) -> pd.DataFrame:
    try:
        with _engine().connect() as conn:
            return pd.read_sql(f'SELECT * FROM "{table}"', conn)
    except Exception as _e:
        _load_errors[table] = str(_e)
        return pd.DataFrame()


tab_seller, tab_site, tab_dsp, tab_deal, tab_pubmatic, tab_settings = st.tabs([
    "Campaigns", "By site / size", "By DSP", "Magnite deals", "Pubmatic deals", "⚙  Configure",
])

with tab_site:
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
            st.line_chart(daily, height=220)
        with col_funnel:
            st.subheader("Bid funnel")
            funnel = pd.Series({
                "Ad requests": view["ad_requests"].sum(),
                "Bid requests": view["bid_requests"].sum(),
                "Impressions": view["impressions"].sum(),
            })
            st.bar_chart(funnel, height=220)

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

with tab_deal:
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
            st.bar_chart(src_rev, height=280, horizontal=True)
        pmp_view = view[view["revenue_source"] == "Deal"]
        with col_deals:
            st.subheader("Top 10 deals by revenue")
            top10_deals = (
                pmp_view.groupby("deal")["publisher_gross_revenue"]
                .sum().nlargest(10).reset_index()
                .rename(columns={"deal": "Deal", "publisher_gross_revenue": "Revenue"})
            )
            chart = alt.Chart(top10_deals).mark_bar().encode(
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
            st.bar_chart(ae_rev, height=280, horizontal=True)

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

with tab_dsp:
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
            st.bar_chart(top10_rev, height=280, horizontal=True)
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

with tab_pubmatic:
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
                chart = alt.Chart(top_deals).mark_bar().encode(
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
                chart_dsp = alt.Chart(top_dsps).mark_bar().encode(
                    x=alt.X("Revenue:Q", title="Revenue ($)"),
                    y=alt.Y("DSP:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
                    tooltip=["DSP", alt.Tooltip("Revenue:Q", format="$,.2f")],
                ).properties(height=320)
                st.altair_chart(chart_dsp, use_container_width=True)

        st.subheader("Daily revenue trend")
        daily_pm = view.groupby("date")["revenue"].sum().rename("Revenue ($)")
        st.line_chart(daily_pm, height=200)

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

with tab_seller:
    # ── Table 1: Direct campaigns from GAM ──────────────────────────────
    st.subheader("Direct Campaigns")

    try:
        gam_df = load("gam_campaigns")
    except Exception:
        gam_df = pd.DataFrame()
        st.info("No GAM data yet. The gam_campaigns table will be created on the next scheduled refresh.")

    if gam_df.empty:
        st.info("No GAM data yet. Run refresh_cache.py to populate gam_campaigns.")
    else:
        last_pull = gam_df["_pulled_at"].max() if "_pulled_at" in gam_df else "unknown"
        st.caption(f"Last refresh: {_fmt_last_refresh(last_pull)}")

        gam_df = gam_df.copy()
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
        # _parse_gam_salesperson is defined at module level.
        _ae_regex = r"Team-(?:USA|INTL)_([A-Za-z]+)"
        if "salesperson" in gam_df.columns:
            gam_df["salesperson"] = gam_df["salesperson"].apply(_parse_gam_salesperson)

        _parsed_sp = gam_df["salesperson"] if "salesperson" in gam_df.columns else pd.Series(dtype=str)
        _null_mask = _parsed_sp.isna()

        _regex_seller = (
            gam_df["order_name"].str.extract(_ae_regex, expand=False).map(AE_NAMES)
        )
        _li_seller = (
            gam_df["line_item_name"].str.extract(_ae_regex, expand=False).map(AE_NAMES)
        )
        gam_df["seller_ae"] = _parsed_sp.where(~_null_mask, _regex_seller.fillna(_li_seller))

        # Extract advertiser (index 7) and campaign (index 8) from line item name
        def _li_part(name, idx):
            if not isinstance(name, str):
                return None
            parts = name.split("_")
            return parts[idx].strip() if len(parts) > idx else None

        # Replace hyphens with spaces so the displayed Advertiser / Campaign
        # columns read as "Ford Motor Company" / "Always On" rather than the
        # hyphenated token form used inside the line-item-name convention.
        gam_df["advertiser"]    = gam_df["line_item_name"].apply(_li_part, idx=7).str.replace("-", " ", regex=False)
        gam_df["campaign_name"] = gam_df["line_item_name"].apply(_li_part, idx=8).str.replace("-", " ", regex=False)
        gam_df["ad_format"]     = gam_df["line_item_name"].apply(_li_part, idx=10)
        _team_map = _cfg.get("team_names", {"USA": "USA", "INTL": "International"})
        gam_df["team"] = (
            gam_df["line_item_name"]
            .str.extract(r"_Team-(USA|INTL)_", expand=False)
            .map(_team_map)
        )
        for _col in ("advertiser", "campaign_name", "ad_format", "seller_ae", "team"):
            if _col in gam_df.columns:
                gam_df[_col] = gam_df[_col].replace({None: pd.NA, "None": pd.NA, "": pd.NA})

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
        f1, f2, f3, f4, f5 = st.columns(5)
        with f1:
            st.markdown('<div class="nw-filter-label">Seller</div>', unsafe_allow_html=True)
            selected_seller = st.selectbox(
                "Seller",
                options=["All"] + all_sellers,
                key="seller_select",
                label_visibility="collapsed",
            )
        with f2:
            st.markdown('<div class="nw-filter-label">Advertiser</div>', unsafe_allow_html=True)
            advertiser_opts = sorted(gam_df["advertiser"].dropna().unique())
            selected_advertisers = st.multiselect(
                "Advertiser",
                options=advertiser_opts,
                key="gam_advertiser_filter",
                label_visibility="collapsed",
            )
        with f3:
            st.markdown('<div class="nw-filter-label">Format</div>', unsafe_allow_html=True)
            format_opts = sorted(gam_df["ad_format"].dropna().unique())
            selected_formats = st.multiselect(
                "Format",
                options=format_opts,
                key="gam_format_filter",
                label_visibility="collapsed",
            )
        with f4:
            st.markdown('<div class="nw-filter-label">Status</div>', unsafe_allow_html=True)
            status_opts = sorted(gam_df["status"].dropna().unique()) if "status" in gam_df.columns else []
            _cfg_defaults = _cfg.get("default_statuses", ["Delivering", "Upcoming"])
            _status_defaults = [s for s in _cfg_defaults if s in status_opts]
            _STATUS_VER = "2"
            if st.session_state.get("_status_ver") != _STATUS_VER and _status_defaults:
                st.session_state["gam_status_filter"] = _status_defaults
                st.session_state["_status_ver"] = _STATUS_VER
            selected_statuses = st.multiselect(
                "Status",
                options=status_opts,
                default=_status_defaults,
                key="gam_status_filter",
                label_visibility="collapsed",
            )
        with f5:
            st.markdown('<div class="nw-filter-label">Team</div>', unsafe_allow_html=True)
            team_opts = sorted(gam_df["team"].dropna().unique())
            selected_teams = st.multiselect(
                "Team",
                options=team_opts,
                key="gam_team_filter",
                label_visibility="collapsed",
            )

        view_gam = gam_df if selected_seller == "All" else gam_df[gam_df["seller_ae"] == selected_seller].copy()
        if selected_advertisers:
            view_gam = view_gam[view_gam["advertiser"].isin(selected_advertisers)]
        if selected_formats:
            view_gam = view_gam[view_gam["ad_format"].isin(selected_formats)]
        if selected_statuses:
            view_gam = view_gam[view_gam["status"].isin(selected_statuses)]
        if selected_teams:
            view_gam = view_gam[view_gam["team"].isin(selected_teams)]

        if view_gam.empty:
            st.info("No campaigns found for the selected seller.")
        else:
            # ── Now that view_gam is filtered, populate the header timestamp.
            try:
                from zoneinfo import ZoneInfo as _ZI
                _now_edt = datetime.now(_ZI("America/New_York"))
                _ts_str = _now_edt.strftime("%-I:%M %p EDT")
            except Exception:
                _ts_str = datetime.now().strftime("%H:%M")
            _n_lines = len(view_gam)
            _header_timestamp_slot.markdown(
                f'<div class="nw-timestamp">🕐 {_ts_str} · {_n_lines:,} line items</div>',
                unsafe_allow_html=True,
            )

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

            # ── Targets (Pacing comes from settings; Viewability uses 70 as the
            # common floor; CTR uses the spec's 0.08% benchmark text). The
            # color thresholds applied to cells live further below.
            _pacing_target = float(_cfg.get("pacing_target_pct", 100.0) or 100.0)

            # ── Exception banners — list the specific offenders, not just counts.
            def _short_advertiser(name):
                if not isinstance(name, str): return "—"
                # Take a recognizable mid-name token (advertiser slot, position 7).
                parts = name.split("_")
                for idx in (7, 6, 8, 2):
                    if len(parts) > idx and parts[idx] and parts[idx] not in ("NA", "N/A"):
                        return parts[idx].replace("-", " ")
                return parts[0]

            _under_rows  = (view_gam[view_gam["pacing_pct"] < 75][["line_item_name", "pacing_pct"]].head(4)
                            if "pacing_pct" in view_gam.columns else pd.DataFrame())
            _over_rows   = (view_gam[view_gam["pacing_pct"] > 110][["line_item_name", "pacing_pct"]].head(6)
                            if "pacing_pct" in view_gam.columns else pd.DataFrame())
            _vw_anom_rows = pd.DataFrame()
            if "lifetime_viewable_imps" in view_gam.columns and "lifetime_measurable_imps" in view_gam.columns:
                _v_rate = pd.to_numeric(view_gam["lifetime_viewable_imps"], errors="coerce") / \
                          pd.to_numeric(view_gam["lifetime_measurable_imps"], errors="coerce") * 100
                _vw_anom_rows = (view_gam.assign(_v=_v_rate)
                                 .loc[_v_rate < 40, ["line_item_name", "_v"]].head(4))

            def _under_detail(rows):
                if rows.empty: return "All line items at or above 75% pacing"
                advs = rows["line_item_name"].apply(_short_advertiser).unique().tolist()
                paces = " &amp; ".join(f"{p:.0f}%" for p in rows["pacing_pct"].head(2))
                return f"{advs[0]} · {paces} pace" if len(advs) == 1 else f"{', '.join(advs[:3])}"
            def _over_detail(rows):
                if rows.empty: return "No overpacers"
                advs = rows["line_item_name"].apply(_short_advertiser).unique().tolist()
                return ", ".join(advs[:4])
            def _vw_detail(rows):
                if rows.empty: return "All line items at or above 40% viewability"
                first = rows.iloc[0]
                return f"{_short_advertiser(first['line_item_name'])} · {first['_v']:.1f}% viewable"

            _b1, _b2, _b3 = st.columns(3)
            with _b1:
                _n = len(_under_rows); _sev = "sev-red" if _n else "sev-ok"
                _icon = "🚨" if _n else "✓"
                st.markdown(
                    f'<div class="nw-banner {_sev}">'
                    f'<div class="nw-banner-head">{_icon} {_n} underpacing</div>'
                    f'<div>{_under_detail(_under_rows)}</div></div>', unsafe_allow_html=True)
            with _b2:
                _n = len(_over_rows); _sev = "sev-amber" if _n else "sev-ok"
                _icon = "⚠" if _n else "✓"
                st.markdown(
                    f'<div class="nw-banner {_sev}">'
                    f'<div class="nw-banner-head">{_icon} {_n} overpacing</div>'
                    f'<div>{_over_detail(_over_rows)}</div></div>', unsafe_allow_html=True)
            with _b3:
                _n = len(_vw_anom_rows); _sev = "sev-amber" if _n else "sev-ok"
                _icon = "⚠" if _n else "✓"
                st.markdown(
                    f'<div class="nw-banner {_sev}">'
                    f'<div class="nw-banner-head">{_icon} {_n} viewability anomaly</div>'
                    f'<div>{_vw_detail(_vw_anom_rows)}</div></div>', unsafe_allow_html=True)

            # ── KPI strip: six tiles, 18px value, target subtitle where applicable.
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
            def _kpi_tile(label, value, target=None):
                target_html = f'<div class="kpi-target">{target}</div>' if target else ""
                return (
                    f'<div class="kpi-tile">'
                    f'<div class="kpi-label">{label}</div>'
                    f'<div class="kpi-value">{value}</div>'
                    f'{target_html}'
                    f'</div>'
                )

            k1, k2, k3, k4, k5, k6 = st.columns(6)
            k1.markdown(_kpi_tile("Revenue",     _fmt_money(total_rev)), unsafe_allow_html=True)
            k2.markdown(_kpi_tile("Impressions", _fmt_count(total_impr)), unsafe_allow_html=True)
            k3.markdown(
                _kpi_tile("Avg pacing",
                          f"{avg_pacing:.1f}%" if pd.notna(avg_pacing) else "—",
                          f"Target {int(_pacing_target)}%"),
                unsafe_allow_html=True,
            )
            k4.markdown(
                _kpi_tile("Viewability",
                          f"{avg_viewability:.1f}%" if pd.notna(avg_viewability) else "—",
                          "Target 70%"),
                unsafe_allow_html=True,
            )
            if _video_li_count > 0 and pd.notna(avg_vcr):
                _vcr_val = f"{avg_vcr:.1f}%"
                _vcr_sub = f"{int(_video_li_count)} video line{'s' if _video_li_count != 1 else ''}"
            else:
                _vcr_val = "—"
                _vcr_sub = "No video"
            k5.markdown(_kpi_tile("VCR", _vcr_val, _vcr_sub), unsafe_allow_html=True)
            k6.markdown(
                _kpi_tile("CTR",
                          f"{avg_ctr:.2f}%" if pd.notna(avg_ctr) else "—",
                          "Benchmark 0.08%"),
                unsafe_allow_html=True,
            )

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
            for _suf in ("1d", "2d"):
                _viewable  = f"viewable_imps_{_suf}"
                _measurable = f"measurable_imps_{_suf}"
                if _viewable in view_gam.columns and _measurable in view_gam.columns:
                    view_gam[f"viewability_rate_{_suf}"] = view_gam.apply(
                        lambda r, v=_viewable, m=_measurable: (
                            r[v] / r[m] * 100 if pd.notna(r[v]) and pd.notna(r[m]) and r[m] > 0 else None
                        ),
                        axis=1,
                    )

            # Prior-day pacing — re-compute from lifetime minus 1d-impressions over a
            # goal pro-rated to one day earlier. No new refresh data needed for this.
            def _prior_pacing(row):
                try:
                    goal = row.get("impressions_goal")
                    lifetime = row.get("lifetime_impressions_delivered")
                    imp_1d = row.get("impressions_1d")
                    if not (goal and goal > 0 and pd.notna(lifetime) and pd.notna(imp_1d)):
                        return None
                    raw_start = pd.to_datetime(row.get("start_date"))
                    raw_end   = pd.to_datetime(row.get("end_date"))
                    if pd.isna(raw_start) or pd.isna(raw_end):
                        return None
                    today    = pd.Timestamp(date.today())
                    yesterday = today - pd.Timedelta(days=1)
                    dbf_yest  = today - pd.Timedelta(days=2)
                    total_days = max((raw_end - raw_start).days, 1)
                    elapsed_dbf = max((min(dbf_yest, raw_end) - raw_start).days, 0)
                    if elapsed_dbf <= 0:
                        return None
                    pro_rated_goal = goal * (elapsed_dbf / total_days)
                    if pro_rated_goal <= 0:
                        return None
                    cum_dbf = max(lifetime - imp_1d, 0)
                    return cum_dbf / pro_rated_goal * 100
                except Exception:
                    return None

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
                # Pace is rendered as a colored pill (background-color),
                # with the delta annotation in a separate 'Δ' column so the
                # box only wraps the percent. Show integer percent in the
                # pill; tenths in the annotation.
                _pacing_numeric = pd.to_numeric(view_gam["pacing_pct"], errors="coerce")
                view_gam["pacing_pct"] = _pacing_numeric.apply(
                    lambda v: "" if pd.isna(v) else f"{int(round(v))}%"
                )
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
            for _suf in ("1d", "2d"):
                _cl = f"clicks_{_suf}"
                _im = f"impressions_{_suf}"
                if _cl in view_gam.columns and _im in view_gam.columns:
                    view_gam[f"ctr_rate_{_suf}"] = view_gam.apply(
                        lambda r, c=_cl, i=_im: (
                            r[c] / r[i] * 100 if pd.notna(r[c]) and pd.notna(r[i]) and r[i] > 0 else None
                        ),
                        axis=1,
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
            for _suf in ("1d", "2d"):
                _vs = f"video_starts_{_suf}"
                _vc = f"video_completes_{_suf}"
                if _vs in view_gam.columns and _vc in view_gam.columns:
                    view_gam[f"vcr_rate_{_suf}"] = view_gam.apply(
                        lambda r, s=_vs, c=_vc: (
                            r[c] / r[s] * 100 if pd.notna(r[s]) and pd.notna(r[c]) and r[s] > 0 else None
                        ),
                        axis=1,
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

            # ── Ordinal badge: within each campaign (order_name), assign
            # #1, #2, ... by ascending line_item_id. Prepended to the Line Item
            # cell so multi-LI orders are disambiguated at a glance.
            if "line_item_id" in view_gam.columns and "order_name" in view_gam.columns:
                view_gam = view_gam.sort_values(
                    ["order_name", "line_item_id"], na_position="last"
                )
                view_gam["_ordinal"] = (
                    view_gam.groupby("order_name", dropna=False).cumcount() + 1
                )
                _ord_max = view_gam.groupby("order_name", dropna=False)["_ordinal"].transform("max")
                # Only show #N when the campaign actually has >1 LI.
                view_gam["line_item_name"] = view_gam.apply(
                    lambda r: (f"#{int(r['_ordinal'])}  {r['line_item_name']}"
                               if pd.notna(r['line_item_name']) and r.get("_ordinal") and _ord_max.loc[r.name] > 1
                               else r['line_item_name']),
                    axis=1,
                )

            # ── Default sort: |pacing - 100| descending. Worst pacers (and
            # worst overpacers) float to the top, healthy lines sink. The
            # earlier ascending sort by pacing_pct is overridden here.
            if "pacing_pct" in view_gam.columns:
                _pace_for_sort = pd.to_numeric(view_gam["pacing_pct"], errors="coerce")
                view_gam = view_gam.assign(_pace_dev=(_pace_for_sort - _pacing_target).abs())
                view_gam = view_gam.sort_values("_pace_dev", ascending=False, na_position="last")
                view_gam = view_gam.drop(columns=["_pace_dev"])

            # ── Restrict to the spec's default column set; the rest live in
            # the per-row detail drawer rendered below. Hardcoded for now —
            # the Settings → direct_sources mapping still drives which fields
            # are AVAILABLE; this filter decides which are SHOWN inline.
            _TABLE_DEFAULT = ["Line Item", "Revenue", "Delivered", "Pace", "Δ",
                              "Viewability %", "CTR %", "VCR %", "Seller", "Progress"]

            available_cols = [c for c in display_cols if c in view_gam.columns]
            # Ensure progress_pct flows through under the "Progress" header.
            if "progress_pct" in view_gam.columns and "progress_pct" not in display_cols:
                display_cols["progress_pct"] = "Progress"
                available_cols.append("progress_pct")
            table_df_full = (
                view_gam[available_cols + ["line_item_id"] if "line_item_id" in view_gam.columns else available_cols]
                .drop_duplicates(subset=["line_item_name"] if "line_item_name" in available_cols else None)
                .rename(columns={c: display_cols[c] for c in available_cols})
            )

            # Friendly transformation for display-only — applied after rename so
            # it works regardless of which source column the user has mapped to
            # Campaign / Advertiser in their settings (DB might map Campaign to
            # order_name, which would otherwise render hyphenated).
            for _friendly_col in ("Campaign", "Advertiser"):
                if _friendly_col in table_df_full.columns:
                    table_df_full[_friendly_col] = (
                        table_df_full[_friendly_col].astype("string").str.replace("-", " ", regex=False)
                    )

            # "Prog." italic placeholder when Seller is empty.
            if "Seller" in table_df_full.columns:
                table_df_full["Seller"] = table_df_full["Seller"].astype("string").fillna("Prog.")

            # Reset index so positional .iloc lookups in the drawer align
            # with the row positions Streamlit returns in _sel.selection.rows.
            table_df_full = table_df_full.reset_index(drop=True)

            # The TABLE shows only the default subset; the FULL set is kept
            # around so the drawer can show every field.
            table_df = table_df_full[[c for c in _TABLE_DEFAULT if c in table_df_full.columns]].copy()

            # ── M/K notation for Delivered. The cell is numeric pre-format;
            # use Streamlit's compact format directly via the column_config.
            def _mk(v):
                if pd.isna(v): return ""
                a = abs(v)
                if a >= 1_000_000: return f"{v/1_000_000:.2f}M"
                if a >= 1_000:     return f"{v/1_000:.1f}K"
                return f"{int(v):,}"

            # CTR formatting: 2 decimals. Apply via inline transform so we can
            # use TextColumn for consistent text styling with other rate cells.
            # (Annotation logic from earlier already produced the percent + delta
            # string; reformat the percent part to 2 decimals.)
            if "CTR %" in table_df.columns:
                _ctr_2dp = pd.Series([
                    re.sub(r"^([0-9.]+)%", lambda m: f"{float(m.group(1)):.2f}%", str(v))
                    if isinstance(v, str) and "%" in v else v
                    for v in table_df["CTR %"]
                ], index=table_df.index)
                table_df["CTR %"] = _ctr_2dp

            col_config = {}
            if "Delivered" in table_df.columns:
                # Numeric column with custom format hook isn't supported in
                # NumberColumn; transform to string M/K and render as text.
                table_df["Delivered"] = table_df["Delivered"].apply(_mk)
                col_config["Delivered"] = st.column_config.TextColumn("Delivered", width="small")
            if "Pace" in table_df.columns:
                col_config["Pace"] = st.column_config.TextColumn("Pace", width="small")
            if "Δ" in table_df.columns:
                col_config["Δ"] = st.column_config.TextColumn("Δ", width="small",
                    help="Pace change vs prior day (percentage points)")
            if "Viewability %" in table_df.columns:
                col_config["Viewability %"] = st.column_config.TextColumn("Viewability", width="small")
            if "VCR %" in table_df.columns:
                col_config["VCR %"] = st.column_config.TextColumn("VCR", width="small")
            if "CTR %" in table_df.columns:
                col_config["CTR %"] = st.column_config.TextColumn("CTR", width="small")
            if "Revenue" in table_df.columns:
                col_config["Revenue"] = st.column_config.NumberColumn(format="dollar")
            if "Progress" in table_df.columns:
                col_config["Progress"] = st.column_config.ProgressColumn(
                    "Progress", format="%.0f%%", min_value=0.0, max_value=1.0,
                )
            if "Line Item" in table_df.columns:
                col_config["Line Item"] = st.column_config.TextColumn("Line item", width="large")
            if "Seller" in table_df.columns:
                col_config["Seller"] = st.column_config.TextColumn("Seller", width="small")

            # Cells in Pacing % / Viewability % / CTR % / VCR % are now
            # annotated strings like "0.6% (▲ +0.1pp)". Parse the leading
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

            def _ramp_color(pct, target):
                """Red→green gradient hitting solid green at `target`."""
                if pct is None or target is None or target <= 0:
                    return ""
                if pct >= target:
                    return "color: hsl(120, 60%, 35%)"
                hue = int(max(0.0, pct) / float(target) * 120)
                return f"color: hsl({hue}, 70%, 38%)"

            # ── Status color: editable keyword → color map from settings.
            #    Substring (case-insensitive); first matching rule wins.
            _status_color_rules = _cfg.get("status_colors", []) or []
            def _status_color(v):
                if not isinstance(v, str): return ""
                sl = v.strip().lower()
                if not sl: return ""
                for rule in _status_color_rules:
                    kw  = (rule.get("keyword") or "").strip().lower()
                    col = (rule.get("color")   or "").strip()
                    if kw and col and kw in sl:
                        return f"color: {col}"
                return ""

            # ── Seller color: editable per-seller overrides, falls back to
            #    deterministic hash-derived hue so unseen sellers still get a
            #    stable color.
            _seller_color_overrides = _cfg.get("seller_colors", {}) or {}
            import hashlib as _hashlib
            def _seller_color(v):
                if not isinstance(v, str) or not v.strip():
                    return ""
                name = v.strip()
                override = _seller_color_overrides.get(name)
                if override and str(override).strip():
                    return f"color: {str(override).strip()}; font-weight: 600"
                h = int(_hashlib.md5(name.encode("utf-8")).hexdigest()[:6], 16)
                return f"color: hsl({h % 360}, 55%, 38%); font-weight: 600"

            # ── New three-tier color thresholds per redesign spec.
            #    Pills only on OUT-OF-TOLERANCE values; in-range = plain colored text.

            # Pace: red <75%, amber 75-90%, green 90-110%, amber >110%.
            def _pace_color(v):
                pct = _parse_leading_pct(v)
                if pct is None: return ""
                ratio = pct / _pacing_target if _pacing_target else None
                if ratio is None: return ""
                if ratio < 0.75:
                    return ("background-color: hsl(0, 35%, 25%); color: hsl(0, 30%, 85%); "
                            "border-radius: 6px; padding: 2px 10px; font-weight: 600")
                if ratio < 0.90:
                    return ("background-color: hsl(35, 45%, 22%); color: hsl(35, 35%, 80%); "
                            "border-radius: 6px; padding: 2px 10px; font-weight: 600")
                if ratio <= 1.10:
                    return "color: hsl(120, 50%, 65%); font-weight: 600"   # plain green text
                return ("background-color: hsl(45, 45%, 22%); color: hsl(45, 35%, 80%); "
                        "border-radius: 6px; padding: 2px 10px; font-weight: 600")

            # Viewability: red <40%, amber 40-65%, green ≥65%.
            def _viewability_color(v):
                pct = _parse_leading_pct(v)
                if pct is None: return ""
                if pct < 40:
                    return ("background-color: hsl(0, 35%, 25%); color: hsl(0, 30%, 85%); "
                            "border-radius: 6px; padding: 2px 10px")
                if pct < 65:
                    return "color: hsl(35, 70%, 65%)"
                return "color: hsl(120, 50%, 65%)"

            # VCR: red <50%, amber 50-60%, green ≥60%. Skip 'N/A' cells.
            def _vcr_color(v):
                if isinstance(v, str) and v.strip().upper() == "N/A":
                    return "color: rgba(250,250,250,0.35)"
                pct = _parse_leading_pct(v)
                if pct is None: return ""
                if pct < 50:
                    return ("background-color: hsl(0, 35%, 25%); color: hsl(0, 30%, 85%); "
                            "border-radius: 6px; padding: 2px 10px")
                if pct < 60:
                    return "color: hsl(35, 70%, 65%)"
                return "color: hsl(120, 50%, 65%)"

            # Bold revenue values above $10K.
            def _revenue_bold(v):
                try:
                    return "font-weight: 700" if float(v) > 10_000 else ""
                except Exception:
                    return ""

            styled_df = table_df.style
            if "Pace" in table_df.columns:
                styled_df = styled_df.map(_pace_color, subset=["Pace"])
            if "Viewability %" in table_df.columns:
                styled_df = styled_df.map(_viewability_color, subset=["Viewability %"])
            if "VCR %" in table_df.columns:
                styled_df = styled_df.map(_vcr_color, subset=["VCR %"])
            if "Seller" in table_df.columns:
                styled_df = styled_df.map(_seller_color, subset=["Seller"])
            if "Revenue" in table_df.columns:
                styled_df = styled_df.map(_revenue_bold, subset=["Revenue"])

            # ── Custom HTML table — multi-line cells and proper typography
            # require this; st.dataframe can't render LI name + subtitle,
            # Pace pill + variance below, or color-coded progress bars per row.
            def _esc(s):
                if s is None: return ""
                s = str(s)
                return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

            def _subtitle(li_name, ad_format, cpm_rate):
                """Extract 'Advertiser · Format · $CPM' from the line-item name."""
                parts = (li_name or "").split("_") if isinstance(li_name, str) else []
                adv = parts[7].replace("-", " ") if len(parts) > 7 and parts[7] not in ("NA","N/A","") else ""
                fmt = ad_format if (isinstance(ad_format, str) and ad_format) else (parts[10] if len(parts) > 10 else "")
                cpm_str = ""
                try:
                    if cpm_rate is not None and not (isinstance(cpm_rate, float) and pd.isna(cpm_rate)):
                        cpm_str = f"${float(cpm_rate):g} CPM"
                except Exception:
                    pass
                bits = [b for b in (adv, fmt, cpm_str) if b]
                return " · ".join(bits)

            def _pace_html(p, p_prior):
                """Pace cell: pill (or green text) + variance below."""
                if pd.isna(p):
                    return '<div class="cell-dash">—</div>'
                ratio = p / _pacing_target if _pacing_target else None
                pct_int = int(round(p))
                if ratio is not None and ratio < 0.75:
                    cell = f'<div class="pill pill-red">{pct_int}%</div>'
                elif ratio is not None and ratio < 0.90:
                    cell = f'<div class="pill pill-amber">{pct_int}%</div>'
                elif ratio is not None and ratio <= 1.10:
                    cell = f'<div class="txt-green">{pct_int}%</div>'
                else:
                    cell = f'<div class="pill pill-amber">{pct_int}%</div>'
                if pd.notna(p_prior):
                    d = p - p_prior
                    if abs(d) >= 0.05 and abs(d) <= 100:
                        arrow = "▲" if d > 0 else "▼"
                        cls = "pace-delta up" if d > 0 else "pace-delta"
                        cell += f'<div class="{cls}">{arrow} {abs(d):.1f}pp</div>'
                    elif abs(d) > 100:
                        cell += '<div class="pace-delta" style="font-style:italic">new line item</div>'
                return cell

            def _viewability_html(p):
                if pd.isna(p): return '<div class="cell-dash">—</div>'
                if p < 40:
                    return f'<div class="pill pill-red">{p:.1f}%</div>'
                if p < 65:
                    return f'<div class="txt-amber">{p:.1f}%</div>'
                return f'<div class="txt-green">{p:.1f}%</div>'

            def _vcr_html(p, is_video):
                if not is_video:
                    return '<div class="cell-dash">—</div>'
                if pd.isna(p): return '<div class="cell-dash">—</div>'
                if p < 50:
                    return f'<div class="pill pill-red">{p:.1f}%</div>'
                if p < 60:
                    return f'<div class="txt-amber">{p:.1f}%</div>'
                return f'<div class="txt-green">{p:.1f}%</div>'

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
                # Color the bar by the row's pace band: red if under, amber if off, green if healthy.
                cls = "prog-green"
                return f'<div class="nw-prog-bar"><div class="nw-prog-fill {cls}" style="width:{pct:.0f}%"></div></div>'

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

            # Iterate; view_gam is already sorted by |pacing - target| desc.
            for _i, (_, row) in enumerate(view_gam.head(25).iterrows()):
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
                _vw = _vw_rate.iloc[view_gam.index.get_loc(row.name)] if _vw_rate is not None else None
                _ctr = _ctr_rate.iloc[view_gam.index.get_loc(row.name)] if _ctr_rate is not None else None
                _vcr_val = row.get("vcr")
                _fmt_str = row.get("ad_format")
                _is_video = isinstance(_fmt_str, str) and "video" in _fmt_str.lower()
                _seller = row.get("seller_ae")
                _seller_html = (f'<span class="seller-prog">Prog.</span>'
                                if not (isinstance(_seller, str) and _seller.strip())
                                else _esc(_seller))
                _progress = row.get("progress_pct")

                # Display name = short slice of the structured LI name. Take
                # tokens 2-4 (category + ssp + dsp / category_advertiser for
                # PMP-style) joined — matches the screenshot's compact form.
                _tokens = _li_clean.split("_")
                if len(_tokens) >= 5:
                    _display_name = "_".join(_tokens[2:5])
                elif len(_tokens) >= 3:
                    _display_name = "_".join(_tokens[2:])
                else:
                    _display_name = _li_clean
                _rows_html.append(
                    "<tr>"
                    f'<td><div class="li-name">{_ord_html}{_esc(_display_name)}</div>'
                    f'<div class="li-sub">{_esc(_sub) or "—"}</div></td>'
                    f'<td class="num">{_revenue_html(_rev)}</td>'
                    f'<td class="num">{_delivered_html(_delivered)}</td>'
                    f'<td class="num">{_pace_html(_pace, _pace_prior)}</td>'
                    f'<td class="num">{_viewability_html(_vw)}</td>'
                    f'<td class="num">{f"{_ctr:.2f}%" if pd.notna(_ctr) else "<span class=cell-dash>—</span>"}</td>'
                    f'<td class="num">{_vcr_html(_vcr_val, _is_video)}</td>'
                    f'<td>{_seller_html}</td>'
                    f'<td>{_progress_html(_progress)}</td>'
                    "</tr>"
                )

            _table_html = (
                '<div class="nw-tbl-wrap">'
                '<div class="nw-tbl-head">'
                '<div class="nw-tbl-title">Direct campaigns'
                '<span class="nw-tbl-sub">· sorted by variance</span></div>'
                '<div class="nw-legend">'
                '<span><span class="nw-legend-dot" style="background:hsl(0,50%,55%)"></span>under</span>'
                '<span><span class="nw-legend-dot" style="background:hsl(40,55%,45%)"></span>off-target</span>'
                '<span><span class="nw-legend-dot" style="background:hsl(120,40%,50%)"></span>healthy</span>'
                '<span>— = N/A</span>'
                '</div>'
                '</div>'
                '<table class="nw-tbl">'
                '<thead><tr>'
                '<th>Line item</th>'
                '<th class="num">Revenue</th>'
                '<th class="num">Delivered</th>'
                '<th class="num">Pace</th>'
                '<th class="num">Viewable</th>'
                '<th class="num">CTR</th>'
                '<th class="num">VCR</th>'
                '<th>Seller</th>'
                '<th>Progress</th>'
                '</tr></thead>'
                '<tbody>' + "".join(_rows_html) + '</tbody>'
                '</table>'
                '</div>'
            )
            st.markdown(_table_html, unsafe_allow_html=True)

            if len(view_gam) > 25:
                st.caption(f"Showing 25 of {len(view_gam):,} line items, sorted by |pace − target|.")

    st.divider()

    # ── Table 2: PMP deals from Pubmatic ────────────────────────────────
    st.subheader("PMP Deals")

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
    _pf1, _pf2, _pf3, _pf4, _pf5, _pf6, _pf7 = st.columns([1, 1, 1, 1, 1, 1, 0.6])
    with _pf1:
        sel_pmp_deal_types = st.multiselect(
            "Deal Type",
            _pmp_deal_types_available,
            key="campaigns_pmp_deal_type_filter",
        )
    with _pf2:
        sel_pmp_ssps = st.multiselect(
            "SSP",
            _pmp_ssps_available,
            key="campaigns_pmp_ssp_filter",
            help="PA → Magnite | PD → Magnite or GAM | PG → GAM",
        )
    with _pf3:
        sel_pmp_dsps = st.multiselect(
            "DSP",
            _pmp_dsps_opts,
            key="campaigns_pmp_dsp_filter",
        )
    with _pf4:
        sel_pmp_formats = st.multiselect(
            "Format",
            _pmp_formats_opts,
            key="campaigns_pmp_format_filter",
        )
    with _pf5:
        sel_pmp_deal_sources = st.multiselect(
            "Deal Source",
            _pmp_deal_sources_opts,
            key="campaigns_pmp_deal_source_filter",
        )
    with _pf6:
        sel_pmp_teams = st.multiselect(
            "Team",
            _pmp_teams_opts,
            key="campaigns_pmp_team_filter",
        )
    with _pf7:
        st.write("")  # align button with multiselect labels
        if st.button("Reset filters", key="pmp_reset_filters"):
            for _k in ("campaigns_pmp_deal_type_filter", "campaigns_pmp_ssp_filter",
                       "campaigns_pmp_dsp_filter", "campaigns_pmp_format_filter",
                       "campaigns_pmp_deal_source_filter", "campaigns_pmp_team_filter"):
                st.session_state.pop(_k, None)
            st.rerun()
    st.caption("PA = Magnite · PD = Magnite or GAM · PG = GAM")

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

                _seller_cfg = _gam_col_map.get("Seller", "[auto]")
                if _seller_cfg not in ("[auto]", "N/A", "", None) and _seller_cfg in _gam_raw.columns:
                    _gam_raw["seller_ae"] = _gam_raw[_seller_cfg].map(AE_NAMES)
                else:
                    # Policy: PD/PG/Direct rely on GAM's order.salesperson (API
                    # is the source of truth — no deal-name regex needed). PA
                    # delivers through Ad Exchange under a backstop order with
                    # no AE assigned, so PA still falls back to the deal-name /
                    # order-name regex.
                    _api_by_order = {}
                    try:
                        _camp = load("gam_campaigns")
                        if not _camp.empty and "order_name" in _camp.columns and "salesperson" in _camp.columns:
                            _api_by_order = (
                                _camp.dropna(subset=["salesperson"])
                                     .drop_duplicates("order_name", keep="first")
                                     .set_index("order_name")["salesperson"]
                                     .to_dict()
                            )
                    except Exception:
                        pass
                    _api_seller = (
                        _gam_raw["order_name"].map(_api_by_order).apply(_parse_gam_salesperson)
                        if "order_name" in _gam_raw.columns else pd.Series([None] * len(_gam_raw), index=_gam_raw.index)
                    )

                    _ae_regex = r"Team-(?:USA|INTL)_([A-Za-z]+)"
                    _regex_from_deal = _gam_raw["deal_name"].str.extract(_ae_regex, expand=False).map(AE_NAMES)
                    _regex_from_order = (
                        _gam_raw["order_name"].str.extract(_ae_regex, expand=False).map(AE_NAMES)
                        if "order_name" in _gam_raw.columns else pd.Series([None] * len(_gam_raw), index=_gam_raw.index)
                    )
                    _regex_seller = _regex_from_deal.fillna(_regex_from_order)

                    _is_pa = _gam_raw["deal_type_label"] == "Private Auction"
                    # PD/PG: API → regex fallback. PA: regex only.
                    _gam_raw["seller_ae"] = _api_seller.where(~_is_pa & _api_seller.notna(), _regex_seller)

                _gam_deals = _gam_raw[_gam_raw["deal_type_label"].isin(_gam_deal_types)].copy()
                if selected_seller != "All":
                    _gam_deals = _gam_deals[_gam_deals["seller_ae"] == selected_seller]
                if sel_pmp_deal_types:
                    _gam_deals = _gam_deals[_gam_deals["deal_type_label"].isin(sel_pmp_deal_types)]
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
                if not _mag_df.empty:
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
    if _format_aliases and "Format" in combined_pmp.columns:
        combined_pmp["Format"] = combined_pmp["Format"].replace(_format_aliases)

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
        combined_pmp["Deal"].str.extract(r"_Team-(USA|INTL)_", expand=False).map(_team_map)
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
        pm1, pm2, pm3 = st.columns(3)
        pm1.metric("Paid impressions", f"{combined_pmp['Paid Impressions'].sum():,.0f}")
        pm2.metric("Revenue", f"${combined_pmp['Revenue'].sum():,.2f}")
        pm3.metric("Avg eCPM", f"${combined_pmp['eCPM'].mean():,.2f}" if len(combined_pmp) else "—")

        _pmp_col_order = ["Seller", "Team", "SSP", "Deal", "Deal Type", "Format", "DSP", "Deal Source",
                          "Deal Status", "Floor CPM",
                          "Paid Impressions", "Revenue", "eCPM",
                          "Win Rate %", "Total Requests", "Bid Responses"]
        combined_pmp = combined_pmp[[c for c in _pmp_col_order if c in combined_pmp.columns]]

        st.dataframe(
            combined_pmp,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Deal Status": st.column_config.TextColumn("Deal Status"),
                "Floor CPM": st.column_config.NumberColumn("Floor CPM", format="dollar"),
                "Paid Impressions": st.column_config.NumberColumn(format="localized"),
                "Revenue": st.column_config.NumberColumn(format="dollar"),
                "eCPM": st.column_config.NumberColumn(format="dollar"),
                "Win Rate %": st.column_config.NumberColumn(format="%.1f"),
                "Total Requests": st.column_config.NumberColumn(format="localized"),
                "Bid Responses": st.column_config.NumberColumn(format="localized"),
            },
        )

        # Inventory-only view: GAM's report API doesn't expose Private Auction
        # delivery at the deal level, so PA can't appear above. This shows what
        # PA inventory exists on the network (auctions + their deals, floors,
        # statuses, buyers) sourced from the PA REST API.
        try:
            _pa_inventory = load("gam_pa_metadata")
            if not _pa_inventory.empty:
                with st.expander(f"GAM Private Auction inventory ({len(_pa_inventory)} deals, no delivery data)", expanded=False):
                    st.caption(
                        "Inventory metadata only — GAM does not report delivery for PA at the deal level. "
                        "Use this to see which PA deals exist, their floors, and buyer / status."
                    )
                    _pa_display = _pa_inventory.rename(columns={
                        "auction_name":     "Auction",
                        "external_deal_id": "External Deal ID",
                        "buyer_account_id": "Buyer Account",
                        "floor_price_usd":  "Floor CPM",
                        "deal_status":      "Status",
                        "end_time":         "End",
                    })
                    st.dataframe(
                        _pa_display,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Floor CPM": st.column_config.NumberColumn(format="dollar"),
                        },
                    )
        except Exception:
            pass

# ── Settings tab ─────────────────────────────────────────────────────────────

with tab_settings:
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
        f'<div style="font-size:22px;font-weight:600;color:rgba(250,250,250,0.92);">Configure</div></div>'
        f'<div style="font-size:11px;color:rgba(250,250,250,0.45);">'
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
            f'<div style="font-size:11px;color:rgba(250,250,250,0.55);margin-bottom:6px">'
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
            f'<div style="font-size:22px;font-weight:600;color:rgba(250,250,250,0.92);">Configure</div></div>'
            f'<div style="font-size:11px;color:rgba(250,250,250,0.45);">Last saved: {_last_saved_label}</div>'
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

        # Line item count per ad_format (used by Benchmarks "Currently applies to").
        _format_counts = {}
        if _gam_for_counts is not None and "ad_format" in _gam_for_counts.columns:
            _format_counts = _gam_for_counts["ad_format"].fillna("").value_counts().to_dict()
        def _format_count(fmt):
            if not isinstance(fmt, str) or not fmt: return 0
            return int(_format_counts.get(fmt, 0))

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
            f'<div style="font-size:11px;color:rgba(250,250,250,0.55);margin-bottom:6px">'
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
            f'<div style="font-size:11px;color:rgba(250,250,250,0.55);">'
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

        _bench_rows = [
            {"Format": fmt,
             "Viewability %": vals.get("viewability_pct"),
             "CTR %":         vals.get("ctr_pct"),
             "VCR %":         vals.get("vcr_pct"),
             "Applies to":    f"~{_format_count(fmt)} line items"}
            for fmt, vals in sorted(_benchmarks_default.items())
        ]
        _bench_edit = st.data_editor(
            pd.DataFrame(_bench_rows) if _bench_rows else pd.DataFrame(
                columns=["Format", "Viewability %", "CTR %", "VCR %", "Applies to"]
            ),
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="settings_benchmarks_by_format",
            column_config={
                "Format":        st.column_config.TextColumn("Format", required=True),
                "Viewability %": st.column_config.NumberColumn("Viewability %", format="%.1f"),
                "CTR %":         st.column_config.NumberColumn("CTR %", format="%.2f"),
                "VCR %":         st.column_config.NumberColumn("VCR %", format="%.1f"),
                "Applies to":    st.column_config.TextColumn("Applies to", disabled=True),
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

        # ── 4c: Status Colors + live preview.
        _status_color_rows = _s.get("status_colors", []) or []
        st.markdown(
            f'<div class="cfg-card-title" style="margin-top:14px">Status colors</div>'
            f'<div style="font-size:11px;color:rgba(250,250,250,0.55);margin-bottom:6px">'
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
                f'<div style="margin:8px 0 0 0;font-size:10px;color:rgba(250,250,250,0.45);'
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
            f'<div style="font-size:11px;color:rgba(250,250,250,0.55);margin-bottom:6px">'
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
                f'font-size:11px;color:rgba(250,250,250,0.55)">'
                f'<span style="width:10px;height:10px;border-radius:2px;'
                f'background:{_hash_fallback_color(r["seller"])}"></span>'
                f'{r["seller"]} <span class="cfg-tertiary">(hash fallback)</span>'
                f'</span>'
                for r in _hash_fb_rows[:8]
            )
            st.markdown(
                f'<div style="margin-top:6px;font-size:11px;color:rgba(250,250,250,0.45);'
                f'letter-spacing:0.05em">{_swatches}</div>',
                unsafe_allow_html=True,
            )


    # ── Save ─────────────────────────────────────────────────────────────
    st.divider()
    if st.button("💾  Save Settings", type="primary"):
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
                    "viewability_pct": _bench_val(r.get("Viewability %")),
                    "ctr_pct":         _bench_val(r.get("CTR %")),
                    "vcr_pct":         _bench_val(r.get("VCR %")),
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
            _save_settings({
                "ssps": _new_ssps, "ae_names": _new_ae, "team_names": _new_team,
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
            })
            st.cache_data.clear()
            st.success("Settings saved — reloading dashboard…")
            st.rerun()
        except Exception as _e:
            st.error(f"Failed to save: {_e}")
