"""
Weekly deal health report — emails a list of deals that look unhealthy
across Magnite, GAM, and Pubmatic, based on the last 7 days of cached data.

Two sections:
  1. Never accepted    — bid_requests > 0, bid_responses = 0 (buyer not bidding)
                         (Magnite + Pubmatic + GAM auction deals)
  2. PG not delivering — Programmatic Guaranteed deals with 0 impressions
                         (GAM only — PG doesn't expose bid metrics)

Run manually:  python weekly_report.py
Run on a cron: GitHub Actions weekly_report.yml
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from agentmail import AgentMail

import pandas as pd
import sqlalchemy


# ── env / config ──────────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# Load AE map and deal-type maps from settings.json so this report uses the
# same source of truth as dashboard.py — edits to ae_names/deal_type_aliases
# flow through here without a separate hardcoded copy to maintain.
_SETTINGS_PATH = Path(__file__).parent / "settings.json"

def _load_settings() -> dict:
    if not _SETTINGS_PATH.exists():
        return {}
    try:
        with open(_SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

_CFG = _load_settings()
AE_NAMES: dict[str, str]          = _CFG.get("ae_names", {}) or {}
DEAL_TYPE_CODES: dict[str, str]   = _CFG.get("deal_type_codes", {}) or {}
DEAL_TYPE_ALIASES: dict[str, str] = _CFG.get("deal_type_aliases", {}) or {}
_CODE_BY_NAME: dict[str, str]     = {v: k for k, v in DEAL_TYPE_CODES.items()}

_AE_REGEX = r"Team-(?:USA|INTL)_([A-Za-z]+)"

# GAM Preferred Deal threshold — PD buyers have first-look optionality, so many
# PDs legitimately receive requests and decline. Flag a PD only if it had real
# traffic AND a full 7 days of data with zero bids. PA gets no threshold (any
# unbid PA merits attention given the invited-auction commitment).
GAM_PD_MIN_REQUESTS = 100_000
GAM_PD_MIN_DAYS = 7


def _deal_type_from_name(deal: str) -> str:
    """Mirrors dashboard._parse_deal: position 1 of the underscore-split name
    is the deal-type code (e.g. `Newsweek_PA_...` → 'PA'). Returns '' if the
    name doesn't fit the convention."""
    if not deal:
        return ""
    parts = str(deal).split("_")
    if len(parts) < 2:
        return ""
    code = parts[1].strip()
    return code if code in DEAL_TYPE_CODES else ""


def _deal_type_from_alias(raw: str) -> str:
    """Normalize an SSP's vendor-specific deal_type string (e.g. Pubmatic's
    'PMP' / 'PMP Preferred') to a short code via the dashboard's
    deal_type_aliases → deal_type_codes pipeline. Returns '' if unrecognized."""
    if not raw:
        return ""
    full = DEAL_TYPE_ALIASES.get(raw, raw)
    return _CODE_BY_NAME.get(full, "")


def _engine() -> sqlalchemy.Engine:
    return sqlalchemy.create_engine(os.environ["DATABASE_URL"])


def _derive_seller(deal_series: pd.Series) -> pd.Series:
    return (
        deal_series
        .str.extract(_AE_REGEX, expand=False)
        .map(AE_NAMES)
        .fillna("Unknown")
    )


# ── data ──────────────────────────────────────────────────────────────────────

def load_magnite() -> pd.DataFrame:
    """Magnite SSP — deals with zero bid_responses in cache window."""
    with _engine().connect() as conn:
        df = pd.read_sql(
            """
            SELECT
                deal,
                SUM(bid_requests)  AS total_requests,
                SUM(bid_responses) AS total_bids,
                COUNT(DISTINCT date) AS days_in_data,
                MIN(date) AS first_seen
            FROM magnite_deal_daily
            WHERE deal IS NOT NULL
              AND deal != ''
              AND UPPER(TRIM(REPLACE(deal, '-', ''))) != 'NA'
            GROUP BY deal
            HAVING SUM(bid_responses) = 0
            """,
            conn,
        )
    df["ssp"] = "Magnite"
    df["deal_type"] = df["deal"].apply(_deal_type_from_name)
    df["seller"] = _derive_seller(df["deal"])
    return df


def load_pubmatic() -> pd.DataFrame:
    """Pubmatic SSP — deals with zero bid responses in cache window."""
    with _engine().connect() as conn:
        df = pd.read_sql(
            """
            SELECT
                deal,
                deal_type AS pubmatic_deal_type,
                SUM(total_requests)         AS total_requests,
                SUM(non_zero_bid_responses) AS total_bids,
                COUNT(DISTINCT date) AS days_in_data,
                MIN(date) AS first_seen
            FROM pubmatic_deals
            WHERE deal IS NOT NULL
              AND deal != ''
              AND UPPER(TRIM(REPLACE(deal, '-', ''))) != 'NA'
            GROUP BY deal, deal_type
            HAVING SUM(non_zero_bid_responses) = 0
            """,
            conn,
        )
    df["ssp"] = "Pubmatic"
    df["deal_type"] = df["pubmatic_deal_type"].apply(_deal_type_from_alias)
    df = df.drop(columns=["pubmatic_deal_type"])
    # Pubmatic deal names don't follow the Newsweek_<TYPE>_..._Team-USA_<AE>
    # convention, so the regex extract will return Unknown for most rows —
    # matches dashboard.py behavior.
    df["seller"] = _derive_seller(df["deal"])
    df["total_requests"] = df["total_requests"].fillna(0).astype("int64")
    df["total_bids"] = df["total_bids"].fillna(0).astype("int64")
    return df


