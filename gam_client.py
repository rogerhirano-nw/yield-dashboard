"""
Google Ad Manager (GAM) client using the REST API (google-ads-admanager v1).

Auth: service account JSON from GAM_SERVICE_ACCOUNT_JSON env var (full JSON string).
      Network ID from GAM_NETWORK_ID env var.

Usage:
    client = GAMClient()
    df_delivery = client.run_delivery_report(date(2024, 1, 1), date(2024, 1, 7))
    df_items    = client.get_active_line_items()
    df_pacing   = client.run_report_with_pacing(date(2024, 1, 1), date(2024, 1, 7))
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from google.ads import admanager_v1
from google.oauth2 import service_account
from google.type import date_pb2

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/admanager"]

_D = admanager_v1.ReportDefinition.Dimension
_M = admanager_v1.ReportDefinition.Metric


def _snake(name: str) -> str:
    """Convert CamelCase or UPPER_CASE strings to snake_case."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower()


def _rv(rv) -> object:
    """Extract a scalar from a proto-plus ReportValue (oneof named 'value')."""
    pb = type(rv).pb(rv)
    field = pb.WhichOneof("value")
    if field == "int_value":
        return pb.int_value
    if field == "double_value":
        return pb.double_value
    if field == "string_value":
        return pb.string_value
    if field == "bool_value":
        return pb.bool_value
    if field == "int_list_value":
        return list(pb.int_list_value.values)
    if field == "double_list_value":
        return list(pb.double_list_value.values)
    if field == "string_list_value":
        return list(pb.string_list_value.values)
    return None


def _money(m) -> Optional[float]:
    """Convert a protobuf Money message (units + nanos) to a float."""
    if m is None:
        return None
    units = int(getattr(m, "units", 0) or 0)
    nanos = int(getattr(m, "nanos", 0) or 0)
    return units + nanos / 1e9


def _enum_name(val) -> str:
    """Return the string name of a proto enum value."""
    name = getattr(val, "name", None)
    return name if name else str(val)


