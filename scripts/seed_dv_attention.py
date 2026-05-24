#!/usr/bin/env python3
"""
Seed (or backfill) the `dv_attention` table from a manually-downloaded
DV Pinnacle Attention CSV.

Two situations where this is the right tool:
  1. First-time seeding before the agentmail polling kicks in tomorrow.
  2. Historical backfill — re-export a longer window from DV Pinnacle
     and load it here. Upserts by date, so re-running with overlapping
     windows is safe (newer file wins for any overlapping dates).

Usage:
    export DATABASE_URL='...'
    python3 scripts/seed_dv_attention.py /path/to/Attention_<start>_<end>.csv

The CSV format must match what `dv_attention_client.parse_dv_csv` expects:
4-line "# …" preamble + blank + header starting with "Date" + data rows.
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

from dv_attention_client import parse_dv_csv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _engine() -> sqlalchemy.Engine:
    url = os.environ.get("DATABASE_URL")
    if not url:
        # Fallback: read from .env (repo root) so the script works without
        # the user pre-exporting the variable. Same convention refresh_cache
        # uses via _load_dotenv().
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.strip().startswith("DATABASE_URL"):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        raise SystemExit("DATABASE_URL not set (env var or repo-root .env)")
    return sqlalchemy.create_engine(url)


def main(csv_path: str) -> int:
    path = Path(csv_path)
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")

    logger.info("Loading %s", path)
    df = parse_dv_csv(path.read_bytes())
    logger.info("Parsed %d rows, %d distinct dates, %d distinct line items",
                len(df), df["date"].nunique(), df["line_item_name"].nunique())

    if df.empty:
        logger.warning("CSV parsed to zero rows; nothing to write")
        return 0

    df["_pulled_at"]        = datetime.now(timezone.utc).isoformat()
    df["_email_message_id"] = f"manual-seed:{path.name}"

    table = "dv_attention"
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
