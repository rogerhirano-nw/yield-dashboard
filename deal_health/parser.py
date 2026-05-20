"""
Newsweek deal-name parser.

Canonical naming schema (underscore-delimited, positional):

    Newsweek_DealType_Vertical_SSP_DSP_AgencyHoldco_Agency_Advertiser_Campaign
      _Geo_Format_Floor_Team_SellerInitials[_suffix]

Real-world deal names come in three lengths:

  * 13 fields — AgencyHoldco omitted (older convention).
  * 14 fields — canonical.
  * 15 fields — canonical + trailing suffix ("_mobile", "_Newsweek 1PD", …).

The parser is tolerant of N/A / NA / blank / None in any slot (normalized to
None) and case-insensitive on SSP, DSP, and seller-initials canonicalization.
Deals that don't start with `Newsweek_` (legacy names like `nw_adx_omd_*`)
parse to a record with most fields None and ssp_is_unknown=True.
"""

from __future__ import annotations

import re
from typing import Optional

import structlog

from .colors import (
    AE_NAMES,
    DEAL_TYPE_CODES,
    DSP_ALIASES,
    SSP_ALIASES,
    UNKNOWN_SSP_TRIGGERS,
)
from .models import ParsedDeal

log = structlog.get_logger(__name__)


# ── canonicalization helpers ────────────────────────────────────────────────

_BLANK_TOKENS = frozenset({"", "n/a", "na", "none", "null"})


def _normalize(token: Optional[str]) -> Optional[str]:
    """Strip whitespace; map N/A / NA / blank / None → None."""
    if token is None:
        return None
    s = token.strip()
    if s.lower() in _BLANK_TOKENS:
        return None
    return s


def canonical_ssp(raw: Optional[str]) -> tuple[str, bool]:
    """
    Map a deal-name SSP field to a canonical display label.

    Returns (canonical, is_unknown). When is_unknown=True the renderer surfaces
    the deal under the "Unknown SSP" bucket and the methodology callout flags
    it as a naming defect to chase down with AdOps.
    """
    if raw is None:
        return "Unknown SSP", True
    lo = raw.strip().lower()
    if lo in UNKNOWN_SSP_TRIGGERS:
        return "Unknown SSP", True
    if lo in SSP_ALIASES:
        return SSP_ALIASES[lo], False
    # A DSP slipping into the SSP slot is the most common defect — flag it.
    if lo in DSP_ALIASES or lo in {v.lower() for v in DSP_ALIASES.values()}:
        return "Unknown SSP", True
    return "Unknown SSP", True


# Pre-compute lowercase-canonical → canonical map so case variants of canonical
# DSP names (e.g. "dv360" → "DV360") canonicalize even when not listed as aliases.
_DSP_CANONICAL_BY_LC: dict[str, str] = {v.lower(): v for v in DSP_ALIASES.values()}


