"""Weekly summary of URLs pushed to the GAM Protection blocklist.

Emails RevOps every Monday morning with:
  - The full list of URLs the daily Confiant -> GAM cron pushed during the
    prior 7 days, grouped by issue type.
  - Per-day counts so it's obvious whether activity is flat, spiking, or
    falling off.
  - The cumulative blocklist size for context.

Reads exclusively from the local state.sqlite that confiant_blocklist.py
maintains -- no Confiant API or GAM API calls happen here, so this is
cheap, never rate-limited, and accurate to what actually landed in GAM.

Schedule via launchd: see .launchd/com.newsweek.confiant-blocklist-weekly.plist
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path


# ── paths / config ───────────────────────────────────────────────────────────

def _state_path() -> Path:
    """Same default as confiant_blocklist.py — overridable via env."""
    override = os.environ.get("CONFIANT_BLOCKLIST_STATE")
    if override:
        return Path(override)
    return Path.home() / ".confiant-blocklist" / "state.sqlite"


DEFAULT_TO = "revops@newsweek.com"
GAM_PROTECTION_URL_TMPL = (
    "https://admanager.google.com/{network}#delivery/protections/detail/protection_id={pid}"
)


# ── data load ────────────────────────────────────────────────────────────────

def _load_week(state: Path, days: int) -> tuple[list[sqlite3.Row], int, str | None, int | None]:
    """Pull domains pushed to GAM in the last N days.

    Returns: (rows, cumulative_total, protection_label, protection_id).
    Rows are ordered by (issue_type, domain) for stable rendering.

    Uses datetime() ISO comparison rather than julianday() so the index
    on first_pushed_to_gam (none right now, but small table) doesn't get
    in our way and the predicate matches stored values byte-wise.
    """
    if not state.exists():
        raise FileNotFoundError(
            f"state.sqlite not found at {state} — has the daily cron ever run?"
        )

    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff_utc.isoformat()

    con = sqlite3.connect(state)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT domain, issue_type, first_seen_in_csv,
                   first_pushed_to_gam, protection_id, protection_label
              FROM blocked_domains
             WHERE first_pushed_to_gam >= ?
             ORDER BY issue_type, domain
            """,
            (cutoff_iso,),
        ).fetchall()
        total = con.execute("SELECT COUNT(*) FROM blocked_domains").fetchone()[0]
        # Use the most-recent protection that actually got pushed to — covers
        # the case where someone changed Protection IDs mid-week.
        latest = con.execute(
            """
            SELECT protection_label, protection_id
              FROM blocked_domains
             ORDER BY first_pushed_to_gam DESC
             LIMIT 1
            """
        ).fetchone()
    finally:
        con.close()

    label = latest["protection_label"] if latest else None
    pid   = latest["protection_id"] if latest else None
    return rows, total, label, pid


# ── HTML rendering ───────────────────────────────────────────────────────────

def _group_by_issue(rows: list[sqlite3.Row]) -> "OrderedDict[str, list[sqlite3.Row]]":
    """Group by issue_type, ordered by descending count so the worst category
    leads."""
    buckets: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        buckets.setdefault(r["issue_type"], []).append(r)
    ordered = OrderedDict(
        sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    )
    return ordered


def _group_by_day(rows: list[sqlite3.Row]) -> "OrderedDict[str, int]":
    """Per-day push counts, oldest -> newest."""
    by_day: dict[str, int] = {}
    for r in rows:
        # first_pushed_to_gam is ISO like '2026-06-08T08:29:13+00:00'; take date.
        day = (r["first_pushed_to_gam"] or "")[:10]
        if not day:
            continue
        by_day[day] = by_day.get(day, 0) + 1
    return OrderedDict(sorted(by_day.items()))


