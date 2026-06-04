"""Daily CPA digest for the Spinfinite betting campaign (IO1109, order 4068491190).

Joins last 7 days of:
- `betting_conversions` — Improvado report rows (clicks/regs/FTPs/Net Cash by date + sub_id_1 + sub_id_2)
- `gam_campaigns`       — GAM delivery (impressions, spend) scoped to the betting order

Composes a plain-text digest with:
  - 7-day blended view + CPA target tracking
  - Daily breakdown table
  - Per-line-item breakdown (when sub_id_2 = "li<id>" is populated)
  - Per-creative-size breakdown
  - Alerts (low-volume warnings, broken-attribution rows like the failed macro test)

Sends via agentmail.to to BETTING_DIGEST_TO (default: roger.hirano@newsweek.com),
using the same outbound pattern as the Apple News daily report:

Triggered by `workflow_dispatch` from cron-job.org (same pattern as apple-news)
because GitHub-native cron drifts ~5-6h. Manual ad-hoc runs:
    python betting_daily_update.py --dry-run    # build + print, no send
    python betting_daily_update.py              # build + send

Env required:
    DATABASE_URL          Postgres (Supabase) — same as refresh_cache.py
    AGENTMAIL_API_KEY     Bearer token for agentmail.to (same as DV intake)
    AGENTMAIL_INBOX_ID    "newsweek@agentmail.to"
Optional:
    BETTING_DIGEST_TO     Default: roger.hirano@newsweek.com
    BETTING_DIGEST_CC     Comma-separated list (default: empty)
    BETTING_CPA_TARGET    Default: 150 (USD per FTP)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import sqlalchemy

logger = logging.getLogger(__name__)

AGENTMAIL_BASE = "https://api.agentmail.to/v0"
BETTING_ORDER_ID = "4068491190"
CONTROL_LI_ID    = "7306352098"
DEFAULT_RECIPIENT = "roger.hirano@newsweek.com"
DEFAULT_CPA_TARGET = 150.0

# Broken attribution patterns we surface in the alerts section so they don't
# silently rot. The failed macro test creative leaked a literal '%eaid!%' into
# sub_id_1; once that creative is deactivated we expect this to fall to zero.
BROKEN_SUB_ID_1_PATTERNS = ("%eaid!%", "InitialTest", "(none)", "(blank)")

LI_SUB_ID_2_RE = re.compile(r"^li(\d+)$")


# ----------------------------------------------------------------------
# Data load
# ----------------------------------------------------------------------

def _engine() -> sqlalchemy.Engine:
    return sqlalchemy.create_engine(os.environ["DATABASE_URL"])


def load_window(days: int = 7) -> dict:
    """Pull last `days` days of betting conversions + matching GAM delivery.

    Returns a dict with keys:
      conv:    DataFrame from betting_conversions
      deliv:   DataFrame from gam_campaigns, filtered to BETTING_ORDER_ID
      end:     end date (yesterday in UTC)
      start:   start date (end - days + 1)
    """
    end   = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start = end - timedelta(days=days - 1)

    with _engine().begin() as conn:
        try:
            conv = pd.read_sql(
                sqlalchemy.text(
                    "SELECT date, sub_id_1, sub_id_2, clicks, registrations, ftps, net_cash "
                    "FROM betting_conversions "
                    "WHERE date BETWEEN :s AND :e"
                ),
                conn, params={"s": start, "e": end},
            )
        except sqlalchemy.exc.ProgrammingError:
            # Table doesn't exist yet — first run before any ingest landed.
            logger.warning("betting_conversions table missing; using empty dataframe")
            conv = pd.DataFrame(columns=["date","sub_id_1","sub_id_2","clicks",
                                         "registrations","ftps","net_cash"])

        deliv = pd.read_sql(
            sqlalchemy.text(
                "SELECT report_start AS date, line_item_id, line_item_name, "
                "       ad_server_impressions AS impressions, "
                "       ad_server_clicks AS clicks, "
                "       ad_server_cpm_and_cpc_revenue AS spend "
                "FROM gam_campaigns "
                "WHERE order_id = :oid AND report_start BETWEEN :s AND :e"
            ),
            conn, params={"oid": int(BETTING_ORDER_ID), "s": start, "e": end},
        )

    if not deliv.empty:
        deliv["date"] = pd.to_datetime(deliv["date"]).dt.date
        deliv["line_item_id"] = deliv["line_item_id"].astype(str)
    if not conv.empty:
        conv["date"] = pd.to_datetime(conv["date"]).dt.date

    return {"conv": conv, "deliv": deliv, "start": start, "end": end}


# ----------------------------------------------------------------------
# Digest composition
# ----------------------------------------------------------------------

def _money(x: float) -> str:
    return f"${x:,.2f}"


def _li_from_sub_id_2(s2: str | None) -> str | None:
    """Extract line_item_id from sub_id_2 = 'li<digits>'. Returns None if shape differs."""
    if not s2:
        return None
    m = LI_SUB_ID_2_RE.match(s2.strip())
    return m.group(1) if m else None


def compose_digest(data: dict, cpa_target: float = DEFAULT_CPA_TARGET) -> tuple[str, str]:
    """Return (subject, body) for the digest."""
    conv  = data["conv"]
    deliv = data["deliv"]
    start, end = data["start"], data["end"]

    out = []
    out.append(f"Spinfinite Betting CPA — {start.isoformat()} to {end.isoformat()}  "
               f"(IO1109 / order {BETTING_ORDER_ID})")
    out.append("=" * 78)
    out.append("")

    # ---- Section 1: 7-day totals
    if conv.empty:
        out.append("No Improvado betting report rows found in the last 7 days.")
        out.append("Likely causes: ash@and1.tech hasn't CC'd newsweek@agentmail.to yet,")
        out.append("or the refresh_improvado() job hasn't run since the inbox got a")
        out.append("matching message. Check Actions logs for 'No Improvado betting")
        out.append("reports found in inbox'.")
        out.append("")
    else:
        clicks = int(conv["clicks"].sum())
        regs   = int(conv["registrations"].sum())
        ftps   = int(conv["ftps"].sum())
        cash   = float(conv["net_cash"].sum())
        imps   = int(deliv["impressions"].sum()) if not deliv.empty else 0
        spend  = float(deliv["spend"].sum()) if not deliv.empty else 0.0
        cpa    = (spend / ftps) if ftps else None
        ctr    = (clicks / imps) if imps else None

        out.append("7-DAY TOTALS")
        out.append(f"  Impressions:   {imps:>12,}")
        out.append(f"  Clicks:        {clicks:>12,}   CTR: " +
                   (f"{ctr:.3%}" if ctr is not None else "n/a"))
        out.append(f"  Registrations: {regs:>12,}")
        out.append(f"  FTPs:          {ftps:>12,}")
        out.append(f"  Net Cash:      {_money(cash):>12}")
        out.append(f"  GAM Spend:     {_money(spend):>12}")
        if cpa is not None:
            arrow = "✓" if cpa <= cpa_target else "↑"
            out.append(f"  Blended CPA:   {_money(cpa):>12}   target ${cpa_target:.0f}  {arrow}")
        else:
            out.append(f"  Blended CPA:   n/a (0 FTPs in window)         target ${cpa_target:.0f}")
        out.append("")

    # ---- Section 2: Daily breakdown
    if not conv.empty:
        daily = conv.groupby("date").agg(
            clicks=("clicks","sum"),
            registrations=("registrations","sum"),
            ftps=("ftps","sum"),
            net_cash=("net_cash","sum"),
        ).reset_index().sort_values("date", ascending=False)
        out.append("DAILY BREAKDOWN")
        out.append(f"  {'date':<12}  {'clicks':>7}  {'regs':>5}  {'ftps':>5}  {'net cash':>10}")
        for _, r in daily.iterrows():
            out.append(f"  {r['date'].isoformat():<12}  {int(r['clicks']):>7,}  "
                       f"{int(r['registrations']):>5}  {int(r['ftps']):>5}  "
                       f"{_money(r['net_cash']):>10}")
        out.append("")

    # ---- Section 3: Per-LI (when sub_id_2 is populated)
    if not conv.empty:
        conv2 = conv.copy()
        conv2["line_item_id"] = conv2["sub_id_2"].map(_li_from_sub_id_2)
        per_li = conv2.dropna(subset=["line_item_id"]).groupby("line_item_id").agg(
            clicks=("clicks","sum"),
            registrations=("registrations","sum"),
            ftps=("ftps","sum"),
            net_cash=("net_cash","sum"),
        ).reset_index()
        if not per_li.empty:
            # Join to GAM for impressions + spend + name
            li_summary = deliv.groupby(["line_item_id","line_item_name"]).agg(
                impressions=("impressions","sum"),
                spend=("spend","sum"),
            ).reset_index() if not deliv.empty else pd.DataFrame(columns=["line_item_id","line_item_name","impressions","spend"])
            joined = per_li.merge(li_summary, on="line_item_id", how="left")
            out.append("PER LINE ITEM  (sub_id_2 = li<id>)")
            out.append(f"  {'LI id':<12}  {'imps':>9}  {'clicks':>7}  {'ftps':>5}  "
                       f"{'spend':>10}  {'CPA':>9}  name")
            for _, r in joined.sort_values("ftps", ascending=False).iterrows():
                imps = int(r["impressions"]) if pd.notna(r["impressions"]) else 0
                spd  = float(r["spend"]) if pd.notna(r["spend"]) else 0.0
                cpa  = (spd / r["ftps"]) if r["ftps"] else None
                cpa_str = _money(cpa) if cpa is not None else "n/a"
                name = (r["line_item_name"] or "")[:60]
                out.append(f"  {r['line_item_id']:<12}  {imps:>9,}  {int(r['clicks']):>7,}  "
                           f"{int(r['ftps']):>5}  {_money(spd):>10}  {cpa_str:>9}  {name}")
            out.append("")
        else:
            out.append("PER LINE ITEM: sub_id_2 not yet populated in any row — "
                       "waiting on test LIs / advertiser confirmation.")
            out.append("")

    # ---- Section 4: Per-size (sub_id_1)
    if not conv.empty:
        per_size = conv.groupby("sub_id_1").agg(
            clicks=("clicks","sum"),
            registrations=("registrations","sum"),
            ftps=("ftps","sum"),
            net_cash=("net_cash","sum"),
        ).reset_index().sort_values("clicks", ascending=False)
        out.append("PER CREATIVE SIZE  (sub_id_1)")
        out.append(f"  {'sub_id_1':<22}  {'clicks':>7}  {'regs':>5}  {'ftps':>5}  {'net cash':>10}")
        for _, r in per_size.iterrows():
            out.append(f"  {str(r['sub_id_1'])[:22]:<22}  {int(r['clicks']):>7,}  "
                       f"{int(r['registrations']):>5}  {int(r['ftps']):>5}  "
                       f"{_money(r['net_cash']):>10}")
        out.append("")

    # ---- Section 5: Alerts
    alerts = []
    if not conv.empty:
        broken = conv[conv["sub_id_1"].astype(str).apply(
            lambda s: any(p in s for p in BROKEN_SUB_ID_1_PATTERNS))]
        if not broken.empty:
            broken_clicks = int(broken["clicks"].sum())
            unique_keys = sorted(broken["sub_id_1"].astype(str).unique().tolist())
            alerts.append(f"Broken-attribution rows detected: {broken_clicks} clicks across "
                          f"sub_id_1 values {unique_keys}. Check that the macro-test creative "
                          f"and any other malformed click URLs have been deactivated.")
    if conv.empty or int(conv["ftps"].sum()) < 3:
        alerts.append("Low FTP volume in window — CPA-by-segment numbers below are "
                      "indicative only, not statistically meaningful. Eval windows of "
                      "1-2 weeks per segment are realistic at current click→FTP rates.")
    if alerts:
        out.append("ALERTS")
        for a in alerts:
            out.append(f"  - {a}")
        out.append("")

    out.append(f"Generated by yield-dashboard.betting_daily_update at "
               f"{datetime.now(timezone.utc).isoformat()}.")

    subject = f"Newsweek Betting CPA digest — {end.isoformat()}"
    body = "\n".join(out)
    return subject, body


# ----------------------------------------------------------------------
# Send
# ----------------------------------------------------------------------

def send_via_agentmail(api_key: str, inbox_id: str, to: list[str], cc: list[str],
                        subject: str, text: str) -> dict:
    """Send a plain-text email via agentmail.to. Mirrors apple-news/daily_report.py."""
    payload: dict = {
        "to":      to,
        "subject": subject,
        "text":    text,
    }
    if cc:
        payload["cc"] = cc
    req = urllib.request.Request(
        f"{AGENTMAIL_BASE}/inboxes/{inbox_id}/messages/send",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"agentmail.to send failed: HTTP {e.code} {e.reason} :: "
            f"{e.read().decode(errors='replace')}"
        ) from e


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    dry_run = "--dry-run" in argv

    # Use `or` (not get's default) so an env set to "" — which is what GitHub
    # Actions injects for unset vars — falls back to the literal default
    # instead of float-parsing the empty string.
    target  = float(os.environ.get("BETTING_CPA_TARGET") or DEFAULT_CPA_TARGET)
    to_env  = os.environ.get("BETTING_DIGEST_TO") or DEFAULT_RECIPIENT
    cc_env  = (os.environ.get("BETTING_DIGEST_CC") or "").strip()
    recipients = [a.strip() for a in to_env.split(",") if a.strip()]
    cc         = [a.strip() for a in cc_env.split(",") if a.strip()]

    data = load_window(days=7)
    subject, body = compose_digest(data, cpa_target=target)

    if dry_run:
        print(f"DRY RUN — would send to {recipients} (cc={cc})", file=sys.stderr)
        print(f"Subject: {subject}", file=sys.stderr)
        print("---")
        print(body)
        return 0

    api_key  = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    if not (api_key and inbox_id):
        logger.error("AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID not set — cannot send")
        return 2

    result = send_via_agentmail(api_key, inbox_id, recipients, cc, subject, body)
    logger.info("Sent digest. agentmail id=%s", result.get("id") or result.get("message_id"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