def canonical_dsp(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    lo = raw.strip().lower()
    if lo in _BLANK_TOKENS:
        return None
    if lo in DSP_ALIASES:
        return DSP_ALIASES[lo]
    if lo in _DSP_CANONICAL_BY_LC:
        return _DSP_CANONICAL_BY_LC[lo]
    return raw.strip()


def canonical_seller(initials: Optional[str]) -> str:
    """Map seller-initials (ILee, Ilee, ilee, etc.) to the canonical name.
    Unrecognized → "Unknown" (excluded from per-seller breakouts but counted
    in the top-line totals)."""
    if initials is None:
        return "Unknown"
    lo = initials.strip().lower()
    if lo in _BLANK_TOKENS:
        return "Unknown"
    return AE_NAMES.get(lo, "Unknown")


# ── field-count detection ──────────────────────────────────────────────────

# Position of seller-initials by field count. The trailing suffix in 15-field
# names sits AFTER the seller initials, so seller_initials is at index -2.
_TEAM_RE = re.compile(r"^Team-(USA|INTL)$", re.IGNORECASE)


def _classify_layout(parts: list[str]) -> tuple[int, dict[str, int]]:
    """
    Identify which positional layout this deal name uses. We anchor on the
    "Team-USA"/"Team-INTL" token because that's the most stable signal across
    legacy/current naming.

    Returns (field_count, position_map) where position_map names each slot
    we care about — keys: vertical, ssp, dsp, agency_holdco, agency,
    advertiser, campaign, geo, ad_format, floor, team, seller_initials,
    suffix.
    """
    n = len(parts)

    # Find the Team-* token. In canonical naming it's at position -2 (14-field)
    # or position -3 (15-field with suffix).
    team_idx = None
    for i, p in enumerate(parts):
        if _TEAM_RE.match(p):
            team_idx = i
            break

    # Default canonical (14-field) positions.
    canonical_14 = {
        "vertical":         2,
        "ssp":              3,
        "dsp":              4,
        "agency_holdco":    5,
        "agency":           6,
        "advertiser":       7,
        "campaign":         8,
        "geo":              9,
        "ad_format":        10,
        "floor":            11,
        "team":             12,
        "seller_initials":  13,
        "suffix":           -1,
    }

    # 13-field: agency_holdco is missing, so positions 5+ shift left by 1.
    canonical_13 = {
        "vertical":         2,
        "ssp":              3,
        "dsp":              4,
        "agency_holdco":    -1,
        "agency":           5,
        "advertiser":       6,
        "campaign":         7,
        "geo":              8,
        "ad_format":        9,
        "floor":            10,
        "team":             11,
        "seller_initials":  12,
        "suffix":           -1,
    }

    # 15-field: canonical 14 + a trailing suffix at index 14.
    canonical_15 = {**canonical_14, "suffix": 14}

    if team_idx is None:
        # No Team-* anchor → fall back to a length-based guess and let the
        # downstream "Unknown" seller handle it.
        if n >= 14:
            return n, {**canonical_14, "suffix": 14 if n == 15 else -1}
        return n, canonical_13 if n == 13 else canonical_14

    # Use team_idx to disambiguate. Canonical layouts have:
    #   13-field → team at index 11
    #   14-field → team at index 12
    #   15-field → team at index 12 (suffix is AFTER seller initials)
    if team_idx == 11 and n == 13:
        return 13, canonical_13
    if team_idx == 12 and n == 14:
        return 14, canonical_14
    if team_idx == 12 and n >= 15:
        return n, canonical_15
    # Anything else — slot positions based on team_idx so we at least pull
    # seller correctly. Mark all other fields by their expected offsets back
    # from team_idx (vertical at +-? offsets get fuzzy here; we accept the
    # canonical_14 mapping for the prefix and trust seller_initials = team_idx+1).
    return n, {**canonical_14, "team": team_idx, "seller_initials": team_idx + 1,
               "suffix": team_idx + 2 if n > team_idx + 2 else -1}


def _at(parts: list[str], idx: int) -> Optional[str]:
    """Return parts[idx] if idx is a valid in-range index, else None."""
    if idx < 0 or idx >= len(parts):
        return None
    return _normalize(parts[idx])


# ── public API ─────────────────────────────────────────────────────────────

def parse_deal(raw: Optional[str]) -> ParsedDeal:
    """
    Parse a Newsweek-style deal name into a ParsedDeal record. Always returns
    a record — invalid/legacy names get most-fields-None and ssp_is_unknown=True.
    """
    raw_str = (raw or "").strip()
    if not raw_str:
        return _empty_parsed("")

    parts = raw_str.split("_")
    n, pos = _classify_layout(parts)

    deal_type_raw = _at(parts, 1)
    vertical      = _at(parts, pos["vertical"])
    ssp_raw       = _at(parts, pos["ssp"])
    dsp_raw       = _at(parts, pos["dsp"])
    agency_holdco = _at(parts, pos["agency_holdco"])
    agency        = _at(parts, pos["agency"])
    advertiser    = _at(parts, pos["advertiser"])
    campaign      = _at(parts, pos["campaign"])
    geo           = _at(parts, pos["geo"])
    ad_format     = _at(parts, pos["ad_format"])
    floor         = _at(parts, pos["floor"])
    team_raw      = _at(parts, pos["team"])
    seller_init   = _at(parts, pos["seller_initials"])
    suffix        = _at(parts, pos["suffix"])

    # Deal type must be a known code; anything else → None.
    deal_type = (deal_type_raw or "").upper() if deal_type_raw else None
    if deal_type and deal_type not in DEAL_TYPE_CODES:
        deal_type = None

    ssp, ssp_unknown = canonical_ssp(ssp_raw)
    dsp = canonical_dsp(dsp_raw)

    # Team-USA / Team-INTL → "USA"/"INTL"
    team = None
    if team_raw:
        m = _TEAM_RE.match(team_raw)
        team = m.group(1).upper() if m else None

    seller = canonical_seller(seller_init)

    return ParsedDeal(
        raw=raw_str,
        deal_type=deal_type,
        vertical=vertical,
        ssp_raw=ssp_raw,
        ssp=ssp,
        ssp_is_unknown=ssp_unknown,
        dsp=dsp,
        agency_holdco=agency_holdco,
        agency=agency,
        advertiser=advertiser,
        campaign=campaign,
        geo=geo,
        ad_format=ad_format,
        floor=floor,
        team=team,
        seller_initials=seller_init,
        seller=seller,
        suffix=suffix,
        field_count=n,
    )


def _empty_parsed(raw: str) -> ParsedDeal:
    return ParsedDeal(
        raw=raw,
        deal_type=None,
        vertical=None,
        ssp_raw=None,
        ssp="Unknown SSP",
        ssp_is_unknown=True,
        dsp=None,
        agency_holdco=None,
        agency=None,
        advertiser=None,
        campaign=None,
        geo=None,
        ad_format=None,
        floor=None,
        team=None,
        seller_initials=None,
        seller="Unknown",
        suffix=None,
        field_count=0,
    )
