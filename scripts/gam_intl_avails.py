"""
Monthly international (non-US) avails from GAM, broken out by country.

Scope (set by RevOps, 2026-09):
  - Display -> top-level ad unit `newsweek`, limited to the four saleable
    sizes 300x250, 320x50, 970x250, 728x90.
  - Video   -> top-level ad unit `vid.newsweek` (all sizes; it serves a
    single 640x360v in-stream slot).
  `applenews.newsweek`, `newsletter.newsweek` and `Default` are out of scope.

GAM-side notes (all verified empirically against network 22541732127)
---------------------------------------------------------------------
- "Avails" must include inventory we did NOT fill, so the pull needs
  UNFILLED_IMPRESSIONS. GAM rejects that metric alongside
  INVENTORY_FORMAT_NAME and LINE_ITEM_ENVIRONMENT_TYPE_NAME with
  REPORT_ERROR_CONSTRAINTS_INCOMPATIBILITY, and alongside AD_REQUEST_SIZES.
  It IS compatible with AD_UNIT_NAME_TOP_LEVEL and REQUESTED_AD_SIZES, which
  is what this pull uses.
- REQUESTED_AD_SIZES is the *set* of sizes a single ad request was eligible
  for ("1x1, 300x250", "300x50, 320x50", ...), not one size per row. So
  per-size avails OVERLAP: one opportunity eligible for both 300x250 and
  970x250 counts toward both, and the four sizes do not sum to the unit
  total. That is the correct reading of an avail — the slot can take any
  eligible size — but it means the sizes must never be added together.
  `_ANY_TARGET_SIZE` gives the de-duplicated "eligible for at least one of
  the four" figure for when a single number is needed.
- MONTH_YEAR comes back as an int code = (year - 1900) * 12 + (month - 1).
  Decoded here and asserted against the requested window.
- Per CLAUDE.md, same-day data has latency; the default window is whole
  months that have already closed.

Usage:
    python scripts/gam_intl_avails.py                      # last 3 closed months
    python scripts/gam_intl_avails.py --months 6
    python scripts/gam_intl_avails.py --start 2026-06 --end 2026-08
    python scripts/gam_intl_avails.py --csv-dir out/
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

env_file = REPO_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from gam_client import GAMClient  # noqa: E402

DISPLAY_UNIT = "newsweek"
VIDEO_UNIT = "vid.newsweek"
DISPLAY_SIZES = ["300x250", "320x50", "970x250", "728x90"]
EXCLUDE_COUNTRIES = {"United States"}
ANY_SIZE = "ANY of the 4 (de-duplicated)"


def _month_code(y: int, m: int) -> int:
    return (y - 1900) * 12 + (m - 1)


def _decode_month(code: int) -> str:
    y, m = 1900 + int(code) // 12, int(code) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _month_bounds(start: str, end: str) -> tuple[date, date]:
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    last = (pd.Timestamp(year=ey + em // 12, month=em % 12 + 1, day=1) - pd.Timedelta(days=1)).date()
    return date(sy, sm, 1), last


def _default_window(months: int) -> tuple[str, str]:
    """The `months` most recent months that have fully closed."""
    today = date.today()
    ey, em = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return _decode_month(_month_code(ey, em) - (months - 1)), f"{ey:04d}-{em:02d}"


def _check_months(df: pd.DataFrame, start_m: str, end_m: str) -> None:
    expected = {_month_code(*(int(x) for x in m.split("-")))
                for m in pd.period_range(start_m, end_m, freq="M").astype(str)}
    seen = set(df["month_year"].unique())
    if not seen <= expected:
        raise SystemExit(f"unexpected MONTH_YEAR codes {sorted(seen - expected)}; decode assumption broken")


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["month"] = df["month_year"].map(_decode_month)
    df["country"] = df["country_name"].replace("", "Unknown")
    df["avails"] = df["impressions"] + df["unfilled_impressions"]
    return df[~df["country"].isin(EXCLUDE_COUNTRIES)]


METRICS = ["AD_REQUESTS", "IMPRESSIONS", "UNFILLED_IMPRESSIONS"]


def pull_display(client: GAMClient, start: date, end: date, start_m: str, end_m: str) -> pd.DataFrame:
    raw = client._run_report(
        dimensions=["MONTH_YEAR", "COUNTRY_NAME", "REQUESTED_AD_SIZES"],
        metrics=METRICS,
        start_date=start,
        end_date=end,
        filters=[("AD_UNIT_NAME_TOP_LEVEL", "IN", [DISPLAY_UNIT])],
    )
    _check_months(raw, start_m, end_m)
    raw = _tidy(raw)
    raw["sizes"] = raw["requested_ad_sizes"].fillna("").apply(
        lambda s: {t.strip() for t in s.split(",") if t.strip()}
    )

    out = []
    for size in DISPLAY_SIZES:
        hit = raw[raw["sizes"].apply(lambda s, z=size: z in s)]
        g = hit.groupby(["month", "country"], as_index=False)[
            ["ad_requests", "impressions", "unfilled_impressions", "avails"]].sum()
        g["format"], g["size"] = "Display", size
        out.append(g)

    # De-duplicated: an opportunity eligible for at least one target size,
    # counted once. This is the row to use for a single display number.
    any_hit = raw[raw["sizes"].apply(lambda s: bool(s & set(DISPLAY_SIZES)))]
    g = any_hit.groupby(["month", "country"], as_index=False)[
        ["ad_requests", "impressions", "unfilled_impressions", "avails"]].sum()
    g["format"], g["size"] = "Display", ANY_SIZE
    out.append(g)
    return pd.concat(out, ignore_index=True)


def pull_video(client: GAMClient, start: date, end: date, start_m: str, end_m: str) -> pd.DataFrame:
    raw = client._run_report(
        dimensions=["MONTH_YEAR", "COUNTRY_NAME"],
        metrics=METRICS,
        start_date=start,
        end_date=end,
        filters=[("AD_UNIT_NAME_TOP_LEVEL", "IN", [VIDEO_UNIT])],
    )
    _check_months(raw, start_m, end_m)
    raw = _tidy(raw)
    g = raw.groupby(["month", "country"], as_index=False)[
        ["ad_requests", "impressions", "unfilled_impressions", "avails"]].sum()
    g["format"], g["size"] = "Video", "All (640x360v)"
    return g



def _write_xlsx(path: Path, df: pd.DataFrame, wide: pd.DataFrame, n_months: int,
                start_m: str, end_m: str) -> None:
    """Three tabs: how-to-read Summary, per-country monthly average, raw detail."""
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    flat = wide.copy()
    flat.columns = [f"{f} {s}" for f, s in flat.columns]
    flat = flat.reset_index().rename(columns={"country": "Country"})

    tot = df.groupby(["format", "size"], as_index=False)[
        ["ad_requests", "impressions", "unfilled_impressions", "avails"]].sum()
    for c in ["ad_requests", "impressions", "unfilled_impressions", "avails"]:
        tot[c] = (tot[c] / n_months).round(0).astype(int)
    tot["fill_rate_pct"] = (tot.impressions / tot.avails.replace(0, float("nan")) * 100).round(1)
    tot = tot.rename(columns={
        "format": "Format", "size": "Size", "ad_requests": "Ad requests / mo",
        "impressions": "Filled / mo", "unfilled_impressions": "Unsold / mo",
        "avails": "Avails / mo", "fill_rate_pct": "Fill rate %"})

    notes = pd.DataFrame({"Newsweek — non-US avails by country": [
        f"Window: {start_m} .. {end_m} ({n_months} closed months). Source: Google Ad Manager "
        f"network {os.environ.get('GAM_NETWORK_ID', '')}, historical reporting API.",
        f"Display = top-level ad unit `{DISPLAY_UNIT}`, sizes {', '.join(DISPLAY_SIZES)}. "
        f"Video = top-level ad unit `{VIDEO_UNIT}`.",
        "United States is excluded. Figures are a monthly AVERAGE across the window.",
        "Avails = impressions + unfilled impressions: every opportunity GAM measured, "
        "whether or not we filled it. This is the sellable pool.",
        "Ad requests is the wider upper bound — it also counts requests that dropped out "
        "before any ad could be returned, so it is not deliverable inventory.",
        "Unsold = unfilled impressions, i.e. the pool that came back empty today.",
        "DISPLAY SIZES OVERLAP AND MUST NOT BE SUMMED. GAM requests one slot against "
        "several eligible sizes at once (e.g. '300x250, 970x250'), so an opportunity is "
        "counted under every size it can serve. Use the de-duplicated column for one number.",
        "Programmatic demand currently fills most display inventory; a direct buy would "
        "outrank it, so avails are not limited to the unsold column.",
    ]})

    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        notes.to_excel(xl, sheet_name="Read me", index=False)
        tot.to_excel(xl, sheet_name="Summary", index=False)
        flat.to_excel(xl, sheet_name="By country (monthly avg)", index=False)
        df.to_excel(xl, sheet_name="By country x month", index=False)

        for name in xl.book.sheetnames:
            ws = xl.book[name]
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            for col in ws.columns:
                letter = get_column_letter(col[0].column)
                width = max((len(str(c.value)) for c in col if c.value is not None), default=10)
                ws.column_dimensions[letter].width = min(max(width + 2, 12), 95 if name == "Read me" else 26)
            if name != "Read me":
                for row in ws.iter_rows(min_row=2):
                    for cell in row:
                        if isinstance(cell.value, (int, float)):
                            cell.number_format = "#,##0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=3, help="how many closed months (default 3)")
    ap.add_argument("--start", help="start month YYYY-MM (overrides --months)")
    ap.add_argument("--end", help="end month YYYY-MM")
    ap.add_argument("--csv-dir", help="directory to write CSV output into")
    ap.add_argument("--xlsx", help="path to write a formatted workbook to")
    ap.add_argument("--top", type=int, default=25, help="rows in the printed country tables")
    args = ap.parse_args()

    start_m, end_m = (args.start, args.end or args.start) if args.start else _default_window(args.months)
    start_d, end_d = _month_bounds(start_m, end_m)
    print(f"# GAM non-US avails — {start_m} .. {end_m}  ({start_d} .. {end_d})")
    print(f"# Display: ad unit `{DISPLAY_UNIT}`, sizes {', '.join(DISPLAY_SIZES)}")
    print(f"# Video:   ad unit `{VIDEO_UNIT}`\n")

    client = GAMClient()
    df = pd.concat(
        [pull_display(client, start_d, end_d, start_m, end_m),
         pull_video(client, start_d, end_d, start_m, end_m)],
        ignore_index=True,
    )
    df["fill_rate_pct"] = (df.impressions / df.avails.replace(0, float("nan")) * 100).round(1)
    df = df[["month", "country", "format", "size", "ad_requests", "impressions",
             "unfilled_impressions", "avails", "fill_rate_pct"]]

    n_months = df.month.nunique()
    print(f"## Non-US totals by month (avails = impressions + unfilled)\n")
    piv = df.pivot_table(index="month", columns=["format", "size"], values="avails", aggfunc="sum", fill_value=0)
    print(piv.to_string())

    print(f"\n## Monthly average avails per country — non-US, mean of {n_months} month(s)\n")
    avg = (df.groupby(["country", "format", "size"])["avails"].sum() / n_months).round(0).astype(int)
    wide = avg.unstack(["format", "size"]).fillna(0).astype(int)
    order = [("Display", s) for s in DISPLAY_SIZES] + [("Display", ANY_SIZE), ("Video", "All (640x360v)")]
    wide = wide.reindex(columns=[c for c in order if c in wide.columns])
    wide = wide.sort_values(("Display", ANY_SIZE), ascending=False)
    print(wide.head(args.top).to_string())
    print(f"\n… {max(0, len(wide) - args.top)} more countries. "
          f"Non-US countries with any avails: {len(wide)}")
    print("\nNOTE: display sizes OVERLAP — one ad request is eligible for several sizes, "
          "so the four size columns must not be summed. Use the de-duplicated column for a single figure.")

    if args.csv_dir:
        out = Path(args.csv_dir)
        out.mkdir(parents=True, exist_ok=True)
        df.sort_values(["month", "format", "size", "avails"], ascending=[True, True, True, False]) \
          .to_csv(out / "gam_intl_avails_by_month.csv", index=False)
        wide.to_csv(out / "gam_intl_avails_monthly_avg.csv")
        print(f"\nwrote {out}/gam_intl_avails_by_month.csv + gam_intl_avails_monthly_avg.csv")
    if args.xlsx:
        xlsx_path = Path(args.xlsx)
        xlsx_path.parent.mkdir(parents=True, exist_ok=True)
        _write_xlsx(xlsx_path, df, wide, n_months, start_m, end_m)
        print(f"\nwrote {xlsx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
