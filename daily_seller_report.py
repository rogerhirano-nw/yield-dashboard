"""
Daily seller (AE) campaign report.

For each Newsweek AE with at least one currently-delivering or recently-
completed direct line item, render a per-AE status report and deliver it
through two channels:

  1. Email — HTML modeled on the Tasklet "GAM Daily Campaign Report"
     format, sent via agentmail with adops on Cc.
  2. Teams — compact Adaptive Card (headline + per-LI bullets) posted to
     a single shared ad-ops channel (e.g. #adops-daily) via a "Post to a
     channel when a webhook request is received" Workflow URL. Each AE's
     card @mentions them so they get pinged; ad-ops sees every post.

Scope:
    - order_name LIKE 'Newsweek_Direct%'
    - status = 'Delivering' OR (status = 'Completed' AND end_date >= today - 7d)
    - seller_ae resolves to a real AE (House / unmapped rows are skipped)

Data sources (both populated by refresh_cache.py refresh_gam):
    - gam_campaigns        — one row per line item (totals, pacing, lifetime)
    - gam_campaigns_daily  — one row per (line_item_id, date) for the last 7d

Email recipients:
    - To: <AE>           — derived from display name as f<first>.<last>@newsweek.com
    - Cc: ADOPS_EMAIL    — adops@newsweek.com by default

Teams recipient:
    - Single Workflow URL in TEAMS_WEBHOOK_URL pointing at the shared
      ad-ops channel. Every AE's card lands there. AE is @mentioned via
      their UPN (j.amalfi@newsweek.com), which the Flow Bot resolves to a
      Teams user.

Dry-run (default ON for first rollout):
    - DRY_RUN=1 routes every per-AE email to DRY_RUN_TO and every Teams
      post to TEAMS_DRY_RUN_WEBHOOK (falls back to TEAMS_WEBHOOK_URL),
      prefixing the headline with "[DRY RUN → <AE>]". Set DRY_RUN=0 to
      go live (cards then ping the real AEs).

Run manually:  python daily_seller_report.py
Run on cron:   .github/workflows/daily_seller_report.yml (12 UTC = 8 AM EDT)
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import urllib.error
import urllib.request

import pandas as pd
import sqlalchemy
from agentmail import AgentMail


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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


SETTINGS = json.loads((Path(__file__).parent / "settings.json").read_text())
AE_NAMES: dict[str, str] = SETTINGS.get("ae_names", {})
AE_REGEX = re.compile(r"Team-(?:USA|INTL)_([A-Za-z]+)")

ADOPS_EMAIL = os.environ.get("ADOPS_EMAIL", "adops@newsweek.com")
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
DRY_RUN_TO = os.environ.get("DRY_RUN_TO", "roger.hirano@newsweek.com")

# Teams: single shared channel (e.g. #adops-daily). Each AE's card is posted
# to this channel and @mentions the AE so they get pinged.
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
TEAMS_DRY_RUN_WEBHOOK = os.environ.get("TEAMS_DRY_RUN_WEBHOOK", "") or TEAMS_WEBHOOK_URL
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://newsweek-magnite.streamlit.app")


# ── seller_ae derivation (mirrors dashboard.py) ───────────────────────────────

def _parse_gam_salesperson(val) -> Optional[str]:
    """Strip GAM's 'Newsweek - Sales - <name> (<email>)' wrapper. Mirrors dashboard.py."""
    if not isinstance(val, str) or not val.strip():
        return None
    m = re.search(r"-\s*([^-(]+?)\s*(?:\(|$)", val)
    return m.group(1).strip() if m else val.strip()


def _resolve_seller_ae(row) -> Optional[str]:
    """salesperson (parsed) → order_name regex → line_item_name regex → display name."""
    sp = _parse_gam_salesperson(row.get("salesperson"))
    if sp:
        if sp in AE_NAMES.values():
            return sp
        if sp in AE_NAMES:
            return AE_NAMES[sp]
        return sp  # honor whatever GAM returned even if not in our map
    for fld in ("order_name", "line_item_name"):
        val = row.get(fld) or ""
        m = AE_REGEX.search(val)
        if m and m.group(1) in AE_NAMES:
            return AE_NAMES[m.group(1)]
    return None


