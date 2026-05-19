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
    """Convert a protobuf Timestamp or Python datetime to a YYYY-MM-DD string."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.date().isoformat()
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
        self._order_client = admanager_v1.OrderServiceClient(credentials=creds)
        self._user_client = admanager_v1.UserServiceClient(credentials=creds)
        self._parent = f"networks/{self.network_id}"
        if hasattr(admanager_v1, "PrivateAuctionServiceClient"):
            self._pa_client = admanager_v1.PrivateAuctionServiceClient(credentials=creds)
            self._pad_client = admanager_v1.PrivateAuctionDealServiceClient(credentials=creds)
        else:
            self._pa_client = None
            self._pad_client = None

        # Creative + LICA clients — used to pull video duration so the
        # dashboard can apply the "Video Preroll >30s" benchmark.
        self._creative_client = (
            admanager_v1.CreativeServiceClient(credentials=creds)
            if hasattr(admanager_v1, "CreativeServiceClient") else None
        )
        _lica_cls = (
            getattr(admanager_v1, "LineItemCreativeAssociationServiceClient", None)
            or getattr(admanager_v1, "LineItemCreativeAssociationsServiceClient", None)
        )
        self._lica_client = _lica_cls(credentials=creds) if _lica_cls else None

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
                # VCR — pull viewership starts + completes; the dashboard's
                # downstream VCR calc is (completes / starts * 100). Probed
                # against admanager_v1.ReportDefinition.Metric to confirm
                # these are the actual valid enum members (the previously-
                # tried VIDEO_INTERACTION_VIDEO_* names don't exist).
                "VIDEO_VIEWERSHIP_STARTS",
                "VIDEO_VIEWERSHIP_COMPLETES",
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
        Pull PA / PD / PG deals from GAM keyed by ORDER_NAME + DEAL_NAME.

        Uses the programmatic / Ad Exchange metric set (IMPRESSIONS,
        REVENUE_WITHOUT_CPD) rather than AD_SERVER_*. AD_SERVER_* counts only
        impressions that flow through the Ad Server pipeline and excludes
        Private Auction entirely (PA serves through Ad Exchange directly),
        which is why earlier versions of this report returned 0 PA rows. The
        programmatic metrics include PA, PD, and PG. eCPM is computed
        post-hoc from impressions and revenue.

        Column names downstream (gam_pmp_deals schema, dashboard code)
        remain ad_server_impressions / ad_server_cpm_and_cpc_revenue /
        ad_server_average_ecpm so existing consumers keep working unchanged.
        """
        df = self._run_report(
            dimensions=["DATE", "ORDER_NAME", "DEAL_NAME", "DEAL_BUYER_NAME", "INVENTORY_FORMAT_NAME", "PROGRAMMATIC_CHANNEL_NAME"],
            metrics=["IMPRESSIONS", "REVENUE_WITHOUT_CPD"],
            start_date=start_date,
            end_date=end_date,
        ).rename(columns={
            "deal_name": "programmatic_deal_name",
            "deal_buyer_name": "dsp",
            "inventory_format_name": "ad_format",
            "impressions": "ad_server_impressions",
            "revenue_without_cpd": "ad_server_cpm_and_cpc_revenue",
        })
        for _col in df.select_dtypes(include="object").columns:
            df[_col] = df[_col].str.strip()

        df = df[
            df["programmatic_deal_name"].notna()
            & ~df["programmatic_deal_name"].isin(["", "(Not applicable)"])
        ].copy()

        # Numeric metrics + eCPM (revenue per thousand impressions).
        imp = pd.to_numeric(df["ad_server_impressions"], errors="coerce").fillna(0)
        rev = pd.to_numeric(df["ad_server_cpm_and_cpc_revenue"], errors="coerce").fillna(0.0)
        df["ad_server_impressions"] = imp.astype("int64")
        df["ad_server_cpm_and_cpc_revenue"] = rev
        df["ad_server_average_ecpm"] = ((rev / imp.where(imp > 0)) * 1000).fillna(0.0)

        logger.info("GAM deals report: %d rows, channels=%s",
                    len(df),
                    df["programmatic_channel_name"].value_counts().to_dict() if not df.empty else {})
        return df

    # ------------------------------------------------------------------
    # Lifetime delivery (for pacing)
    # ------------------------------------------------------------------

    def run_lifetime_delivery(self) -> pd.DataFrame:
        """
        Fetch cumulative delivery metrics per line item over a 2-year window.
        Used for pacing + the lifetime cell values in the Direct Campaigns
        table (Clicks, Revenue, Viewability, CTR are computed from these).
        """
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=730)

        df = self._run_report(
            dimensions=["LINE_ITEM_ID", "LINE_ITEM_COMPUTED_STATUS_NAME"],
            metrics=[
                "AD_SERVER_IMPRESSIONS",
                "AD_SERVER_CLICKS",
                "AD_SERVER_REVENUE",
                "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
                "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
            ],
            start_date=start,
            end_date=end,
        )
        df["line_item_id"] = df["line_item_id"].astype(str)
        return df.rename(columns={
            "ad_server_impressions": "lifetime_impressions_delivered",
            "ad_server_clicks": "lifetime_clicks",
            "ad_server_revenue": "lifetime_revenue",
            "ad_server_active_view_viewable_impressions": "lifetime_viewable_imps",
            "ad_server_active_view_measurable_impressions": "lifetime_measurable_imps",
            "line_item_computed_status_name": "status_api",
        })

    # ------------------------------------------------------------------
    # Line items
    # ------------------------------------------------------------------

    _LI_COLUMNS = [
        "line_item_id", "line_item_name", "order_id", "order_name",
        "line_item_type", "impressions_goal", "cpm_rate",
        "start_date", "end_date", "status", "salesperson",
    ]

    def _fetch_order_info(self, order_resource_names: set[str]) -> dict[str, dict]:
        """
        Given a set of order resource names, return a dict mapping each to
        {"order_name": str | None, "salesperson": str | None}.

        Fetches each order individually and resolves the salesperson user's
        display_name, caching user lookups to avoid redundant calls.
        """
        user_cache: dict[str, Optional[str]] = {}
        result: dict[str, dict] = {}
        for order_ref in order_resource_names:
            if not order_ref:
                continue
            try:
                order = self._order_client.get_order(
                    admanager_v1.GetOrderRequest(name=order_ref)
                )
                sp_ref = order.salesperson or ""
                if sp_ref not in user_cache:
                    try:
                        user = self._user_client.get_user(
                            admanager_v1.GetUserRequest(name=sp_ref)
                        )
                        user_cache[sp_ref] = user.display_name or None
                    except Exception:
                        user_cache[sp_ref] = None
                result[order_ref] = {
                    "order_name": order.display_name or None,
                    "salesperson": user_cache.get(sp_ref),
                }
            except Exception:
                result[order_ref] = {"order_name": None, "salesperson": None}
        return result

    def get_active_line_items(self) -> pd.DataFrame:
        """
        Fetch active line items with their metadata.

        Returns DataFrame with: line_item_id, line_item_name, order_id,
        order_name, line_item_type, impressions_goal, cpm_rate,
        start_date, end_date, status, salesperson.
        """
        today = date.today()
        cutoff = (today - timedelta(days=30)).isoformat() + "T00:00:00Z"

        # First pass: collect all line items and unique order resource names.
        raw_rows = []
        order_refs: set[str] = set()
        for li in self._li_client.list_line_items(
            admanager_v1.ListLineItemsRequest(
                parent=self._parent,
                filter=f'endTime > "{cutoff}"',
            )
        ):
            li_id_m = re.search(r"/lineItems/(\d+)$", li.name)
            li_id = li_id_m.group(1) if li_id_m else li.name

            order_ref = str(getattr(li, "order", "") or "")
            ord_id_m = re.search(r"/orders/(\d+)", order_ref)
            ord_id = ord_id_m.group(1) if ord_id_m else order_ref
            order_refs.add(order_ref)

            goal = getattr(li, "goal", None) or getattr(li, "primary_goal", None)
            units = int(goal.units) if goal and getattr(goal, "units", None) else None
            impressions_goal = units if units and units > 0 else None

            rate = getattr(li, "rate", None)
            cpm_rate = _money(rate) if rate else None

            # Status — REST API v1 LineItem has no status field; derive from dates.
            start_str = _ts_to_date(getattr(li, "start_time", None))
            end_str   = _ts_to_date(getattr(li, "end_time", None))
            if start_str and end_str:
                start_d, end_d = date.fromisoformat(start_str), date.fromisoformat(end_str)
                if end_d < today:
                    li_status = "Completed"
                elif start_d > today:
                    li_status = "Upcoming"
                else:
                    li_status = "Delivering"
            else:
                li_status = ""

            raw_rows.append({
                "line_item_id": li_id,
                "line_item_name": getattr(li, "display_name", None),
                "order_id": ord_id,
                "_order_ref": order_ref,
                "line_item_type": _enum_name(getattr(li, "line_item_type", "") or ""),
                "impressions_goal": impressions_goal,
                "cpm_rate": cpm_rate,
                "start_date": start_str,
                "end_date": end_str,
                "status": li_status,
            })

        # Second pass: fetch order metadata (name + salesperson) for all unique orders.
        order_info = self._fetch_order_info(order_refs)
        logger.info("GAM: fetched %d line items across %d orders", len(raw_rows), len(order_refs))

        rows = []
        for r in raw_rows:
            info = order_info.get(r.pop("_order_ref"), {})
            rows.append({**r, "order_name": info.get("order_name"), "salesperson": info.get("salesperson")})
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    # Private Auction deal metadata
    # ------------------------------------------------------------------

    def get_private_auctions(self) -> pd.DataFrame:
        """
        Fetch Private Auction deal metadata via the GAM REST API.

        ListPrivateAuctionDeals takes the network as parent (not an auction).
        One call returns every PA deal on the network; auction display names
        come from a separate ListPrivateAuctions call and are joined in via
        each deal's `private_auction` reference.

        Returns DataFrame with one row per PA deal: auction_name, deal_name,
        external_deal_id, buyer_account_id, floor_price_usd, deal_status,
        end_time. Note: this is inventory metadata only — GAM's reporting
        API does not expose PA delivery (impressions/revenue) at the deal
        level, so this can't be joined to gam_pmp_deals for revenue stats.
        """
        _cols = [
            "auction_name", "external_deal_id",
            "buyer_account_id", "floor_price_usd", "deal_status", "end_time",
        ]
        if self._pa_client is None:
            logger.warning("PrivateAuctionServiceClient not available — upgrade google-ads-admanager")
            return pd.DataFrame(columns=_cols)

        # Build auction resource name → display name lookup (skip archived)
        auction_names: dict[str, str] = {}
        for auction in self._pa_client.list_private_auctions(
            admanager_v1.ListPrivateAuctionsRequest(parent=self._parent)
        ):
            if not getattr(auction, "archived", False):
                auction_names[auction.name] = auction.display_name or ""

        # List all deals at network level — parent must be "networks/*" not per-auction

        rows = []
        for deal in self._pad_client.list_private_auction_deals(
            admanager_v1.ListPrivateAuctionDealsRequest(parent=self._parent)
        ):
            # deal.name = networks/X/privateAuctions/Y/privateAuctionDeals/Z
            # extract auction ref = networks/X/privateAuctions/Y
            parts = deal.name.rsplit("/privateAuctionDeals/", 1)
            auction_ref = parts[0] if len(parts) == 2 else None
            if auction_ref not in auction_names:
                continue  # skip deals belonging to archived auctions
            rows.append({
                "auction_name":     auction_names[auction_ref],
                "deal_name":        deal.display_name or None,
                "external_deal_id": str(deal.external_deal_id) if getattr(deal, "external_deal_id", None) else None,
                "buyer_account_id": str(deal.buyer_account_id) if getattr(deal, "buyer_account_id", None) else None,
                "floor_price_usd":  _money(getattr(deal, "floor_price", None)),
                "deal_status":      _enum_name(deal.status) if getattr(deal, "status", None) else None,
                "end_time":         _ts_to_date(getattr(deal, "end_time", None)),
            })

        logger.info("GAM private auctions: %d deals across %d non-archived auctions", len(rows), len(auction_names))
        return pd.DataFrame(rows, columns=_cols) if rows else pd.DataFrame(columns=_cols)

    # ------------------------------------------------------------------
    # Creatives + LineItemCreativeAssociations (LICA) — powers the
    # "Video Preroll >30s" benchmark by exposing creative duration.
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_duration_seconds(msg) -> Optional[float]:
        """Try several common shapes for a video-creative duration field.
        Returns seconds (float) or None. Defensive against SDK variation:
        v1 Creative is polymorphic and may expose duration directly or
        nested inside a sub-message like video_creative / vast_creative."""
        if msg is None:
            return None

        def _coerce(v):
            if v is None:
                return None
            # google.protobuf.Duration (seconds + nanos)
            if hasattr(v, "seconds"):
                nanos = getattr(v, "nanos", 0) or 0
                return float(v.seconds) + nanos / 1e9
            # Plain number — assume ms when over 1000 (common for GAM).
            if isinstance(v, (int, float)):
                return float(v) / 1000.0 if v > 1000 else float(v)
            return None

        # Direct fields
        for fld in ("duration", "video_duration", "duration_ms"):
            sec = _coerce(getattr(msg, fld, None))
            if sec is not None:
                return sec
        # Sub-messages by likely creative-type name
        for sub_name in ("video_creative", "vast_xml_creative",
                         "vast_redirect_creative", "video",
                         "vast_xml", "vast_redirect"):
            sub = getattr(msg, sub_name, None)
            if sub is None:
                continue
            for fld in ("duration", "video_duration", "duration_ms"):
                sec = _coerce(getattr(sub, fld, None))
                if sec is not None:
                    return sec
        return None

    def list_creatives_with_duration(self) -> pd.DataFrame:
        """List all creatives on the network with extracted video duration.
        Non-video creatives have duration_seconds = None."""
        _cols = ["creative_id", "display_name", "creative_type", "duration_seconds"]
        if self._creative_client is None:
            logger.warning("CreativeServiceClient not available — upgrade google-ads-admanager")
            return pd.DataFrame(columns=_cols)

        ListReq = getattr(admanager_v1, "ListCreativesRequest", None)
        rows = []
        try:
            if ListReq is not None:
                iterator = self._creative_client.list_creatives(ListReq(parent=self._parent))
            else:
                iterator = self._creative_client.list_creatives(parent=self._parent)
            for c in iterator:
                name = getattr(c, "name", "") or ""
                cid = name.rsplit("/", 1)[-1] if name else None
                dn = getattr(c, "display_name", "") or ""
                # Detect creative type — try a few common attributes.
                ct = None
                for attr in ("creative_type", "type"):
                    v = getattr(c, attr, None)
                    if v is not None:
                        ct = getattr(v, "name", None) or str(v)
                        break
                # Fall back to whichever sub-message is populated.
                if not ct:
                    for sub in ("image_creative", "video_creative", "vast_xml_creative",
                                "vast_redirect_creative", "third_party_creative",
                                "custom_creative", "template_creative"):
                        if getattr(c, sub, None):
                            ct = sub
                            break
                duration = self._extract_duration_seconds(c)
                rows.append({
                    "creative_id":      cid,
                    "display_name":     dn,
                    "creative_type":    ct,
                    "duration_seconds": duration,
                })
        except Exception:
            logger.exception("list_creatives failed")
            return pd.DataFrame(columns=_cols)

        logger.info("GAM creatives: %d total, %d with duration",
                    len(rows), sum(1 for r in rows if r["duration_seconds"] is not None))
        return pd.DataFrame(rows, columns=_cols) if rows else pd.DataFrame(columns=_cols)

    def list_line_item_creative_associations(self) -> pd.DataFrame:
        """List all LICAs on the network. Returns (line_item_id, creative_id)
        pairs so the dashboard can join creative metadata onto line items."""
        _cols = ["line_item_id", "creative_id"]
        if self._lica_client is None:
            logger.warning("LineItemCreativeAssociation client not available "
                           "— check google-ads-admanager version")
            return pd.DataFrame(columns=_cols)

        ListReq = (getattr(admanager_v1, "ListLineItemCreativeAssociationsRequest", None)
                   or getattr(admanager_v1, "ListLineItemCreativeAssociationRequest", None))
        rows = []
        try:
            # Method name varies by SDK version — try common forms.
            list_method = (getattr(self._lica_client, "list_line_item_creative_associations", None)
                           or getattr(self._lica_client, "list_lineitem_creative_associations", None))
            if list_method is None:
                logger.warning("LICA list method not found on the client")
                return pd.DataFrame(columns=_cols)
            if ListReq is not None:
                iterator = list_method(ListReq(parent=self._parent))
            else:
                iterator = list_method(parent=self._parent)
            for lica in iterator:
                li = getattr(lica, "line_item", "") or getattr(lica, "line_item_id", "")
                cr = getattr(lica, "creative", "")  or getattr(lica, "creative_id", "")
                rows.append({
                    "line_item_id": str(li).rsplit("/", 1)[-1] if li else None,
                    "creative_id":  str(cr).rsplit("/", 1)[-1] if cr else None,
                })
        except Exception:
            logger.exception("list_line_item_creative_associations failed")
            return pd.DataFrame(columns=_cols)

        logger.info("GAM LICA: %d associations", len(rows))
        return pd.DataFrame(rows, columns=_cols) if rows else pd.DataFrame(columns=_cols)

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
            "video_viewership_starts",
            "video_viewership_completes",
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

        # Per-day breakouts for the 1-day-vs-prior-day annotations in the
        # Direct Campaigns table. We capture both the latest day's totals
        # (impressions/clicks/viewable/measurable) and the day-before-latest's
        # totals so the dashboard can render "X (▲ +Y vs prior day)" strings.
        sorted_dates = sorted(d for d in df_delivery["date"].dropna().unique())
        latest_date  = sorted_dates[-1] if sorted_dates else None
        prior_date   = sorted_dates[-2] if len(sorted_dates) >= 2 else None

        _PER_DAY_METRICS = {
            "ad_server_impressions": "impressions",
            "ad_server_clicks":      "clicks",
            "ad_server_active_view_viewable_impressions":   "viewable_imps",
            "ad_server_active_view_measurable_impressions": "measurable_imps",
            "video_viewership_starts":    "video_starts",
            "video_viewership_completes": "video_completes",
            "ad_server_cpm_and_cpc_revenue": "revenue",
        }

        def _per_day(d, suffix):
            cols = {api: out for api, out in _PER_DAY_METRICS.items() if api in df_delivery.columns}
            if d is None or not cols:
                return pd.DataFrame(columns=["line_item_id"])
            sub = df_delivery[df_delivery["date"] == d]
            if sub.empty:
                return pd.DataFrame(columns=["line_item_id"])
            df_d = sub.groupby("line_item_id", as_index=False)[list(cols.keys())].sum()
            df_d = df_d.rename(columns={api: f"{out}_{suffix}" for api, out in cols.items()})
            df_d["line_item_id"] = df_d["line_item_id"].astype(str)
            return df_d

        agg["line_item_id"] = agg["line_item_id"].astype(str)
        df_items["line_item_id"] = df_items["line_item_id"].astype(str)

        merged = df_items.merge(agg, on="line_item_id", how="left")
        merged = merged.merge(df_lifetime, on="line_item_id", how="left")
        # Per-day breakouts for the last up-to-7 days — suffix 1d = most
        # recent, 7d = oldest. The KPI-strip sparklines consume these.
        _recent_dates = list(reversed(sorted_dates[-7:]))  # newest first
        for _i, _d in enumerate(_recent_dates, start=1):
            merged = merged.merge(_per_day(_d, f"{_i}d"), on="line_item_id", how="left")

        # Replace placeholders with real values from the reporting API.
        if "status_api" in merged.columns:
            _had_no_api_status = merged["status_api"].isna()
            merged["status"] = merged["status_api"].fillna(merged["status"])
            # Items with no delivery history that the date logic called "Delivering" are actually
            # paused or not yet live — a truly delivering line item would have impression data.
            merged.loc[_had_no_api_status & (merged["status"] == "Delivering"), "status"] = "Paused"
            merged = merged.drop(columns=["status_api"])

        # VCR — completes / starts. GAM v1's enum exposes these as
        # VIDEO_VIEWERSHIP_STARTS / VIDEO_VIEWERSHIP_COMPLETES (snake-cased
        # downstream).
        _vcr_starts     = "video_viewership_starts"
        _vcr_completes  = "video_viewership_completes"
        if _vcr_starts in merged.columns and _vcr_completes in merged.columns:
            merged["vcr"] = merged.apply(
                lambda r: (r[_vcr_completes] / r[_vcr_starts] * 100)
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
                # Sponsorship line items commit to a percentage of inventory,
                # not impressions. Pacing for sponsorships is structurally
                # "on track" — surface that as 100% regardless of delivery.
                lit = (row.get("line_item_type") or "").upper()
                if lit == "SPONSORSHIP":
                    return 100.0

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
