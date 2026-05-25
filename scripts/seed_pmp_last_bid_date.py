#!/usr/bin/env python3
"""
Seed pmp_last_bid_date with historical bid-activity from Pubmatic, Magnite,
and GAM over a wider window than the normal 7-day sweep.

Run this once after first deploy so the "Stale deals" card in the dashboard
has real signal immediately instead of waiting 90 days for the rolling
tracker to accumulate enough history.

Usage:
    export DATABASE_URL='...'   # or set in repo-root .env
    python3 scripts/seed_pmp_last_bid_date.py                  # 90 days, all sources
    python3 scripts/seed_pmp_last_bid_date.py --days=180       # wider window
    python3 scripts/seed_pmp_last_bid_date.py --sources=gam    # single source
    python3 scripts/seed_pmp_last_bid_date.py --dry-run        # print without writing

Sources: pubmatic, magnite, gam  (comma-separated, default all three)

The upsert is safe to re-run: last_bid_date only ever moves forward, so
running with overlapping windows never regresses existing data.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import sqlalchemy
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _engine() -> sqlalchemy.Engine:
    url = os.environ.get("DATABASE_URL")
    if not url:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL"):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        raise SystemExit("DATABASE_URL not set (env var or repo-root .env)")
    return sqlalchemy.create_engine(url)


def _ensure_table(engine: sqlalchemy.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pmp_last_bid_date (
                ssp             TEXT NOT NULL,
                deal_key        TEXT NOT NULL,
                last_bid_date   TEXT,
                first_seen_date TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                PRIMARY KEY (ssp, deal_key)
            )
        """))


def _load_existing(engine: sqlalchemy.Engine) -> pd.DataFrame:
    try:
        with engine.connect() as conn:
            return pd.read_sql(text("SELECT * FROM pmp_last_bid_date"), conn)
    except Exception:
        return pd.DataFrame(
            columns=["ssp", "deal_key", "last_bid_date", "first_seen_date", "updated_at"]
        )


def _upsert(
    engine: sqlalchemy.Engine,
    new_rows: pd.DataFrame,
    today_str: str,
    now_ts: str,
    dry_run: bool,
) -> int:
    """Merge new_rows (ssp, deal_key, new_last_bid_date) into pmp_last_bid_date.

    last_bid_date only moves forward; first_seen_date is set on first insert.
    Returns number of rows written.
    """
    if new_rows.empty:
        return 0

    existing = _load_existing(engine)

    merged = pd.merge(
        existing[["ssp", "deal_key", "last_bid_date", "first_seen_date"]],
        new_rows[["ssp", "deal_key", "new_last_bid_date"]],
        on=["ssp", "deal_key"],
        how="outer",
    )
    merged["first_seen_date"] = merged["first_seen_date"].fillna(today_str)

    def _max_date(row) -> object:
        candidates = [
            v for v in (row["last_bid_date"], row["new_last_bid_date"])
            if pd.notna(v) and str(v) not in ("", "None", "nan", "NaT")
        ]
        return max(candidates) if candidates else None

    merged["last_bid_date"] = merged.apply(_max_date, axis=1)
    merged["updated_at"]    = now_ts
    merged = merged[["ssp", "deal_key", "last_bid_date", "first_seen_date", "updated_at"]]

    if dry_run:
        n_with_history = int(merged["last_bid_date"].notna().sum())
        logger.info("[dry-run] Would write %d rows (%d with bid history) to pmp_last_bid_date",
                    len(merged), n_with_history)
        print(merged.to_string(index=False))
        return len(merged)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pmp_last_bid_date"))
        merged.to_sql("pmp_last_bid_date", conn, if_exists="append", index=False)

    n_with_history = int(merged["last_bid_date"].notna().sum())
    logger.info("Wrote %d rows to pmp_last_bid_date (%d with bid history)",
                len(merged), n_with_history)
    return len(merged)


# ---------------------------------------------------------------------------
# Per-source pullers
# ---------------------------------------------------------------------------

def _pull_pubmatic(start, end) -> pd.DataFrame:
    """Pull 90-day (or custom) deal-level bid-response history from Pubmatic."""
    logger.info("Pubmatic: pulling %s → %s", start, end)
    from pubmatic_client import PubmaticClient
    client = PubmaticClient()
    df = client.run_deal_report(start, end)
    if df.empty:
        logger.warning("Pubmatic: empty response")
        return pd.DataFrame()

    df["non_zero_bid_responses"] = pd.to_numeric(
        df.get("non_zero_bid_responses", 0), errors="coerce"
    ).fillna(0)
    df["deal_meta_id"] = df["deal_meta_id"].astype(str)

    result = (
        df[df["non_zero_bid_responses"] > 0]
        .groupby("deal_meta_id", as_index=False)["date"]
        .max()
        .rename(columns={"deal_meta_id": "deal_key", "date": "new_last_bid_date"})
    )
    # Ensure all deals appear (even those with no bids) so first_seen_date is set.
    all_deals = df["deal_meta_id"].drop_duplicates().rename("deal_key").to_frame()
    result = pd.merge(all_deals, result, on="deal_key", how="left")
    result["ssp"] = "Pubmatic"
    logger.info("Pubmatic: %d deals, %d with bid responses",
                len(result), int(result["new_last_bid_date"].notna().sum()))
    return result[["ssp", "deal_key", "new_last_bid_date"]]


