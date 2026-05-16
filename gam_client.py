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
                    "PROGRAMMATIC_CHANNEL",
                ],
                "columns": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CLICKS",
                    "AD_SERVER_CTR",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",
                    "AD_SERVER_AVERAGE_ECPM",
                    "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
                    "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS_RATE",
                    "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
                    "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS_RATE",
                    "AD_SERVER_ACTIVE_VIEW_ELIGIBLE_IMPRESSIONS",
                    "VIDEO_INTERACTION_VIDEO_STARTS",
                    "VIDEO_INTERACTION_VIDEO_FIRST_QUARTILE",
                    "VIDEO_INTERACTION_VIDEO_MIDPOINT",
                    "VIDEO_INTERACTION_VIDEO_THIRD_QUARTILE",
                    "VIDEO_INTERACTION_VIDEO_COMPLETIONS",
                    "VIDEO_INTERACTION_VIDEO_SKIPS",
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

        # GAM CSV_DUMP expresses all monetary values in micro-currency (1/1,000,000).
        for _money_col in ("ad_server_cpm_and_cpc_revenue", "ad_server_average_ecpm"):
            if _money_col in df.columns:
                df[_money_col] = df[_money_col] / 1_000_000

        return df

    # ------------------------------------------------------------------
    # Line items
    # ------------------------------------------------------------------

    def _get_order_salespersons(self) -> dict:
        """Return {order_id_str: salesperson_name} for all orders with a salesperson set."""
        order_service = self._order_service()
        statement = ad_manager.StatementBuilder(version=_API_VERSION)
        statement.Limit(500)
        statement.Offset(0)

        result_map = {}
        while True:
            result = order_service.getOrdersByStatement(statement.ToStatement())
            if not getattr(result, "results", None):
                break
            for order in result.results:
                sp = getattr(order, "salesperson", None)
                if sp:
                    name = getattr(sp, "name", None)
                    if name:
                        result_map[str(order.id)] = name
            if result.totalResultSetSize <= statement.offset + len(result.results):
                break
            statement.Offset(statement.offset + 500)

        logger.info("GAM: loaded salesperson for %d orders", len(result_map))
        return result_map

    def get_active_line_items(self) -> pd.DataFrame:
        """
        Fetch READY and DELIVERING line items with their metadata.

        Returns DataFrame with: line_item_id, line_item_name, order_id,
        order_name, impressions_goal, start_date, end_date, status, salesperson.
        """
        salesperson_map = self._get_order_salespersons()

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
                _units = int(primary_goal.units) if primary_goal and getattr(primary_goal, "units", None) is not None else None
                # GAM returns -1 for click-based or unlimited campaigns — treat as no goal
                impressions_goal = _units if _units is not None and _units > 0 else None

                def _gam_date_to_str(gd) -> Optional[str]:
                    if gd is None:
                        return None
                    try:
                        # GAM DateTime has a nested .date sub-object
                        d = getattr(gd, "date", gd)
                        return f"{d.year}-{d.month:02d}-{d.day:02d}"
                    except Exception:
                        return None

                # CPM rate from costPerUnit (stored in microcurrency)
                cost_type = str(getattr(li, "costType", ""))
                cost_per_unit = getattr(li, "costPerUnit", None)
                cpm_rate = (
                    int(cost_per_unit.microAmount) / 1_000_000
                    if cost_per_unit and cost_type == "CPM"
                    else None
                )

                order_id_str = str(li.orderId)
                rows.append(
                    {
                        "line_item_id": str(li.id),
                        "line_item_name": li.name,
                        "order_id": order_id_str,
                        "order_name": getattr(li, "orderName", None),
                        "line_item_type": str(getattr(li, "lineItemType", "") or ""),
                        "impressions_goal": impressions_goal,
                        "cpm_rate": cpm_rate,
                        "start_date": _gam_date_to_str(getattr(li, "startDateTime", None)),
                        "end_date": _gam_date_to_str(getattr(li, "endDateTime", None)),
                        "status": str(li.status),
                        "salesperson": salesperson_map.get(order_id_str),
                    }
                )

            if result.totalResultSetSize <= statement.offset + len(result.results):
                break
            statement.Offset(statement.offset + 500)

        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Lifetime delivery (for pacing)
    # ------------------------------------------------------------------

    def run_lifetime_delivery(self) -> pd.DataFrame:
        """
        Fetch cumulative impressions per line item over a 2-year window.
        Used for pacing — covers all realistic active campaign durations.
        GAM does not support dateRangeType=LIFETIME for impression reports.
        """
        report_service = self._report_service()

        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=730)  # 2-year window

        report_job = {
            "reportQuery": {
                "dimensions": ["LINE_ITEM_ID"],
                "columns": ["AD_SERVER_IMPRESSIONS"],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": self._gam_date(start),
                "endDate": self._gam_date(end),
                "reportCurrency": "USD",
            }
        }

        report_job_result = report_service.runReportJob(report_job)
        job_id = report_job_result["id"]
        logger.info("GAM lifetime report job submitted, id=%s", job_id)

        status   = "IN_PROGRESS"
        deadline = time.time() + 600
        while status == "IN_PROGRESS":
            if time.time() > deadline:
                raise RuntimeError(f"GAM lifetime report job {job_id} timed out")
            time.sleep(10)
            status = report_service.getReportJobStatus(job_id)
            logger.info("GAM lifetime report job %s status: %s", job_id, status)

        if status != "COMPLETED":
            raise RuntimeError(f"GAM lifetime report job {job_id} ended with status {status!r}")

        url = report_service.getReportDownloadURL(job_id, "CSV_DUMP")
        import urllib.request
        with urllib.request.urlopen(url) as resp:
            raw = resp.read()

        if raw[:2] == b"\x1f\x8b":
            import gzip
            raw = gzip.decompress(raw)

        df = pd.read_csv(io.BytesIO(raw))
        df.columns = [_snake(re.sub(r"^(?:Dimension|Column)\.", "", col)) for col in df.columns]
        df["line_item_id"] = df["line_item_id"].astype(str)
        df = df.rename(columns={"ad_server_impressions": "lifetime_impressions_delivered"})
        return df[["line_item_id", "lifetime_impressions_delivered"]]

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
        df_lifetime = self.run_lifetime_delivery()

        # Aggregate 7-day delivery per line item (for trend metrics)
        agg_spec = {
            "impressions_delivered": ("ad_server_impressions", "sum"),
            "ad_server_clicks": ("ad_server_clicks", "sum"),
            "ad_server_ctr": ("ad_server_ctr", "mean"),
            "ad_server_cpm_and_cpc_revenue": ("ad_server_cpm_and_cpc_revenue", "sum"),
        }
        # All remaining columns are optional — only aggregate if present in the report
        _optional_sum = [
            "ad_server_active_view_viewable_impressions",
            "ad_server_active_view_measurable_impressions",
            "ad_server_active_view_eligible_impressions",
            "video_interaction_video_starts",
            "video_interaction_video_first_quartile",
            "video_interaction_video_midpoint",
            "video_interaction_video_third_quartile",
            "video_interaction_video_completions",
            "video_interaction_video_skips",
        ]
        _optional_mean = [
            "ad_server_average_ecpm",
            "ad_server_active_view_viewable_impressions_rate",
            "ad_server_active_view_measurable_impressions_rate",
        ]
        for _col in _optional_sum:
            if _col in df_delivery.columns:
                agg_spec[_col] = (_col, "sum")
        for _col in _optional_mean:
            if _col in df_delivery.columns:
                agg_spec[_col] = (_col, "mean")

        _grp_dims = ["line_item_id"]
        if "programmatic_channel" in df_delivery.columns:
            _grp_dims.append("programmatic_channel")
        agg = df_delivery.groupby(_grp_dims, as_index=False).agg(**agg_spec)

        # Yesterday's impressions only (most recent date in the report window)
        latest_date = df_delivery["date"].max()
        agg_1d = (
            df_delivery[df_delivery["date"] == latest_date]
            .groupby("line_item_id", as_index=False)["ad_server_impressions"]
            .sum()
            .rename(columns={"ad_server_impressions": "impressions_1d"})
        )
        agg_1d["line_item_id"] = agg_1d["line_item_id"].astype(str)

        # Ensure consistent string keys for join
        agg["line_item_id"] = agg["line_item_id"].astype(str)
        df_items["line_item_id"] = df_items["line_item_id"].astype(str)

        merged = df_items.merge(agg, on="line_item_id", how="left")
        merged = merged.merge(df_lifetime, on="line_item_id", how="left")
        merged = merged.merge(agg_1d, on="line_item_id", how="left")

        # VCR — only computable when video columns were present in the report
        _vcr_starts = "video_interaction_video_starts"
        _vcr_completions = "video_interaction_video_completions"
        if _vcr_starts in merged.columns and _vcr_completions in merged.columns:
            merged["vcr"] = merged.apply(
                lambda r: (r[_vcr_completions] / r[_vcr_starts] * 100)
                if pd.notna(r.get(_vcr_starts)) and r.get(_vcr_starts, 0) > 0
                else (0.0 if pd.notna(r.get(_vcr_starts)) else None),
                axis=1,
            )
        else:
            merged["vcr"] = None

        # Pacing — uses lifetime impressions, not the rolling 7-day window
        today = date.today()

        def _pacing(row) -> Optional[float]:
            try:
                goal      = row["impressions_goal"]
                delivered = row["lifetime_impressions_delivered"]
                has_goal  = goal and goal > 0 and pd.notna(delivered)

                raw_start = pd.to_datetime(row["start_date"])
                raw_end   = pd.to_datetime(row["end_date"])
                has_dates = pd.notna(raw_start) and pd.notna(raw_end)

                if has_dates:
                    li_start   = raw_start.date()
                    li_end     = raw_end.date()
                    total_days = max((li_end - li_start).days, 1)
                    elapsed    = max((min(today, li_end) - li_start).days, 1)
                    if has_goal:
                        return (delivered / goal) / (elapsed / total_days) * 100
                    # Elapsed-time pacing when no impression goal
                    return elapsed / total_days * 100

                # No end date (open-ended flight) — fall back to impression pacing only
                if has_goal:
                    return delivered / goal * 100

                return None
            except Exception:
                return None

        merged["pacing_pct"] = merged.apply(_pacing, axis=1)

        # Also attach per-day delivery rows (for time-series use if needed)
        merged["report_start"] = start_date.isoformat()
        merged["report_end"] = end_date.isoformat()

        return merged