def ae_to_email(display_name: str) -> Optional[str]:
    """'Julie Amalfi' → 'j.amalfi@newsweek.com'. Skip House / unmapped / single-word names."""
    if not display_name or display_name == "House":
        return None
    parts = display_name.strip().split()
    if len(parts) < 2:
        return None
    return f"{parts[0][0].lower()}.{parts[-1].lower()}@newsweek.com"


# ── data ──────────────────────────────────────────────────────────────────────

def _engine() -> sqlalchemy.Engine:
    return sqlalchemy.create_engine(os.environ["DATABASE_URL"])


def load_active_direct(today: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (campaigns, daily) DataFrames filtered to in-scope direct line items."""
    cutoff = (today - timedelta(days=7)).isoformat()
    engine = _engine()
    with engine.connect() as conn:
        campaigns = pd.read_sql(
            sqlalchemy.text(
                """
                SELECT *
                FROM gam_campaigns
                WHERE order_name LIKE 'Newsweek_Direct%'
                  AND (
                      status = 'Delivering'
                      OR (status = 'Completed' AND end_date >= :cutoff)
                  )
                """
            ),
            conn,
            params={"cutoff": cutoff},
        )
        if campaigns.empty:
            return campaigns, pd.DataFrame()

        # gam_campaigns_daily is populated by the updated refresh_gam — it may
        # not exist yet on the first run after this change ships. Treat a
        # missing table as "no per-day rows" and let the renderer fall back
        # to the _1d / _2d columns already on gam_campaigns.
        from sqlalchemy import inspect as sa_inspect
        if "gam_campaigns_daily" in sa_inspect(conn).get_table_names():
            ids = campaigns["line_item_id"].astype(str).unique().tolist()
            daily = pd.read_sql(
                sqlalchemy.text(
                    """
                    SELECT *
                    FROM gam_campaigns_daily
                    WHERE line_item_id = ANY(:ids)
                    ORDER BY line_item_id, date DESC
                    """
                ),
                conn,
                params={"ids": ids},
            )
        else:
            logger.warning("gam_campaigns_daily table not found — using _1d/_2d fallback, no 7-day table")
            daily = pd.DataFrame()

    campaigns["seller_ae"] = campaigns.apply(_resolve_seller_ae, axis=1)
    return campaigns, daily


# ── number formatting ────────────────────────────────────────────────────────

def _fmt_int(v) -> str:
    if pd.isna(v):
        return "—"
    return f"{int(round(float(v))):,}"


def _fmt_money(v) -> str:
    if pd.isna(v):
        return "—"
    return f"${float(v):,.2f}"


def _fmt_pct(v) -> str:
    if pd.isna(v):
        return "—"
    return f"{float(v):.2f}%"


def _delta_imp(curr, prior) -> str:
    if pd.isna(curr) or pd.isna(prior) or round(float(curr) - float(prior)) == 0:
        return ""
    diff = float(curr) - float(prior)
    arrow = "▲" if diff > 0 else "▼"
    return f"{arrow} {diff:+,.0f} vs prior day"


def _delta_pp(curr_pct, prior_pct) -> str:
    if pd.isna(curr_pct) or pd.isna(prior_pct) or abs(float(curr_pct) - float(prior_pct)) < 0.005:
        return ""
    diff = float(curr_pct) - float(prior_pct)
    arrow = "▲" if diff > 0 else "▼"
    return f"{arrow} {diff:+.2f}pp vs prior day"


def _delta_money(curr, prior) -> str:
    if pd.isna(curr) or pd.isna(prior) or abs(float(curr) - float(prior)) < 0.005:
        return ""
    diff = float(curr) - float(prior)
    arrow = "▲" if diff > 0 else "▼"
    return f"{arrow} ${diff:+,.2f} vs prior day"


def _with_delta(value_str: str, delta_str: str) -> str:
    """Append ' (Δ)' only when there's a meaningful delta to show."""
    return f"{value_str} ({delta_str})" if delta_str else value_str


def _fmt_date(s) -> str:
    """'2026-05-16' → '5/16'."""
    if not s:
        return "?"
    try:
        d = datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
        return f"{d.month}/{d.day}"
    except Exception:
        return str(s)


def _fmt_flight(start, end) -> str:
    return f"{_fmt_date(start)} - {_fmt_date(end)}"


# ── HTML render ────────────────────────────────────────────────────────────────

def _per_day_metrics(daily_li: pd.DataFrame, li: pd.Series) -> dict:
    """
    Yesterday and day-before metrics for one line item.

    Prefers gam_campaigns_daily (full per-day breakdown including revenue);
    falls back to the _1d/_2d columns on gam_campaigns for callers where
    the daily table isn't available yet (revenue Δ unavailable in fallback).
    """
    if not daily_li.empty:
        sorted_df = daily_li.sort_values("date", ascending=False)
        y = sorted_df.iloc[0] if len(sorted_df) >= 1 else None
        p = sorted_df.iloc[1] if len(sorted_df) >= 2 else None
        def get(row, col):
            return None if row is None or col not in row.index else row[col]
        return {
            "y_imp":   get(y, "ad_server_impressions"),
            "p_imp":   get(p, "ad_server_impressions"),
            "y_clk":   get(y, "ad_server_clicks"),
            "p_clk":   get(p, "ad_server_clicks"),
            "y_rev":   get(y, "ad_server_cpm_and_cpc_revenue"),
            "p_rev":   get(p, "ad_server_cpm_and_cpc_revenue"),
            "y_view":  get(y, "ad_server_active_view_viewable_impressions"),
            "p_view":  get(p, "ad_server_active_view_viewable_impressions"),
            "y_meas":  get(y, "ad_server_active_view_measurable_impressions"),
            "p_meas":  get(p, "ad_server_active_view_measurable_impressions"),
        }
    # Fallback: per-day fields stored directly on gam_campaigns.
    return {
        "y_imp":  li.get("impressions_1d"),
        "p_imp":  li.get("impressions_2d"),
        "y_clk":  li.get("clicks_1d"),
        "p_clk":  li.get("clicks_2d"),
        "y_rev":  None,
        "p_rev":  None,
        "y_view": li.get("viewable_imps_1d"),
        "p_view": li.get("viewable_imps_2d"),
        "y_meas": li.get("measurable_imps_1d"),
        "p_meas": li.get("measurable_imps_2d"),
    }


def _ctr(clicks, imps) -> Optional[float]:
    if pd.isna(clicks) or pd.isna(imps) or float(imps) == 0:
        return None
    return float(clicks) / float(imps) * 100


def _vw(viewable, measurable) -> Optional[float]:
    if pd.isna(viewable) or pd.isna(measurable) or float(measurable) == 0:
        return None
    return float(viewable) / float(measurable) * 100


def _seven_day_table_html(daily_li: pd.DataFrame) -> str:
    if daily_li.empty:
        return ""
    rows = []
    th = "padding:8px 12px;border:1px solid #e4e4e4;background:#efefef;font-weight:600;text-align:left;color:#1c1c1e"
    td = "padding:8px 12px;border:1px solid #e4e4e4;color:#1c1c1e"
    rows.append(
        f"<tr>"
        f"<th style='{th}'>Date</th>"
        f"<th style='{th}'>Impressions</th>"
        f"<th style='{th}'>Clicks</th>"
        f"<th style='{th}'>CTR</th>"
        f"<th style='{th}'>Revenue</th>"
        f"<th style='{th}'>Viewability</th>"
        f"</tr>"
    )
    for _, r in daily_li.sort_values("date", ascending=False).iterrows():
        rows.append(
            f"<tr>"
            f"<td style='{td}'>{_fmt_date(r.get('date'))}</td>"
            f"<td style='{td}'>{_fmt_int(r.get('ad_server_impressions'))}</td>"
            f"<td style='{td}'>{_fmt_int(r.get('ad_server_clicks'))}</td>"
            f"<td style='{td}'>{_fmt_pct(_ctr(r.get('ad_server_clicks'), r.get('ad_server_impressions')))}</td>"
            f"<td style='{td}'>{_fmt_money(r.get('ad_server_cpm_and_cpc_revenue'))}</td>"
            f"<td style='{td}'>{_fmt_pct(_vw(r.get('ad_server_active_view_viewable_impressions'), r.get('ad_server_active_view_measurable_impressions')))}</td>"
            f"</tr>"
        )
    return (
        "<table style='margin:8px 0;border-collapse:collapse;width:100%;"
        "font:14px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif'>"
        + "".join(rows)
        + "</table>"
    )


def _line_item_html(li: pd.Series, daily_li: pd.DataFrame) -> str:
    pm = _per_day_metrics(daily_li, li)
    name = li.get("line_item_name") or "(no name)"
    flight = _fmt_flight(li.get("start_date"), li.get("end_date"))

    pacing = li.get("pacing_pct")
    pacing_str = "—" if pd.isna(pacing) else f"{float(pacing):.1f}%"
    if not pd.isna(pacing) and float(pacing) >= 100 and (li.get("status") == "Completed" or (li.get("remaining_impressions") or 1) <= 0):
        pacing_line = f"Pacing at {pacing_str} — fully delivered"
    else:
        pacing_line = f"Pacing at {pacing_str}"

    goal = li.get("impressions_goal")
    delivered = li.get("lifetime_impressions_delivered")
    remaining = None
    if pd.notna(goal) and pd.notna(delivered):
        remaining = max(float(goal) - float(delivered), 0)

    y_imp, p_imp = pm.get("y_imp"), pm.get("p_imp")
    y_rev, p_rev = pm.get("y_rev"), pm.get("p_rev")
    y_ctr = _ctr(pm.get("y_clk"), pm.get("y_imp"))
    p_ctr = _ctr(pm.get("p_clk"), pm.get("p_imp"))
    y_vw = _vw(pm.get("y_view"), pm.get("y_meas"))
    p_vw = _vw(pm.get("p_view"), pm.get("p_meas"))

    viewability_line = ""
    if y_vw is not None or p_vw is not None:
        viewability_line = f"Viewability: {_with_delta(_fmt_pct(y_vw), _delta_pp(y_vw, p_vw))}<br>"

    delta_imp_str = _delta_imp(y_imp, p_imp)
    rev_line = f"Revenue: {_with_delta(_fmt_money(y_rev), _delta_money(y_rev, p_rev))}" if y_rev is not None else ""

    p = (
        "<p style='margin:0 0 16px 0;font:14px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1c1c1e'>"
        f"<strong style='font-weight:600'>{name}</strong><br>"
        f"Flight: {flight}<br>"
        f"{pacing_line}<br>"
        f"Goal: {_fmt_int(goal)} IMP<br>"
        f"Remaining: {_fmt_int(remaining)} IMP<br>"
        f"Yesterday: {_fmt_int(y_imp)} IMP | Total Delivered: {_fmt_int(delivered)} IMP<br>"
        + (f"{delta_imp_str}<br>" if delta_imp_str else "")
        + f"CTR: {_with_delta(_fmt_pct(y_ctr), _delta_pp(y_ctr, p_ctr))}<br>"
        f"{viewability_line}"
        f"{rev_line}"
        "</p>"
    )
    return p + _seven_day_table_html(daily_li)


def _campaign_html(order_name: str, items: pd.DataFrame, daily: pd.DataFrame) -> str:
    h3 = (
        "margin:16px 0 8px 0;font:600 18px/1.35 -apple-system,BlinkMacSystemFont,"
        "Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1c1c1e"
    )
    blocks = [f"<h3 style='{h3}'>Campaign: {order_name}</h3>"]
    has_daily = not daily.empty and "line_item_id" in daily.columns
    for _, li in items.iterrows():
        if has_daily:
            daily_li = daily[daily["line_item_id"].astype(str) == str(li["line_item_id"])]
        else:
            daily_li = daily  # empty DataFrame; renderer falls back to _1d/_2d
        blocks.append(_line_item_html(li, daily_li))
    return "".join(blocks)


def render_email(ae_name: str, ae_items: pd.DataFrame, daily: pd.DataFrame, today: date) -> str:
    n = len(ae_items)
    # Roll-up = yesterday's impressions/revenue across the AE's line items
    if not daily.empty:
        daily_y_per_li = (
            daily.sort_values("date", ascending=False)
                 .drop_duplicates(subset=["line_item_id"], keep="first")
        )
        y_subset = daily_y_per_li[daily_y_per_li["line_item_id"].astype(str).isin(ae_items["line_item_id"].astype(str))]
        total_imp = int(y_subset["ad_server_impressions"].fillna(0).sum()) if "ad_server_impressions" in y_subset.columns else 0
        total_rev = float(y_subset["ad_server_cpm_and_cpc_revenue"].fillna(0).sum()) if "ad_server_cpm_and_cpc_revenue" in y_subset.columns else 0.0
    else:
        # Fallback: yesterday's impressions come from impressions_1d on gam_campaigns;
        # per-line-item per-day revenue isn't stored there, so the roll-up shows 0.
        total_imp = int(ae_items["impressions_1d"].fillna(0).sum()) if "impressions_1d" in ae_items.columns else 0
        total_rev = 0.0
    date_str = f"{today.month}/{today.day}/{today.year}"

    h1 = (
        "margin:24px 0 16px 0;font:600 24px/1.35 -apple-system,BlinkMacSystemFont,"
        "Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1c1c1e"
    )
    body_open = (
        "<div style='max-width:600px;padding:16px;"
        "font:14px/1.7 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1c1c1e'>"
        f"<h1 style='{h1}'>Campaign Status: {ae_name} — {date_str}</h1>"
        f"<p style='margin:0 0 16px 0'><strong style='font-weight:600'>"
        f"{n} campaigns | {total_imp:,} impressions | {_fmt_money(total_rev)} revenue"
        "</strong></p>"
        "<hr style='margin:24px 0;border:none;border-top:1px solid #e4e4e4'>"
    )

    blocks = []
    for order_name, items in ae_items.groupby("order_name", dropna=False):
        display_order = order_name or "(no order name)"
        blocks.append(_campaign_html(display_order, items, daily))

    footer = (
        "<hr style='margin:24px 0;border:none;border-top:1px solid #e4e4e4'>"
        "<p style='font-size:11px;color:#999;margin:0'>Generated by yield-dashboard — "
        "<a href='https://newsweek-magnite.streamlit.app' style='color:#4f6f52'>View dashboard</a></p>"
        "</div>"
    )
    return f"<html><body style='margin:0'>{body_open}{''.join(blocks)}{footer}</body></html>"


# ── send ──────────────────────────────────────────────────────────────────────

def _agentmail_client() -> tuple[AgentMail, str]:
    return AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"]), os.environ["AGENTMAIL_INBOX_ID"]


def send_one(ae_name: str, ae_email: str, html: str, today: date) -> None:
    client, inbox_id = _agentmail_client()
    date_str = today.strftime("%m/%d/%Y")
    subject = f"Campaign Status: {ae_name} — {date_str}"

    if DRY_RUN:
        to = DRY_RUN_TO
        cc = None
        subject = f"[DRY RUN → {ae_name} <{ae_email}>] {subject}"
    else:
        to = ae_email
        cc = [ADOPS_EMAIL] if ADOPS_EMAIL else None

    kwargs = {"to": to, "subject": subject, "html": html}
    if cc:
        kwargs["cc"] = cc

    client.inboxes.messages.send(inbox_id, **kwargs)
    logger.info("Sent: %s → to=%s cc=%s", subject, to, cc)


# ── Teams (Workflow webhook) ──────────────────────────────────────────────────

def _rollup(ae_items: pd.DataFrame, daily: pd.DataFrame) -> tuple[int, float]:
    """Yesterday's total impressions + revenue across an AE's line items."""
    if not daily.empty and "line_item_id" in daily.columns:
        daily_y = (
            daily.sort_values("date", ascending=False)
                 .drop_duplicates(subset=["line_item_id"], keep="first")
        )
        ids = ae_items["line_item_id"].astype(str)
        y_subset = daily_y[daily_y["line_item_id"].astype(str).isin(ids)]
        total_imp = int(y_subset["ad_server_impressions"].fillna(0).sum()) if "ad_server_impressions" in y_subset.columns else 0
        total_rev = float(y_subset["ad_server_cpm_and_cpc_revenue"].fillna(0).sum()) if "ad_server_cpm_and_cpc_revenue" in y_subset.columns else 0.0
        return total_imp, total_rev
    total_imp = int(ae_items["impressions_1d"].fillna(0).sum()) if "impressions_1d" in ae_items.columns else 0
    return total_imp, 0.0


def _li_friendly_name(li: pd.Series) -> str:
    """
    Try to derive a short, Tasklet-style "<Advertiser> <Campaign> · <Format>"
    label from the line_item_name; fall back to the full name.

    Splits on '_' using the dashboard.py convention: idx 7 = advertiser,
    idx 8 = campaign (hyphens → spaces), idx 10 = ad format.
    """
    name = li.get("line_item_name") or ""
    parts = name.split("_") if isinstance(name, str) else []
    def _at(i): return parts[i].strip() if i < len(parts) else ""
    advertiser = _at(7)
    campaign = _at(8).replace("-", " ").strip()
    fmt = _at(10).replace("-", " ").strip()
    pieces = [p for p in (advertiser, campaign, fmt) if p]
    short = " · ".join(pieces) if pieces else name
    return short[:120] + ("…" if len(short) > 120 else "")


def render_teams_card(ae_name: str, ae_email: Optional[str], ae_items: pd.DataFrame, daily: pd.DataFrame, today: date, headline_prefix: str = "") -> dict:
    """
    Build the Workflow-webhook payload (a Teams 'message' wrapping an Adaptive Card).

    When ae_email is provided, includes a msteams mention entity so the AE is
    pinged. Adaptive Cards posted via the Flow Bot resolve the mentioned `id`
    against AAD — a Newsweek UPN like j.amalfi@newsweek.com is the right value.
    """
    n = len(ae_items)
    total_imp, total_rev = _rollup(ae_items, daily)
    date_str = f"{today.month}/{today.day}/{today.year}"

    # Mention syntax: an <at>display</at> token in the TextBlock plus an entity
    # in msteams.entities that resolves it. Falls back to plain bold name if
    # we don't have an email (House etc. — but those are filtered upstream).
    if ae_email:
        mention_token = f"<at>{ae_name}</at>"
        mention_entities = [
            {
                "type": "mention",
                "text": mention_token,
                "mentioned": {"id": ae_email, "name": ae_name},
            }
        ]
        headline_text = f"{headline_prefix}Campaign Status — {mention_token}"
    else:
        mention_entities = []
        headline_text = f"{headline_prefix}Campaign Status: {ae_name}"

    body: list[dict] = [
        {
            "type": "TextBlock",
            "text": headline_text,
            "size": "Large",
            "weight": "Bolder",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": f"{date_str} · {n} campaigns · {total_imp:,} impressions · {_fmt_money(total_rev)}",
            "isSubtle": True,
            "spacing": "None",
            "wrap": True,
        },
    ]

    has_daily = not daily.empty and "line_item_id" in daily.columns
    for _, li in ae_items.sort_values("order_name", na_position="last").iterrows():
        if has_daily:
            daily_li = daily[daily["line_item_id"].astype(str) == str(li["line_item_id"])]
        else:
            daily_li = pd.DataFrame()
        pm = _per_day_metrics(daily_li, li)
        y_imp, p_imp = pm.get("y_imp"), pm.get("p_imp")
        y_ctr = _ctr(pm.get("y_clk"), pm.get("y_imp"))

        pacing = li.get("pacing_pct")
        pacing_str = "—" if pd.isna(pacing) else f"{float(pacing):.1f}%"
        fully = (
            not pd.isna(pacing)
            and float(pacing) >= 100
            and (li.get("status") == "Completed" or (li.get("remaining_impressions") or 1) <= 0)
        )
        if fully:
            pacing_str += " (fully delivered)"

        delta = _delta_imp(y_imp, p_imp)
        yesterday_bit = f"Yesterday {_fmt_int(y_imp)} imp" + (f" ({delta})" if delta else "")
        ctr_bit = f"CTR {_fmt_pct(y_ctr)}"

        body.append({
            "type": "TextBlock",
            "text": f"▸ **{_li_friendly_name(li)}**",
            "wrap": True,
            "spacing": "Medium",
            "separator": True,
        })
        body.append({
            "type": "TextBlock",
            "text": f"Pacing {pacing_str} · {yesterday_bit} · {ctr_bit}",
            "wrap": True,
            "spacing": "None",
            "isSubtle": True,
        })

    card: dict = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": body,
        "actions": [
            {"type": "Action.OpenUrl", "title": "View dashboard", "url": DASHBOARD_URL}
        ],
    }
    if mention_entities:
        card["msteams"] = {"entities": mention_entities}
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }


