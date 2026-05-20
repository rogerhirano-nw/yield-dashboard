"""Redaction tests — sensitive columns must be stripped from the public CSV."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from deal_health.publish import write_csv
from deal_health.redact import BANNER_LINE, REDACTED_COLUMNS, redact_csv
from tests.fixtures.sample_payload import build_sample_payload


def test_redact_strips_sensitive_columns(tmp_path: Path):
    payload = build_sample_payload()

    full_path = tmp_path / "full.csv"
    write_csv(payload, full_path)

    redacted_path = tmp_path / "public.csv"
    redact_csv(full_path, redacted_path)

    text = redacted_path.read_text(encoding="utf-8")

    # First line is the public-safe banner.
    first_line = text.split("\n", 1)[0]
    assert first_line == BANNER_LINE, f"banner missing or wrong: {first_line!r}"

    # Each redacted column must NOT appear in the header.
    header_line = text.split("\n", 2)[1]
    for col in REDACTED_COLUMNS:
        assert col not in header_line, f"redacted column leaked into public CSV: {col!r}"

    # Non-redacted columns we explicitly require to remain.
    for kept in ("Seller", "SSP", "DSP", "Agency", "Advertiser",
                 "Deal Type", "Vertical", "Geo", "Format",
                 "Days in Data", "First Seen"):
        assert kept in header_line, f"expected column missing from public CSV: {kept!r}"


def test_redact_row_count_preserved(tmp_path: Path):
    payload = build_sample_payload()
    full_path = tmp_path / "full.csv"
    write_csv(payload, full_path)
    redacted_path = redact_csv(full_path, tmp_path / "public.csv")

    # Skip the banner line, then DictReader reads the header + data rows.
    with redacted_path.open() as f:
        next(f)  # banner
        reader = csv.DictReader(f)
        public_rows = list(reader)

    with full_path.open() as f:
        full_reader = csv.DictReader(f)
        full_rows = list(full_reader)

    assert len(public_rows) == len(full_rows)
