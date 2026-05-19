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
        "dimensions": ["date", "deal", "deal_id"],  # partner/ad_format not returned by API; derived from deal name in dashboard
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
    # demand_type_ad_resp and revenue_source are "Demand Fields" — incompatible with auction
    # metrics (bid_requests, bid_responses, impressions). Pull separately with ad metrics only
    # and join to magnite_deal_daily in the dashboard.
    # No date dimension — this is a lookup table (deal_id → demand_type + revenue_source).
    # last_30 maximises coverage: deals with zero impressions in the past 7 days still appear.
    "magnite_deal_demand": {
        "dimensions": ["deal", "deal_id", "demand_type_ad_resp", "revenue_source"],
        "metrics": [
            "paid_impression",
            "publisher_gross_revenue",
        ],
        "date_range": "last_30",
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

    with _engine().begin() as conn:
        existing_tables = sa_inspect(conn).get_table_names()
        if table in existing_tables:
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            new_cols = set(df.columns)
            if existing_cols != new_cols:
                logger.info("Schema change detected for %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            elif "date" in df.columns:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")
                conn.execute(text(f'DELETE FROM "{table}" WHERE date >= :cutoff'), {"cutoff": cutoff})
            else:
                # Lookup table with no date column — replace all rows each run.
                conn.execute(text(f'DELETE FROM "{table}"'))
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

    # Diagnostic: log every distinct order name prefix so we can find PA line items.
    if "order_name" in df.columns:
        order_names = sorted(df["order_name"].dropna().unique().tolist())
        logger.info("GAM distinct order names (%d): %s", len(order_names), order_names)
    if "line_item_name" in df.columns:
        prefixes = sorted({n[:20] for n in df["line_item_name"].dropna().unique()})
        logger.info("GAM line_item_name prefixes (first 20 chars, %d unique): %s", len(prefixes), prefixes)

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

    table = "gam_pmp_deals"
    with _engine().begin() as conn:
        df.to_sql(table, conn, if_exists="replace", index=False)

    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


def refresh_gam_private_auctions() -> int:
    """Fetch PA deal metadata from the GAM REST API and write to gam_pa_metadata."""
    logger.info("Refreshing gam_pa_metadata (GAM Private Auctions)")
    gam = GAMClient()
    df = gam.get_private_auctions()
    if df.empty:
        logger.warning("No PA deal metadata found — nothing to write")
        return 0
    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    with _engine().begin() as conn:
        df.to_sql("gam_pa_metadata", conn, if_exists="replace", index=False)
    logger.info("Wrote %d rows to gam_pa_metadata", len(df))
    return len(df)


def refresh_gam_creatives() -> int:
    """Fetch creative metadata (display name + video duration) and write
    to gam_creatives. Used by the dashboard to detect lines whose creative
    duration crosses the 30-second threshold (→ "Video Preroll >30s")."""
    logger.info("Refreshing gam_creatives")
    gam = GAMClient()
    df = gam.list_creatives_with_duration()
    if df.empty:
        logger.warning("No creatives returned from GAM — skipping write")
        return 0
    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    with _engine().begin() as conn:
        df.to_sql("gam_creatives", conn, if_exists="replace", index=False)
    logger.info("Wrote %d rows to gam_creatives", len(df))
    return len(df)


def refresh_gam_lica() -> int:
    """Fetch line-item ↔ creative associations and write to gam_lica.
    Joined with gam_creatives to give each line item its set of
    creative durations."""
    logger.info("Refreshing gam_lica (LineItemCreativeAssociation)")
    gam = GAMClient()
    df = gam.list_line_item_creative_associations()
    if df.empty:
        logger.warning("No LICAs returned from GAM — skipping write")
        return 0
    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    with _engine().begin() as conn:
        df.to_sql("gam_lica", conn, if_exists="replace", index=False)
    logger.info("Wrote %d rows to gam_lica", len(df))
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
        total += refresh_gam_private_auctions()
    except Exception:
        logger.exception("Refresh failed for gam_pa_metadata — continuing")

    try:
        total += refresh_gam_creatives()
    except Exception:
        logger.exception("Refresh failed for gam_creatives — continuing")

    try:
        total += refresh_gam_lica()
    except Exception:
        logger.exception("Refresh failed for gam_lica — continuing")

    try:
        total += refresh_pubmatic()
    except Exception:
        logger.exception("Refresh failed for pubmatic_deals — continuing")


if __name__ == "__main__":
    main()
