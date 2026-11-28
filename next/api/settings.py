"""Dashboard settings, built exactly as dashboard.py's ``_load_settings`` does
(copied verbatim from it; dashboard.py is being retired). Source of truth is
Postgres ``dashboard_settings`` (key 'main'), falling back to settings.json."""
from __future__ import annotations

import json
from pathlib import Path

_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "settings.json"

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
    # TTD acquisition CPA goal ($) — the target the Priority-flights card
    # (Chumba) grades against: the CPA hero shows over/under vs this,
    # and the Daily CPA chart draws a dashed reference line at it. $150 per Roger
    # (2026-06-22); editable in Settings → Direct.
    "ttd_cpa_goal": 50.0,
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


def load_settings(main_json: str | None = None) -> dict:
    """Settings exactly as dashboard.py builds them; ``main_json`` is
    dashboard_settings.value for key 'main' (None → settings.json)."""
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

    # Primary: database (survives redeployments)
    if main_json:
        try:
            return _patch_ae_names(_patch_ssp_defaults(_patch_direct_columns(_with_defaults(json.loads(main_json)))))
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


