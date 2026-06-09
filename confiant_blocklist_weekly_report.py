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


def _load_dotenv() -> None:
    """Read ~/code/yield-dashboard/.env so AGENTMAIL_API_KEY +
    AGENTMAIL_INBOX_ID don't have to be hardcoded in the launchd plist.
    Mirrors confiant_blocklist._load_dotenv so .env is the single source
    of truth for blocklist credentials. Uses setdefault so env vars
    passed explicitly (e.g. by a wrapper) take precedence."""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ── paths / config ───────────────────────────────────────────────────────────

def _state_path() -> Path:
    """Same default as confiant_blocklist.py — overridable via env."""
    override = os.environ.get("CONFIANT_BLOCKLIST_STATE")
    if override:
        return Path(override)
    return Path.home() / ".confiant-blocklist" / "state.sqlite"


DEFAULT_TO = "confiant-alerts@newsweek.com"
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
#
# Email-client-safe rules of the road:
#   - Inline CSS only; no <style> tags (Gmail web strips them).
#   - Table-based layout for KPI tiles + horizontal layout (Outlook 2016+
#     ignores flexbox/grid; tables are the lowest common denominator).
#   - Web-safe fonts only — Arial / Helvetica / monospace fallback.
#   - No external assets (no <img>, no <link>) so the email renders the same
#     in dark mode, in clipped previews, and offline.
#
# Brand palette (sampled from dashboard.py + Newsweek's identity):
#   #d72638 — Newsweek red, used for the brand strip + KPI accent
#   #1a1a1a — primary text
#   #6b7280 — secondary text / metadata
#   #f0f4f8 — KPI tile + table-header backgrounds
#   #e1e5eb — divider lines

_NW_RED       = "#d72638"
_NW_DARK      = "#1a1a1a"
_NW_TEXT      = "#222222"
_NW_MUTED     = "#6b7280"
_NW_BG_SOFT   = "#f0f4f8"
_NW_BG_LIGHT  = "#f9fafb"
_NW_BORDER    = "#e1e5eb"
_NW_LINK      = "#1a73e8"


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


def _day_label(iso_day: str) -> str:
    """`2026-06-08` -> `Mon 06-08`. Cheap, no extra deps."""
    try:
        d = datetime.strptime(iso_day, "%Y-%m-%d").date()
        return f"{d.strftime('%a')} {d.strftime('%m-%d')}"
    except Exception:
        return iso_day


def _kpi_tile(label: str, value: str, accent_color: str = _NW_DARK) -> str:
    """Single KPI box. Rendered as a <td> so the strip can be a one-row table
    (Outlook-safe alternative to flexbox)."""
    return (
        f"<td valign='top' "
        f"style='background:{_NW_BG_SOFT};border:1px solid {_NW_BORDER};"
        f"padding:14px 16px;border-radius:6px;width:25%'>"
        f"<div style='font-size:11px;color:{_NW_MUTED};text-transform:uppercase;"
        f"letter-spacing:0.5px;font-weight:600;margin-bottom:6px'>{escape(label)}</div>"
        f"<div style='font-size:24px;color:{accent_color};font-weight:700;line-height:1.1'>"
        f"{escape(value)}</div>"
        f"</td>"
    )


def _kpi_strip(new_count: int, cumulative: int, issue_count: int,
               top_issue: str | None) -> str:
    """Header KPI strip: 4 tiles side by side using table layout for
    cross-client width consistency."""
    tiles = [
        _kpi_tile("This week", f"{new_count:,}", _NW_RED if new_count else _NW_DARK),
        _kpi_tile("Cumulative", f"{cumulative:,}"),
        _kpi_tile("Issue types", f"{issue_count}"),
        _kpi_tile("Top issue", top_issue or "—"),
    ]
    # 6px gap between tiles via 6px padding between cells; outer table has 0 spacing.
    spacer = "<td style='width:8px;font-size:0;line-height:0'>&nbsp;</td>"
    inner = spacer.join(tiles)
    return (
        f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        f"style='border-collapse:separate;width:100%;margin:18px 0 24px 0'>"
        f"<tr>{inner}</tr></table>"
    )