def _per_day_html(by_day: "OrderedDict[str, int]") -> str:
    if not by_day:
        return ""
    rows = "".join(
        f"<tr><td style='padding:4px 12px;border-bottom:1px solid #eee'>{escape(d)}</td>"
        f"<td style='padding:4px 12px;border-bottom:1px solid #eee;text-align:right'><strong>{n}</strong></td></tr>"
        for d, n in by_day.items()
    )
    return (
        "<table style='border-collapse:collapse;font-size:13px;margin:10px 0 20px 0'>"
        "<thead><tr><th style='padding:6px 12px;text-align:left;border-bottom:2px solid #1a1a1a'>Day</th>"
        "<th style='padding:6px 12px;text-align:right;border-bottom:2px solid #1a1a1a'>New URLs</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _per_issue_section(issue: str, rows: list[sqlite3.Row]) -> str:
    """One <h3> per issue type plus a tight 2-column table of the domains."""
    items = "".join(
        f"<tr>"
        f"<td style='padding:3px 10px;border-bottom:1px solid #f1f1f1;font-family:Menlo,Consolas,monospace;font-size:12px;color:#1a1a1a'>"
        f"{escape(r['domain'])}</td>"
        f"<td style='padding:3px 10px;border-bottom:1px solid #f1f1f1;font-size:12px;color:#666'>"
        f"{escape((r['first_pushed_to_gam'] or '')[:10])}</td>"
        f"</tr>"
        for r in rows
    )
    return (
        f"<h3 style='margin:24px 0 6px 0;color:#1a1a1a;font-size:14px'>"
        f"{escape(issue)} <span style='color:#888;font-weight:normal'>({len(rows)})</span></h3>"
        f"<table style='border-collapse:collapse;width:100%;max-width:680px'>"
        f"<thead><tr>"
        f"<th style='padding:5px 10px;text-align:left;border-bottom:1px solid #1a1a1a;font-size:12px'>Domain</th>"
        f"<th style='padding:5px 10px;text-align:left;border-bottom:1px solid #1a1a1a;font-size:12px'>Pushed</th>"
        f"</tr></thead>"
        f"<tbody>{items}</tbody></table>"
    )


def build_html(
    rows: list[sqlite3.Row],
    cumulative_total: int,
    days: int,
    protection_label: str | None,
    protection_id: int | None,
    gam_network_id: str | None,
) -> str:
    today = date.today()
    start = today - timedelta(days=days)
    new_count = len(rows)

    if new_count == 0:
        body_html = (
            "<p>No new URLs were added to the GAM Protection blocklist this week. "
            "Either the daily cron didn't find new Google-flagged Security domains, "
            "or every flagged domain was already on the blocklist.</p>"
        )
    else:
        by_issue = _group_by_issue(rows)
        by_day = _group_by_day(rows)
        sections = "".join(_per_issue_section(it, rs) for it, rs in by_issue.items())
        per_day = _per_day_html(by_day)
        body_html = (
            f"<p>The daily Confiant&nbsp;&rarr;&nbsp;GAM cron pushed "
            f"<strong>{new_count} new URL{'s' if new_count != 1 else ''}</strong> "
            f"to the GAM Protection blocklist this week "
            f"(<strong>{start.isoformat()}</strong> through "
            f"<strong>{today.isoformat()}</strong>).</p>"
            f"<h2 style='margin:24px 0 6px 0;font-size:15px;color:#1a1a1a'>Per-day activity</h2>"
            f"{per_day}"
            f"<h2 style='margin:24px 0 0 0;font-size:15px;color:#1a1a1a'>URLs by issue type</h2>"
            f"{sections}"
        )

    protection_html = ""
    if protection_label and protection_id:
        if gam_network_id:
            url = GAM_PROTECTION_URL_TMPL.format(network=gam_network_id, pid=protection_id)
            protection_html = (
                f"GAM Protection: <a href='{escape(url)}' style='color:#1a73e8'>"
                f"<strong>{escape(protection_label)}</strong> "
                f"(id <code>{protection_id}</code>)</a>"
            )
        else:
            protection_html = (
                f"GAM Protection: <strong>{escape(protection_label)}</strong> "
                f"(id <code>{protection_id}</code>)"
            )

    return f"""\
<html><body style='font-family:Arial,sans-serif;color:#222;max-width:780px;margin:auto;padding:20px;font-size:14px;line-height:1.55'>
  <h1 style='margin:0 0 8px 0;color:#1a1a1a;font-size:18px'>
    GAM blocklist — weekly summary
  </h1>
  <p style='margin:0 0 16px 0;color:#666;font-size:12px'>
    {escape(start.isoformat())} &rarr; {escape(today.isoformat())} &middot;
    {protection_html or "GAM Protection: (unknown)"} &middot;
    cumulative blocklist size: <strong>{cumulative_total:,}</strong>
  </p>

  {body_html}

  <hr style='margin-top:30px;border:none;border-top:1px solid #e1e1e1'>
  <p style='font-size:11px;color:#999;margin-top:14px'>
    Generated by <code>confiant_blocklist_weekly_report.py</code> on
    {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}.
    Source of truth: local <code>state.sqlite</code> (the cron writes one row
    per domain on its first push to GAM).
  </p>
</body></html>"""


# ── send ─────────────────────────────────────────────────────────────────────

def _send(html: str, subject: str, to: list[str], cc: list[str]) -> None:
    """Send via agentmail using the same SDK call confiant_blocklist.py uses
    for its post-run summary."""
    from agentmail import AgentMail  # local import — heavy dep

    api_key = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    if not (api_key and inbox_id):
        raise RuntimeError(
            "AGENTMAIL_API_KEY and AGENTMAIL_INBOX_ID must be set "
            "(both are already required by the daily blocklist cron)."
        )
    kwargs: dict = {"to": to, "subject": subject, "html": html}
    if cc:
        kwargs["cc"] = cc
    AgentMail(api_key=api_key).inboxes.messages.send(inbox_id, **kwargs)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Weekly summary of URLs pushed to the GAM Protection blocklist.",
    )
    p.add_argument("--days", type=int, default=7,
                   help="Lookback window in days (default 7).")
    p.add_argument(
        "--to",
        default=os.environ.get("CONFIANT_BLOCKLIST_WEEKLY_TO") or DEFAULT_TO,
        help=f"Primary recipient. Default: env CONFIANT_BLOCKLIST_WEEKLY_TO "
             f"or {DEFAULT_TO}.",
    )
    p.add_argument(
        "--cc",
        default=os.environ.get("CONFIANT_BLOCKLIST_WEEKLY_CC", ""),
        help="Comma-separated CC list. Default: env CONFIANT_BLOCKLIST_WEEKLY_CC.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Render the email + print a summary, don't send.")
    p.add_argument("--print-html", action="store_true",
                   help="Print the rendered HTML to stdout (implies --dry-run).")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    if args.print_html:
        args.dry_run = True

    state = _state_path()
    rows, total, label, pid = _load_week(state, args.days)
    gam_network_id = os.environ.get("GAM_NETWORK_ID")
    html = build_html(rows, total, args.days, label, pid, gam_network_id)
    today = date.today()
    subject = (
        f"GAM blocklist weekly — {len(rows)} new URL"
        f"{'s' if len(rows) != 1 else ''} added (week ending {today.isoformat()})"
    )

    if args.print_html:
        print(html)
        return 0

    to_list = [args.to.strip()] if args.to else []
    cc_list = [c.strip() for c in (args.cc or "").split(",") if c.strip()]
    if not to_list:
        print("ERROR: no --to recipient and no env default", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[dry-run] would send")
        print(f"  to:      {', '.join(to_list)}")
        print(f"  cc:      {', '.join(cc_list) if cc_list else '(none)'}")
        print(f"  subject: {subject}")
        print(f"  body:    {len(html):,} bytes of HTML")
        print(f"  rows:    {len(rows)} new, {total:,} cumulative")
        return 0

    _send(html, subject, to_list, cc_list)
    print(f"Sent: {subject}")
    print(f"  to: {', '.join(to_list)}")
    if cc_list:
        print(f"  cc: {', '.join(cc_list)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