def load_gam() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    GAM — returns (pa_unhealthy, pd_unhealthy, pg_undelivered).

    Deal type is derived from the deal-name prefix (Newsweek_PA_ / Newsweek_PD_
    / Newsweek_PG_). Legacy names that don't match the convention (e.g.
    `nw_adx_omd_apple_*`, `*_Google-Demand-Facilitation-deals_*`) are bucketed
    with PD since they behave like preferred deals (first-look, optional).

    PA: zero bids over the window, any request volume → flagged.
    PD: zero bids over the full {GAM_PD_MIN_DAYS}-day window with at least
        {GAM_PD_MIN_REQUESTS:,} requests → flagged.
    PG: no bid metrics available — falls back to ad_server_impressions=0
        from gam_pmp_deals.
    """
    with _engine().connect() as conn:
        bids = pd.read_sql(
            """
            SELECT
                programmatic_deal_name AS deal,
                SUM(deals_bid_requests) AS total_requests,
                SUM(deals_bids)         AS total_bids,
                COUNT(DISTINCT date)    AS days_in_data,
                MIN(date)               AS first_seen
            FROM gam_deal_bid_daily
            WHERE programmatic_deal_name IS NOT NULL
              AND programmatic_deal_name != ''
            GROUP BY programmatic_deal_name
            HAVING SUM(deals_bids) = 0
               AND SUM(deals_bid_requests) > 0
            """,
            conn,
        )
        pg = pd.read_sql(
            """
            SELECT
                programmatic_deal_name AS deal,
                SUM(ad_server_impressions) AS total_impressions,
                COUNT(DISTINCT date) AS days_in_data,
                MIN(date) AS first_seen
            FROM gam_pmp_deals
            WHERE programmatic_deal_name IS NOT NULL
              AND programmatic_deal_name != ''
              AND programmatic_channel_name = 'Programmatic Guaranteed'
            GROUP BY programmatic_deal_name
            HAVING SUM(ad_server_impressions) = 0
            """,
            conn,
        )

    bids["ssp"] = "GAM"
    bids["seller"] = _derive_seller(bids["deal"])
    bids["deal_type"] = bids["deal"].apply(_deal_type_from_name)
    for col in ("total_requests", "total_bids"):
        bids[col] = bids[col].fillna(0).astype("int64")

    is_pa = bids["deal_type"] == "PA"
    is_pg = bids["deal_type"] == "PG"
    # Legacy names that don't fit the Newsweek_<TYPE>_ convention get a blank
    # deal_type but are still treated as PD for thresholding (they behave like
    # preferred deals).

    pa_unhealthy = bids[is_pa].copy()

    pd_candidates = bids[~is_pa & ~is_pg].copy()
    pd_unhealthy = pd_candidates[
        (pd_candidates["days_in_data"] >= GAM_PD_MIN_DAYS)
        & (pd_candidates["total_requests"] >= GAM_PD_MIN_REQUESTS)
    ].copy()

    pg["seller"] = _derive_seller(pg["deal"])

    return pa_unhealthy, pd_unhealthy, pg


def load_unhealthy() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (never_accepted, gam_pg_undelivered)."""
    magnite = load_magnite()
    pubmatic = load_pubmatic()
    gam_pa, gam_pd, gam_pg = load_gam()

    common_cols = ["ssp", "deal_type", "deal", "seller", "total_requests", "total_bids", "days_in_data", "first_seen"]
    combined = pd.concat(
        [magnite[common_cols], pubmatic[common_cols], gam_pa[common_cols], gam_pd[common_cols]],
        ignore_index=True,
    )

    never_accepted = combined[combined["total_requests"] > 0].copy().sort_values(
        ["ssp", "deal_type", "total_requests"], ascending=[True, True, False]
    )

    return never_accepted, gam_pg


# ── email ─────────────────────────────────────────────────────────────────────

