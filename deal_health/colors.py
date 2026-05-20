"""
Color palette, thresholds, and canonicalization tables for the weekly deal
health report. All hex codes and the SSP/DSP/seller alias lookups live here
so the rest of the package never embeds a literal hex value.
"""

from __future__ import annotations

import json
from pathlib import Path

# ── Backgrounds ────────────────────────────────────────────────────────────
PAGE_BG         = "#f4f5f7"
CONTENT_BG      = "#ffffff"
HEADER_BG       = "#0a0a0a"
METHODOLOGY_BG  = "#fafbfc"
SUBTLE_GRAY_BG  = "#f9fafb"
KPI_TILE_BG     = "#ffffff"
TINT_PURPLE     = "#ede9fe"
TINT_BLUE       = "#dbeafe"
TINT_GREEN      = "#dcfce7"

# ── Text ───────────────────────────────────────────────────────────────────
TEXT_PRIMARY    = "#0a0a0a"
TEXT_DEFAULT    = "#374151"
TEXT_MUTED      = "#6b7280"
TEXT_HINT       = "#9ca3af"
TEXT_FAINT      = "#4b5563"
TEXT_INVERSE    = "#ffffff"

# ── Borders ────────────────────────────────────────────────────────────────
BORDER_GRAY     = "#e5e7eb"
BORDER_BLUE_LNK = "#1e40af"
BORDER_DEEP_BLUE= "#1e3a8a"

# ── Deal-type chips (background, text) ─────────────────────────────────────
CHIP_PA_BG, CHIP_PA_TEXT = TINT_BLUE,   "#1e40af"
CHIP_PD_BG, CHIP_PD_TEXT = TINT_PURPLE, "#7c3aed"
CHIP_PG_BG, CHIP_PG_TEXT = TINT_GREEN,  "#16a34a"

# ── Semantic ───────────────────────────────────────────────────────────────
ACCENT_PURPLE   = "#7c3aed"
WARNING         = "#b45309"

# ── Thresholds ─────────────────────────────────────────────────────────────
GAM_PD_MIN_REQUESTS         = 100_000
GAM_PD_MIN_DAYS             = 7
DEAL_AGE_MIN_DAYS           = 90
TOP_DARK_ADVERTISERS_LIMIT  = 5
EMAIL_MAX_WIDTH_PX          = 720

# ── Canonicalization tables ────────────────────────────────────────────────
# All keys are lowercased; lookups do `.lower().strip()` first.

# SSP field in deal name → canonical display label. Anything not in this
# table (and not in UNKNOWN_SSP_TRIGGERS / DSP aliases) is also treated as
# Unknown SSP — see parser.canonical_ssp().
SSP_ALIASES: dict[str, str] = {
    "adx":              "AdX",
    "googleadx":        "AdX",
    "googleadmanager":  "AdX",
    "google ad manager":"AdX",
    "magnite":          "Magnite",
    "rubicon":          "Magnite",
    "pubmatic":         "Pubmatic",
}
KNOWN_SSPS: frozenset[str] = frozenset({"AdX", "Magnite", "Pubmatic"})

# Strings that LOOK like an SSP field but are placeholders / wrong → flag as
# Unknown SSP. The full check in parser.canonical_ssp also routes DSP-looking
# values to Unknown SSP.
UNKNOWN_SSP_TRIGGERS: frozenset[str] = frozenset({"gam", "ssp", "n/a", "na", "none", ""})


def _load_settings() -> dict:
    """Pull dsp_aliases + ae_names from settings.json (same source as the
    dashboard) so the report stays consistent with the rest of the app."""
    path = Path(__file__).resolve().parent.parent / "settings.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


_settings = _load_settings()

# DSP_ALIASES is keyed lowercase, but the settings.json file stores them
# mixed-case for readability — flatten on load.
DSP_ALIASES: dict[str, str] = {
    k.lower(): v for k, v in (_settings.get("dsp_aliases") or {}).items()
}

# AE map: ILee → Ivy Lee, etc. Lowercase keys.
AE_NAMES: dict[str, str] = {
    k.lower(): v for k, v in (_settings.get("ae_names") or {}).items()
}

# Deal-type code mappings (PA / PD / PG / PMP …) come from settings.json
# but we only surface the three short codes in the report.
DEAL_TYPE_CODES: dict[str, str] = _settings.get("deal_type_codes") or {
    "PA": "Private Auction", "PD": "Preferred Deal",
    "PG": "Programmatic Guaranteed", "PMP": "Private Marketplace",
}
DEAL_TYPE_ALIASES: dict[str, str] = _settings.get("deal_type_aliases") or {}
