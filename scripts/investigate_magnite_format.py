"""
Magnite RPM investigation by ad format — 2026-05-24.

Pulls from the Magnite DV+ Analytics API. Uses a probe-first strategy because
not all dimension names are publicly documented — if a dimension is invalid the
API returns an error and we fall back gracefully.

Probe order:
  Format:  media_type  →  size (infer display/video from WxH patterns)
  Hourly:  hour_of_day  →  hour  →  daily only

Reports produced (whichever combos succeed):
  1. Format summary        — imps / revenue / eCPM by format
  2. Hourly eCPM by format — 24-hour curve per format
  3. Site breakdown        — top sites by eCPM per format
  4. DSP breakdown         — top demand partners by format eCPM

Run via: .github/workflows/investigate_high_rpm.yml
Requires: MAGNITE_KEY, MAGNITE_SECRET, MAGNITE_PUBLISHER_ID
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import date

import pandas as pd

# Import from the repo's own client module (checked out by the workflow)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from client import MagniteClient, MagniteAPIError

# Unbuffered output so partial output survives a crash
sys.stdout.reconfigure(line_buffering=True)

# ── auth ──────────────────────────────────────────────────────────────────────
INVESTIGATE_DATE = "2026-05-24"

_key    = os.environ.get("MAGNITE_KEY", "")
_secret = os.environ.get("MAGNITE_SECRET", "")
_pub_id = os.environ.get("MAGNITE_PUBLISHER_ID", "")

if not _key or not _secret or not _pub_id:
    print("⚠ Magnite credentials not configured (MAGNITE_KEY / MAGNITE_SECRET / "
          "MAGNITE_PUBLISHER_ID secrets missing). Skipping Magnite investigation.\n")
    sys.exit(0)

client = MagniteClient(api_key=_key, api_secret=_secret, account_id=_pub_id)

BASE_METRICS = ["publisher_gross_revenue", "impressions", "ecpm"]

# ── helpers ───────────────────────────────────────────────────────────────────

def run(dimensions: list[str], metrics: list[str] = BASE_METRICS,
        extra_filters: str | None = None) -> pd.DataFrame:
    return client.run_report(
        dimensions=dimensions,
        metrics=metrics,
        start=INVESTIGATE_DATE,
        end=INVESTIGATE_DATE,
        filters=extra_filters,
    )


def prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in ["publisher_gross_revenue", "ecpm", "impressions"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    if "impressions" in df.columns:
        df["impressions"] = df["impressions"].astype(int)
    return df


def ecpm_calc(rev: pd.Series, imp: pd.Series) -> pd.Series:
    return (rev / imp.where(imp > 0) * 1000).fillna(0.0)


# Known video sizes in Magnite — covers VAST in-stream, outstream, CTV
_VIDEO_SIZE_PATTERNS = {
    "1x1",       # VAST/in-stream token
    "640x480", "640x360", "854x480",
    "1280x720", "1920x1080",
    "400x300", "320x240",
    "426x240", "480x270",
}


def classify_format(size: str) -> str:
    s = str(size).lower().strip()
    if s in _VIDEO_SIZE_PATTERNS:
        return "video"
    if "video" in s or "vast" in s:
        return "video"
    return "display"


def pct_rev(df: pd.DataFrame, rev_col: str = "publisher_gross_revenue") -> pd.Series:
    total = df[rev_col].sum()
    return (df[rev_col] / total * 100).round(1) if total else pd.Series([0.0] * len(df))


# ── 1. probe format dimension ─────────────────────────────────────────────────

print("\n## 1 · Magnite format summary — 2026-05-24\n")
print("Probing format dimension …", file=sys.stderr)

format_col   = None   # which column holds the format label
format_df    = pd.DataFrame()

# Attempt A: media_type dimension
try:
    print("  Trying media_type …", file=sys.stderr)
    raw = prep(run(["media_type"]))
    if not raw.empty and "media_type" in raw.columns:
        format_col = "media_type"
        format_df  = raw.copy()
        print("  media_type ✓", file=sys.stderr)
except Exception as e:
    print(f"  media_type ✗ ({e})", file=sys.stderr)

# Attempt B: size dimension → infer format
if format_col is None:
    try:
        print("  Trying size dimension …", file=sys.stderr)
        raw = prep(run(["size"]))
        if not raw.empty and "size" in raw.columns:
            raw["format"] = raw["size"].apply(classify_format)
            # Aggregate size rows into display / video
            raw = raw.groupby("format").agg(
                impressions=("impressions", "sum"),
                publisher_gross_revenue=("publisher_gross_revenue", "sum"),
            ).reset_index()
            raw["ecpm"] = ecpm_calc(raw["publisher_gross_revenue"], raw["impressions"])
            format_col = "format"
            format_df  = raw.copy()
            print("  size → format ✓ (inferred)", file=sys.stderr)
    except Exception as e:
        print(f"  size ✗ ({e})", file=sys.stderr)

if format_df.empty:
    print("⚠ Could not determine format breakdown — no valid format dimension found.\n")
else:
    total_imps = format_df["impressions"].sum()
    total_rev  = format_df["publisher_gross_revenue"].sum()
    print(f"**Magnite total:** {total_imps:,} imps · ${total_rev:,.2f} · "
          f"${total_rev/total_imps*1000:.2f} blended eCPM\n")

    fmt_display = format_df.copy()
    fmt_display["pct_rev"] = pct_rev(fmt_display)
    fmt_display = fmt_display.sort_values("publisher_gross_revenue", ascending=False)

    print(f"| Format | Impressions | Revenue | eCPM | % Revenue |")
    print(f"|--------|-------------|---------|------|-----------|")
    for _, r in fmt_display.iterrows():
        print(f"| {str(r[format_col]):<20} | {int(r['impressions']):>11,} | "
              f"${r['publisher_gross_revenue']:>9.2f} | ${r['ecpm']:>6.2f} | "
              f"{r['pct_rev']:>5.1f}% |")


# ── 2. hourly eCPM by format ──────────────────────────────────────────────────

print("\n\n## 2 · Magnite hourly eCPM by format — 2026-05-24\n")
print("Probing hourly dimension …", file=sys.stderr)

hour_col  = None
hourly_df = pd.DataFrame()

# Try each hour dimension × format dimension combo (best → worst)
hour_candidates   = ["hour_of_day", "hour"]
format_candidates = ([format_col] if format_col else []) + ["media_type", "size"]

for h_dim in hour_candidates:
    if hour_col:
        break
    for f_dim in format_candidates:
        try:
            dims = [h_dim, f_dim]
            print(f"  Trying {dims} …", file=sys.stderr)
            raw = prep(run(dims))
            if not raw.empty and h_dim in raw.columns:
                hour_col  = h_dim
                fmt_dim_h = f_dim
                hourly_df = raw.copy()
                print(f"  {dims} ✓", file=sys.stderr)
                break
        except Exception as e:
            print(f"  {dims} ✗ ({e})", file=sys.stderr)

# If no format+hour combo worked, try hour alone
if hour_col is None:
    for h_dim in hour_candidates:
        try:
            print(f"  Trying [{h_dim}] alone …", file=sys.stderr)
            raw = prep(run([h_dim]))
            if not raw.empty and h_dim in raw.columns:
                hour_col  = h_dim
                fmt_dim_h = None
                hourly_df = raw.copy()
                print(f"  [{h_dim}] alone ✓", file=sys.stderr)
                break
        except Exception as e:
            print(f"  [{h_dim}] ✗ ({e})", file=sys.stderr)

if hourly_df.empty:
    print("⚠ Hourly dimension not supported by Magnite API for this date range.\n")
    print("_(Magnite may only expose hourly data for the current day via the General dataset.)_\n")
else:
    hourly_df[hour_col] = hourly_df[hour_col].astype(int)
    if fmt_dim_h and fmt_dim_h in hourly_df.columns:
        # Infer format from size if that's what we got
        if fmt_dim_h == "size":
            hourly_df["format"] = hourly_df["size"].apply(classify_format)
            fmt_dim_h = "format"

        # Show hourly by format
        formats_found = sorted(hourly_df[fmt_dim_h].unique())
        for fmt in formats_found:
            sub = hourly_df[hourly_df[fmt_dim_h] == fmt].groupby(hour_col).agg(
                impressions=("impressions", "sum"),
                revenue=("publisher_gross_revenue", "sum"),
            ).reset_index().sort_values(hour_col)
            sub["ecpm"] = ecpm_calc(sub["revenue"], sub["impressions"])

            print(f"### {fmt}\n")
            print(f"| Hour (UTC) | Imps | Revenue | eCPM |")
            print(f"|------------|------|---------|------|")
            for _, r in sub.iterrows():
                marker = " ◀ HIGH" if r["ecpm"] >= 40 else ""
                print(f"| {int(r[hour_col]):02d}:00       | {int(r['impressions']):>9,} | "
                      f"${r['revenue']:>9.2f} | ${r['ecpm']:>7.2f}{marker} |")
            print()
    else:
        # Hour only, no format
        hourly_df_agg = hourly_df.groupby(hour_col).agg(
            impressions=("impressions", "sum"),
            revenue=("publisher_gross_revenue", "sum"),
        ).reset_index().sort_values(hour_col)
        hourly_df_agg["ecpm"] = ecpm_calc(hourly_df_agg["revenue"], hourly_df_agg["impressions"])

        print("_(No format dimension available alongside hour — showing all inventory combined)_\n")
        print(f"| Hour (UTC) | Imps | Revenue | eCPM |")
        print(f"|------------|------|---------|------|")
        for _, r in hourly_df_agg.iterrows():
            marker = " ◀ HIGH" if r["ecpm"] >= 40 else ""
            print(f"| {int(r[hour_col]):02d}:00       | {int(r['impressions']):>9,} | "
                  f"${r['revenue']:>9.2f} | ${r['ecpm']:>7.2f}{marker} |")


# ── 3. site breakdown by format ───────────────────────────────────────────────

print("\n\n## 3 · Top Magnite sites by format — 2026-05-24\n")
print("Pulling site × format data …", file=sys.stderr)

site_fmt_dims = (["site", format_col] if format_col else ["site", "size"])
try:
    site_raw = prep(run(site_fmt_dims))
    if "size" in site_raw.columns and format_col != "size":
        site_raw["format"] = site_raw["size"].apply(classify_format)
        fmt_col_s = "format"
    elif format_col and format_col in site_raw.columns:
        fmt_col_s = format_col
    else:
        fmt_col_s = None

    if fmt_col_s:
        for fmt in sorted(site_raw[fmt_col_s].unique()):
            sub = site_raw[site_raw[fmt_col_s] == fmt].groupby("site").agg(
                impressions=("impressions", "sum"),
                revenue=("publisher_gross_revenue", "sum"),
            ).reset_index()
            sub["ecpm"] = ecpm_calc(sub["revenue"], sub["impressions"])
            sub = sub[sub["impressions"] >= 1000].sort_values("revenue", ascending=False).head(10)
            if sub.empty:
                continue
            print(f"### {fmt} — top 10 sites by revenue\n")
            print(f"| Site | Imps | Revenue | eCPM |")
            print(f"|------|------|---------|------|")
            for _, r in sub.iterrows():
                print(f"| {str(r['site']):<40} | {int(r['impressions']):>9,} | "
                      f"${r['revenue']:>9.2f} | ${r['ecpm']:>7.2f} |")
            print()
    else:
        # No format dim, just top sites
        site_agg = site_raw.groupby("site").agg(
            impressions=("impressions", "sum"),
            revenue=("publisher_gross_revenue", "sum"),
        ).reset_index()
        site_agg["ecpm"] = ecpm_calc(site_agg["revenue"], site_agg["impressions"])
        site_agg = site_agg[site_agg["impressions"] >= 1000].sort_values("revenue", ascending=False).head(15)
        print("| Site | Imps | Revenue | eCPM |")
        print("|------|------|---------|------|")
        for _, r in site_agg.iterrows():
            print(f"| {str(r['site']):<40} | {int(r['impressions']):>9,} | "
                  f"${r['revenue']:>9.2f} | ${r['ecpm']:>7.2f} |")

except Exception as e:
    print(f"⚠ Site breakdown failed: {e}\n")


# ── 4. DSP breakdown by format ────────────────────────────────────────────────

print("\n\n## 4 · Top Magnite DSPs by format — 2026-05-24\n")
print("Pulling DSP × format data …", file=sys.stderr)

dsp_fmt_dims = (["partner", format_col] if format_col else ["partner", "size"])
try:
    dsp_raw = prep(run(dsp_fmt_dims))
    if "size" in dsp_raw.columns and format_col != "size":
        dsp_raw["format"] = dsp_raw["size"].apply(classify_format)
        fmt_col_d = "format"
    elif format_col and format_col in dsp_raw.columns:
        fmt_col_d = format_col
    else:
        fmt_col_d = None

    if fmt_col_d:
        for fmt in sorted(dsp_raw[fmt_col_d].unique()):
            sub = dsp_raw[dsp_raw[fmt_col_d] == fmt].groupby("partner").agg(
                impressions=("impressions", "sum"),
                revenue=("publisher_gross_revenue", "sum"),
            ).reset_index()
            sub["ecpm"] = ecpm_calc(sub["revenue"], sub["impressions"])
            sub = sub[sub["impressions"] >= 100].sort_values("revenue", ascending=False).head(10)
            if sub.empty:
                continue
            print(f"### {fmt} — top 10 DSPs by revenue\n")
            print(f"| DSP | Imps | Revenue | eCPM |")
            print(f"|-----|------|---------|------|")
            for _, r in sub.iterrows():
                print(f"| {str(r['partner']):<35} | {int(r['impressions']):>9,} | "
                      f"${r['revenue']:>9.2f} | ${r['ecpm']:>7.2f} |")
            print()
    else:
        dsp_agg = dsp_raw.groupby("partner").agg(
            impressions=("impressions", "sum"),
            revenue=("publisher_gross_revenue", "sum"),
        ).reset_index()
        dsp_agg["ecpm"] = ecpm_calc(dsp_agg["revenue"], dsp_agg["impressions"])
        dsp_agg = dsp_agg[dsp_agg["impressions"] >= 100].sort_values("revenue", ascending=False).head(15)
        print("| DSP | Imps | Revenue | eCPM |")
        print("|-----|------|---------|------|")
        for _, r in dsp_agg.iterrows():
            print(f"| {str(r['partner']):<35} | {int(r['impressions']):>9,} | "
                  f"${r['revenue']:>9.2f} | ${r['ecpm']:>7.2f} |")

except Exception as e:
    print(f"⚠ DSP breakdown failed: {e}\n")


print("\n---\n_Magnite DV+ General dataset. UTC times. Subtract 4h for EDT._\n")