def _ts_to_date(ts) -> Optional[str]:
    """Convert a protobuf Timestamp to a YYYY-MM-DD string."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts.seconds, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


class GAMClient:
    """Thin wrapper around the google-ads-admanager REST client."""

    def __init__(self) -> None:
        sa_json = os.environ["GAM_SERVICE_ACCOUNT_JSON"]
        self.network_id = os.environ["GAM_NETWORK_ID"]
        creds = service_account.Credentials.from_service_account_info(
            json.loads(sa_json), scopes=_SCOPES
        )
        self._report_client = admanager_v1.ReportServiceClient(credentials=creds)
        self._li_client = admanager_v1.LineItemServiceClient(credentials=creds)
        self._parent = f"networks/{self.network_id}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _gam_date(d: date) -> date_pb2.Date:
        return date_pb2.Date(year=d.year, month=d.month, day=d.day)

    def _run_report(
        self,
        dimensions: list[str],
        metrics: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        Create, run, and fetch a GAM Historical report.

        Dimension and metric names must match the ReportDefinition.Dimension /
        Metric enum identifiers (e.g. "DATE", "LINE_ITEM_ID", "AD_SERVER_IMPRESSIONS").

        Returns a DataFrame with snake_cased column names in the same order as
        the requested dimensions followed by the requested metrics.
        """
        report = admanager_v1.Report(
            report_definition=admanager_v1.ReportDefinition(
                dimensions=[_D[d] for d in dimensions],
                metrics=[_M[m] for m in metrics],
                date_range=admanager_v1.ReportDefinition.DateRange(
                    fixed=admanager_v1.ReportDefinition.DateRange.FixedDateRange(
                        start_date=self._gam_date(start_date),
                        end_date=self._gam_date(end_date),
                    )
                ),
                report_type=admanager_v1.ReportDefinition.ReportType.HISTORICAL,
                currency_code="USD",
            )
        )

        created = self._report_client.create_report(
            admanager_v1.CreateReportRequest(parent=self._parent, report=report)
        )
        logger.info("GAM report created: %s", created.name)

        operation = self._report_client.run_report(
            admanager_v1.RunReportRequest(name=created.name)
        )
        result = operation.result()
        logger.info("GAM report complete: %s", result.report_result)

        col_names = [d.lower() for d in dimensions] + [m.lower() for m in metrics]
        records = []
        for row in self._report_client.fetch_report_result_rows(
            admanager_v1.FetchReportResultRowsRequest(
                name=result.report_result, page_size=10_000
            )
        ):
            d_vals = [_rv(v) for v in row.dimension_values]
            m_vals = (
                [_rv(v) for v in row.metric_value_groups[0].primary_values]
                if row.metric_value_groups
                else [None] * len(metrics)
            )
            records.append(d_vals + m_vals)

        df = pd.DataFrame(records, columns=col_names)

        # REST API returns DATE dimension as an integer (YYYYMMDD). Convert to string.
        if "date" in df.columns and pd.api.types.is_integer_dtype(df["date"]):
            df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")

        logger.info("GAM report: %d rows, columns=%s", len(df), list(df.columns))
        return df

    # ------------------------------------------------------------------
    # Delivery report
    # ------------------------------------------------------------------

    def run_delivery_report(self, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Run a GAM delivery report for line items between start_date and end_date.

        Returns a DataFrame with columns matching the existing downstream expectations,
        including ad_server_cpm_and_cpc_revenue (mapped from AD_SERVER_GROSS_REVENUE).
        """
        df = self._run_report(
            dimensions=[
                "DATE",
                "LINE_ITEM_ID",
                "LINE_ITEM_NAME",
                "ORDER_ID",
                "ORDER_NAME",
            ],
            metrics=[
                "AD_SERVER_IMPRESSIONS",
                "AD_SERVER_CLICKS",
                "AD_SERVER_CTR",
                "AD_SERVER_REVENUE",
                "AD_SERVER_AVERAGE_ECPM",
                "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
                "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS_RATE",
                "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
                "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS_RATE",
                "AD_SERVER_ACTIVE_VIEW_ELIGIBLE_IMPRESSIONS",
            ],
            start_date=start_date,
            end_date=end_date,
        )
        df = df.rename(columns={"ad_server_revenue": "ad_server_cpm_and_cpc_revenue"})
        return df

    # ------------------------------------------------------------------
    # Programmatic deal report (PA / PD / PG by deal name)
    # ------------------------------------------------------------------

    def run_deals_report(self, start_date: date, end_date: date) -> pd.DataFrame:
        """
        Pull PA / PD / PG deals from GAM via ORDER_NAME + DEAL_NAME.

        Using ORDER_NAME instead of DEAL_ID because PA deals carry a valid DEAL_NAME
        (e.g. Newsweek_PA_*) but their DEAL_ID is 0 in the API — filtering on non-zero
        DEAL_ID silently drops all PA rows. DEAL_NAME alone is the authoritative key;
        deal type is classified from the name by _parse_deal() in the dashboard.
        """
        df = self._run_report(
            dimensions=["DATE", "ORDER_NAME", "DEAL_NAME", "DEAL_BUYER_NAME", "INVENTORY_FORMAT_NAME", "PROGRAMMATIC_CHANNEL_NAME"],
            metrics=["AD_SERVER_IMPRESSIONS", "AD_SERVER_REVENUE", "AD_SERVER_AVERAGE_ECPM"],
            start_date=start_date,
            end_date=end_date,
        ).rename(columns={
            "deal_name": "programmatic_deal_name",
            "deal_buyer_name": "dsp",
            "inventory_format_name": "ad_format",
            "ad_server_revenue": "ad_server_cpm_and_cpc_revenue",
        })
        # Strip whitespace from all string columns so duplicates don't appear in DSP/Format filters
        for _col in df.select_dtypes(include="object").columns:
            df[_col] = df[_col].str.strip()

        df = df[
            df["programmatic_deal_name"].notna()
            & ~df["programmatic_deal_name"].isin(["", "(Not applicable)"])
        ]
        logger.info("GAM deals report: %d rows, channels=%s",
                    len(df),
                    df["programmatic_channel_name"].value_counts().to_dict() if not df.empty else {})
        return df

    # ------------------------------------------------------------------
    # Lifetime delivery (for pacing)
    # ------------------------------------------------------------------

    def run_lifetime_delivery(self) -> pd.DataFrame:
        """
        Fetch cumulative impressions per line item over a 2-year window.
        Used for pacing — covers all realistic active campaign durations.
        """
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=730)

        df = self._run_report(
            dimensions=["LINE_ITEM_ID"],
            metrics=["AD_SERVER_IMPRESSIONS"],
            start_date=start,
            end_date=end,
        )
        df["line_item_id"] = df["line_item_id"].astype(str)
        return df.rename(columns={"ad_server_impressions": "lifetime_impressions_delivered"})

    # ------------------------------------------------------------------
    # Line items
    # ------------------------------------------------------------------

    _LI_COLUMNS = [
        "line_item_id", "line_item_name", "order_id", "order_name",
        "line_item_type", "impressions_goal", "cpm_rate",
        "start_date", "end_date", "status", "salesperson",
    ]

    def get_active_line_items(self) -> pd.DataFrame:
        """
        Fetch active line items with their metadata.

        Returns DataFrame with: line_item_id, line_item_name, order_id,
        order_name, line_item_type, impressions_goal, cpm_rate,
        start_date, end_date, status, salesperson.
        """
        today = date.today()
        cutoff = (today - timedelta(days=30)).isoformat() + "T00:00:00Z"

        rows = []
        for li in self._li_client.list_line_items(
            admanager_v1.ListLineItemsRequest(
                parent=self._parent,
                filter=f'endTime > "{cutoff}"',
            )
        ):
            # Parse numeric IDs from resource name strings
            li_id_m = re.search(r"/lineItems/(\d+)$", li.name)
            li_id = li_id_m.group(1) if li_id_m else li.name

            order_ref = str(getattr(li, "order", "") or "")
            ord_id_m = re.search(r"/orders/(\d+)", order_ref)
            ord_id = ord_id_m.group(1) if ord_id_m else order_ref

            # Impression goal
            goal = getattr(li, "goal", None) or getattr(li, "primary_goal", None)
            units = int(goal.units) if goal and getattr(goal, "units", None) else None
            impressions_goal = units if units and units > 0 else None

            # CPM rate
            rate = getattr(li, "rate", None)
            cost_type = _enum_name(getattr(li, "cost_type", "") or "").upper()
            cpm_rate = _money(rate) if rate and cost_type == "CPM" else None

            rows.append({
                "line_item_id": li_id,
                "line_item_name": getattr(li, "display_name", None),
                "order_id": ord_id,
                "order_name": None,
                "line_item_type": _enum_name(getattr(li, "line_item_type", "") or ""),
                "impressions_goal": impressions_goal,
                "cpm_rate": cpm_rate,
                "start_date": _ts_to_date(getattr(li, "start_time", None)),
                "end_date": _ts_to_date(getattr(li, "end_time", None)),
                "status": _enum_name(getattr(li, "status", "") or ""),
                "salesperson": None,
            })

        logger.info("GAM: fetched %d line items (endTime > %s)", len(rows), cutoff)
        if not rows:
            return pd.DataFrame(columns=self._LI_COLUMNS)
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
        df_lifetime = self.run_lifetime_delivery()

        # Aggregate 7-day delivery per line item (for trend metrics)
        agg_spec = {
            "impressions_delivered": ("ad_server_impressions", "sum"),
            "ad_server_clicks": ("ad_server_clicks", "sum"),
            "ad_server_ctr": ("ad_server_ctr", "mean"),
            "ad_server_cpm_and_cpc_revenue": ("ad_server_cpm_and_cpc_revenue", "sum"),
        }
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

        agg = df_delivery.groupby(["line_item_id"], as_index=False).agg(**agg_spec)

        # Yesterday's impressions only (most recent date in the report window)
        latest_date = df_delivery["date"].max()
        agg_1d = (
            df_delivery[df_delivery["date"] == latest_date]
            .groupby("line_item_id", as_index=False)["ad_server_impressions"]
            .sum()
            .rename(columns={"ad_server_impressions": "impressions_1d"})
        )
        agg_1d["line_item_id"] = agg_1d["line_item_id"].astype(str)

        agg["line_item_id"] = agg["line_item_id"].astype(str)
        df_items["line_item_id"] = df_items["line_item_id"].astype(str)

        merged = df_items.merge(agg, on="line_item_id", how="left")
        merged = merged.merge(df_lifetime, on="line_item_id", how="left")
        merged = merged.merge(agg_1d, on="line_item_id", how="left")

        # VCR
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

        # Pacing
        today = date.today()

        def _pacing(row) -> Optional[float]:
            try:
                goal = row["impressions_goal"]
                delivered = row["lifetime_impressions_delivered"]
                has_goal = goal and goal > 0 and pd.notna(delivered)

                raw_start = pd.to_datetime(row["start_date"])
                raw_end = pd.to_datetime(row["end_date"])
                has_dates = pd.notna(raw_start) and pd.notna(raw_end)

                if has_dates:
                    li_start = raw_start.date()
                    li_end = raw_end.date()
                    total_days = max((li_end - li_start).days, 1)
                    elapsed = max((min(today, li_end) - li_start).days, 1)
                    if has_goal:
                        return (delivered / goal) / (elapsed / total_days) * 100
                    return elapsed / total_days * 100

                if has_goal:
                    return delivered / goal * 100

                return None
            except Exception:
                return None

        merged["pacing_pct"] = merged.apply(_pacing, axis=1)
        merged["report_start"] = start_date.isoformat()
        merged["report_end"] = end_date.isoformat()

        return merged
