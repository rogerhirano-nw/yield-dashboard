#!/usr/bin/env python3
"""
Seed (or backfill) one of the DV cache tables from a manually-downloaded
DV Pinnacle CSV. Auto-detects which report type from the header row:

  - "Date,..." with "Attention Index"   → dv_attention
  - "Traffic Validity,..."              → dv_ivt

Two situations where this is the right tool:
  1. First-time seeding before the agentmail polling kicks in tomorrow.
  2. Historical backfill — re-export a longer window from DV Pinnacle
     and load it here. Upserts by date, so re-running with overlapping
     windows is safe (newer file wins for any overlapping dates).

Usage:
    export DATABASE_URL='...'
    python3 scripts/seed_dv_attention.py /path/to/<CSV>

Despite the filename it handles both DV report types — the original
file was named when only the Attention report existed. Kept stable to
avoid breaking any documented invocations.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# So this script works whether run from repo root or scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy
from sqlalchemy import inspect as sa_inspect, text

from dv_attention_client import parse_dv_csv as parse_attention_csv
from dv_ivt_client       import parse_dv_ivt_csv as parse_ivt_csv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _engine() -> sqlalchemy.Engine:
    url = os.environ.get("DATABASE_URL")
    if not url:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.strip().startswith("DATABASE_URL"):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        raise SystemExit("DATABASE_URL not set (env var or repo-root .env)")
    return sqlalchemy.create_engine(url)


def _detect_and_parse(content: bytes) -> tuple[str, "pandas.DataFrame"]:
    """Sniff the header to pick the right parser + return (table_name, df)."""
    head = content[:4000].decode("utf-8-sig", errors="replace")
    if "Traffic Validity" in head:
        logger.info("Detected DV IVT report (Traffic Validity header)")
        return ("dv_ivt", parse_ivt_csv(content))
    if "Attention Index" in head:
        logger.info("Detected DV Attention report (Attention Index header)")
        return ("dv_attention", parse_attention_csv(content))
    raise SystemExit(
        "Could not identify CSV report type — header had neither "
        "'Traffic Validity' (IVT) nor 'Attention Index' (Attention)."
    )


def main(csv_path: str) -> int:
    path = Path(csv_path)
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")

    logger.info("Loading %s", path)
    table, df = _detect_and_parse(path.read_bytes())
    logger.info("Parsed %d rows, %d distinct dates, %d distinct line items into %s",
                len(df), df["date"].nunique(),
                df["line_item_name"].nunique() if "line_item_name" in df.columns else 0,
                table)
    if df.empty:
        logger.warning("CSV parsed to zero rows; nothing to write")
        return 0

    df["_pulled_at"]        = datetime.now(timezone.utc).isoformat()
    df["_email_message_id"] = f"manual-seed:{path.name}"

    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            if existing_cols != set(df.columns):
                logger.info("Schema change vs existing %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            else:
                dates = [d.isoformat() if d is not None else None
                         for d in df["date"].dropna().unique().tolist()]
                if dates:
                    conn.execute(
                        text(f'DELETE FROM "{table}" WHERE date::text = ANY(:dates)'),
                        {"dates": dates},
                    )
        df.to_sql(table, conn, if_exists="append", index=False)

    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(0 if main(sys.argv[1]) > 0 else 1)
