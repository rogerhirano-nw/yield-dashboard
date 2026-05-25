"""
High-RPM investigation for 2026-05-24.

Pulls three focused reports from GAM to explain why eCPM/RPM was elevated
(~$50) at certain hours yesterday:

  1. Hourly eCPM curve  — DATE + HOUR_OF_DAY, all metrics
  2. Top line-item contributors at peak hours (Direct / PG / PD)
  3. Programmatic deal breakdown by hour (PA / PD / PG)

Run via the GitHub Actions workflow: .github/workflows/investigate_high_rpm.yml
Requires GAM_SERVICE_ACCOUNT_JSON and GAM_NETWORK_ID environment variables.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from google.ads import admanager_v1
from google.oauth2 import service_account
from google.type import date_pb2

# ── auth ──────────────────────────────────────────────────────────────────────
_SCOPES = ["https://www.googleapis.com/auth/admanager"]
_D = admanager_v1.ReportDefinition.Dimension
_M = admanager_v1.ReportDefinition.Metric

INVESTIGATE_DATE = date(2026, 5, 24)   # yesterday
NETWORK_ID       = os.environ["GAM_NETWORK_ID"]
SA_JSON          = os.environ["GAM_SERVICE_ACCOUNT_JSON"]

creds   = service_account.Credentials.from_service_account_info(json.loads(SA_JSON), scopes=_SCOPES)
client  = admanager_v1.ReportServiceClient(credentials=creds)
PARENT  = f"networks/{NETWORK_ID}"


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
    # DATE comes back as YYYYMMDD int
    if "date" in df.columns and pd.api.types.is_integer_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    for c in df.select_dtypes(include="object").columns:
        df[c] = df[c].astype(str).str.strip()
    return df


def ecpm(rev: "pd.Series", imp: "pd.Series") -> "pd.Series":
    return (rev / imp.where(imp > 0) * 1000).fillna(0.0)


# ── report 1: hourly eCPM curve (all inventory combined) ─────────────────────

print("\n## 1 · Hourly eCPM curve — all inventory, 2026-05-24\n")
print("Pulling hourly direct (ad-server) delivery …", file=sys.stderr)

try:
    hourly = run_report(
        dimensions=["DATE", "HOUR_OF_DAY"],
        metrics=["AD_SERVER_IMPRESSIONS", "AD_SERVER_REVENUE", "AD_SERVER_AVERAGE_ECPM"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    hourly = hourly.rename(columns={
        "ad_server_revenue": "revenue",
        "ad_server_average_ecpm": "avg_ecpm_gam",
    })
    hourly["impressions"]    = pd.to_numeric(hourly["ad_server_impressions"], errors="coerce").fillna(0).astype(int)
    hourly["revenue"]        = pd.to_numeric(hourly["revenue"], errors="coerce").fillna(0.0)
    hourly["avg_ecpm_gam"]   = pd.to_numeric(hourly["avg_ecpm_gam"], errors="coerce").fillna(0.0)
    hourly["ecpm_calc"]      = ecpm(hourly["revenue"], hourly["impressions"])
    hourly = hourly.sort_values("hour_of_day")

    # Annotate peak hours (eCPM ≥ $40)
    peak = hourly[hourly["ecpm_calc"] >= 40.0]
    peak_hours = sorted(peak["hour_of_day"].astype(int).tolist())

    print(f"| Hour (ET) | Impressions | Revenue | eCPM (calc) |")
    print(f"|-----------|-------------|---------|-------------|")
    for _, row in hourly.iterrows():
        marker = " ◀ HIGH" if row["ecpm_calc"] >= 40 else ""
        print(f"| {int(row['hour_of_day']):02d}:00     | {int(row['impressions']):>11,} | ${row['revenue']:>7.2f} | ${row['ecpm_calc']:>10.2f}{marker} |")

    print(f"\n**Peak hours (eCPM ≥ $40): {peak_hours}**\n")
    daily_imps = hourly["impressions"].sum()
    daily_rev  = hourly["revenue"].sum()
    print(f"Day total: {daily_imps:,} impressions, ${daily_rev:,.2f} revenue → ${ecpm(pd.Series([daily_rev]), pd.Series([daily_imps])).iloc[0]:.2f} day-avg eCPM\n")

except Exception as e:
    print(f"⚠ Hourly curve report failed: {e}\n")
    peak_hours = []


# ── report 2: top line items at peak hours (direct / PG / PD) ────────────────

print("\n## 2 · Line-item breakdown at peak hours\n")

if not peak_hours:
    print("(No peak hours identified — skipping line-item drill-down)\n")
else:
    print("Pulling hourly line-item delivery …", file=sys.stderr)
    try:
        li_hourly = run_report(
            dimensions=["DATE", "HOUR_OF_DAY", "LINE_ITEM_ID", "LINE_ITEM_NAME", "ORDER_NAME"],
            metrics=["AD_SERVER_IMPRESSIONS", "AD_SERVER_REVENUE"],
            start=INVESTIGATE_DATE,
            end=INVESTIGATE_DATE,
        )
        li_hourly = li_hourly.rename(columns={"ad_server_revenue": "revenue"})
        li_hourly["impressions"] = pd.to_numeric(li_hourly["ad_server_impressions"], errors="coerce").fillna(0).astype(int)
        li_hourly["revenue"]     = pd.to_numeric(li_hourly["revenue"], errors="coerce").fillna(0.0)
        li_hourly["hour_of_day"] = li_hourly["hour_of_day"].astype(int)

        peak_li = li_hourly[li_hourly["hour_of_day"].isin(peak_hours)].copy()
        peak_li["ecpm"] = ecpm(peak_li["revenue"], peak_li["impressions"])

        # Aggregate across peak hours, top-15 by revenue
        agg = (
            peak_li.groupby(["line_item_id", "line_item_name", "order_name"])
            .agg(impressions=("impressions", "sum"), revenue=("revenue", "sum"))
            .reset_index()
        )
        agg["ecpm"] = ecpm(agg["revenue"], agg["impressions"])
        agg = agg.sort_values("revenue", ascending=False).head(15)

        print(f"Top line items during peak hours {peak_hours}:\n")
        print(f"| Line Item | Order | Imps | Revenue | eCPM |")
        print(f"|-----------|-------|------|---------|------|")
        for _, row in agg.iterrows():
            li_name = str(row["line_item_name"])[:55]
            ord_name = str(row["order_name"])[:35]
            print(f"| {li_name:<55} | {ord_name:<35} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

        # Also show the full-day eCPM per top LI (for context)
        all_li = li_hourly.groupby(["line_item_id", "line_item_name", "order_name"]).agg(
            impressions=("impressions", "sum"), revenue=("revenue", "sum")
        ).reset_index()
        all_li["ecpm"] = ecpm(all_li["revenue"], all_li["impressions"])
        all_li = all_li.sort_values("revenue", ascending=False).head(20)

        print(f"\n\nTop 20 line items — full day (for context):\n")
        print(f"| Line Item | Order | Imps | Revenue | eCPM |")
        print(f"|-----------|-------|------|---------|------|")
        for _, row in all_li.iterrows():
            li_name  = str(row["line_item_name"])[:55]
            ord_name = str(row["order_name"])[:35]
            print(f"| {li_name:<55} | {ord_name:<35} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

    except Exception as e:
        print(f"⚠ Line-item report failed: {e}\n")


# ── report 3: programmatic deal breakdown by hour ─────────────────────────────

print("\n## 3 · Programmatic deal eCPM by hour (PA / PD / PG)\n")
print("Pulling hourly programmatic deal revenue …", file=sys.stderr)

try:
    deal_hourly = run_report(
        dimensions=["DATE", "HOUR_OF_DAY", "DEAL_NAME", "PROGRAMMATIC_CHANNEL_NAME"],
        metrics=["IMPRESSIONS", "REVENUE_WITHOUT_CPD"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    deal_hourly = deal_hourly.rename(columns={
        "impressions": "ad_server_impressions",
        "revenue_without_cpd": "revenue",
    })
    # Filter out rows with no deal name (open auction spillover)
    deal_hourly = deal_hourly[
        deal_hourly["deal_name"].notna()
        & ~deal_hourly["deal_name"].isin(["", "(Not applicable)", "nan"])
    ].copy()
    deal_hourly["impressions"]    = pd.to_numeric(deal_hourly["ad_server_impressions"], errors="coerce").fillna(0).astype(int)
    deal_hourly["revenue"]        = pd.to_numeric(deal_hourly["revenue"], errors="coerce").fillna(0.0)
    deal_hourly["hour_of_day"]    = deal_hourly["hour_of_day"].astype(int)
    deal_hourly["ecpm"]           = ecpm(deal_hourly["revenue"], deal_hourly["impressions"])

    # Summarise deal revenue by hour
    deal_by_hour = deal_hourly.groupby("hour_of_day").agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index().sort_values("hour_of_day")
    deal_by_hour["ecpm"] = ecpm(deal_by_hour["revenue"], deal_by_hour["impressions"])

    print(f"| Hour | Deal Imps | Deal Revenue | Deal eCPM |")
    print(f"|------|-----------|--------------|-----------|")
    for _, row in deal_by_hour.iterrows():
        marker = " ◀ HIGH" if row["ecpm"] >= 40 else ""
        print(f"| {int(row['hour_of_day']):02d}:00 | {int(row['impressions']):>9,} | ${row['revenue']:>12.2f} | ${row['ecpm']:>8.2f}{marker} |")

    # Top deals overall
    deal_agg = deal_hourly.groupby(["deal_name", "programmatic_channel_name"]).agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index()
    deal_agg["ecpm"] = ecpm(deal_agg["revenue"], deal_agg["impressions"])
    deal_agg = deal_agg.sort_values("revenue", ascending=False).head(15)

    print(f"\n\nTop 15 deals — full day:\n")
    print(f"| Deal | Channel | Imps | Revenue | eCPM |")
    print(f"|------|---------|------|---------|------|")
    for _, row in deal_agg.iterrows():
        dn = str(row["deal_name"])[:50]
        ch = str(row["programmatic_channel_name"])[:20]
        print(f"| {dn:<50} | {ch:<20} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

except Exception as e:
    print(f"⚠ Deal hourly report failed: {e}\n")


# ── report 4: open-auction (non-deal) hourly eCPM ────────────────────────────

print("\n## 4 · Open Bidding eCPM by hour (non-deal programmatic)\n")
print("Pulling yield-group hourly data …", file=sys.stderr)

try:
    ob_hourly = run_report(
        dimensions=["DATE", "HOUR_OF_DAY", "YIELD_GROUP_NAME"],
        metrics=["YIELD_GROUP_IMPRESSIONS", "YIELD_GROUP_ESTIMATED_REVENUE"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    ob_hourly["impressions"] = pd.to_numeric(ob_hourly["yield_group_impressions"], errors="coerce").fillna(0).astype(int)
    ob_hourly["revenue"]     = pd.to_numeric(ob_hourly["yield_group_estimated_revenue"], errors="coerce").fillna(0.0)
    ob_hourly["hour_of_day"] = ob_hourly["hour_of_day"].astype(int)
    ob_hourly["ecpm"]        = ecpm(ob_hourly["revenue"], ob_hourly["impressions"])

    ob_by_hour = ob_hourly.groupby("hour_of_day").agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index().sort_values("hour_of_day")
    ob_by_hour["ecpm"] = ecpm(ob_by_hour["revenue"], ob_by_hour["impressions"])

    print(f"| Hour | OB Imps | OB Revenue | OB eCPM |")
    print(f"|------|---------|------------|---------|")
    for _, row in ob_by_hour.iterrows():
        marker = " ◀ HIGH" if row["ecpm"] >= 40 else ""
        print(f"| {int(row['hour_of_day']):02d}:00 | {int(row['impressions']):>7,} | ${row['revenue']:>10.2f} | ${row['ecpm']:>7.2f}{marker} |")

    # OB by yield group
    og_agg = ob_hourly.groupby("yield_group_name").agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index()
    og_agg["ecpm"] = ecpm(og_agg["revenue"], og_agg["impressions"])
    print(f"\n\nBy yield group:\n")
    print(f"| Yield Group | Imps | Revenue | eCPM |")
    print(f"|-------------|------|---------|------|")
    for _, row in og_agg.iterrows():
        print(f"| {str(row['yield_group_name']):<40} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

except Exception as e:
    print(f"⚠ OB yield-group report failed: {e}\n")


print("\n---\n_Investigation complete. All times are UTC (GAM reports in UTC)._\n")
