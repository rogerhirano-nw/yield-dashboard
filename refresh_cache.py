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
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import sqlalchemy
from sqlalchemy import inspect as sa_inspect, text

from client import MagniteClient
from dv_attention_client import pull_dv_attention
from dv_ivt_client import pull_dv_ivt
from gam_client import GAMClient
from opensincera_client import OpenSinceraClient
from pubmatic_client import PubmaticClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _engine() -> sqlalchemy.Engine:
    return sqlalchemy.create_engine(os.environ["DATABASE_URL"])


def _safe_replace(df: pd.DataFrame, table: str, conn) -> None:
    """Write df to table using TRUNCATE when the schema is unchanged, falling
    back to DROP only when columns differ. TRUNCATE generates a single WAL
    record vs. per-row WAL from DROP, cutting catalog churn and autovacuum
    pressure on tables that are replaced daily."""
    existing_tables = sa_inspect(conn).get_table_names()
    if table in existing_tables:
        existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
        if existing_cols == set(df.columns):
            conn.execute(text(f'TRUNCATE TABLE "{table}"'))
        else:
            logger.info("Schema change detected for %s — dropping and recreating", table)
            conn.execute(text(f'DROP TABLE "{table}"'))
    df.to_sql(table, conn, if_exists="append", index=False)


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
        _safe_replace(df, table, conn)

    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


