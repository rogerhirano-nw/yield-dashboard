"""
SQL-side data layer for the weekly deal health report. Replaces the
load_magnite/load_pubmatic/load_gam functions that lived in weekly_report.py.

Returns a list[UnhealthyDeal] — already parsed and joined with the per-SSP
deal-metadata tables (gam_pa_metadata, gam_pd_metadata, *_deal_metadata)
that supply the age anchor. The aggregate + render layers see only
UnhealthyDeal records, never raw SQL rows.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import sqlalchemy
import structlog

from .colors import DEAL_AGE_MIN_DAYS, GAM_PD_MIN_DAYS, GAM_PD_MIN_REQUESTS
from .models import UnhealthyDeal
from .parser import parse_deal

log = structlog.get_logger(__name__)


def _engine() -> sqlalchemy.Engine:
    return sqlalchemy.create_engine(os.environ["DATABASE_URL"])


# ── per-SSP loaders ────────────────────────────────────────────────────────

def _load_magnite(conn) -> pd.DataFrame:
    """Magnite SSP — deals with zero bid responses in the cache window,
    joined to magnite_deal_metadata for deal age."""
    df = pd.read_sql(
        """
        SELECT
            deal,
            SUM(bid_requests)    AS bid_requests,
            SUM(bid_responses)   AS total_bids,
            COUNT(DISTINCT date) AS days_in_data,
            MIN(date)            AS first_seen
        FROM magnite_deal_daily
        WHERE deal IS NOT NULL
          AND deal != ''
          AND UPPER(TRIM(REPLACE(REPLACE(deal, '-', ''), '/', ''))) != 'NA'
        GROUP BY deal
        HAVING SUM(bid_responses) = 0
           AND SUM(bid_requests)  > 0
        """,
        conn,
    )
    df["source_ssp"] = "Magnite"
    return _join_age(conn, df, "magnite_deal_metadata", "deal", "first_seen")


def _load_pubmatic(conn) -> pd.DataFrame:
    df = pd.read_sql(
        """
        SELECT
            deal,
            SUM(total_requests)         AS bid_requests,
            SUM(non_zero_bid_responses) AS total_bids,
            COUNT(DISTINCT date)        AS days_in_data,
            MIN(date)                   AS first_seen
        FROM pubmatic_deals
        WHERE deal IS NOT NULL
          AND deal != ''
          AND UPPER(TRIM(REPLACE(REPLACE(deal, '-', ''), '/', ''))) != 'NA'
        GROUP BY deal
        HAVING SUM(non_zero_bid_responses) = 0
           AND SUM(total_requests)         > 0
        """,
        conn,
    )
    df["source_ssp"] = "Pubmatic"
    return _join_age(conn, df, "pubmatic_deal_metadata", "deal", "first_seen")


def _load_gam(conn) -> pd.DataFrame:
    """
    GAM — uses gam_deal_bid_daily for bid metrics. Splits into:
      - PA  (Newsweek_PA_*)             — flagged on any zero-bid window;
                                          joined to gam_pa_metadata for age.
      - PD/other                        — requires ≥ GAM_PD_MIN_DAYS days and
                                          ≥ GAM_PD_MIN_REQUESTS bid requests;
                                          joined to gam_pd_metadata for age.
    Returns a single DataFrame with both groups (deal_type column tags them).
    """
    bids = pd.read_sql(
        """
        SELECT
            programmatic_deal_name AS deal,
            SUM(deals_bid_requests) AS bid_requests,
            SUM(deals_bids)         AS total_bids,
            COUNT(DISTINCT date)    AS days_in_data,
            MIN(date)               AS first_seen
        FROM gam_deal_bid_daily
        WHERE programmatic_deal_name IS NOT NULL
          AND programmatic_deal_name != ''
        GROUP BY programmatic_deal_name
        HAVING SUM(deals_bids) = 0
           AND SUM(deals_bid_requests) > 0
        """,
        conn,
    )
    if bids.empty:
        return bids

    bids["source_ssp"] = "AdX"
    is_pa = bids["deal"].str.startswith("Newsweek_PA_", na=False)
    is_pg = bids["deal"].str.startswith("Newsweek_PG_", na=False)

    pa = bids[is_pa].copy()
    pa = _join_age(conn, pa, "gam_pa_metadata", "auction_name", "create_time")

    pd_pool = bids[~is_pa & ~is_pg].copy()
    pd_unhealthy = pd_pool[
        (pd_pool["days_in_data"] >= GAM_PD_MIN_DAYS)
        & (pd_pool["bid_requests"] >= GAM_PD_MIN_REQUESTS)
    ].copy()
    pd_unhealthy = _join_age(conn, pd_unhealthy, "gam_pd_metadata", "deal_name", "start_date")

    return pd.concat([pa, pd_unhealthy], ignore_index=True)


# ── age-anchor join ────────────────────────────────────────────────────────

def _join_age(conn, df: pd.DataFrame, table: str, key_col: str, date_col: str) -> pd.DataFrame:
    """Left-join a metadata table's date column onto df by deal name.
    Tolerates missing metadata tables — leaves age_anchor_date as NaT in
    that case. Multiple metadata rows per deal → earliest date wins."""
    if df is None or df.empty:
        out = df.copy() if df is not None else pd.DataFrame()
        out["age_anchor_date"] = pd.NaT
        return out
    df = df.copy()
    df["age_anchor_date"] = pd.NaT
    try:
        meta = pd.read_sql(
            f"SELECT {key_col} AS deal, MIN({date_col})::date AS age_anchor_date FROM {table} GROUP BY {key_col}",
            conn,
        )
    except Exception:
        log.warning("metadata table missing", table=table)
        return df
    meta["age_anchor_date"] = pd.to_datetime(meta["age_anchor_date"], errors="coerce")
    return df.drop(columns=["age_anchor_date"]).merge(meta, on="deal", how="left")


# ── public API ─────────────────────────────────────────────────────────────

def load_deals(
    report_date: Optional[date] = None,
    min_age_days: int = DEAL_AGE_MIN_DAYS,
) -> list[UnhealthyDeal]:
    """
    Load the full set of unhealthy deals for the weekly report, parse each
    deal name, compute age, and apply the ≥ min_age_days filter.

    Rows missing an age anchor (deal absent from its SSP's *_metadata table)
    are EXCLUDED — we don't flag deals whose age we can't verify.
    """
    today = report_date or datetime.now(timezone.utc).date()
    with _engine().connect() as conn:
        magnite  = _load_magnite(conn)
        pubmatic = _load_pubmatic(conn)
        gam      = _load_gam(conn)

    rows = []
    for src in (magnite, pubmatic, gam):
        if src is None or src.empty:
            continue
        rows.append(src)
    if not rows:
        return []

    df = pd.concat(rows, ignore_index=True)

    # Compute deal age.
    today_ts = pd.Timestamp(today)
    df["deal_age_days"] = (today_ts - pd.to_datetime(df["age_anchor_date"], errors="coerce")).dt.days

    # Apply the age filter (NaN drops out automatically — strict mode).
    df = df[df["deal_age_days"] >= min_age_days].copy()

    if df.empty:
        return []

    # Coerce numerics.
    for col in ("bid_requests", "total_bids", "days_in_data"):
        df[col] = df[col].fillna(0).astype("int64")
    df["deal_age_days"] = df["deal_age_days"].astype("int64")

    deals: list[UnhealthyDeal] = []
    for _, r in df.iterrows():
        deals.append(UnhealthyDeal(
            parsed=parse_deal(r["deal"]),
            source_ssp=str(r["source_ssp"]),
            bid_requests=int(r["bid_requests"]),
            days_in_data=int(r["days_in_data"]),
            first_seen=str(r["first_seen"]),
            deal_age_days=int(r["deal_age_days"]),
        ))
    log.info("loaded unhealthy deals", count=len(deals), report_date=today.isoformat())
    return deals