def _post_teams(webhook_url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status >= 300:
                body = resp.read().decode("utf-8", "replace")
                raise RuntimeError(f"Teams webhook returned {resp.status}: {body}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        raise RuntimeError(f"Teams webhook HTTP {e.code}: {body}") from e


def maybe_send_teams(ae_name: str, ae_email: Optional[str], ae_items: pd.DataFrame, daily: pd.DataFrame, today: date) -> bool:
    """
    Post one card per AE to the shared ad-ops channel.

    DRY_RUN routes to TEAMS_DRY_RUN_WEBHOOK (defaults to TEAMS_WEBHOOK_URL) and
    suppresses the @mention entity so the AE isn't pinged during testing.
    Returns True if a post was attempted.
    """
    url = TEAMS_DRY_RUN_WEBHOOK if DRY_RUN else TEAMS_WEBHOOK_URL
    if not url:
        logger.info("Teams: %s not set — skipping Teams for %s",
                    "TEAMS_DRY_RUN_WEBHOOK / TEAMS_WEBHOOK_URL" if DRY_RUN else "TEAMS_WEBHOOK_URL",
                    ae_name)
        return False

    prefix = f"[DRY RUN → {ae_name}] " if DRY_RUN else ""
    # Suppress mention during dry-run so the real AE isn't pinged from a test channel.
    mention_email = None if DRY_RUN else ae_email

    payload = render_teams_card(ae_name, mention_email, ae_items, daily, today, headline_prefix=prefix)
    _post_teams(url, payload)
    logger.info("Teams: posted card for %s (%s)", ae_name, "dry-run channel" if DRY_RUN else "shared channel")
    return True


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _load_dotenv()
    today = date.today()
    campaigns, daily = load_active_direct(today)
    if campaigns.empty:
        logger.info("No in-scope direct campaigns today — nothing to send")
        return

    # Group by AE, drop House / unresolved
    campaigns = campaigns[campaigns["seller_ae"].notna() & (campaigns["seller_ae"] != "House")]
    if campaigns.empty:
        logger.info("All in-scope rows resolved to House / unmapped — nothing to send")
        return

    emails_sent = 0
    teams_sent = 0
    skipped_email = []
    for ae_name, ae_items in campaigns.groupby("seller_ae"):
        ae_email = ae_to_email(ae_name)
        if ae_email:
            html = render_email(ae_name, ae_items, daily, today)
            send_one(ae_name, ae_email, html, today)
            emails_sent += 1
        else:
            skipped_email.append(ae_name)

        try:
            if maybe_send_teams(ae_name, ae_email, ae_items, daily, today):
                teams_sent += 1
        except Exception:
            # Don't let a Teams webhook failure block the rest of the run.
            logger.exception("Teams post failed for %s — continuing", ae_name)

    logger.info(
        "Done. Emails sent: %d (skipped, no address: %s). Teams cards posted: %d.",
        emails_sent, skipped_email or "none", teams_sent,
    )
    if DRY_RUN:
        logger.info(
            "DRY_RUN=1 — email routed to %s, Teams routed to %s. Set DRY_RUN=0 to go live.",
            DRY_RUN_TO, TEAMS_DRY_RUN_WEBHOOK or "(unset)",
        )


if __name__ == "__main__":
    main()
