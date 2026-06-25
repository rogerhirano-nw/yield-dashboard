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

import json
import logging
import os
import re
import sys
import time
import urllib.request
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
from ttd_client import pull_ttd, CHUMBA_SUBJECT_NEEDLE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class _IssueCollector(logging.Handler):
    """Collects WARNING+ log records emitted during a sweep for post-sweep alerting."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _run_with_alert(mode_label: str, callables: list) -> int:
    """Run each callable in sequence, catch exceptions per-callable so one
    failure doesn't abort the rest. Sends an email alert when any WARNING+
    log records are emitted. Returns total row count."""
    collector = _IssueCollector()
    logging.getLogger().addHandler(collector)
    total = 0
    for fn in callables:
        try:
            total += fn() or 0
        except Exception:
            logger.exception("Refresh failed for %s — continuing",
                             getattr(fn, "__name__", repr(fn)))
    logging.getLogger().removeHandler(collector)
    if collector.records:
        _send_sweep_alert(collector.records, total)
    logger.info("Done (%s). %d rows written.", mode_label, total)
    return total


_ENGINE: sqlalchemy.Engine | None = None


def _probe_connect_with_retry(engine: sqlalchemy.Engine,
                              attempts: int = 4, base_sleep: int = 10) -> None:
    """Probe DB connectivity, retrying transient pooler failures.

    All six sweep jobs open connections at 09:00 UTC sharp, and the Supabase
    pooler occasionally times out an initial connect under that stampede
    (2026-06-11: both pooler IPs returned 'timeout expired' and the gam job
    died in 53s). pool_pre_ping only revalidates already-established
    connections — the first connect needs its own retry."""
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except sqlalchemy.exc.OperationalError as exc:
            if attempt == attempts:
                raise
            sleep_s = base_sleep * attempt
            # INFO, not WARNING — recovered retries shouldn't email; the
            # final attempt re-raises and surfaces as ERROR.
            logger.info(
                "DB connect failed (attempt %d/%d): %s — retrying in %ds",
                attempt, attempts, exc, sleep_s,
            )
            time.sleep(sleep_s)


def _engine() -> sqlalchemy.Engine:
    global _ENGINE
    if _ENGINE is None:
        # pool_pre_ping: validate before checkout — protects against stale
        # connections the Supabase pooler may have already closed on its end.
        # pool_recycle=300: actively drop+remake connections older than 5 min
        # so the pooler doesn't time them out from under us mid-query.
        # pool_size + max_overflow kept small because refresh runs are
        # serial — never need more than 1 connection at a time. Default
        # (5 + 10 = 15) was hoarding pooler slots needlessly and racing
        # the cap_digest + dashboard for capacity.
        _ENGINE = sqlalchemy.create_engine(
            os.environ["DATABASE_URL"],
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=2,
            max_overflow=0,
            connect_args={
                "connect_timeout": 10,
                "options": "-c statement_timeout=120000",
            },
        )
        _probe_connect_with_retry(_ENGINE)
    return _ENGINE


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


# (table, index_name, column_expression)
_INDEXES = [
    ("magnite_site_daily",    "idx_magnite_site_daily_date",      '"date"'),
    ("magnite_dsp_daily",     "idx_magnite_dsp_daily_date",       '"date"'),
    ("magnite_deal_daily",    "idx_magnite_deal_daily_date",      '"date"'),
    ("gam_campaigns",         "idx_gam_campaigns_report_start",   "report_start"),
    ("gam_campaigns",         "idx_gam_campaigns_pulled_at",      "_pulled_at"),
    ("gam_campaigns_hourly",  "idx_gam_campaigns_hourly_date_li", '"date", line_item_id'),
    ("gam_campaigns_weekly",  "idx_gam_campaigns_weekly_ws_li",   'week_start, line_item_id'),
    ("pubmatic_deals",        "idx_pubmatic_deals_date",          '"date"'),
    ("pubmatic_deals",        "idx_pubmatic_deals_deal_meta_id",  "deal_meta_id"),
    ("pubmatic_deals",        "idx_pubmatic_deals_deal",          "deal"),
    ("dv_attention",          "idx_dv_attention_date",            '"date"'),
    ("dv_ivt",                "idx_dv_ivt_date",                  '"date"'),
    ("ttd_luckyland",         "idx_ttd_luckyland_date",           '"date"'),
    ("ttd_chumba",            "idx_ttd_chumba_date",              '"date"'),
    ("opensincera_ecosystem", "idx_opensincera_ecosystem_date",   '"date"'),
    ("gam_deal_bid_daily",    "idx_gam_deal_bid_daily_date",      '"date"'),
    ("gam_pmp_deals",         "idx_gam_pmp_deals_date",           '"date"'),
    # Join/filter columns on metadata tables and LICA/creatives
    ("gam_lica",              "idx_gam_lica_creative_id",         "creative_id"),
    ("gam_lica",              "idx_gam_lica_line_item_id",        "line_item_id"),
    ("gam_creatives",         "idx_gam_creatives_creative_id",    "creative_id"),
    ("pubmatic_deal_metadata","idx_pubmatic_deal_metadata_deal",  "deal"),
    ("gam_pa_metadata",       "idx_gam_pa_metadata_auction_name", "auction_name"),
    ("gam_pd_metadata",       "idx_gam_pd_metadata_deal_name",    "deal_name"),
]


# Matches the IO/SO order-reference token in a line item name when the name is
# split on "_": IO1104-6, IO1104_8, IO1104, SO01104, SO01090, IO1109, etc.
_ORDER_TOKEN_RE = re.compile(r'^(?:IO|SO)0?\d+(?:[-_]\d+)?$', re.IGNORECASE)


def _strip_order_token(name: str) -> str:
    """Remove the IO/SO order-reference token from a split-on-underscore name."""
    return "_".join(p for p in name.split("_") if not _ORDER_TOKEN_RE.match(p))


def _warn_dv_gam_mismatches(df: pd.DataFrame, label: str) -> None:
    """Validate DV line items against gam_campaigns; auto-correct names where needed.

    Preferred path (when DV exports line_item_id):
      Join by numeric ID — immune to name changes. Warns for any DV ID not found
      in gam_campaigns. Non-Direct lines (PD/PG/PMP) are silently skipped since
      they join via order_name on the PMP tab.

    Fallback path (line_item_id absent):
      Name-based validation with order-token auto-correction.
      Strips IO/SO order-reference tokens from both sides to handle name drift
      (e.g. SO01104 → IO1104-6). Modifies df in-place so corrected names are
      written to DB. Non-Direct lines silently skipped."""
    has_id = "line_item_id" in df.columns and df["line_item_id"].notna().any()

    try:
        with _engine().connect() as conn:
            if has_id:
                gam_ids = {
                    str(r[0]) for r in conn.execute(text(
                        "SELECT DISTINCT line_item_id FROM gam_campaigns "
                        "WHERE line_item_id IS NOT NULL"
                    ))
                }
            else:
                gam_names = {
                    str(r[0]) for r in conn.execute(text(
                        "SELECT DISTINCT line_item_name FROM gam_campaigns "
                        "WHERE line_item_name IS NOT NULL"
                    ))
                }
    except Exception:
        logger.warning("[%s] Could not query gam_campaigns for DV join validation", label)
        return

    if has_id:
        # ID-based validation — robust, no name mangling needed
        dv_ids = set(df["line_item_id"].dropna().astype(str).unique())
        for dv_id in sorted(dv_ids - gam_ids):
            # Look up the name for a more useful warning message
            name = df.loc[df["line_item_id"].astype(str) == dv_id, "line_item_name"].iloc[0] \
                if "line_item_name" in df.columns else dv_id
            if not str(name).startswith("Newsweek_Direct_"):
                continue
            logger.warning(
                "[%s] DV line item ID %s (%r) has no GAM match — will show '—' in dashboard",
                label, dv_id, name,
            )
        return

    # ── Fallback: name-based with order-token auto-correction ──────────────
    if "line_item_name" not in df.columns:
        return

    gam_by_norm: dict[str, str | None] = {}
    for gam_name in gam_names:  # type: ignore[possibly-undefined]
        norm = _strip_order_token(gam_name)
        if not norm:
            continue
        if norm not in gam_by_norm:
            gam_by_norm[norm] = gam_name
        elif gam_by_norm[norm] != gam_name:
            gam_by_norm[norm] = None  # ambiguous — two GAM names share the same key

    fixes: dict[str, str] = {}
    for dv_name in sorted(set(df["line_item_name"].dropna().unique()) - gam_names):
        dv_name = str(dv_name)
        if not dv_name.startswith("Newsweek_Direct_"):
            continue
        norm = _strip_order_token(dv_name)
        candidate = gam_by_norm.get(norm) if norm else None
        if candidate is not None:
            fixes[dv_name] = candidate
            logger.info(
                "[%s] Auto-correcting DV name (order-token drift): %r → %r",
                label, dv_name, candidate,
            )
        else:
            logger.warning(
                "[%s] DV line item has no GAM match — will show '—' in dashboard: %r",
                label, dv_name,
            )

    if fixes:
        df["line_item_name"] = df["line_item_name"].replace(fixes)


def _ensure_indexes() -> None:
    """Create missing BTree indexes on date/filter columns. Idempotent — skips
    tables that don't exist yet and is a no-op when indexes are already present."""
    with _engine().begin() as conn:
        existing_tables = set(sa_inspect(conn).get_table_names())
        for table, idx_name, cols in _INDEXES:
            if table in existing_tables:
                conn.execute(text(
                    f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{table}" ({cols})'
                ))
    logger.info("Index check complete")
    return 0


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
        # 14-day window powers the dashboard's week-vs-week spend momentum (the
        # PMP summary windows itself back to 7 days). Explicit window via
        # window_days; retention 15 = 14 + 1 keeps the replace duplicate-free.
        # The other Magnite reports keep their last_7 preset + default-8
        # retention, untouched by this report's wider window.
        "window_days": 14,
        "retention_days": 15,
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
    """Pull a single report and write it to its own table. Returns row count.

    **Per-report retention.** Two optional, non-API config keys tune the date
    window independently per report (popped before the API call):
      - ``window_days``: pull an explicit N-day window via start/end instead of
        a ``date_range`` preset (Magnite rejects bare YYYY-MM-DD, so the dates
        are sent as ISO-8601 with timezone). Lets one report carry 14 days
        without changing the others.
      - ``retention_days``: the DELETE cutoff (default 8). The no-duplicate
        invariant is ``retention_days == pull_window + 1`` — the DELETE must
        clear yesterday's oldest row so the fresh pull replaces the window
        cleanly. A 14-day pull therefore sets ``retention_days=15``. Widening
        one report's window no longer risks duplicating rows in the others,
        which keep the default 8 (7-day pull + 1)."""
    logger.info("Refreshing %s", table)
    cfg = dict(config)  # copy — we pop non-API keys, leave the caller's dict intact
    retention_days = cfg.pop("retention_days", 8)
    window_days    = cfg.pop("window_days", None)

    if window_days is not None:
        # Explicit window via start/end (Magnite requires ISO-8601 + tz, not
        # bare dates). date_range must be None when start/end are set.
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        start     = yesterday - timedelta(days=window_days - 1)
        cfg.pop("date_range", None)
        df = client.run_report(
            date_range=None,
            start=f"{start.isoformat()}T00:00:00Z",
            end=f"{yesterday.isoformat()}T23:59:59Z",
            **cfg,
        )
    else:
        df = client.run_report(**cfg)

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
                cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
                conn.execute(text(f'DELETE FROM "{table}" WHERE date >= :cutoff'), {"cutoff": cutoff})
            else:
                # Lookup table with no date column — TRUNCATE is cheaper than
                # DELETE for a full-table clear (single WAL record, no dead tuples).
                conn.execute(text(f'TRUNCATE TABLE "{table}"'))
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

    # Attach the programmatic DEAL_ID per LI (separate report — DEAL_ID is
    # incompatible with the delivery report's metric set). GAM's DEAL_ID equals
    # the TTD feed's deal_id for our PG flights, so the gambling CPA join keys
    # LINE_ITEM ↔ TTD by deal id instead of brittle name tokens. Left-merge
    # keeps every campaigns row; non-deal LIs get deal_id = NA.
    if "deal_id" not in df.columns:
        try:
            deal_map = gam.run_li_deal_map_report(seven_days_ago, yesterday)
        except Exception:
            logger.exception("deal map report failed — gam_campaigns.deal_id left empty")
            deal_map = pd.DataFrame(columns=["line_item_id", "deal_id"])
        df["line_item_id"] = df["line_item_id"].astype(str)
        if not deal_map.empty:
            df = df.merge(deal_map, on="line_item_id", how="left")
        else:
            df["deal_id"] = pd.NA

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


