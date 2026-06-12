"""
Magnite DV+ Performance Analytics REST API client.

Handles the offline-report flow: create → poll status → paginate results.
Built against the General dataset shape. Swap DATASET to target Prebid Analytics
or First Party data once you have the Prebid column docs from the logged-in
help center.

Usage:
    from magnite_client import MagniteClient

    client = MagniteClient(
        api_key="YOUR_KEY",
        api_secret="YOUR_SECRET",
        account_id="YOUR_PUBLISHER_ID",
    )
    df = client.run_report(
        dimensions=["site", "date", "size"],
        metrics=["ad_requests", "auctions", "publisher_gross_revenue"],
        date_range="yesterday",
    )
    print(df.head())
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rubiconproject.com"

# Dataset endpoints. The doc lists "default" (General) and "firstparty".
# Prebid Analytics has its own path — confirm exact slug from the logged-in
# Magnite docs (https://help.magnite.com/help/prebid-analytics-api) and swap
# the value here. Pattern is identical: POST create, GET status, GET data.
Dataset = Literal["default", "firstparty", "prebid"]

# Terminal statuses — polling stops when one of these is hit.
TERMINAL_STATUSES = {"success", "error", "canceled"}

# Per-page row cap from the doc. Total report cap is 500_000.
PAGE_SIZE = 50_000
MAX_REPORT_ROWS = 500_000


@dataclass
class MagniteClient:
    api_key: str
    api_secret: str
    account_id: str
    base_url: str = BASE_URL
    # Polling cadence — start gentle to be a good neighbor.
    poll_interval_seconds: int = 30
    poll_timeout_seconds: int = 1800  # 30 minutes
    # Auto-retry on 429 (queue full, max 5 parallel reports).
    retry_429_seconds: int = 60
    retry_429_attempts: int = 10
    # Short budget for transient 5xx responses / dropped connections.
    retry_5xx_seconds: int = 20
    retry_5xx_attempts: int = 3
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self.session.auth = HTTPBasicAuth(self.api_key, self.api_secret)
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------ #
    # Public: one-shot convenience method
    # ------------------------------------------------------------------ #

    def run_report(
        self,
        dimensions: Iterable[str],
        metrics: Iterable[str],
        date_range: str | None = "yesterday",
        start: str | None = None,
        end: str | None = None,
        filters: str | None = None,
        timezone: str | None = None,
        currency: str = "USD",
        limit: int = MAX_REPORT_ROWS,
        dataset: Dataset = "default",
    ) -> pd.DataFrame:
        """
        Create a report, wait for it, fetch all pages, return a DataFrame.

        Either pass `date_range` (e.g. "yesterday", "last_7") OR both `start`
        and `end` in ISO-8601. Don't pass both.

        `filters` uses Magnite's syntax:
            "dimension:site_id==180726;dimension:device_type_name_v1==Connected TV;metric:bid_requests>0"
        """
        report_id = self.create_report(
            dimensions=dimensions,
            metrics=metrics,
            date_range=date_range,
            start=start,
            end=end,
            filters=filters,
            timezone=timezone,
            currency=currency,
            limit=limit,
            dataset=dataset,
        )
        logger.info("Created report %s, polling for completion", report_id)
        self.wait_for_report(report_id, dataset=dataset)
        return self.fetch_all_pages(report_id, dataset=dataset)

    # ------------------------------------------------------------------ #
    # Step 1: create
    # ------------------------------------------------------------------ #

    def create_report(
        self,
        dimensions: Iterable[str],
        metrics: Iterable[str],
        date_range: str | None = "yesterday",
        start: str | None = None,
        end: str | None = None,
        filters: str | None = None,
        timezone: str | None = None,
        currency: str = "USD",
        limit: int = MAX_REPORT_ROWS,
        dataset: Dataset = "default",
    ) -> int:
        """POST a new offline report. Returns the offline_report_id."""
        if (start or end) and date_range:
            raise ValueError(
                "Pass either date_range OR start+end, not both. "
                "The API treats date_range as overriding when both are present."
            )
        if bool(start) ^ bool(end):
            raise ValueError("Both start and end must be set together.")

        body = {
            "criteria": {
                "dimension": ",".join(dimensions),
                "metric": ",".join(metrics),
                "limit": limit,
                "date_range": date_range,
                "start": start,
                "end": end,
                "timezone": timezone,
                "currency": currency,
                "filters": filters,
            }
        }
        url = f"{self.base_url}/analytics/v2/{dataset}"
        params = {"account": f"publisher/{self.account_id}"}

        resp = self._request_with_retry("POST", url, params=params, json=body)
        payload = resp.json()
        report_id = payload.get("offline_report_id")
        if report_id is None:
            raise MagniteAPIError(f"No offline_report_id in response: {payload}")
        return int(report_id)

    # ------------------------------------------------------------------ #
    # Step 2: poll status
    # ------------------------------------------------------------------ #

    def get_report_status(self, report_id: int, dataset: Dataset = "default") -> dict[str, Any]:
        url = f"{self.base_url}/analytics/v2/{dataset}/{report_id}"
        params = {"account": f"publisher/{self.account_id}"}
        resp = self._request_with_retry("GET", url, params=params)
        return resp.json()

    def wait_for_report(self, report_id: int, dataset: Dataset = "default") -> dict[str, Any]:
        """Block until the report reaches a terminal status, or timeout."""
        deadline = time.monotonic() + self.poll_timeout_seconds
        while True:
            status_payload = self.get_report_status(report_id, dataset=dataset)
            status = status_payload.get("status")
            logger.debug("Report %s status: %s", report_id, status)

            if status in TERMINAL_STATUSES:
                if status != "success":
                    raise MagniteReportFailed(
                        f"Report {report_id} ended with status '{status}': {status_payload}"
                    )
                return status_payload

            if time.monotonic() > deadline:
                raise MagniteReportTimeout(
                    f"Report {report_id} not done after {self.poll_timeout_seconds}s "
                    f"(last status: {status})"
                )

            time.sleep(self.poll_interval_seconds)

    # ------------------------------------------------------------------ #
    # Step 3: paginate data
    # ------------------------------------------------------------------ #

    def fetch_page(
        self,
        report_id: int,
        page: int,
        size: int = PAGE_SIZE,
        dataset: Dataset = "default",
        fmt: Literal["json", "csv"] = "json",
    ) -> dict[str, Any]:
        url = f"{self.base_url}/analytics/v2/{dataset}/{report_id}/data"
        params = {
            "account": f"publisher/{self.account_id}",
            "format": fmt,
            "page": page,
            "size": size,
        }
        resp = self._request_with_retry("GET", url, params=params)
        if fmt == "csv":
            return {"_raw_csv": resp.text}
        return resp.json()

    def fetch_all_pages(
        self,
        report_id: int,
        dataset: Dataset = "default",
        page_size: int = PAGE_SIZE,
    ) -> pd.DataFrame:
        """
        Walk pages until we get a short page (fewer rows than page_size).

        The doc doesn't return a total_pages on the data endpoint reliably, so
        we use the "page shorter than size" heuristic. Capped at MAX_REPORT_ROWS
        worth of pages so a runaway loop can't happen.
        """
        max_pages = (MAX_REPORT_ROWS // page_size) + 1
        rows: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            payload = self.fetch_page(report_id, page=page, size=page_size, dataset=dataset)
            content = payload.get("content", [])
            if not content:
                break
            rows.extend(content)
            logger.debug("Page %d returned %d rows (running total: %d)", page, len(content), len(rows))
            if len(content) < page_size:
                break

        df = pd.DataFrame(rows)
        if len(df) >= MAX_REPORT_ROWS:
            logger.warning(
                "Hit the %d-row cap. Magnite may have silently truncated. "
                "Break the query by date or filter on a high-cardinality dim.",
                MAX_REPORT_ROWS,
            )
        return df

    # ------------------------------------------------------------------ #
    # Step 4 (optional): list recent reports for the account
    # ------------------------------------------------------------------ #

    def list_recent_reports(
        self,
        date_range: Literal["today", "yesterday", "last_3", "last_24"] = "today",
        dataset: Dataset = "default",
    ) -> list[dict[str, Any]]:
        url = f"{self.base_url}/analytics/v2/{dataset}/data"
        params = {
            "account": f"publisher/{self.account_id}",
            "date_range": date_range,
        }
        resp = self._request_with_retry("GET", url, params=params)
        return resp.json().get("content", [])

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """
        429 = the per-account "5 reports in parallel" cap, per the doc.
        Doc warns this error can also be misleading — could be a system-wide issue.
        We back off, retry, and surface a clear exception if it persists.

        5xx responses and dropped connections get a much shorter retry budget:
        enough to ride out a transient blip, while a real outage still fails
        the refresh (which logs it and moves on to the next source).
        """
        attempts_429 = 0
        attempts_5xx = 0
        while True:
            try:
                resp = self.session.request(method, url, timeout=60, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                attempts_5xx += 1
                if attempts_5xx >= self.retry_5xx_attempts:
                    raise MagniteAPIError(
                        f"{method} {url} failed after {attempts_5xx} attempts: {exc}"
                    ) from exc
                # INFO, not WARNING: a retry that may yet succeed isn't an
                # incident — the sweep alert collects WARNING+, and recovered
                # blips were emailing (2026-06-12). Exhaustion raises → ERROR.
                logger.info(
                    "Connection error from Magnite (attempt %d/%d): %s. Sleeping %ds before retry.",
                    attempts_5xx, self.retry_5xx_attempts, exc, self.retry_5xx_seconds,
                )
                time.sleep(self.retry_5xx_seconds)
                continue
            if resp.status_code == 429:
                attempts_429 += 1
                if attempts_429 >= self.retry_429_attempts:
                    raise MagniteAPIError(
                        f"Exhausted {self.retry_429_attempts} 429-retry attempts on {method} {url}"
                    )
                logger.info(
                    "429 from Magnite (attempt %d/%d). Sleeping %ds before retry.",
                    attempts_429, self.retry_429_attempts, self.retry_429_seconds,
                )
                time.sleep(self.retry_429_seconds)
                continue
            if resp.status_code == 409:
                # "report not ready" — only meaningful if someone hits the data
                # endpoint before polling success. Raise so the caller can react.
                raise MagniteReportNotReady(f"409 from {url}: {resp.text}")
            if resp.status_code >= 500:
                attempts_5xx += 1
                if attempts_5xx >= self.retry_5xx_attempts:
                    raise MagniteAPIError(
                        f"{method} {url} returned {resp.status_code} after "
                        f"{attempts_5xx} attempts: {resp.text}"
                    )
                logger.info(
                    "%d from Magnite (attempt %d/%d). Sleeping %ds before retry.",
                    resp.status_code, attempts_5xx, self.retry_5xx_attempts,
                    self.retry_5xx_seconds,
                )
                time.sleep(self.retry_5xx_seconds)
                continue
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise MagniteAPIError(
                    f"{method} {url} returned {resp.status_code}: {resp.text}"
                ) from exc
            return resp


# ---------------------------------------------------------------------- #
# Exceptions
# ---------------------------------------------------------------------- #


class MagniteAPIError(Exception):
    """Catch-all for Magnite API errors."""


class MagniteReportFailed(MagniteAPIError):
    """Report reached a terminal non-success state."""


class MagniteReportTimeout(MagniteAPIError):
    """Polled past the configured deadline without reaching a terminal state."""


class MagniteReportNotReady(MagniteAPIError):
    """409 — data endpoint hit before report completed."""
