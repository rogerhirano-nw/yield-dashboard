"""
Public-safe redaction. The repo is public; the published CSV must not
expose floor prices, raw bid-request volumes, or full structured deal names.

Inline-rendered HTML email continues to carry full data — that goes to
internal recipients only.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# Columns stripped from the public CSV. Floor + Bid Requests are the
# sensitive numerics; Raw Deal Name encodes both anyway.
REDACTED_COLUMNS: tuple[str, ...] = ("Floor", "Bid Requests", "Raw Deal Name")

BANNER_LINE = "# Redacted for public hosting. Full data in the email and dashboard."


def is_public_safe_enabled() -> bool:
    """PUBLIC_SAFE env var: 'true' (default), 'false' to bypass redaction."""
    return os.environ.get("PUBLIC_SAFE", "true").strip().lower() not in ("false", "0", "no")


def redact_csv(input_path: Path, output_path: Path) -> Path:
    """Read input_path, strip REDACTED_COLUMNS, write to output_path with a
    leading banner row. Idempotent — overwrites output_path."""
    with input_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        if reader.fieldnames is None:
            raise ValueError(f"{input_path} has no header row")
        kept_fields = [c for c in reader.fieldnames if c not in REDACTED_COLUMNS]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        f.write(BANNER_LINE + "\n")
        writer = csv.DictWriter(f, fieldnames=kept_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in kept_fields})

    log.info(
        "csv redacted",
        input=str(input_path),
        output=str(output_path),
        kept_columns=kept_fields,
        dropped_columns=list(REDACTED_COLUMNS),
        rows=len(rows),
    )
    return output_path