def refresh_gam_hourly() -> int:
    """Pull today's hourly GAM delivery for line items with weekly budget caps.

    Line item IDs are read from the GAM_HOURLY_LINE_ITEMS environment variable
    (comma-separated string). Only those IDs are queried and stored. Skips
    silently if the variable is unset.

    Writes to gam_campaigns_hourly (date, hour, line_item_id, ad_server_impressions,
    pulled_at). Upserts by deleting today's rows for these LIs then re-inserting
    so re-runs throughout the day always reflect the latest intraday delivery.
    """
    import os
    li_ids_raw = os.environ.get("GAM_HOURLY_LINE_ITEMS", "").strip()
    if not li_ids_raw:
        logger.info("GAM_HOURLY_LINE_ITEMS not set — skipping hourly refresh")
        return 0

    li_ids = [x.strip() for x in li_ids_raw.split(",") if x.strip()]
    # Use ET date to match what the cap digest queries — the 7:30 PM ET refresh
    # runs at 23:30 UTC (still May 29 ET) and must write the same date the
    # 8 PM cap digest (= 00:00 UTC = May 30 UTC) will query.
    from zoneinfo import ZoneInfo
    today = datetime.now(tz=ZoneInfo("America/New_York")).date()
    logger.info("Refreshing gam_campaigns_hourly for LIs %s, date=%s (ET)", li_ids, today)

    gam = GAMClient()
    df = gam.run_hourly_report(today, li_ids)
    if df.empty:
        logger.info("No hourly data returned for date=%s", today)
        return 0

    df["pulled_at"] = datetime.now(timezone.utc).isoformat()
    table = "gam_campaigns_hourly"
    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            conn.execute(
                text(f"DELETE FROM \"{table}\" WHERE date = :d AND line_item_id::text = ANY(:ids)"),
                {"d": today.isoformat(), "ids": li_ids},
            )
        df.to_sql(table, conn, if_exists="append", index=False)
    logger.info("Wrote %d hourly rows to %s", len(df), table)
    return len(df)


