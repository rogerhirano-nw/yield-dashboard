"""
Google Ad Manager (GAM) client for line-item delivery reporting and pacing.

Auth: service account JSON loaded from GAM_SERVICE_ACCOUNT_JSON env var (full JSON string).
      Network ID from GAM_NETWORK_ID env var.

Usage:
    client = GAMClient()
    df_delivery = client.run_delivery_report(date(2024, 1, 1), date(2024, 1, 7))
    df_items    = client.get_active_line_items()
    df_pacing   = client.run_report_with_pacing(date(2024, 1, 1), date(2024, 1, 7))
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from googleads import ad_manager
from googleads.oauth2 import GoogleServiceAccountClient

logger = logging.getLogger(__name__)

_API_VERSION = "v202605"
_SCOPES = "https://www.googleapis.com/auth/dfp"


def _snake(name: str) -> str:
    """Convert CamelCase or mixed strings to snake_case."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower()


class GAMClient:
    """Thin wrapper around the googleads ad_manager client for delivery reporting."""

    def __init__(self) -> None:
        sa_json = os.environ["GAM_SERVICE_ACCOUNT_JSON"]
        self.network_id = os.environ["GAM_NETWORK_ID"]
        self._client = self._build_client(sa_json, self.network_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_client(sa_json_str: str, network_code: str) -> ad_manager.AdManagerClient:
        """Write service account JSON to a temp file and build the client."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            fh.write(sa_json_str)
            tmp_path = fh.name

        oauth2_client = GoogleServiceAccountClient(
            tmp_path,
            scope=_SCOPES,
        )
        return ad_manager.AdManagerClient(
            oauth2_client,
            "Newsweek Ad Ops Dashboard",
            network_code=network_code,
        )

    def _report_service(self):
        return self._client.GetService("ReportService", version=_API_VERSION)

    def _line_item_service(self):
        return self._client.GetService("LineItemService", version=_API_VERSION)

    def _order_service(self):
        return self._client.GetService("OrderService", version=_API_VERSION)

    @staticmethod
    def _gam_date(d: date) -> dict:
        return {"year": d.year, "month": d.month, "day": d.day}

    # ------------------------------------------------------------------
    # Delivery report
    # ------------------------------------------------------------------

    def run_delivery_report(self, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Run a GAM Report for line-item delivery between start_date and end_date.

        Returns a DataFrame with columns stripped of Dimension./Column. prefixes
        and converted to snake_case.
        """
        report_service = self._report_service()

        report_job = {
            "reportQuery": {
                "dimensions": [
                    "DATE",
                    "LINE_ITEM_ID",
                    "LINE_ITEM_NAME",
                    "ORDER_ID",
                    "ORDER_NAME",
                ],
                "columns": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CLICKS",
                    "AD_SERVER_CTR",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",
                    "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS_RATE",
                    "VIDEO_INTERACTION_VIDEO_STARTS",
                    "VIDEO_INTERACTION_VIDEO_COMPLETIONS",
                ],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": self._gam_date(start_date),
                "endDate": self._gam_date(end_date),
                "reportCurrency": "USD",
            }
        }

        report_job_result = report_service.runReportJob(report_job)
        job_id = report_job_result["id"]
        logger.info("GAM report job submitted, id=%s", job_id)

        # Poll until complete (max 10 minutes)
        status    = "IN_PROGRESS"
        deadline  = time.time() + 600
        while status == "IN_PROGRESS":
            if time.time() > deadline:
                raise RuntimeError(f"GAM report job {job_id} timed out after 10 minutes")
            time.sleep(10)
            status = report_service.getReportJobStatus(job_id)
            logger.info("GAM report job %s status: %s", job_id, status)

        if status != "COMPLETED":
            raise RuntimeError(f"GAM report job {job_id} ended with status {status!r}")

        # Download as CSV_DUMP
        url = report_service.getReportDownloadURL(job_id, "CSV_DUMP")
        import urllib.request
        with urllib.request.urlopen(url) as resp:
            raw = resp.read()

        # GAM CSV_DUMP may be gzip-compressed
        if raw[:2] == b"\x1f\x8b":
            import gzip
            raw = gzip.decompress(raw)

        df = pd.read_csv(io.BytesIO(raw))

        # Strip "Dimension." and "Column." prefixes, then snake_case
        df.columns = [
            _snake(
                re.sub(r"^(?:Dimension|Column)\.", "", col)
            )
            for col in df.columns
        ]

        return df

    # ------------------------------------------------------------------
    # Line items
    # ------------------------------------------------------------------

    def get_active_line_items(self) -> pd.DataFrame:
        """
        Fetch READY and DELIVERING line items with their metadata.

        Returns DataFrame with: line_item_id, line_item_name, order_id,
        order_name, impressions_goal, start_date, end_date, status.
        """
        li_service = self._line_item_service()
        statement = ad_manager.StatementBuilder(version=_API_VERSION)
        statement.Where("status IN ('READY', 'DELIVERING')")
        statement.Limit(500)
        statement.Offset(0)

        rows = []
        while True:
            result = li_service.getLineItemsByStatement(statement.ToStatement())
            if not getattr(result, "results", None):
                break

            for li in result.results:
                primary_goal = getattr(li, "primaryGoal", None)
                impressions_goal = (
                    int(primary_goal.units)
                    if primary_goal and getattr(primary_goal, "units", None) is not None
                    else None
                )

                def _gam_date_to_str(gd) -> Optional[str]:
                    if gd is None:
                        return None
                    try:
                        return f"{gd.year}-{gd.month:02d}-{gd.day:02d}"
                    except Exception:
                        return str(gd)

                rows.append(
                    {
                        "line_item_id": str(li.id),
                        "line_item_name": li.name,
                        "order_id": str(li.orderId),
                        "order_name": getattr(li, "orderName", None),
                        "impressions_goal": impressions_goal,
                        "start_date": _gam_date_to_str(getattr(li, "startDateTime", None)),
                        "end_date": _gam_date_to_str(getattr(li, "endDateTime", None)),
                        "status": str(li.status),
                    }
                )

            if result.totalResultSetSize <= statement.offset + len(result.results):
                break
            statement.IncreaseOffsetBy(500)

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Combined pacing report
    # ------------------------------------------------------------------

    def run_report_with_pacing(self, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Combine delivery data with line-item metadata and compute pacing metrics.

        Added columns:
            pacing_pct  — (impressions_delivered / impressions_goal) /
                          (days_elapsed / total_days) * 100
            vcr         — video_completions / video_starts * 100

        Returns the merged DataFrame.
        """
        df_delivery = self.run_delivery_report(start_date, end_date)
        df_items = self.get_active_line_items()

        # Aggregate delivery per line item across all dates in range
        # (keep date-level rows; pacing is computed per line item overall)
        agg = (
            df_delivery.groupby("line_item_id", as_index=False)
            .agg(
                impressions_delivered=("ad_server_impressions", "sum"),
                ad_server_clicks=("ad_server_clicks", "sum"),
                ad_server_ctr=("ad_server_ctr", "mean"),
                ad_server_cpm_and_cpc_revenue=("ad_server_cpm_and_cpc_revenue", "sum"),
                ad_server_active_view_viewable_impressions_rate=(
                    "ad_server_active_view_viewable_impressions_rate",
                    "mean",
                ),
                video_starts=("video_interaction_video_starts", "sum"),
                video_completions=("video_interaction_video_completions", "sum"),
            )
        )

        # Ensure consistent string keys for join
        agg["line_item_id"] = agg["line_item_id"].astype(str)
        df_items["line_item_id"] = df_items["line_item_id"].astype(str)

        merged = df_items.merge(agg, on="line_item_id", how="left")

        # VCR
        merged["vcr"] = merged.apply(
            lambda r: (r["video_completions"] / r["video_starts"] * 100)
            if pd.notna(r.get("video_starts")) and r.get("video_starts", 0) > 0
            else None,
            axis=1,
        )

        # Pacing
        today = date.today()

        def _pacing(row) -> Optional[float]:
            try:
                goal = row["impressions_goal"]
                delivered = row["impressions_delivered"]
                if not goal or goal == 0:
                    return None
                li_start = pd.to_datetime(row["start_date"]).date()
                li_end = pd.to_datetime(row["end_date"]).date()
                total_days = max((li_end - li_start).days, 1)
                elapsed = max((min(today, li_end) - li_start).days, 1)
                return (delivered / goal) / (elapsed / total_days) * 100
            except Exception:
                return None

        merged["pacing_pct"] = merged.apply(_pacing, axis=1)

        # Also attach per-day delivery rows (for time-series use if needed)
        merged["report_start"] = start_date.isoformat()
        merged["report_end"] = end_date.isoformat()

        return merged