def refresh_gam_deal_bids() -> int:
    """Pull per-day per-deal bid metrics for the last 7 days into gam_deal_bid_daily.
    Separate from gam_pmp_deals because DEALS_* metrics aren't compatible with
    ORDER_NAME/DEAL_BUYER_NAME in a single GAM report."""
    logger.info("Refreshing gam_deal_bid_daily (GAM deal bid report)")
    gam = GAMClient()

    yesterday      = datetime.now(timezone.utc).date() - timedelta(days=1)
    seven_days_ago = yesterday - timedelta(days=6)

    df = gam.run_deal_bid_report(seven_days_ago, yesterday)
    if df.empty:
        logger.warning("GAM deal-bid report came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    table = "gam_deal_bid_daily"
    with _engine().begin() as conn:
        _safe_replace(df, table, conn)

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
        _safe_replace(df, "gam_pa_metadata", conn)
    logger.info("Wrote %d rows to gam_pa_metadata", len(df))
    return len(df)


def refresh_gam_preferred_deals() -> int:
    """Fetch non-archived PD/PG/Sponsorship proposal-line-item metadata from
    GAM via SOAP, write to gam_pd_metadata. Used by weekly_report.py to
    apply the ≥ 90-day age threshold on Preferred Deals."""
    logger.info("Refreshing gam_pd_metadata (GAM Preferred Deals / Proposal Line Items)")
    gam = GAMClient()
    df = gam.get_preferred_deals()
    if df.empty:
        logger.warning("No PD metadata returned — nothing to write")
        return 0
    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    with _engine().begin() as conn:
        _safe_replace(df, "gam_pd_metadata", conn)
    logger.info("Wrote %d rows to gam_pd_metadata", len(df))
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
        _safe_replace(df, "gam_creatives", conn)
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
        _safe_replace(df, "gam_lica", conn)
    logger.info("Wrote %d rows to gam_lica", len(df))
    return len(df)


def refresh_magnite_deal_metadata() -> int:
    """Pull a 180-day Magnite deal-keyed report to determine each deal's
    earliest appearance. Used by weekly_report.py as an age proxy since
    Magnite's reports API doesn't expose a deal creation_date dimension.

    Conservative — only underestimates true age, never overestimates."""
    logger.info("Refreshing magnite_deal_metadata (180-day age proxy)")
    api_key    = os.environ["MAGNITE_KEY"]
    api_secret = os.environ["MAGNITE_SECRET"]
    account_id = os.environ["MAGNITE_PUBLISHER_ID"]
    client = MagniteClient(api_key=api_key, api_secret=api_secret, account_id=account_id)

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    start     = yesterday - timedelta(days=179)

    # Magnite rejects bare YYYY-MM-DD; requires ISO-8601 with timezone, e.g.
        # "yyyy-MM-dd'T'HH:mm:ssZ". Send midnight UTC for start and end-of-day UTC for end.
    df = client.run_report(
        dimensions=["deal", "deal_id", "date"],
        metrics=["bid_requests"],
        start=f"{start.isoformat()}T00:00:00Z",
        end=f"{yesterday.isoformat()}T23:59:59Z",
        date_range=None,
    )
    if df.empty:
        logger.warning("Magnite metadata report came back empty — nothing to write")
        return 0

    agg = df.groupby(["deal", "deal_id"], dropna=False).agg(
        first_seen=("date", "min"),
        last_seen=("date", "max"),
        days_seen=("date", "nunique"),
    ).reset_index()
    agg["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    with _engine().begin() as conn:
        _safe_replace(agg, "magnite_deal_metadata", conn)
    logger.info("Wrote %d Magnite deals to magnite_deal_metadata", len(agg))
    return len(agg)


def refresh_pubmatic_deal_metadata() -> int:
    """Pull a 180-day Pubmatic deal-keyed report (minimal dims/metrics) to
    determine each deal's earliest appearance. Same proxy as Magnite —
    Pubmatic's Analytics API doesn't expose deal creation_date."""
    logger.info("Refreshing pubmatic_deal_metadata (180-day age proxy)")
    from pubmatic_client import PubmaticClient
    client = PubmaticClient()

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    start     = yesterday - timedelta(days=179)

    df = client.run_deal_first_seen_report(start, yesterday)
    if df.empty:
        logger.warning("Pubmatic metadata report came back empty — nothing to write")
        return 0

    keep_cols = [c for c in ("deal", "deal_meta_id") if c in df.columns]
    agg = df.groupby(keep_cols, dropna=False).agg(
        first_seen=("date", "min"),
        last_seen=("date", "max"),
        days_seen=("date", "nunique"),
    ).reset_index()
    agg["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    with _engine().begin() as conn:
        _safe_replace(agg, "pubmatic_deal_metadata", conn)
    logger.info("Wrote %d Pubmatic deals to pubmatic_deal_metadata", len(agg))
    return len(agg)


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


def refresh_dv_attention() -> int:
    """Poll the newsweek@agentmail.to inbox for daily DV Attention CSV
    emails, parse the latest, write to `dv_attention` table.

    The DV team mails the Pinnacle "Unified Analytics Report: Attention
    Metrics" CSV each morning. We poll the agentmail inbox for matching
    subjects, download every CSV attachment, parse, and upsert by date:
    any date present in the new batch is deleted from the table first
    so the latest email wins. Older dates stay untouched as historical
    backfill.

    Skips silently (returns 0) when agentmail credentials aren't set —
    so local refreshes without `.env` don't crash."""
    logger.info("Refreshing dv_attention (DoubleVerify Attention metrics)")
    api_key  = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    if not api_key or not inbox_id:
        logger.warning(
            "AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID not set — "
            "skipping DV Attention refresh"
        )
        return 0

    df = pull_dv_attention(api_key, inbox_id)
    if df.empty:
        logger.warning("No DV Attention CSV attachments found in inbox")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    table = "dv_attention"
    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            if existing_cols != set(df.columns):
                logger.info("Schema change detected for %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            else:
                # Upsert-by-date: delete any rows for dates the new batch
                # covers, so the freshest email's view wins. Older dates
                # stay (historical backfill survives).
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


def refresh_dv_ivt() -> int:
    """Poll newsweek@agentmail.to for DV IVT CSV emails (subject:
    'Unified Analytics Report: IVT'), parse, upsert by date.

    NOTE on what this report contains and doesn't: each (Date, Line Item)
    appears as multiple rows — one per Traffic Validity bucket
    (Valid Traffic / Fraud/SIVT / Fraud/GIVT). The three rate columns
    are tautological (1.0 = "this row is in that bucket", 0.0 = "isn't"),
    NOT impression-weighted percentages. The CSV does not include
    impression counts.

    The dashboard derives a *day-prevalence* IVT proxy (count of distinct
    dates a line has any Fraud row / total dates seen) — see the
    `_ivt_html` column rendering. For a true impression-weighted IVT%
    you'd need DV to add an Impressions metric to the Pinnacle export.

    Same skip-silently-when-creds-missing pattern as refresh_dv_attention."""
    logger.info("Refreshing dv_ivt (DoubleVerify IVT classification rows)")
    api_key  = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    if not api_key or not inbox_id:
        logger.warning(
            "AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID not set — "
            "skipping DV IVT refresh"
        )
        return 0

    df = pull_dv_ivt(api_key, inbox_id)
    if df.empty:
        logger.warning("No DV IVT CSV attachments found in inbox")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    table = "dv_ivt"
    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            if existing_cols != set(df.columns):
                logger.info("Schema change detected for %s — dropping and recreating", table)
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


# Hardcoded watch-list for the OpenSincera /publishers endpoint.
# Newsweek + editorial peers we care about for quality benchmarking
# (A2CR, ads-in-view, ad refresh, page weight, ID absorption).
OPENSINCERA_WATCHLIST = [
    "newsweek.com",
    "businessinsider.com",
    "forbes.com",
    "theatlantic.com",
    "time.com",
    "slate.com",
    "salon.com",
    "thedailybeast.com",
    "huffpost.com",
    "buzzfeed.com",
    "cnn.com",
    "usatoday.com",
    "nytimes.com",
    "washingtonpost.com",
]


def refresh_opensincera_ecosystem() -> int:
    """Append today's /ecosystem snapshot to opensincera_ecosystem.

    Each row is one daily snapshot — kept as an append-only history so
    the dashboard can show how the ecosystem metrics drift over time."""
    logger.info("Refreshing opensincera_ecosystem")
    client = OpenSinceraClient()
    df = client.get_ecosystem()
    if df.empty:
        logger.warning("OpenSincera ecosystem came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    table = "opensincera_ecosystem"
    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            if existing_cols != set(df.columns):
                logger.info("Schema change detected for %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            elif "date" in df.columns:
                # Keep one row per snapshot date — replace today's if rerun same day.
                conn.execute(
                    text(f'DELETE FROM "{table}" WHERE date = :d'),
                    {"d": df["date"].iloc[0]},
                )
        df.to_sql(table, conn, if_exists="append", index=False)
    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


def refresh_opensincera_publishers() -> int:
    """Pull /publishers for each domain in the watch-list and replace the table."""
    logger.info("Refreshing opensincera_publishers (%d domains)",
                len(OPENSINCERA_WATCHLIST))
    client = OpenSinceraClient()
    df = client.get_publishers(OPENSINCERA_WATCHLIST)
    if df.empty:
        logger.warning("OpenSincera publishers came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    with _engine().begin() as conn:
        _safe_replace(df, "opensincera_publishers", conn)
    logger.info("Wrote %d rows to opensincera_publishers", len(df))
    return len(df)


def refresh_opensincera_adsystems() -> int:
    """Pull /adsystems and replace the table."""
    logger.info("Refreshing opensincera_adsystems")
    client = OpenSinceraClient()
    df = client.get_adsystems()
    if df.empty:
        logger.warning("OpenSincera adsystems came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    with _engine().begin() as conn:
        _safe_replace(df, "opensincera_adsystems", conn)
    logger.info("Wrote %d rows to opensincera_adsystems", len(df))
    return len(df)


def refresh_opensincera_modules() -> int:
    """Pull /mapping_modules and replace the table."""
    logger.info("Refreshing opensincera_modules")
    client = OpenSinceraClient()
    df = client.get_mapping_modules()
    if df.empty:
        logger.warning("OpenSincera modules came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    with _engine().begin() as conn:
        _safe_replace(df, "opensincera_modules", conn)
    logger.info("Wrote %d rows to opensincera_modules", len(df))
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

    # --mode=direct → only refresh GAM direct campaigns (gam_campaigns).
    # Used by refresh_direct.yml on its intra-day fires (11 AM + 3 PM ET)
    # so dashboard users get fresh direct-campaign delivery without
    # re-pulling the slower PMP / Magnite / Pubmatic feeds. Default mode
    # is the full sweep — what refresh.yml runs at 5 AM ET.
    mode = "all"
    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1].strip().lower()
    if mode not in ("all", "direct", "opensincera", "deal-metadata"):
        logger.error("Unknown --mode=%s (use 'all', 'direct', 'opensincera', or 'deal-metadata')", mode)
        raise SystemExit(2)
    logger.info("refresh_cache v3 — mode=%s", mode)

    migrate_table_names()

    if mode == "direct":
        total = 0
        try:
            total += refresh_gam()
        except Exception:
            logger.exception("Refresh failed for gam_campaigns")
        logger.info("Done (direct-only). %d rows written.", total)
        return

    if mode == "opensincera":
        total = 0
        for fn in (
            refresh_opensincera_ecosystem,
            refresh_opensincera_publishers,
            refresh_opensincera_adsystems,
            refresh_opensincera_modules,
        ):
            try:
                total += fn()
            except Exception:
                logger.exception("Refresh failed for %s — continuing", fn.__name__)
        logger.info("Done (opensincera-only). %d rows written.", total)
        return

    if mode == "deal-metadata":
        total = 0
        for fn in (refresh_magnite_deal_metadata, refresh_pubmatic_deal_metadata):
            try:
                total += fn()
            except Exception:
                logger.exception("Refresh failed for %s — continuing", fn.__name__)
        logger.info("Done (deal-metadata). %d rows written.", total)
        return

    # Full sweep below — everything in dependency-independent order.
    logger.info("refresh_cache v3 — Magnite date_range=%s", next(iter(REPORTS.values()))["date_range"])
    api_key    = os.environ["MAGNITE_KEY"]
    api_secret = os.environ["MAGNITE_SECRET"]
    account_id = os.environ["MAGNITE_PUBLISHER_ID"]

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
        total += refresh_gam_deal_bids()
    except Exception:
        logger.exception("Refresh failed for gam_deal_bid_daily — continuing")

    try:
        total += refresh_gam_private_auctions()
    except Exception:
        logger.exception("Refresh failed for gam_pa_metadata — continuing")

    try:
        total += refresh_gam_preferred_deals()
    except Exception:
        logger.exception("Refresh failed for gam_pd_metadata — continuing")

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

    try:
        total += refresh_opensincera_ecosystem()
    except Exception:
        logger.exception("Refresh failed for opensincera_ecosystem — continuing")

    try:
        total += refresh_opensincera_publishers()
    except Exception:
        logger.exception("Refresh failed for opensincera_publishers — continuing")

    try:
        total += refresh_opensincera_adsystems()
    except Exception:
        logger.exception("Refresh failed for opensincera_adsystems — continuing")

    try:
        total += refresh_opensincera_modules()
    except Exception:
        logger.exception("Refresh failed for opensincera_modules — continuing")

    try:
        total += refresh_dv_attention()
    except Exception:
        logger.exception("Refresh failed for dv_attention — continuing")

    try:
        total += refresh_dv_ivt()
    except Exception:
        logger.exception("Refresh failed for dv_ivt — continuing")


if __name__ == "__main__":
    main()