def _bar_row(day: str, count: int, max_count: int) -> str:
    """One row of the inline bar chart. Bar width is a percentage of the row
    width; rendered with two divs inside a fixed-width <td>."""
    pct = max(2, int(round(100 * count / max_count))) if max_count else 0
    return (
        f"<tr>"
        # Day label column
        f"<td style='padding:5px 12px 5px 0;font-size:12px;color:{_NW_MUTED};"
        f"font-family:Menlo,Consolas,monospace;white-space:nowrap;width:90px'>"
        f"{escape(_day_label(day))}</td>"
        # Bar column (background = track, inner div = filled portion)
        f"<td style='padding:5px 0'>"
        f"<div style='background:{_NW_BG_SOFT};border-radius:3px;height:14px;width:100%'>"
        f"<div style='background:{_NW_RED};height:14px;width:{pct}%;"
        f"border-radius:3px'></div>"
        f"</div>"
        f"</td>"
        # Count column
        f"<td style='padding:5px 0 5px 12px;font-size:13px;color:{_NW_DARK};"
        f"font-weight:600;text-align:right;width:50px'>{count}</td>"
        f"</tr>"
    )


def _per_day_chart(by_day: "OrderedDict[str, int]") -> str:
    """Horizontal bar chart of the per-day pushes. Inline-CSS only, no SVG,
    no images — renders identically in Outlook/Gmail/Apple Mail."""
    if not by_day:
        return ""
    max_count = max(by_day.values())
    bars = "".join(_bar_row(d, n, max_count) for d, n in by_day.items())
    return (
        f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        f"style='border-collapse:collapse;width:100%;margin:6px 0 20px 0'>"
        f"<tbody>{bars}</tbody></table>"
    )


def _per_issue_section(issue: str, rows: list[sqlite3.Row]) -> str:
    """One issue-type card: badge with count, plus a tight 2-column table
    of the domains pushed."""
    domain_rows = "".join(
        f"<tr>"
        f"<td style='padding:6px 12px;border-bottom:1px solid {_NW_BORDER};"
        f"font-family:Menlo,Consolas,monospace;font-size:12px;color:{_NW_DARK};"
        f"word-break:break-all'>{escape(r['domain'])}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid {_NW_BORDER};"
        f"font-size:12px;color:{_NW_MUTED};white-space:nowrap;text-align:right'>"
        f"{escape((r['first_pushed_to_gam'] or '')[:10])}</td>"
        f"</tr>"
        for r in rows
    )
    return (
        # Outer card with subtle border
        f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        f"style='border-collapse:collapse;width:100%;margin:0 0 18px 0;"
        f"border:1px solid {_NW_BORDER};border-radius:6px;background:#ffffff;"
        f"overflow:hidden'>"
        # Header band — issue name + count badge
        f"<tr><td style='background:{_NW_BG_LIGHT};padding:10px 14px;"
        f"border-bottom:1px solid {_NW_BORDER}'>"
        f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        f"style='width:100%'><tr>"
        f"<td style='font-size:14px;font-weight:600;color:{_NW_DARK}'>"
        f"{escape(issue)}</td>"
        f"<td style='text-align:right;font-size:12px;font-weight:700;"
        f"color:#ffffff;background:{_NW_RED};padding:3px 10px;border-radius:10px;"
        f"width:1px;white-space:nowrap'>{len(rows)} URLs</td>"
        f"</tr></table>"
        f"</td></tr>"
        # Domain table
        f"<tr><td style='padding:0'>"
        f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        f"style='border-collapse:collapse;width:100%'>"
        f"<tbody>{domain_rows}</tbody></table>"
        f"</td></tr>"
        f"</table>"
    )


def _hero_header(start: date, today: date, days: int) -> str:
    """Brand-colored top band + page title + subtitle. Renders as two stacked
    table rows so the 4px red band stays full-bleed."""
    return (
        f"<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        f"style='border-collapse:collapse;width:100%'>"
        # Brand color band
        f"<tr><td style='background:{_NW_RED};height:4px;font-size:0;line-height:0'>&nbsp;</td></tr>"
        # Eyebrow
        f"<tr><td style='padding:18px 24px 0 24px'>"
        f"<div style='font-size:11px;font-weight:700;color:{_NW_RED};"
        f"text-transform:uppercase;letter-spacing:1.2px'>"
        f"Newsweek &middot; Brand Safety</div>"
        # Title
        f"<h1 style='margin:6px 0 4px 0;color:{_NW_DARK};font-size:22px;"
        f"font-weight:700;line-height:1.2'>GAM Blocklist — Weekly Summary</h1>"
        # Subtitle
        f"<div style='color:{_NW_MUTED};font-size:13px'>"
        f"Week ending {today.strftime('%B %-d, %Y')} "
        f"&middot; {days}-day window ({start.isoformat()} → {today.isoformat()})"
        f"</div>"
        f"</td></tr></table>"
    )


