"""
Data model for the weekly deal health report. All dataclasses are frozen —
the renderer expects a fully-prepared payload and never mutates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class ParsedDeal:
    """Output of parser.parse_deal(). Every field is Optional because the
    naming convention is positional and tolerant of N/A in any slot."""
    raw: str
    deal_type: Optional[str]           # PA / PD / PG (code)
    vertical: Optional[str]
    ssp_raw: Optional[str]             # what was in position 3 of the name
    ssp: str                           # canonical: AdX / Magnite / Pubmatic / "Unknown SSP"
    ssp_is_unknown: bool
    dsp: Optional[str]                 # canonical via DSP_ALIASES
    agency_holdco: Optional[str]
    agency: Optional[str]
    advertiser: Optional[str]
    campaign: Optional[str]
    geo: Optional[str]
    ad_format: Optional[str]
    floor: Optional[str]               # e.g. "$5" — prefix preserved
    team: Optional[str]                # USA / INTL (extracted from "Team-USA")
    seller_initials: Optional[str]     # raw initials, e.g. "ILee"
    seller: str                        # canonical via AE_NAMES; "Unknown" if unresolved
    suffix: Optional[str]              # 15th field if present
    field_count: int                   # 13 / 14 / 15 — for telemetry


@dataclass(frozen=True)
class UnhealthyDeal:
    """One unhealthy deal: parsed name + the metrics that flagged it."""
    parsed: ParsedDeal
    bid_requests: int
    days_in_data: int
    first_seen: str                    # ISO date — earliest date in cache window
    deal_age_days: Optional[int]       # may be None when no metadata anchor


@dataclass(frozen=True)
class SSPRollup:
    """Aggregated row for the 'By SSP' breakdown table."""
    ssp: str
    deal_count: int
    bid_requests: int
    share_of_total: float              # 0.0–1.0


@dataclass(frozen=True)
class AdvertiserRollup:
    """Aggregated row for the 'Top dark advertisers' table."""
    advertiser: str
    deal_count: int
    bid_requests: int


@dataclass(frozen=True)
class Payload:
    """The complete input to render.render_email(). Render is a pure function
    of this payload — no I/O, no globals, no datetime.now()."""
    report_date: date
    lookback_days: int

    deals: tuple[UnhealthyDeal, ...]

    # Pre-computed aggregations.
    by_ssp: tuple[SSPRollup, ...]
    top_dark_advertisers: tuple[AdvertiserRollup, ...]
    top_dsp: tuple[str, float]         # (dsp_name, share 0.0–1.0)

    total_deals: int                   # incl. House/Unknown (matches "Unhealthy deals" KPI)
    total_bid_requests: int
    seller_count: int                  # excludes House + Unknown — matches "across N sellers"

    # External links (kept in payload so render stays pure).
    csv_url: str
    dashboard_url: str