def refresh_gam_weekly() -> int:
    """Pull ~5 weeks of daily GAM delivery for cap-tracked LIs, bucket by week.

    Reads the same GAM_HOURLY_LINE_ITEMS env var (the cap-tracked LIs). Pulls
    the last 35 days of DATE × LINE_ITEM_ID impressions, buckets each date into
    a Monday-anchored ISO week, and upserts the per-(LI, week_start) impression
    totals into gam_campaigns_weekly. Powers the "Last 4 weeks" summary in the
    seller-comms cap digest.

    Skips silently when GAM_HOURLY_LINE_ITEMS is unset.
    """
    import os
    from datetime import timedelta as _td
    li_ids_raw = os.environ.get("GAM_HOURLY_LINE_ITEMS", "").strip()
    if not li_ids_raw:
        logger.info("GAM_HOURLY_LINE_ITEMS not set — skipping weekly refresh")
        return 0

    li_ids = [x.strip() for x in li_ids_raw.split(",") if x.strip()]
    from zoneinfo import ZoneInfo
    today = datetime.now(tz=ZoneInfo("America/New_York")).date()
    start = today - _td(days=35)
    logger.info("Refreshing gam_campaigns_weekly for LIs %s, %s..%s (ET)", li_ids, start, today)

    gam = GAMClient()
    df = gam.run_daily_li_report(start, today, li_ids)
    if df.empty:
        logger.info("No daily data returned for weekly history")
        return 0

    # Bucket each date into its Monday-anchored week_start, sum impressions.
    df["_d"] = pd.to_datetime(df["date"]).dt.date
    df["week_start"] = df["_d"].map(lambda d: (d - _td(days=d.weekday())).isoformat())
    agg = (
        df.groupby(["line_item_id", "week_start"], as_index=False)["ad_server_impressions"]
        .sum()
    )
    agg["pulled_at"] = datetime.now(timezone.utc).isoformat()

    table = "gam_campaigns_weekly"
    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            # Replace all rows for these LIs in the pulled window (re-derive fully each run).
            conn.execute(
                text(f"DELETE FROM \"{table}\" WHERE line_item_id::text = ANY(:ids) AND week_start >= :ws"),
                {"ids": li_ids, "ws": (start - _td(days=start.weekday())).isoformat()},
            )
        agg.to_sql(table, conn, if_exists="append", index=False)
    logger.info("Wrote %d weekly rows to %s", len(agg), table)
    return len(agg)