def _footer(protection_label: str | None, protection_id: int | None,
            gam_network_id: str | None) -> str:
    """CTA button (link to GAM Protection when network id is known) + generated
    metadata line."""
    cta_html = ""
    if protection_label and protection_id and gam_network_id:
        url = GAM_PROTECTION_URL_TMPL.format(network=gam_network_id, pid=protection_id)
        cta_html = (
            f"<p style='margin:0 0 18px 0'>"
            f"<a href='{escape(url)}' "
            f"style='display:inline-block;background:{_NW_DARK};color:#ffffff;"
            f"padding:10px 18px;border-radius:4px;text-decoration:none;"
            f"font-size:13px;font-weight:600'>"
            f"View Protection &ldquo;{escape(protection_label)}&rdquo; in GAM &rarr;"
            f"</a></p>"
        )
    elif protection_label and protection_id:
        cta_html = (
            f"<p style='margin:0 0 18px 0;color:{_NW_MUTED};font-size:12px'>"
            f"GAM Protection: <strong>{escape(protection_label)}</strong> "
            f"(id <code>{protection_id}</code>)</p>"
        )
    return (
        f"{cta_html}"
        f"<hr style='border:none;border-top:1px solid {_NW_BORDER};margin:8px 0 14px 0'>"
        f"<p style='font-size:11px;color:#9aa0a6;margin:0;line-height:1.5'>"
        f"Generated by <code>confiant_blocklist_weekly_report.py</code> on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M %Z').strip()}. "
        f"Source of truth: the daily cron's local state DB "
        f"(<code>~/.confiant-blocklist/state.sqlite</code>). "
        f"Each row is recorded the first time its domain is pushed to GAM."
        f"</p>"
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
    by_issue = _group_by_issue(rows)
    by_day = _group_by_day(rows)
    top_issue = next(iter(by_issue), None) if by_issue else None

    kpi_strip = _kpi_strip(new_count, cumulative_total, len(by_issue), top_issue)

    if new_count == 0:
        body_html = (
            f"<div style='background:{_NW_BG_LIGHT};border:1px solid {_NW_BORDER};"
            f"border-left:4px solid #22c55e;padding:14px 18px;border-radius:4px;"
            f"margin:0 0 18px 0;color:{_NW_DARK};font-size:13px'>"
            f"<strong>No new URLs blocked this week.</strong> "
            f"Either the daily cron didn't find new Google-flagged Security "
            f"domains, or everything flagged was already on the blocklist."
            f"</div>"
        )
    else:
        per_day_chart = _per_day_chart(by_day)
        issue_cards = "".join(
            _per_issue_section(it, rs) for it, rs in by_issue.items()
        )
        body_html = (
            f"<h2 style='margin:18px 0 4px 0;color:{_NW_DARK};font-size:15px;"
            f"font-weight:700'>Activity by day</h2>"
            f"<p style='margin:0 0 6px 0;color:{_NW_MUTED};font-size:12px'>"
            f"New URLs pushed to GAM each day in the window.</p>"
            f"{per_day_chart}"
            f"<h2 style='margin:26px 0 4px 0;color:{_NW_DARK};font-size:15px;"
            f"font-weight:700'>URLs by issue type</h2>"
            f"<p style='margin:0 0 14px 0;color:{_NW_MUTED};font-size:12px'>"
            f"Confiant Security category; ordered by count.</p>"
            f"{issue_cards}"
        )

    return f"""\
<html><body style='margin:0;padding:0;background:#f5f6f8;color:{_NW_TEXT};font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55'>
<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='background:#f5f6f8;width:100%;padding:20px 0'>
<tr><td align='center'>
<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='background:#ffffff;width:100%;max-width:760px;border:1px solid {_NW_BORDER};border-radius:8px;overflow:hidden'>
  <tr><td>{_hero_header(start, today, days)}</td></tr>
  <tr><td style='padding:0 24px'>{kpi_strip}</td></tr>
  <tr><td style='padding:0 24px 8px 24px'>{body_html}</td></tr>
  <tr><td style='padding:18px 24px 24px 24px;background:{_NW_BG_LIGHT};border-top:1px solid {_NW_BORDER}'>
    {_footer(protection_label, protection_id, gam_network_id)}
  </td></tr>
</table>
</td></tr></table>
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
    _load_dotenv()
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
