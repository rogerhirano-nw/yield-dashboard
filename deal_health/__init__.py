"""deal_health — weekly Newsweek deal health report generator."""

from .aggregate import build_payload
from .data import load_deals
from .models import (
    AdvertiserRollup,
    Payload,
    ParsedDeal,
    SSPRollup,
    UnhealthyDeal,
)
from .parser import canonical_dsp, canonical_seller, canonical_ssp, parse_deal
from .publish import build_csv_url, commit_and_push, csv_filename_for, verify_url, write_csv
from .redact import is_public_safe_enabled, redact_csv
from .render import render_email

__all__ = [
    "AdvertiserRollup",
    "Payload",
    "ParsedDeal",
    "SSPRollup",
    "UnhealthyDeal",
    "build_csv_url",
    "build_payload",
    "canonical_dsp",
    "canonical_seller",
    "canonical_ssp",
    "commit_and_push",
    "csv_filename_for",
    "is_public_safe_enabled",
    "load_deals",
    "parse_deal",
    "redact_csv",
    "render_email",
    "verify_url",
    "write_csv",
]
