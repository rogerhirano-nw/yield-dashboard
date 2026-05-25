"""
High-RPM investigation for 2026-05-24.

GAM REST API constraint: AD_SERVER_* metrics are incompatible with the HOUR
dimension entirely (REPORT_ERROR_CONSTRAINTS_INCOMPATIBILITY regardless of
which other entity dimensions are added). HOUR works only with programmatic
metrics (IMPRESSIONS, REVENUE_WITHOUT_CPD, YIELD_GROUP_*).

Strategy:
  1. Direct line items — daily granularity, sorted by eCPM (DATE + LINE_ITEM_*)
  2. Direct by line-item type — daily revenue/eCPM breakdown (DATE + LINE_ITEM_TYPE_NAME)
  3. Programmatic deals by hour — PA/PD/PG (HOUR + DEAL_NAME)  [already working]
  4. Open Bidding by hour — yield-group level (HOUR + YIELD_GROUP_NAME)  [already working]

Together these answer: which direct campaigns drove the high eCPM, and what
the hourly programmatic + OB contribution looked like alongside them.

Run via: .github/workflows/investigate_high_rpm.yml
Requires: GAM_SERVICE_ACCOUNT_JSON, GAM_NETWORK_ID
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pandas as pd
from google.ads import admanager_v1
from google.oauth2 import service_account
from google.type import date_pb2

# ── auth ──────────────────────────────────────────────────────────────────────
_SCOPES = ["https://www.googleapis.com/auth/admanager"]
_D = admanager_v1.ReportDefinition.Dimension
_M = admanager_v1.ReportDefinition.Metric

INVESTIGATE_DATE = date(2026, 5, 24)
NETWORK_ID       = os.environ["GAM_NETWORK_ID"]
SA_JSON          = os.environ["GAM_SERVICE_ACCOUNT_JSON"]

creds  = service_account.Credentials.from_service_account_info(json.loads(SA_JSON), scopes=_SCOPES)
client = admanager_v1.ReportServiceClient(credentials=creds)
PARENT = f"networks/{NETWORK_ID}"


# ── helpers ───────────────────────────────────────────────────────────────────

def _rv(rv):
    pb    = type(rv).pb(rv)
    field = pb.WhichOneof("value")
    if field == "int_value":    return pb.int_value
    if field == "double_value": return pb.double_value
    if field == "string_value": return pb.string_value
    return None


def _gam_date(d: date) -> date_pb2.Date:
    return date_pb2.Date(year=d.year, month=d.month, day=d.day)


def run_report(dimensions: list[str], metrics: list[str],
               start: date, end: date) -> pd.DataFrame:
    report = admanager_v1.Report(
        report_definition=admanager_v1.ReportDefinition(
            dimensions=[_D[d] for d in dimensions],
            metrics=[_M[m] for m in metrics],
            date_range=admanager_v1.ReportDefinition.DateRange(
                fixed=admanager_v1.ReportDefinition.DateRange.FixedDateRange(
                    start_date=_gam_date(start),
                    end_date=_gam_date(end),
                )
            ),
            report_type=admanager_v1.ReportDefinition.ReportType.HISTORICAL,
            currency_code="USD",
        )
    )
    created   = client.create_report(admanager_v1.CreateReportRequest(parent=PARENT, report=report))
    operation = client.run_report(admanager_v1.RunReportRequest(name=created.name))
    result    = operation.result()

    col_names = [d.lower() for d in dimensions] + [m.lower() for m in metrics]
    records   = []
    for row in client.fetch_report_result_rows(
        admanager_v1.FetchReportResultRowsRequest(name=result.report_result, page_size=10_000)
    ):
        d_vals = [_rv(v) for v in row.dimension_values]
        m_vals = (
            [_rv(v) for v in row.metric_value_groups[0].primary_values]
            if row.metric_value_groups else [None] * len(metrics)
        )
        records.append(d_vals + m_vals)

    df = pd.DataFrame(records, columns=col_names)
    if "date" in df.columns and pd.api.types.is_integer_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].astype(str).str.strip()
    return df


def ecpm(rev: "pd.Series", imp: "pd.Series") -> "pd.Series":
    return (rev / imp.where(imp > 0) * 1000).fillna(0.0)


# ── report 1: direct line-item eCPM ranking (daily) ──────────────────────────
# AD_SERVER_* metrics work with DATE + entity dims but NOT with HOUR.
# Daily granularity is sufficient to identify which campaigns drove the spike.

print("\n## 1 · Direct line items — eCPM ranking, 2026-05-24\n")
print("Pulling direct line-item delivery …", file=sys.stderr)

try:
    li_daily = run_report(
        dimensions=["DATE", "LINE_ITEM_ID", "LINE_ITEM_NAME", "ORDER_NAME"],
        metrics=["AD_SERVER_IMPRESSIONS", "AD_SERVER_REVENUE"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    li_daily = li_daily.rename(columns={"ad_server_revenue": "revenue"})
    li_daily["impressions"] = pd.to_numeric(li_daily["ad_server_impressions"], errors="coerce").fillna(0).astype(int)
    li_daily["revenue"]     = pd.to_numeric(li_daily["revenue"], errors="coerce").fillna(0.0)
    li_daily["ecpm"]        = ecpm(li_daily["revenue"], li_daily["impressions"])

    # Day totals
    total_imps = li_daily["impressions"].sum()
    total_rev  = li_daily["revenue"].sum()
    print(f"**Direct total:** {total_imps:,} impressions · ${total_rev:,.2f} revenue · "
          f"${ecpm(pd.Series([total_rev]), pd.Series([total_imps])).iloc[0]:.2f} avg eCPM\n")

    # Top 25 by eCPM (reveals the high-CPM campaigns)
    top_ecpm = li_daily[li_daily["impressions"] >= 100].sort_values("ecpm", ascending=False).head(25)
    print("**Top 25 line items by eCPM** (min 100 imps):\n")
    print("| Line Item | Order | Imps | Revenue | eCPM |")
    print("|-----------|-------|------|---------|------|")
    for _, row in top_ecpm.iterrows():
        li   = str(row["line_item_name"])[:55]
        ord_ = str(row["order_name"])[:35]
        print(f"| {li:<55} | {ord_:<35} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

    # Top 25 by revenue (shows what actually drove the day)
    top_rev = li_daily.sort_values("revenue", ascending=False).head(25)
    print("\n\n**Top 25 line items by revenue:**\n")
    print("| Line Item | Order | Imps | Revenue | eCPM |")
    print("|-----------|-------|------|---------|------|")
    for _, row in top_rev.iterrows():
        li   = str(row["line_item_name"])[:55]
        ord_ = str(row["order_name"])[:35]
        print(f"| {li:<55} | {ord_:<35} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

except Exception as e:
    print(f"⚠ Direct line-item report failed: {e}\n")


# ── report 2: direct revenue by line-item type (daily) ───────────────────────

print("\n## 2 · Direct revenue by line-item type, 2026-05-24\n")
print("Pulling line-item-type breakdown …", file=sys.stderr)

try:
    type_daily = run_report(
        dimensions=["DATE", "LINE_ITEM_TYPE_NAME"],
        metrics=["AD_SERVER_IMPRESSIONS", "AD_SERVER_REVENUE"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    type_daily = type_daily.rename(columns={"ad_server_revenue": "revenue"})
    type_daily["impressions"] = pd.to_numeric(type_daily["ad_server_impressions"], errors="coerce").fillna(0).astype(int)
    type_daily["revenue"]     = pd.to_numeric(type_daily["revenue"], errors="coerce").fillna(0.0)
    type_daily["ecpm"]        = ecpm(type_daily["revenue"], type_daily["impressions"])
    type_daily = type_daily.sort_values("revenue", ascending=False)

    print("| Line-Item Type | Impressions | Revenue | eCPM |")
    print("|----------------|-------------|---------|------|")
    for _, row in type_daily.iterrows():
        print(f"| {str(row['line_item_type_name']):<40} | {int(row['impressions']):>11,} | ${row['revenue']:>9.2f} | ${row['ecpm']:>6.2f} |")

except Exception as e:
    print(f"⚠ Line-item-type report failed: {e}\n")


# ── report 3: programmatic deal eCPM by hour (PA / PD / PG) ──────────────────

print("\n## 3 · Programmatic deal eCPM by hour (PA / PD / PG)\n")
print("Pulling hourly programmatic deal revenue …", file=sys.stderr)

try:
    deal_hourly = run_report(
        dimensions=["HOUR", "DEAL_NAME", "PROGRAMMATIC_CHANNEL_NAME"],
        metrics=["IMPRESSIONS", "REVENUE_WITHOUT_CPD"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    deal_hourly = deal_hourly.rename(columns={
        "impressions": "imps_raw",
        "revenue_without_cpd": "revenue",
    })
    deal_hourly = deal_hourly[
        deal_hourly["deal_name"].notna()
        & ~deal_hourly["deal_name"].isin(["", "(Not applicable)", "nan"])
    ].copy()
    deal_hourly["impressions"] = pd.to_numeric(deal_hourly["imps_raw"], errors="coerce").fillna(0).astype(int)
    deal_hourly["revenue"]     = pd.to_numeric(deal_hourly["revenue"], errors="coerce").fillna(0.0)
    deal_hourly["hour"]        = deal_hourly["hour"].astype(int)

    deal_by_hour = deal_hourly.groupby("hour").agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index().sort_values("hour")
    deal_by_hour["ecpm"] = ecpm(deal_by_hour["revenue"], deal_by_hour["impressions"])

    print("| Hour (UTC) | Deal Imps | Deal Revenue | Deal eCPM |")
    print("|------------|-----------|--------------|-----------|")
    for _, row in deal_by_hour.iterrows():
        print(f"| {int(row['hour']):02d}:00       | {int(row['impressions']):>9,} | ${row['revenue']:>12.2f} | ${row['ecpm']:>8.2f} |")

    deal_agg = deal_hourly.groupby(["deal_name", "programmatic_channel_name"]).agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index()
    deal_agg["ecpm"] = ecpm(deal_agg["revenue"], deal_agg["impressions"])
    deal_agg = deal_agg.sort_values("revenue", ascending=False).head(15)

    print(f"\n\nTop 15 deals — full day:\n")
    print("| Deal | Channel | Imps | Revenue | eCPM |")
    print("|------|---------|------|---------|------|")
    for _, row in deal_agg.iterrows():
        print(f"| {str(row['deal_name'])[:50]:<50} | {str(row['programmatic_channel_name'])[:20]:<20} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

except Exception as e:
    print(f"⚠ Deal hourly report failed: {e}\n")


# ── report 4: open-bidding eCPM by hour ──────────────────────────────────────

print("\n## 4 · Open Bidding eCPM by hour\n")
print("Pulling yield-group hourly data …", file=sys.stderr)

try:
    ob_hourly = run_report(
        dimensions=["HOUR", "YIELD_GROUP_NAME"],
        metrics=["YIELD_GROUP_IMPRESSIONS", "YIELD_GROUP_ESTIMATED_REVENUE"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    ob_hourly["impressions"] = pd.to_numeric(ob_hourly["yield_group_impressions"], errors="coerce").fillna(0).astype(int)
    ob_hourly["revenue"]     = pd.to_numeric(ob_hourly["yield_group_estimated_revenue"], errors="coerce").fillna(0.0)
    ob_hourly["hour"]        = ob_hourly["hour"].astype(int)

    ob_by_hour = ob_hourly.groupby("hour").agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index().sort_values("hour")
    ob_by_hour["ecpm"] = ecpm(ob_by_hour["revenue"], ob_by_hour["impressions"])

    print("| Hour (UTC) | OB Imps | OB Revenue | OB eCPM |")
    print("|------------|---------|------------|---------|")
    for _, row in ob_by_hour.iterrows():
        print(f"| {int(row['hour']):02d}:00       | {int(row['impressions']):>7,} | ${row['revenue']:>10.2f} | ${row['ecpm']:>7.2f} |")

    og_agg = ob_hourly.groupby("yield_group_name").agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index()
    og_agg["ecpm"] = ecpm(og_agg["revenue"], og_agg["impressions"])
    print(f"\n\nBy yield group:\n")
    print("| Yield Group | Imps | Revenue | eCPM |")
    print("|-------------|------|---------|------|")
    for _, row in og_agg.iterrows():
        print(f"| {str(row['yield_group_name']):<40} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

except Exception as e:
    print(f"⚠ OB yield-group report failed: {e}\n")


print("\n---\n_Investigation complete. UTC times; subtract 4h for EDT._\n")
