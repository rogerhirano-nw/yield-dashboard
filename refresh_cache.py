"""
Example: pull yesterday's Prebid data on a schedule, cache to SQLite.
Dashboard reads from the cache, NOT directly from Magnite.

Why: the Magnite API is offline/batch — reports can queue for minutes and
you're capped at 5 in flight. A "live" dashboard pointed straight at the
API will be slow and rate-limit itself. Pull into a local store on a cron,
serve the dashboard from the store.

Wire this into cron, Airflow, or a systemd timer:
    0 8 * * *  cd /opt/magnite && python -m magnite_client.refresh_cache

Then point Streamlit/Metabase/Looker Studio at magnite_cache.db.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy
from sqlalchemy import inspect as sa_inspect, text

from client import MagniteClient
from gam_client import GAMClient
from pubmatic_client import PubmaticClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _engine() -> sqlalchemy.Engine:
    return sqlalchemy.create_engine(os.environ["DATABASE_URL"])


# Tune these to match the dashboard's actual filter dimensions.
# Don't grab every dim — the row count blows up fast and you'll hit the 500K cap.
REPORTS = {
    "magnite_site_daily": {
        "dimensions": ["date", "site", "size", "device_type_name_v1"],
        "metrics": [
            "ad_requests",
            "bid_requests",
            "bid_responses",
            "auctions",
            "impressions",
            "publisher_gross_revenue",
            "ecpm",
        ],
        "date_range": "last_7",
    },
    "magnite_dsp_daily": {
        "dimensions": ["date", "partner", "site"],
        "metrics": [
            "bid_requests",
            "bid_responses",
            "auctions_won",
            "impressions",
            "publisher_gross_revenue",
            "win_rate",
        ],
        "date_range": "last_7",
    },
    "magnite_deal_daily": {
        "dimensions": ["date", "deal", "deal_id", "partner", "ad_format"],
        "metrics": [
            "bid_requests",
            "bid_responses",
            "impressions",
            "paid_impression",
            "publisher_gross_revenue",
            "seller_net_revenue",
            "ecpm",
            "win_rate",
        ],
        "date_range": "last_7",
    },
    # Add Prebid-specific reports here once you've confirmed the column names
    # against the logged-in Prebid Analytics API docs, and set dataset="prebid".
}


def refresh_one_report(client: MagniteClient, table: str, config: dict) -> int:
    """Pull a single report and write it to its own table. Returns row count."""
    logger.info("Refreshing %s", table)
    df = client.run_report(**config)
    if df.empty:
        logger.warning("%s came back empty — nothing to write", table)
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")

    with _engine().begin() as conn:
        existing_tables = sa_inspect(conn).get_table_names()
        if table in existing_tables:
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            new_cols = set(df.columns)
            if existing_cols != new_cols:
                logger.info("Schema change detected for %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            else:
                conn.execute(text(f'DELETE FROM "{table}" WHERE date >= :cutoff'), {"cutoff": cutoff})
        df.to_sql(table, conn, if_exists="append", index=False)
    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


def refresh_gam() -> int:
    """Pull GAM delivery + pacing for the last 7 days and write to gam_campaigns."""
    from datetime import date as _date

    logger.info("Refreshing gam_campaigns (GAM)")
    gam = GAMClient()

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    seven_days_ago = yesterday - timedelta(days=6)  # last 7 days inclusive

    df = gam.run_report_with_pacing(seven_days_ago, yesterday)
    if df.empty:
        logger.warning("GAM report came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    df["source"] = "gam"
    df["campaign_type"] = "direct"

    table = "gam_campaigns"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")

    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            if existing_cols != set(df.columns):
                logger.info("Schema change detected for %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            else:
                conn.execute(
                    text(f'DELETE FROM "{table}" WHERE report_start >= :cutoff'),
                    {"cutoff": cutoff},
                )
        df.to_sql(table, conn, if_exists="append", index=False)

    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


def refresh_gam_pmp_deals() -> int:
    """Pull GAM PMP deal data (PA / PD / PG) by deal name and write to gam_pmp_deals."""
    logger.info("Refreshing gam_pmp_deals (GAM deals report)")
    gam = GAMClient()

    yesterday      = datetime.now(timezone.utc).date() - timedelta(days=1)
    seven_days_ago = yesterday - timedelta(days=6)

    df = gam.run_deals_report(seven_days_ago, yesterday)
    if df.empty:
        logger.warning("GAM deals report came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    table  = "gam_pmp_deals"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")

    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            if existing_cols != set(df.columns):
                logger.info("Schema change detected for %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            else:
                conn.execute(text(f'DELETE FROM "{table}" WHERE date >= :cutoff'), {"cutoff": cutoff})
        df.to_sql(table, conn, if_exists="append", index=False)

    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


def refresh_pubmatic() -> int:
    """Pull Pubmatic PMP deal data for the last 7 days and write to pubmatic_deals."""
    logger.info("Refreshing pubmatic_deals (Pubmatic)")
    client = PubmaticClient()

    yesterday      = datetime.now(timezone.utc).date() - timedelta(days=1)
    seven_days_ago = yesterday - timedelta(days=6)

    df = client.run_deal_report(seven_days_ago, yesterday)
    if df.empty:
        logger.warning("Pubmatic report came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    table  = "pubmatic_deals"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")

    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            if existing_cols != set(df.columns):
                logger.info("Schema change detected for %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            else:
                conn.execute(
                    text(f'DELETE FROM "{table}" WHERE date >= :cutoff'),
                    {"cutoff": cutoff},
                )
        df.to_sql(table, conn, if_exists="append", index=False)

    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


def _load_dotenv() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_TABLE_RENAMES = {
    "by_site_size_daily": "magnite_site_daily",
    "by_dsp_daily":       "magnite_dsp_daily",
    "by_deal_daily":      "magnite_deal_daily",
    "campaigns_gam":      "gam_campaigns",
    "deals_pubmatic":     "pubmatic_deals",
}


def migrate_table_names() -> None:
    """One-time rename of old table names to the new {source}_{content} convention."""
    with _engine().begin() as conn:
        existing = set(sa_inspect(conn).get_table_names())
        for old, new in _TABLE_RENAMES.items():
            if old in existing and new not in existing:
                conn.execute(text(f'ALTER TABLE "{old}" RENAME TO "{new}"'))
                logger.info("Renamed table %s → %s", old, new)


def main() -> None:
    _load_dotenv()
    logger.info("refresh_cache v3 — Magnite date_range=%s", next(iter(REPORTS.values()))["date_range"])
    api_key    = os.environ["MAGNITE_KEY"]
    api_secret = os.environ["MAGNITE_SECRET"]
    account_id = os.environ["MAGNITE_PUBLISHER_ID"]

    migrate_table_names()

    client = MagniteClient(
        api_key=api_key,
        api_secret=api_secret,
        account_id=account_id,
    )

    total = 0
    for table, config in REPORTS.items():
        try:
            total += refresh_one_report(client, table, config)
        except Exception:
            logger.exception("Refresh failed for %s — continuing with others", table)

    logger.info("Done. %d total rows written across %d reports.", total, len(REPORTS))

    try:
        total += refresh_gam()
    except Exception:
        logger.exception("Refresh failed for gam_campaigns — continuing")

    try:
        total += refresh_gam_pmp_deals()
    except Exception:
        logger.exception("Refresh failed for gam_pmp_deals — continuing")

    try:
        total += refresh_pubmatic()
    except Exception:
        logger.exception("Refresh failed for pubmatic_deals — continuing")


if __name__ == "__main__":
    main()
