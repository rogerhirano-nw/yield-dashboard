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
_PAGE_SIZE     = 1000


class PubmaticClient:

    def __init__(self) -> None:
        self.publisher_id       = os.environ["PUBMATIC_PUBLISHER_ID"]
        self._email             = os.environ["PUBMATIC_EMAIL"]
        self._seed_refresh_token = os.environ["PUBMATIC_REFRESH_TOKEN"]
        self._access_token      = self._load_or_refresh_token()

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
        # Pubmatic returns the new token under one of these keys
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

    def _get_page(self, params: dict, page: int) -> dict:
        resp = requests.get(
            f"{_BASE_URL}/{self.publisher_id}",
            headers=self._headers(),
            params={**params, "page": page, "limit": _PAGE_SIZE},
            timeout=60,
        )
        logger.info("Pubmatic response status: %s — URL: %s", resp.status_code, resp.url)
        if not resp.ok:
            logger.error("Pubmatic error body: %s", resp.text[:500])
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Deal report
    # ------------------------------------------------------------------

    def run_deal_report(self, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Fetch PMP deal analytics for the given date range.

        Returns DataFrame with columns: date, deal, deal_id, partner_id,
        impressions, paid_impressions, revenue, ecpm, bid_requests,
        bid_responses, win_rate, vcr, viewability, ctr, source.
        """
        params = {
            "startDate":  start_date.strftime("%Y-%m-%d"),
            "endDate":    end_date.strftime("%Y-%m-%d"),
            "dimensions": "date",
            "metrics":    "adImpressions,publisherRevenue,bidRequests,bidResponses",
        }

        rows: list[dict] = []
        page = 1
        while True:
            data  = self._get_page(params, page)
            items = data.get("items") or data.get("data") or []
            if not items:
                break
            rows.extend(items)

            meta        = data.get("metaData") or data.get("metadata") or {}
            total_pages = (
                meta.get("totalPages")
                or meta.get("total_pages")
                or meta.get("totalPage")
                or 1
            )
            if page >= int(total_pages):
                break
            page += 1

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        rename = {
            "date":           "date",
            "deal":           "deal",
            "dealId":         "deal_id",
            "partnerId":      "partner_id",
            "impressions":    "impressions",
            "paidImpressions":"paid_impressions",
            "revenue":        "revenue",
            "ecpm":           "ecpm",
            "bidRequests":    "bid_requests",
            "bidResponses":   "bid_responses",
            "winRate":        "win_rate",
            "vcr":            "vcr",
            "viewability":    "viewability",
            "ctr":            "ctr",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        df["source"] = "pubmatic"

        return df
