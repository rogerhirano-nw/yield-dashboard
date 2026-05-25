"""
Full RPM investigation by ad format — 2026-05-24.

Four reports, all broken down by ad format (Display / Video / Native / …):

  1. Direct (AD_SERVER) by format
       - DATE + LINE_ITEM_ID + INVENTORY_FORMAT_NAME + AD_SERVER_*
       - Aggregated to format-level totals + top line items per format
  2. Programmatic deals by format
       - DATE + INVENTORY_FORMAT_NAME + PROGRAMMATIC_CHANNEL_NAME + programmatic metrics
  3. Open Bidding by yield group (display vs video)
       - YIELD_GROUP_NAME + YIELD_GROUP_* metrics
  4. Combined cross-source summary table

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


def ecpm_col(df: pd.DataFrame, rev_col: str, imp_col: str) -> pd.Series:
    rev = pd.to_numeric(df[rev_col], errors="coerce").fillna(0.0)
    imp = pd.to_numeric(df[imp_col], errors="coerce").fillna(0)
    return (rev / imp.where(imp > 0) * 1000).fillna(0.0)


def print_table(rows: pd.DataFrame, cols: list[tuple[str, str, str]]) -> None:
    """cols: list of (df_col, header, fmt)  where fmt is 'int'/'$'/'str'."""
    headers = [h for _, h, _ in cols]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["-" * (len(h) + 2) for h in headers]) + "|")
    for _, row in rows.iterrows():
        parts = []
        for col, _, fmt in cols:
            v = row[col]
            if fmt == "int":
                parts.append(f"{int(v):>10,}")
            elif fmt == "$":
                parts.append(f"${float(v):>8.2f}")
            elif fmt == "str":
                parts.append(str(v))
            else:
                parts.append(str(v))
        print("| " + " | ".join(parts) + " |")


# ── report 1: direct (AD_SERVER) by format ───────────────────────────────────
# Use LINE_ITEM_ID + INVENTORY_FORMAT_NAME — same pattern as run_li_metadata_report
# which is proven to work. Then aggregate to format level.

print("\n## 1 · Direct delivery by ad format — 2026-05-24\n")
print("Pulling direct line-item delivery by format …", file=sys.stderr)

direct_fmt: pd.DataFrame = pd.DataFrame()

try:
    raw = run_report(
        dimensions=["DATE", "LINE_ITEM_ID", "LINE_ITEM_NAME", "ORDER_NAME", "INVENTORY_FORMAT_NAME"],
        metrics=["AD_SERVER_IMPRESSIONS", "AD_SERVER_REVENUE"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    raw = raw.rename(columns={"ad_server_revenue": "revenue"})
    raw["impressions"] = pd.to_numeric(raw["ad_server_impressions"], errors="coerce").fillna(0).astype(int)
    raw["revenue"]     = pd.to_numeric(raw["revenue"], errors="coerce").fillna(0.0)
    raw["ecpm"]        = ecpm_col(raw, "revenue", "impressions")

    # ── format-level totals
    fmt_agg = raw.groupby("inventory_format_name").agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index().rename(columns={"inventory_format_name": "format"})
    fmt_agg["ecpm"]   = ecpm_col(fmt_agg, "revenue", "impressions")
    fmt_agg["pct_rev"] = (fmt_agg["revenue"] / fmt_agg["revenue"].sum() * 100).round(1)
    fmt_agg = fmt_agg.sort_values("revenue", ascending=False)

    total_imps = raw["impressions"].sum()
    total_rev  = raw["revenue"].sum()
    blended    = total_rev / total_imps * 1000 if total_imps else 0
    print(f"**Direct total:** {total_imps:,} imps · ${total_rev:,.2f} · ${blended:.2f} blended eCPM\n")

    print("| Format | Impressions | Revenue | eCPM | % of Revenue |")
    print("|--------|-------------|---------|------|--------------|")
    for _, r in fmt_agg.iterrows():
        print(f"| {str(r['format']):<20} | {int(r['impressions']):>11,} | ${r['revenue']:>9.2f} | ${r['ecpm']:>6.2f} | {r['pct_rev']:>5.1f}% |")

    # ── top 15 line items per format, by eCPM (min 100 imps)
    direct_fmt = raw.copy()
    for fmt in fmt_agg["format"]:
        subset = raw[
            (raw["inventory_format_name"] == fmt) & (raw["impressions"] >= 100)
        ].sort_values("ecpm", ascending=False).head(15)
        if subset.empty:
            continue
        print(f"\n\n### Direct · {fmt} — top 15 line items by eCPM\n")
        print("| Line Item | Order | Imps | Revenue | eCPM |")
        print("|-----------|-------|------|---------|------|")
        for _, row in subset.iterrows():
            li  = str(row["line_item_name"])[:55]
            ord_ = str(row["order_name"])[:35]
            print(f"| {li:<55} | {ord_:<35} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

except Exception as e:
    print(f"⚠ Direct by-format report failed: {e}\n")


# ── report 2: programmatic deals by format ────────────────────────────────────

print("\n\n## 2 · Programmatic deals by ad format — 2026-05-24\n")
print("Pulling deals by format …", file=sys.stderr)

deal_fmt: pd.DataFrame = pd.DataFrame()

try:
    deals = run_report(
        dimensions=["DATE", "INVENTORY_FORMAT_NAME", "PROGRAMMATIC_CHANNEL_NAME", "DEAL_NAME"],
        metrics=["IMPRESSIONS", "REVENUE_WITHOUT_CPD"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    deals = deals.rename(columns={
        "impressions": "impressions_raw",
        "revenue_without_cpd": "revenue",
    })
    # Drop no-deal rows (open exchange bleed-through)
    deals = deals[
        deals["deal_name"].notna()
        & ~deals["deal_name"].isin(["", "(Not applicable)", "nan"])
    ].copy()
    deals["impressions"] = pd.to_numeric(deals["impressions_raw"], errors="coerce").fillna(0).astype(int)
    deals["revenue"]     = pd.to_numeric(deals["revenue"], errors="coerce").fillna(0.0)
    deals["ecpm"]        = ecpm_col(deals, "revenue", "impressions")

    # Format totals
    deal_fmt_agg = deals.groupby("inventory_format_name").agg(
        impressions=("impressions", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index().rename(columns={"inventory_format_name": "format"})
    deal_fmt_agg["ecpm"]    = ecpm_col(deal_fmt_agg, "revenue", "impressions")
    deal_fmt_agg["pct_rev"] = (deal_fmt_agg["revenue"] / deal_fmt_agg["revenue"].sum() * 100).round(1)
    deal_fmt_agg = deal_fmt_agg.sort_values("revenue", ascending=False)
    deal_fmt = deal_fmt_agg.copy()

    d_total_imps = deals["impressions"].sum()
    d_total_rev  = deals["revenue"].sum()
    d_blended    = d_total_rev / d_total_imps * 1000 if d_total_imps else 0
    print(f"**Deals total:** {d_total_imps:,} imps · ${d_total_rev:,.2f} · ${d_blended:.2f} blended eCPM\n")

    print("| Format | Impressions | Revenue | eCPM | % of Revenue |")
    print("|--------|-------------|---------|------|--------------|")
    for _, r in deal_fmt_agg.iterrows():
        print(f"| {str(r['format']):<20} | {int(r['impressions']):>11,} | ${r['revenue']:>9.2f} | ${r['ecpm']:>6.2f} | {r['pct_rev']:>5.1f}% |")

    # Top deals per format
    for fmt in deal_fmt_agg["format"]:
        subset = deals[deals["inventory_format_name"] == fmt].groupby(
            ["deal_name", "programmatic_channel_name"]
        ).agg(impressions=("impressions", "sum"), revenue=("revenue", "sum")).reset_index()
        subset["ecpm"] = ecpm_col(subset, "revenue", "impressions")
        subset = subset.sort_values("revenue", ascending=False).head(10)
        if subset.empty:
            continue
        print(f"\n\n### Deals · {fmt} — top 10 by revenue\n")
        print("| Deal | Channel | Imps | Revenue | eCPM |")
        print("|------|---------|------|---------|------|")
        for _, row in subset.iterrows():
            print(f"| {str(row['deal_name'])[:55]:<55} | {str(row['programmatic_channel_name'])[:22]:<22} | {int(row['impressions']):>8,} | ${row['revenue']:>8.2f} | ${row['ecpm']:>6.2f} |")

except Exception as e:
    print(f"⚠ Deals by-format report failed: {e}\n")


# ── report 3: open bidding by yield group (display vs video) ─────────────────

print("\n\n## 3 · Open Bidding by yield group (format proxy) — 2026-05-24\n")
print("Pulling OB yield-group data …", file=sys.stderr)

ob_fmt: pd.DataFrame = pd.DataFrame()

try:
    ob = run_report(
        dimensions=["DATE", "YIELD_GROUP_NAME"],
        metrics=["YIELD_GROUP_IMPRESSIONS", "YIELD_GROUP_ESTIMATED_REVENUE"],
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
    )
    ob["impressions"] = pd.to_numeric(ob["yield_group_impressions"], errors="coerce").fillna(0).astype(int)
    ob["revenue"]     = pd.to_numeric(ob["yield_group_estimated_revenue"], errors="coerce").fillna(0.0)
    ob["ecpm"]        = ecpm_col(ob, "revenue", "impressions")
    ob["pct_rev"]     = (ob["revenue"] / ob["revenue"].sum() * 100).round(1)
    ob = ob.sort_values("revenue", ascending=False)
    ob_fmt = ob.rename(columns={"yield_group_name": "format"})[["format", "impressions", "revenue", "ecpm", "pct_rev"]]

    ob_total_imps = ob["impressions"].sum()
    ob_total_rev  = ob["revenue"].sum()
    ob_blended    = ob_total_rev / ob_total_imps * 1000 if ob_total_imps else 0
    print(f"**OB total:** {ob_total_imps:,} imps · ${ob_total_rev:,.2f} · ${ob_blended:.2f} blended eCPM\n")

    print("| Yield Group (Format) | Impressions | Revenue | eCPM | % of Revenue |")
    print("|----------------------|-------------|---------|------|--------------|")
    for _, r in ob.iterrows():
        print(f"| {str(r['yield_group_name']):<22} | {int(r['impressions']):>11,} | ${r['revenue']:>9.2f} | ${r['ecpm']:>6.2f} | {r['pct_rev']:>5.1f}% |")

except Exception as e:
    print(f"⚠ OB by-format report failed: {e}\n")


# ── report 4: cross-source summary by format ─────────────────────────────────

print("\n\n## 4 · Cross-source RPM summary by format — 2026-05-24\n")
print("_Direct = AD_SERVER delivery; Deals = PA/PD/PG programmatic; OB = yield-group estimated revenue_\n")

# Normalise format names: GAM uses "Display ads", "Video ads", etc.
def norm_fmt(s: str) -> str:
    s = s.lower()
    if "video" in s:    return "Video"
    if "display" in s:  return "Display"
    if "native" in s:   return "Native"
    if "audio" in s:    return "Audio"
    return s.title()

rows_out = []

# Direct
if not direct_fmt.empty:
    for fmt, grp in direct_fmt.groupby("inventory_format_name"):
        rows_out.append({
            "source": "Direct",
            "format": norm_fmt(fmt),
            "impressions": int(grp["impressions"].sum()),
            "revenue": float(grp["revenue"].sum()),
        })

# Deals
if not deal_fmt.empty:
    for _, r in deal_fmt.iterrows():
        rows_out.append({
            "source": "Deals",
            "format": norm_fmt(str(r["format"])),
            "impressions": int(r["impressions"]),
            "revenue": float(r["revenue"]),
        })

# OB
if not ob_fmt.empty:
    for _, r in ob_fmt.iterrows():
        rows_out.append({
            "source": "OB",
            "format": norm_fmt(str(r["format"])),
            "impressions": int(r["impressions"]),
            "revenue": float(r["revenue"]),
        })

if rows_out:
    summary = pd.DataFrame(rows_out)
    # Pivot: one row per format, columns = source eCPMs + combined
    fmt_summary = summary.groupby("format").agg(
        total_imps=("impressions", "sum"),
        total_rev=("revenue", "sum"),
    ).reset_index()
    fmt_summary["blended_ecpm"] = (fmt_summary["total_rev"] / fmt_summary["total_imps"].where(fmt_summary["total_imps"] > 0) * 1000).fillna(0)
    fmt_summary = fmt_summary.sort_values("total_rev", ascending=False)

    print("| Format | Total Imps | Total Revenue | Blended eCPM |")
    print("|--------|------------|---------------|--------------|")
    for _, r in fmt_summary.iterrows():
        print(f"| {str(r['format']):<10} | {int(r['total_imps']):>10,} | ${r['total_rev']:>13,.2f} | ${r['blended_ecpm']:>12.2f} |")

    # Per-source breakdown
    print("\n\nPer-source eCPM by format:\n")
    print("| Format | Source | Imps | Revenue | eCPM |")
    print("|--------|--------|------|---------|------|")
    for fmt in fmt_summary["format"]:
        sub = summary[summary["format"] == fmt].copy()
        sub["ecpm"] = (sub["revenue"] / sub["impressions"].where(sub["impressions"] > 0) * 1000).fillna(0)
        sub = sub.sort_values("revenue", ascending=False)
        for _, r in sub.iterrows():
            print(f"| {fmt:<10} | {str(r['source']):<6} | {int(r['impressions']):>10,} | ${r['revenue']:>9.2f} | ${r['ecpm']:>6.2f} |")

else:
    print("(No data — all upstream reports failed)\n")


print("\n---\n_UTC dates. Direct = GAM ad-server; Deals = PA/PD/PG via programmatic metrics; OB = yield-group estimated. Subtract 4h for EDT._\n")