def _table_html(df: pd.DataFrame, columns: list[str], headers: list[str]) -> str:
    rows_html = ""
    for _, row in df[columns].iterrows():
        cells = "".join(f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>{row[c]}</td>" for c in columns)
        rows_html += f"<tr>{cells}</tr>"

    header_html = "".join(
        f"<th style='padding:6px 12px;text-align:left;background:#f0f4f8;border-bottom:2px solid #ccc'>{h}</th>"
        for h in headers
    )
    return f"""
    <table style='border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:13px'>
      <thead><tr>{header_html}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>"""


def _section(title: str, color: str, blurb: str, df: pd.DataFrame, cols: list[str], headers: list[str]) -> str:
    if df.empty:
        return f"<h3 style='color:{color}'>{title}</h3><p>None — all clear.</p>"
    return f"""
    <h3 style='color:{color}'>{title} ({len(df)} deals)</h3>
    <p style='font-size:12px;color:#666;margin:-6px 0 8px'>{blurb}</p>
    {_table_html(df, cols, headers)}
    <br>"""


def _section_by_seller(title: str, color: str, blurb: str, df: pd.DataFrame, cols: list[str], headers: list[str]) -> str:
    """Section split into one sub-table per seller, sorted by deal count desc.
    Unknown sellers come last regardless of count."""
    if df.empty:
        return f"<h3 style='color:{color}'>{title}</h3><p>None — all clear.</p>"

    out = (
        f"<h3 style='color:{color}'>{title} ({len(df)} deals)</h3>"
        f"<p style='font-size:12px;color:#666;margin:-6px 0 12px'>{blurb}</p>"
    )

    sellers = df["seller"].fillna("Unknown").replace("", "Unknown").unique().tolist()
    known   = sorted([s for s in sellers if s != "Unknown"],
                     key=lambda s: -len(df[df["seller"] == s]))
    ordered = known + (["Unknown"] if "Unknown" in sellers else [])

    for seller in ordered:
        if seller == "Unknown":
            grp = df[df["seller"].fillna("Unknown").replace("", "Unknown") == "Unknown"]
        else:
            grp = df[df["seller"] == seller]
        grp = grp.sort_values(["ssp", "deal_type", "total_requests"], ascending=[True, True, False])
        out += (
            f"<h4 style='color:#555;margin:18px 0 6px;font-size:14px'>"
            f"{seller} <span style='color:#888;font-weight:normal'>— {len(grp)} deals</span></h4>"
            f"{_table_html(grp, cols, headers)}<br>"
        )
    return out


def build_email(never_accepted: pd.DataFrame, gam_pg: pd.DataFrame) -> str:
    today = date.today().strftime("%B %d, %Y")

    body = f"""
    <html><body style='font-family:Arial,sans-serif;color:#333;max-width:900px;margin:auto;padding:20px'>
      <h2>Weekly Deal Health Report — {today}</h2>
      <p>Unhealthy deals across Magnite, GAM, and Pubmatic — based on the <strong>last 7 days</strong> of cached data.</p>

      {_section_by_seller(
          "⚠️ Sent but never accepted by buyer",
          "#e67e22",
          (
              "Auction deals receiving bid requests but the buyer hasn't bid. "
              "Likely a buyer-side issue (deal not activated, targeting mismatch). "
              "Grouped by seller, then by SSP within. "
              f"GAM PD threshold: only flagged if days_in_data ≥ {GAM_PD_MIN_DAYS} "
              f"and total bid requests ≥ {GAM_PD_MIN_REQUESTS:,} "
              "(PDs have first-look optionality so low-volume zero-bid deals are noise). "
              "GAM PA has no threshold."
          ),
          never_accepted,
          ["ssp", "deal_type", "deal", "total_requests", "days_in_data", "first_seen"],
          ["SSP", "Deal Type", "Deal", "Total bid requests", "Days in data", "First seen"],
      )}

      {_section(
          "📭 GAM PG deals not delivering",
          "#8e44ad",
          "Programmatic Guaranteed lines with zero impressions over the last 7 days. PG doesn't expose bid metrics, so this is the only health signal available.",
          gam_pg,
          ["deal", "seller", "days_in_data", "first_seen"],
          ["Deal", "Seller", "Days in data", "First seen"],
      )}

      <hr style='margin-top:30px'>
      <p style='font-size:11px;color:#999'>
        Generated by Newsweek yield-dashboard &mdash;
        <a href='https://newsweek-magnite.streamlit.app'>View dashboard</a>
      </p>
    </body></html>
    """
    return body


def send_email(html_body: str) -> None:
    client    = AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
    inbox_id  = os.environ["AGENTMAIL_INBOX_ID"]
    recipient = os.environ["REPORT_TO_EMAIL"]

    client.inboxes.messages.send(
        inbox_id,
        to=recipient,
        subject=f"Weekly Deal Health Report — {date.today().strftime('%b %d, %Y')}",
        html=html_body,
    )
    print(f"Email sent to {recipient}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _load_dotenv()
    never_accepted, gam_pg = load_unhealthy()
    print(
        f"Never accepted: {len(never_accepted)} | "
        f"GAM PG undelivered: {len(gam_pg)}"
    )
    html = build_email(never_accepted, gam_pg)
    send_email(html)


if __name__ == "__main__":
    main()