def _pull_magnite(start, end) -> pd.DataFrame:
    """Pull deal-level bid-response history from Magnite."""
    logger.info("Magnite: pulling %s → %s", start, end)
    from client import MagniteClient
    mc = MagniteClient(
        api_key    = os.environ["MAGNITE_KEY"],
        api_secret = os.environ["MAGNITE_SECRET"],
        account_id = os.environ["MAGNITE_PUBLISHER_ID"],
    )
    df = mc.run_report(
        dimensions=["date", "deal", "deal_id"],
        metrics=["bid_responses"],
        date_range=None,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    if df.empty:
        logger.warning("Magnite: empty response")
        return pd.DataFrame()

    df["bid_responses"] = pd.to_numeric(df.get("bid_responses", 0), errors="coerce").fillna(0)
    df["deal_id"]       = df["deal_id"].astype(str)

    result = (
        df[df["bid_responses"] > 0]
        .groupby("deal_id", as_index=False)["date"]
        .max()
        .rename(columns={"deal_id": "deal_key", "date": "new_last_bid_date"})
    )
    all_deals = df["deal_id"].drop_duplicates().rename("deal_key").to_frame()
    result = pd.merge(all_deals, result, on="deal_key", how="left")
    result["ssp"] = "Magnite"
    logger.info("Magnite: %d deals, %d with bid responses",
                len(result), int(result["new_last_bid_date"].notna().sum()))
    return result[["ssp", "deal_key", "new_last_bid_date"]]


def _pull_gam(start, end) -> pd.DataFrame:
    """Pull deal-level bid history from GAM."""
    logger.info("GAM: pulling %s → %s", start, end)
    from gam_client import GAMClient
    df = GAMClient().run_deal_bid_report(start, end)
    if df.empty:
        logger.warning("GAM: empty response")
        return pd.DataFrame()

    df["deals_bids"] = pd.to_numeric(df.get("deals_bids", 0), errors="coerce").fillna(0)

    result = (
        df[df["deals_bids"] > 0]
        .groupby("programmatic_deal_name", as_index=False)["date"]
        .max()
        .rename(columns={"programmatic_deal_name": "deal_key", "date": "new_last_bid_date"})
    )
    all_deals = (
        df["programmatic_deal_name"].drop_duplicates()
        .rename("deal_key").to_frame()
    )
    result = pd.merge(all_deals, result, on="deal_key", how="left")
    result["ssp"] = "GAM"
    logger.info("GAM: %d deals, %d with bid responses",
                len(result), int(result["new_last_bid_date"].notna().sum()))
    return result[["ssp", "deal_key", "new_last_bid_date"]]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days",    type=int,  default=90,
                        help="How many days of history to pull (default 90, max ~180 for Pubmatic)")
    parser.add_argument("--sources", type=str,  default="pubmatic,magnite,gam",
                        help="Comma-separated sources to pull (default: pubmatic,magnite,gam)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written without touching the DB")
    args = parser.parse_args()

    sources = {s.strip().lower() for s in args.sources.split(",")}
    valid   = {"pubmatic", "magnite", "gam"}
    unknown = sources - valid
    if unknown:
        raise SystemExit(f"Unknown sources: {unknown}. Valid: {valid}")

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    start     = yesterday - timedelta(days=args.days - 1)
    today_str = datetime.now(timezone.utc).date().isoformat()
    now_ts    = datetime.now(timezone.utc).isoformat()

    logger.info("Seeding pmp_last_bid_date: %s → %s (%d days), sources=%s, dry_run=%s",
                start, yesterday, args.days, sorted(sources), args.dry_run)

    engine = _engine()
    if not args.dry_run:
        _ensure_table(engine)

    parts: list[pd.DataFrame] = []

    if "pubmatic" in sources:
        try:
            parts.append(_pull_pubmatic(start, yesterday))
        except Exception:
            logger.exception("Pubmatic pull failed — skipping")

    if "magnite" in sources:
        try:
            parts.append(_pull_magnite(start, yesterday))
        except Exception:
            logger.exception("Magnite pull failed — skipping")

    if "gam" in sources:
        try:
            parts.append(_pull_gam(start, yesterday))
        except Exception:
            logger.exception("GAM pull failed — skipping")

    if not parts:
        logger.error("All sources failed or returned empty — nothing to write")
        return 1

    combined = pd.concat(parts, ignore_index=True)
    n = _upsert(engine, combined, today_str, now_ts, dry_run=args.dry_run)
    logger.info("Done — %d total rows in pmp_last_bid_date", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
