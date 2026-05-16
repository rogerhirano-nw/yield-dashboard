"""
Pubmatic Analytics API client for PMP deal reporting.

Auth:  Bearer token stored in the Supabase api_tokens table.
       Seeds from PUBMATIC_TOKEN env var on first run.
       Automatically refreshed every 55 days (before the 60-day expiry).

Required env vars:
  PUBMATIC_PUBLISHER_ID   — numeric publisher ID
  PUBMATIC_TOKEN          — initial access token (seed only; rotated automatically)
  PUBMATIC_REFRESH_TOKEN  — long-lived refresh token
  PUBMATIC_EMAIL          — account email (required by the refresh endpoint)
  DATABASE_URL            — Supabase connection string

Usage:
    client = PubmaticClient()
    df = client.run_deal_report(date(2024, 1, 1), date(2024, 1, 7))
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone

import pandas as pd
import requests
import sqlalchemy
from sqlalchemy import text

logger = logging.getLogger(__name__)

_BASE_URL      = "https://api.pubmatic.com/v1/analytics/data/publisher"
_REFRESH_URL   = "https://api.pubmatic.com/v1/developer-integrations/developer/refreshToken"
_TOKEN_MAX_AGE = 55   # days — refresh 5 days before the 60-day expiry


class PubmaticClient:

    def __init__(self) -> None:
        self.publisher_id        = os.environ["PUBMATIC_PUBLISHER_ID"]
        self._email              = os.environ["PUBMATIC_EMAIL"]
        self._seed_refresh_token = os.environ["PUBMATIC_REFRESH_TOKEN"]
        self._access_token       = self._load_or_refresh_token()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    @staticmethod
    def _engine() -> sqlalchemy.Engine:
        return sqlalchemy.create_engine(os.environ["DATABASE_URL"])

    @staticmethod
    def _ensure_tokens_table(engine: sqlalchemy.Engine) -> None:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS api_tokens (
                    service          TEXT PRIMARY KEY,
                    access_token     TEXT NOT NULL,
                    refresh_token    TEXT NOT NULL,
                    token_updated_at TEXT NOT NULL
                )
            """))

    def _load_or_refresh_token(self) -> str:
        engine = self._engine()
        self._ensure_tokens_table(engine)

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT access_token, refresh_token, token_updated_at "
                    "FROM api_tokens WHERE service = 'pubmatic'"
                )
            ).fetchone()

        if row is None:
            token   = os.environ["PUBMATIC_TOKEN"]
            refresh = self._seed_refresh_token
            self._save_token(engine, token, refresh)
            logger.info("Pubmatic: seeded access token from env var")
            return token

        access_token, refresh_token, updated_at_str = row
        updated_at = datetime.fromisoformat(updated_at_str)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - updated_at).days

        if age_days >= _TOKEN_MAX_AGE:
            logger.info("Pubmatic: token is %d days old — refreshing", age_days)
            new_token = self._call_refresh(refresh_token)
            self._save_token(engine, new_token, refresh_token)
            return new_token

        logger.info("Pubmatic: token age %d days — no refresh needed", age_days)
        return access_token

    def _call_refresh(self, refresh_token: str) -> str:
        resp = requests.put(
            _REFRESH_URL,
            json={
                "email":        self._email,
                "apiProduct":   "PUBLISHER",
                "refreshToken": refresh_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        new_token = (
            data.get("accessToken")
            or data.get("access_token")
            or data.get("token")
        )
        if not new_token:
            raise RuntimeError(f"Unexpected token refresh response: {data}")
        logger.info("Pubmatic: token refreshed successfully")
        return new_token

    @staticmethod
    def _save_token(
        engine: sqlalchemy.Engine,
        access_token: str,
        refresh_token: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM api_tokens WHERE service = 'pubmatic'"))
            conn.execute(
                text(
                    "INSERT INTO api_tokens "
                    "(service, access_token, refresh_token, token_updated_at) "
                    "VALUES ('pubmatic', :at, :rt, :now)"
                ),
                {"at": access_token, "rt": refresh_token, "now": now},
            )

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type":  "application/json",
        }

    def _fetch(self, params: dict) -> list[dict]:
        """
        Call the analytics endpoint and unpack the columnar response into
        a list of dicts. displayValue entries are merged in as _name fields.
        """
        resp = requests.get(
            f"{_BASE_URL}/{self.publisher_id}",
            headers=self._headers(),
            params=params,
            timeout=60,
        )
        logger.info("Pubmatic response status: %s — URL: %s", resp.status_code, resp.url)
        if not resp.ok:
            logger.error("Pubmatic error body: %s", resp.text[:500])
        resp.raise_for_status()

        data    = resp.json()
        columns = data.get("columns") or []
        rows    = data.get("rows")    or []
        dv      = data.get("displayValue") or {}

        if not columns or not rows:
            logger.info("Pubmatic: empty result (columns=%s rows=%d)", columns, len(rows))
            return []

        records = []
        for row in rows:
            record = dict(zip(columns, row))
            # Merge human-readable names for ID dimensions (e.g. dealMetaId → deal name)
            for col, id_map in dv.items():
                if col in record:
                    record[f"{col}_name"] = id_map.get(str(record[col]))
            records.append(record)

        logger.info("Pubmatic: fetched %d rows", len(records))
        return records

    # ------------------------------------------------------------------
    # Deal report
    # ------------------------------------------------------------------

    def run_deal_report(self, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Fetch PMP deal analytics for the given date range.

        Returns DataFrame with columns: date, deal_meta_id, deal,
        publisher_deal_id, dsp_id, dsp, paid_impressions, revenue,
        ecpm, non_zero_bid_responses, win_rate, total_requests, source.
        """
        params = {
            # Pubmatic requires timestamp format; end at 23:59 to include full day
            "fromDate":   start_date.strftime("%Y-%m-%dT00:00"),
            "toDate":     end_date.strftime("%Y-%m-%dT23:59"),
            "dateUnit":   "date",
            "dimensions": "date,dealMetaId,publisherDealId,dspId,adFormatId",
            "metrics":    "paidImpressions,revenue,ecpm,nonZeroBidResponses,winRate,totalRequests",
        }

        records = self._fetch(params)
        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)

        rename = {
            "date":                  "date",
            "dealMetaId":            "deal_meta_id",
            "dealMetaId_name":       "deal",
            "publisherDealId":       "publisher_deal_id",
            "dspId":                 "dsp_id",
            "dspId_name":            "dsp",
            "adFormatId":            "ad_format_id",
            "adFormatId_name":       "ad_format",
            "paidImpressions":       "paid_impressions",
            "revenue":               "revenue",
            "ecpm":                  "ecpm",
            "nonZeroBidResponses":   "non_zero_bid_responses",
            "winRate":               "win_rate",
            "totalRequests":         "total_requests",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        df["source"] = "pubmatic"

        return df