def refresh_gam_pmp_deals() -> int:
    """Pull GAM PMP deal data (PA / PD / PG) by deal name and write to gam_pmp_deals.

    Pulls a 14-day window so the dashboard can compute week-vs-week spend
    momentum; the PMP summary windows itself back to 7 days. Written via
    _safe_replace (full TRUNCATE+append), so the table holds exactly what the
    report returns — widening the window can't duplicate rows."""
    logger.info("Refreshing gam_pmp_deals (GAM deals report)")
    gam = GAMClient()

    yesterday         = datetime.now(timezone.utc).date() - timedelta(days=1)
    fourteen_days_ago = yesterday - timedelta(days=13)  # 14-day inclusive window

    df = gam.run_deals_report(fourteen_days_ago, yesterday)
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


def refresh_gam_lica() -> int:
    """Incremental: append LICAs only for video line items not yet in gam_lica.

    Scoped to inventory_format_name = 'In-stream video' — the only format
    where creative duration matters (Video Preroll >30s recategorization).
    Never truncates; only new line item IDs trigger a GAM SOAP call."""
    logger.info("Refreshing gam_lica (incremental, video only)")
    with _engine().connect() as conn:
        try:
            video_li_ids = {
                str(r[0]) for r in conn.execute(text(
                    "SELECT DISTINCT line_item_id FROM gam_campaigns "
                    "WHERE inventory_format_name = 'In-stream video'"
                ))
            }
        except Exception:
            logger.warning("Could not query gam_campaigns for video LIs — skipping")
            return 0
        if not video_li_ids:
            logger.info("No in-stream video line items in gam_campaigns")
            return 0
        try:
            known_li_ids = {
                str(r[0]) for r in conn.execute(text(
                    "SELECT DISTINCT line_item_id FROM gam_lica"
                ))
            }
        except Exception:
            known_li_ids = set()

    new_li_ids = video_li_ids - known_li_ids
    if not new_li_ids:
        logger.info("gam_lica up to date — all %d video LIs already present", len(video_li_ids))
        return 0

    logger.info("Fetching LICAs for %d new video LIs (%d already known)",
                len(new_li_ids), len(known_li_ids & video_li_ids))
    gam = GAMClient()
    df = gam.list_line_item_creative_associations(line_item_ids=list(new_li_ids))
    if df.empty:
        logger.warning("No LICAs returned for new video LIs")
        return 0
    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    with _engine().begin() as conn:
        df.to_sql("gam_lica", conn, if_exists="append", index=False)
    logger.info("Appended %d LICAs to gam_lica", len(df))
    return len(df)


def refresh_gam_creatives() -> int:
    """Incremental: append creative metadata only for creative IDs in gam_lica
    not yet in gam_creatives. Only video-associated creatives are ever in
    gam_lica (refresh_gam_lica is scoped to In-stream video LIs)."""
    logger.info("Refreshing gam_creatives (incremental)")
    with _engine().connect() as conn:
        try:
            lica_creative_ids = {
                str(r[0]) for r in conn.execute(text(
                    "SELECT DISTINCT creative_id FROM gam_lica "
                    "WHERE creative_id IS NOT NULL"
                ))
            }
        except Exception:
            logger.warning("Could not query gam_lica for creative IDs — skipping")
            return 0
        if not lica_creative_ids:
            logger.info("No creative IDs in gam_lica yet")
            return 0
        try:
            known_creative_ids = {
                str(r[0]) for r in conn.execute(text(
                    "SELECT DISTINCT creative_id FROM gam_creatives"
                ))
            }
        except Exception:
            known_creative_ids = set()

    new_creative_ids = lica_creative_ids - known_creative_ids
    if not new_creative_ids:
        logger.info("gam_creatives up to date — all %d creatives already present",
                    len(lica_creative_ids))
        return 0

    logger.info("Fetching %d new creatives (%d already known)",
                len(new_creative_ids), len(known_creative_ids & lica_creative_ids))
    gam = GAMClient()
    df = gam.list_creatives_with_duration(creative_ids=list(new_creative_ids))
    if df.empty:
        logger.warning("No creative metadata returned for new creative IDs")
        return 0
    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    with _engine().begin() as conn:
        df.to_sql("gam_creatives", conn, if_exists="append", index=False)
    logger.info("Appended %d rows to gam_creatives", len(df))
    return len(df)


def refresh_magnite() -> int:
    """Pull all four standard Magnite reports (site, DSP, deal, demand)."""
    logger.info("Refreshing Magnite reports")
    api_key    = os.environ["MAGNITE_KEY"]
    api_secret = os.environ["MAGNITE_SECRET"]
    account_id = os.environ["MAGNITE_PUBLISHER_ID"]
    client = MagniteClient(api_key=api_key, api_secret=api_secret, account_id=account_id)
    total = 0
    for table, config in REPORTS.items():
        try:
            total += refresh_one_report(client, table, config)
        except Exception:
            logger.exception("Refresh failed for %s — continuing", table)
    return total


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


def refresh_pmp_last_bid_date() -> int:
    """Upsert pmp_last_bid_date with the latest bid-activity date per deal per SSP.

    Cumulative: last_bid_date only ever moves forward. first_seen_date is set
    on the first insert and never changed. Deals with no bid responses in the
    current 7-day window keep their previously recorded last_bid_date.
    """
    logger.info("Refreshing pmp_last_bid_date")
    engine = _engine()
    today_str = datetime.now(timezone.utc).date().isoformat()
    now_ts    = datetime.now(timezone.utc).isoformat()

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pmp_last_bid_date (
                ssp             TEXT NOT NULL,
                deal_key        TEXT NOT NULL,
                last_bid_date   TEXT,
                last_seen_date  TEXT,
                first_seen_date TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                PRIMARY KEY (ssp, deal_key)
            )
        """))
        # last_seen_date (added 2026-06): the last day the deal appeared in ANY
        # source row, bid or not. Lets the stale-deals list separate deals that
        # stopped being reported entirely (paused/removed → hide) from deals
        # still live but not winning bids (actionable → keep). Add the column on
        # existing tables and seed it from each row's last activity so old rows
        # aren't all treated as "never seen"; the upsert below then bumps any
        # deal present in the current window up to today's data.
        conn.execute(text(
            "ALTER TABLE pmp_last_bid_date ADD COLUMN IF NOT EXISTS last_seen_date TEXT"))
        conn.execute(text(
            "UPDATE pmp_last_bid_date SET last_seen_date = "
            "COALESCE(last_bid_date, first_seen_date) WHERE last_seen_date IS NULL"))

    parts: list[pd.DataFrame] = []

    def _query(sql: str, ssp: str) -> None:
        try:
            with engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)
            df["ssp"] = ssp
            parts.append(df)
        except Exception:
            logger.warning("pmp_last_bid_date: could not query %s source — skipping", ssp)

    _query("""
        SELECT
            CAST(deal_meta_id AS TEXT) AS deal_key,
            MAX(CASE WHEN non_zero_bid_responses > 0 THEN date ELSE NULL END) AS new_last_bid_date,
            MAX(date) AS new_last_seen_date
        FROM pubmatic_deals
        WHERE deal_meta_id IS NOT NULL
        GROUP BY deal_meta_id
    """, "Pubmatic")

    _query("""
        SELECT
            CAST(deal_id AS TEXT) AS deal_key,
            MAX(CASE WHEN bid_responses > 0 THEN date ELSE NULL END) AS new_last_bid_date,
            MAX(date) AS new_last_seen_date
        FROM magnite_deal_daily
        WHERE deal_id IS NOT NULL
        GROUP BY deal_id
    """, "Magnite")

    _query("""
        SELECT
            programmatic_deal_name AS deal_key,
            MAX(CASE WHEN deals_bids > 0 THEN date ELSE NULL END) AS new_last_bid_date,
            MAX(date) AS new_last_seen_date
        FROM gam_deal_bid_daily
        WHERE programmatic_deal_name IS NOT NULL
        GROUP BY programmatic_deal_name
    """, "GAM")

    if not parts:
        logger.warning("pmp_last_bid_date: no source tables available — skipping")
        return 0

    new_df = pd.concat(parts, ignore_index=True)
    records = new_df.rename(columns={"new_last_bid_date": "last_bid_date",
                                     "new_last_seen_date": "last_seen_date"}).copy()
    # Normalise SQL NULL / Python None / "None" / "nan" → None so SQLAlchemy
    # passes SQL NULL to GREATEST() (GREATEST(NULL, x) = x).
    for _c in ("last_bid_date", "last_seen_date"):
        records[_c] = records[_c].astype(object)
        records[_c] = records[_c].where(records[_c].notna(), other=None)
    records["first_seen_date"] = today_str
    records["updated_at"] = now_ts

    # ON CONFLICT keeps last_bid_date / last_seen_date monotonically
    # non-decreasing (GREATEST handles NULLs: GREATEST(NULL, x) = x).
    # first_seen_date is excluded from DO UPDATE — set once on first insert.
    upsert_sql = text("""
        INSERT INTO pmp_last_bid_date (ssp, deal_key, last_bid_date, last_seen_date, first_seen_date, updated_at)
        VALUES (:ssp, :deal_key, :last_bid_date, :last_seen_date, :first_seen_date, :updated_at)
        ON CONFLICT (ssp, deal_key) DO UPDATE SET
            last_bid_date = GREATEST(EXCLUDED.last_bid_date, pmp_last_bid_date.last_bid_date),
            last_seen_date = GREATEST(EXCLUDED.last_seen_date, pmp_last_bid_date.last_seen_date),
            updated_at = EXCLUDED.updated_at
    """)
    with engine.begin() as conn:
        conn.execute(
            upsert_sql,
            records[["ssp", "deal_key", "last_bid_date", "last_seen_date", "first_seen_date", "updated_at"]].to_dict("records"),
        )

    n_with_history = int(records["last_bid_date"].notna().sum())
    logger.info("pmp_last_bid_date: %d deals upserted, %d with bid history",
                len(records), n_with_history)
    return len(records)


def refresh_pubmatic() -> int:
    """Pull Pubmatic PMP deal data for the last 14 days and write to pubmatic_deals.

    14-day window powers the dashboard's week-vs-week spend momentum; the PMP
    summary windows itself back to 7 days. Retention (DELETE cutoff) = pull
    window + 1 day so the table replaces cleanly without duplicating rows."""
    logger.info("Refreshing pubmatic_deals (Pubmatic)")
    client = PubmaticClient()

    yesterday         = datetime.now(timezone.utc).date() - timedelta(days=1)
    fourteen_days_ago = yesterday - timedelta(days=13)  # 14-day inclusive window

    df = client.run_deal_report(fourteen_days_ago, yesterday)
    if df.empty:
        logger.warning("Pubmatic report came back empty — nothing to write")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()

    table  = "pubmatic_deals"
    # Retention = 14-day pull + 1 day = 15, so the DELETE clears yesterday's
    # oldest row and the new pull replaces the window with no duplicates.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=15)).strftime("%Y-%m-%d")

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

    # Pull only the 2 most recent CSV attachments instead of the default
    # 30. Each DV CSV covers a rolling 7-day window with daily overlap, so
    # 2 emails ≈ 8 days of fresh coverage — plenty for the dashboard (no
    # view shows >7d of DV data). The original default-30 pull processed
    # 12 emails × ~13k rows = 160k rows per refresh and DELETE+INSERT-ed
    # the entire rolling window daily (the 2026-06-07 disk-IO incident);
    # limit=2 is ~5× less IO. That incident was blamed on the "Nano tier"
    # budget at the time — the instance is actually Micro on Pro, so the
    # budget is roomier than feared, but rewriting rows no view renders
    # is waste at any size, so limit=2 stays. Historical rows for dates
    # NOT in the current pull stay untouched.
    df = pull_dv_attention(api_key, inbox_id, limit=2)
    if df.empty:
        logger.warning("No DV Attention CSV attachments found in inbox")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    _warn_dv_gam_mismatches(df, "dv_attention")

    table = "dv_attention"
    with _engine().begin() as conn:
        _safe_replace(df, table, conn)

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

    # Same IO-budget protection as refresh_dv_attention — see comment there.
    df = pull_dv_ivt(api_key, inbox_id, limit=2)
    if df.empty:
        logger.warning("No DV IVT CSV attachments found in inbox")
        return 0

    df["_pulled_at"] = datetime.now(timezone.utc).isoformat()
    _warn_dv_gam_mismatches(df, "dv_ivt")

    table = "dv_ivt"
    with _engine().begin() as conn:
        _safe_replace(df, table, conn)

    logger.info("Wrote %d rows to %s", len(df), table)
    return len(df)


def _refresh_ttd_campaign(
    subject_needle: str,
    table: str,
    primary_conv_col: str | None = None,
) -> int:
    """Poll newsweek@agentmail.to for a TTD report whose subject contains
    *subject_needle*, download the XLSX/CSV, and upsert into *table*.

    Rolls a 30-day window: deletes existing rows for every date present in the
    new batch then re-inserts, so the table stays current while retaining older
    history.  Schema changes (column set drift) auto-recreate the table.

    Skips silently (returns 0) when agentmail credentials aren't set.

    `primary_conv_col` — forwarded to `pull_ttd`/`parse_ttd_csv`; sets the
    single authoritative conversion KPI column for this campaign.
    """
    logger.info("Refreshing TTD report: table=%s needle=%r", table, subject_needle)
    api_key  = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    if not api_key or not inbox_id:
        logger.warning(
            "AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID not set — "
            "skipping TTD report refresh (%s)", table,
        )
        return 0

    df, meta = pull_ttd(
        api_key, inbox_id,
        subject_needle=subject_needle,
        primary_conv_col=primary_conv_col,
    )
    if df.empty:
        logger.warning(
            "No TTD report found for %s (exec_id=%s)", table, meta.get("execution_id")
        )
        return 0

    df["_pulled_at"]    = datetime.now(timezone.utc).isoformat()
    df["_execution_id"] = df.get("_execution_id", meta.get("execution_id"))

    with _engine().begin() as conn:
        if table in sa_inspect(conn).get_table_names():
            existing_cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
            if existing_cols != set(df.columns):
                logger.info("Schema change for %s — dropping and recreating", table)
                conn.execute(text(f'DROP TABLE "{table}"'))
            elif "date" in df.columns:
                dates = [
                    d.isoformat() if d is not None else None
                    for d in df["date"].dropna().unique().tolist()
                ]
                if dates:
                    conn.execute(
                        text(f'DELETE FROM "{table}" WHERE date::text = ANY(:dates)'),
                        {"dates": dates},
                    )
        df.to_sql(table, conn, if_exists="append", index=False)

    logger.info(
        "Wrote %d rows to %s (exec_id=%s, %d dates)",
        len(df), table,
        meta.get("execution_id"),
        df["date"].nunique() if "date" in df.columns else 0,
    )
    return len(df)


def refresh_ttd() -> int:
    """Poll for TTD Luckyland Casino report and upsert into ttd_luckyland."""
    from ttd_client import TTD_SUBJECT_NEEDLE
    # Luckyland KPI = "usergenLLC Purchase [IdentityAlliance]" — the authoritative
    # acquisition pixel (26 June conversions, CPA $346), matching the manually-
    # produced report methodology.  The automated TTD scheduled report must include
    # this column; if it's absent the parser logs a WARNING and falls back to the
    # conversion auto-sum until TTD adds it to the report configuration.
    return _refresh_ttd_campaign(
        TTD_SUBJECT_NEEDLE, "ttd_luckyland",
        primary_conv_col="usergenLLC Purchase [IdentityAlliance] - Total Click + View Conversions",
    )


def refresh_ttd_chumba() -> int:
    """Poll for VGW Chumba Casino TTD report and upsert into ttd_chumba."""
    # Chumba KPI = pixel 01 (registrations) only, not pixel_01 + pixel_03.
    return _refresh_ttd_campaign(
        CHUMBA_SUBJECT_NEEDLE, "ttd_chumba",
        primary_conv_col="01 - Total Click + View Conversions",
    )


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


def _send_sweep_alert(records: list[logging.LogRecord], total_rows: int) -> None:
    """Email a concise summary of WARNING/ERROR records to REFRESH_ALERT_TO.
    Silently skips if credentials or recipient are not configured."""
    api_key  = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    to_addr  = os.environ.get("REFRESH_ALERT_TO")
    if not (api_key and inbox_id and to_addr):
        return

    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"refresh sweep — {len(records)} issue(s) — {today}"
    lines   = [f"  {r.levelname:<8}  {r.getMessage()}" for r in records]
    body    = (
        f"The daily refresh sweep completed with {len(records)} issue(s):\n\n"
        + "\n".join(lines)
        + f"\n\n{total_rows:,} rows written total.\n"
    )
    payload = {"to": [to_addr], "subject": subject, "text": body}
    req = urllib.request.Request(
        f"https://api.agentmail.to/v0/inboxes/{inbox_id}/messages/send",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            logger.info("Sweep alert sent to %s", to_addr)
    except Exception:
        logger.exception("Failed to send sweep alert")


def main() -> None:
    _load_dotenv()

    mode = "all"
    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1].strip().lower()

    _VALID_MODES = (
        "all", "direct", "opensincera", "deal-metadata", "gam_hourly",
        "dv", "magnite", "gam", "gam-lica", "pubmatic", "post-sweep", "ttd", "ttd-chumba",
    )
    if mode not in _VALID_MODES:
        logger.error("Unknown --mode=%s  valid: %s", mode, ", ".join(_VALID_MODES))
        raise SystemExit(2)
    logger.info("refresh_cache — mode=%s", mode)

    migrate_table_names()

    # ── intraday / ad-hoc single-source modes ──────────────────────────────

    if mode == "direct":
        # GAM direct-only; used by refresh_direct.yml at 11 AM + 3 PM ET.
        _run_with_alert("direct", [refresh_gam])
        return

    if mode == "gam_hourly":
        _run_with_alert("gam_hourly", [refresh_gam_hourly, refresh_gam_weekly])
        return

    if mode == "dv":
        _run_with_alert("dv", [refresh_dv_attention, refresh_dv_ivt])
        return

    if mode == "ttd":
        _run_with_alert("ttd", [refresh_ttd])
        return

    if mode == "ttd-chumba":
        _run_with_alert("ttd-chumba", [refresh_ttd_chumba])
        return

    if mode == "opensincera":
        _run_with_alert("opensincera", [
            refresh_opensincera_ecosystem,
            refresh_opensincera_publishers,
            refresh_opensincera_adsystems,
            refresh_opensincera_modules,
        ])
        return

    if mode == "deal-metadata":
        _run_with_alert("deal-metadata", [
            refresh_magnite_deal_metadata,
            refresh_pubmatic_deal_metadata,
        ])
        return

    # ── parallel-sweep modes (one GitHub Actions job each) ─────────────────

    if mode == "magnite":
        _run_with_alert("magnite", [refresh_magnite, refresh_magnite_deal_metadata])
        return

    if mode == "gam":
        # All GAM except LICA (which is the slow full-table fetch).
        _run_with_alert("gam", [
            refresh_gam,
            refresh_gam_hourly,
            refresh_gam_weekly,
            refresh_gam_pmp_deals,
            refresh_gam_deal_bids,
            refresh_gam_private_auctions,
            refresh_gam_preferred_deals,
        ])
        return

    if mode == "gam-lica":
        # Slow full-table pulls; run in its own job so it can't delay others.
        _run_with_alert("gam-lica", [refresh_gam_creatives, refresh_gam_lica])
        return

    if mode == "pubmatic":
        _run_with_alert("pubmatic", [refresh_pubmatic, refresh_pubmatic_deal_metadata])
        return

    if mode == "post-sweep":
        # Runs after magnite + gam + pubmatic complete (needs their tables).
        _run_with_alert("post-sweep", [refresh_pmp_last_bid_date, _ensure_indexes])
        return

    # ── mode=all: sequential full sweep (local dev / backwards compat) ─────
    _run_with_alert("all", [
        refresh_magnite,
        refresh_magnite_deal_metadata,
        refresh_gam,
        refresh_gam_hourly,
        refresh_gam_weekly,
        refresh_gam_pmp_deals,
        refresh_gam_deal_bids,
        refresh_gam_private_auctions,
        refresh_gam_preferred_deals,
        refresh_gam_creatives,
        refresh_gam_lica,
        refresh_pubmatic,
        refresh_pubmatic_deal_metadata,
        refresh_dv_attention,
        refresh_dv_ivt,
        refresh_ttd,
        refresh_ttd_chumba,
        refresh_opensincera_ecosystem,
        refresh_opensincera_publishers,
        refresh_opensincera_adsystems,
        refresh_opensincera_modules,
        refresh_pmp_last_bid_date,
        _ensure_indexes,
    ])


if __name__ == "__main__":
    main()
