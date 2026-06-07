"""
confiant_outreach — weekly job that pulls Confiant's flagged-ad data via
their REST API, groups creatives by SSP, and emails each SSP's policy team
asking them to block at source.

This is the SSP-side companion to confiant_blocklist.py:
  * confiant_blocklist  -> blocks domains in OUR GAM Protection (publisher side)
  * confiant_outreach   -> emails SSPs asking THEM to block at source

The outreach approach is what Confiant's weekly "SSP Outreach" docx is meant
for — instead of manually copy-pasting from the docx into Outlook 10x per
week, this automates it. SSP-side blocking is way more powerful than
publisher-side blocking because the SSP's policy enforcement applies to
every publisher in their inventory, not just ours.

Typical use (dry-run first to preview):
  python confiant_outreach.py --dry-run

Real run (sends to SSP contacts in settings.json):
  python confiant_outreach.py

Test for one SSP only:
  python confiant_outreach.py --provider Xandr --dry-run

Required env vars:
  CONFIANT_API_KEY                  — Confiant API token
  AGENTMAIL_API_KEY                 — agentmail.to credentials
  AGENTMAIL_INBOX_ID                — the inbox to send from

Required config in settings.json:
  confiant_outreach.ssp_contacts    — { "SSP name": "email@addr" } map
  confiant_outreach.publisher_name  — your publisher name (e.g. "Newsweek")
  confiant_outreach.cc_emails       — list of emails to CC (e.g. yourself)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import confiant_client


# ── env / state ───────────────────────────────────────────────────────────────

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


def _state_path() -> Path:
    return Path(os.environ.get(
        "CONFIANT_BLOCKLIST_STATE",
        "~/.confiant-blocklist/state.sqlite",
    )).expanduser()


def _open_state() -> sqlite3.Connection:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS outreach_runs (
            run_id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at                     TEXT NOT NULL,
            days_window                INTEGER,
            providers_with_flags       INTEGER,
            emails_sent                INTEGER,
            emails_skipped_no_contact  INTEGER,
            emails_skipped_no_flags    INTEGER,
            dry_run                    INTEGER,
            status                     TEXT,
            error                      TEXT
        );
        CREATE TABLE IF NOT EXISTS outreach_emails (
            email_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id             INTEGER NOT NULL,
            provider           TEXT NOT NULL,
            recipient          TEXT NOT NULL,
            sent_at            TEXT NOT NULL,
            flagged_creatives  INTEGER,
            subject            TEXT,
            dry_run            INTEGER,
            FOREIGN KEY (run_id) REFERENCES outreach_runs(run_id)
        );
    """)
    return conn


# ── config loading ────────────────────────────────────────────────────────────

@dataclass
class OutreachConfig:
    ssp_contacts: dict[str, str] = field(default_factory=dict)
    publisher_name: str = "Newsweek"
    cc_emails: list[str] = field(default_factory=list)
    min_flagged_impressions: int = 1


def _load_settings(repo_root: Path) -> OutreachConfig:
    settings_path = repo_root / "settings.json"
    if not settings_path.exists():
        raise FileNotFoundError(
            f"settings.json not found at {settings_path}; "
            "expected confiant_outreach.ssp_contacts here."
        )
    data = json.loads(settings_path.read_text())
    outreach = data.get("confiant_outreach", {})
    return OutreachConfig(
        ssp_contacts=outreach.get("ssp_contacts", {}),
        publisher_name=outreach.get("publisher_name", "Newsweek"),
        cc_emails=outreach.get("cc_emails", []),
        min_flagged_impressions=int(outreach.get("min_flagged_impressions_for_outreach", 1)),
    )


# ── core data shape: SSP-grouped creatives ───────────────────────────────────

@dataclass
class ProviderFlags:
    provider: str
    rows: list[confiant_client.FlaggedRow]

    @property
    def total_impressions(self) -> int:
        return sum(r.flagged_impressions for r in self.rows)

    @property
    def issue_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for r in self.rows:
            counts[r.issue_type] += 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def rows_by_issue_type(self) -> dict[str, list[confiant_client.FlaggedRow]]:
        bucket: dict[str, list[confiant_client.FlaggedRow]] = defaultdict(list)
        for r in self.rows:
            bucket[r.issue_type].append(r)
        # Sort each bucket by impressions desc.
        return {
            it: sorted(rs, key=lambda r: -r.flagged_impressions)
            for it, rs in sorted(bucket.items(), key=lambda kv: -sum(r.flagged_impressions for r in kv[1]))
        }


def group_by_provider(
    report: confiant_client.ConfiantReport,
    min_impressions: int,
) -> dict[str, ProviderFlags]:
    """Group all flagged rows by Provider. Each Provider gets one bucket.

    Filters out:
      * Rows with < min_impressions (noise)
      * Confiant-internal Provider="" rows (malformed)
    """
    buckets: dict[str, list[confiant_client.FlaggedRow]] = defaultdict(list)
    for r in report.rows:
        if not r.provider:
            continue
        if r.flagged_impressions < min_impressions:
            continue
        buckets[r.provider].append(r)
    return {p: ProviderFlags(provider=p, rows=rs) for p, rs in buckets.items()}


# ── email templates ──────────────────────────────────────────────────────────

def render_email_html(
    pf: ProviderFlags,
    publisher_name: str,
    window_days: int,
) -> tuple[str, str]:
    """Returns (subject, html_body) for one SSP outreach email."""
    creative_count = len(pf.rows)
    window_label = f"past {window_days} days"
    # Subject format: `<SSP>//<Publisher> — N flagged creatives on <publisher>.com (past 7 days)`
    # Leading with SSP name puts the most-relevant identifier for the recipient
    # first in their inbox preview. publisher_name + ".com" makes it obvious
    # which property is affected.
    subject = (
        f"{pf.provider}//{publisher_name} — {creative_count} flagged "
        f"creative{'s' if creative_count != 1 else ''} on "
        f"{publisher_name.lower()}.com ({window_label})"
    )

    issue_type_summary = ", ".join(
        f"{it} ({n})" for it, n in pf.issue_type_counts.items()
    )

    rows_html_by_type = ""
    for issue_type, rows in pf.rows_by_issue_type().items():
        rows_html_by_type += (
            f"<h3 style='margin:18px 0 6px 0;color:#1a1a1a;font-size:14px'>"
            f"{issue_type} ({len(rows)})</h3>"
            "<ul style='margin:0;padding-left:20px;font-size:13px;line-height:1.5'>"
        )
        for r in rows:
            rows_html_by_type += (
                f"<li><a href='{r.adtrace_url}' style='color:#1a73e8'>"
                f"{r.adtrace_url.rsplit('/', 1)[-1][:16]}…</a> "
                f"&mdash; <strong>{r.detail}</strong> "
                f"<span style='color:#666'>"
                f"({r.flagged_impressions:,} imps, last seen {r.last_seen})"
                f"</span></li>"
            )
        rows_html_by_type += "</ul>"

    html = f"""
<html><body style='font-family:Arial,sans-serif;color:#222;max-width:760px;margin:auto;padding:20px;font-size:14px;line-height:1.55'>
  <p>Hi {pf.provider} team,</p>

  <p>Confiant detected <strong>{creative_count} unique creative{'s' if creative_count != 1 else ''}</strong>
     from {pf.provider} demand on <strong>{publisher_name}</strong>.com over the {window_label}
     that violated ad-quality policy ({issue_type_summary}). Total flagged
     impressions: <strong>{pf.total_impressions:,}</strong>.</p>

  <p>Since we run Confiant in real-time on our end, we captured the ad traces below
     so your trust &amp; safety / policy team can investigate and block this
     activity at source &mdash; preventing it from running on any publisher
     in your inventory, not just ours.</p>

  <p>Traces are grouped by issue type, ordered by total impressions:</p>

  {rows_html_by_type}

  <hr style='margin-top:30px;border:0;border-top:1px solid #eee'>
  <p style='font-size:12px;color:#888'>
    This message was sent automatically based on Confiant's
    <code>issue_type_by_domain</code> API report. Reply to this thread or contact
    your {publisher_name} account team with questions.
  </p>
</body></html>"""
    return subject, html


# ── send via agentmail ───────────────────────────────────────────────────────

def _split_recipients(s: str) -> list[str]:
    """Split a comma- or semicolon-separated recipient string into a list of
    clean email addresses. Handles values stored in settings.json like
    'a@x.com, b@y.com' (which represents multiple SSP contacts that should
    all be on the same outreach email).
    """
    import re
    parts = re.split(r"[,;]+", s)
    return [p.strip() for p in parts if p.strip()]


def _send_email(
    subject: str,
    html: str,
    recipient: str,
    cc: list[str] | None = None,
) -> None:
    from agentmail import AgentMail

    api_key = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    if not (api_key and inbox_id):
        raise RuntimeError(
            "AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID required for sending. "
            "Set them in .env or the launchd plist."
        )

    # Some SSPs have multiple contacts on file — settings.json stores them as
    # 'a@x.com, b@x.com'. agentmail's SDK accepts Union[str, List[str]] for
    # `to`, so we always pass the split list (a single recipient becomes a
    # one-element list, which the SDK handles identically to a bare string).
    to_list = _split_recipients(recipient)

    client = AgentMail(api_key=api_key)
    payload: dict = {"to": to_list, "subject": subject, "html": html}
    if cc:
        payload["cc"] = cc
    client.inboxes.messages.send(inbox_id, **payload)


# ── orchestrator ─────────────────────────────────────────────────────────────

@dataclass
class OutreachRunSummary:
    run_id: int | None = None
    days_window: int = 0
    providers_with_flags: int = 0
    emails_sent: int = 0
    emails_skipped_no_contact: int = 0
    emails_skipped_no_flags: int = 0
    dry_run: bool = False
    sent_details: list[tuple[str, str, int]] = field(default_factory=list)  # (provider, recipient, n_creatives)
    skipped_no_contact: list[tuple[str, int]] = field(default_factory=list)
    error: str | None = None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Email each SSP a list of their Confiant-flagged creatives.",
    )
    p.add_argument("--days", type=int, default=7,
                   help="Lookback window in days (default 7).")
    p.add_argument("--provider", default=None,
                   help="Send only for this Provider (case-sensitive match "
                        "against the Confiant Provider column). Useful for "
                        "testing one SSP in isolation.")
    p.add_argument("--dry-run", action="store_true",
                   help="Pull data, render emails, print summary, but do not "
                        "send anything.")
    p.add_argument("--csv", default=None,
                   help="Use a local CSV instead of the Confiant API (debugging).")
    return p.parse_args()


def main() -> int:
    _load_dotenv()
    args = _parse_args()

    repo_root = Path(__file__).parent
    cfg = _load_settings(repo_root)
    if not cfg.ssp_contacts:
        print("settings.json has no confiant_outreach.ssp_contacts map; "
              "nothing to send. Add SSP contacts and re-run.", file=sys.stderr)
        return 2

    # Pull data
    if args.csv:
        report = confiant_client.parse_csv(args.csv)
        print(f"Loaded Confiant CSV from {args.csv}")
    else:
        report = confiant_client.fetch_via_api(days=args.days)
        print(f"Pulled Confiant API report ({len(report.rows)} rows, "
              f"source={report.source})")

    by_provider = group_by_provider(report, cfg.min_flagged_impressions)
    print(f"Providers with flagged creatives (>= "
          f"{cfg.min_flagged_impressions} imps): {len(by_provider)}")

    if args.provider:
        if args.provider not in by_provider:
            print(f"No flags for provider {args.provider!r} in the window "
                  f"(known: {sorted(by_provider)})", file=sys.stderr)
            return 1
        by_provider = {args.provider: by_provider[args.provider]}

    summary = OutreachRunSummary(
        days_window=args.days,
        providers_with_flags=len(by_provider),
        dry_run=args.dry_run,
    )

    for provider, pf in sorted(by_provider.items(), key=lambda kv: -len(kv[1].rows)):
        recipient = cfg.ssp_contacts.get(provider)
        if not recipient:
            summary.emails_skipped_no_contact += 1
            summary.skipped_no_contact.append((provider, len(pf.rows)))
            print(f"  [skip] {provider}: {len(pf.rows)} creatives flagged "
                  f"but no contact in settings.json")
            continue

        subject, html = render_email_html(pf, cfg.publisher_name, args.days)

        if args.dry_run:
            print(f"  [dry-run] {provider} -> {recipient} "
                  f"({len(pf.rows)} creatives, {pf.total_impressions:,} imps) "
                  f"subj={subject!r}")
        else:
            try:
                _send_email(subject, html, recipient, cc=cfg.cc_emails or None)
                print(f"  [sent] {provider} -> {recipient} "
                      f"({len(pf.rows)} creatives)")
            except Exception as e:
                summary.error = f"{provider}: {type(e).__name__}: {e}"
                print(f"  [error] {provider} -> {recipient} failed: {e}",
                      file=sys.stderr)
                continue

        summary.emails_sent += 1
        summary.sent_details.append((provider, recipient, len(pf.rows)))

    conn = _open_state()
    cur = conn.execute(
        """INSERT INTO outreach_runs
           (run_at, days_window, providers_with_flags, emails_sent,
            emails_skipped_no_contact, emails_skipped_no_flags,
            dry_run, status, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            summary.days_window,
            summary.providers_with_flags,
            summary.emails_sent,
            summary.emails_skipped_no_contact,
            summary.emails_skipped_no_flags,
            int(summary.dry_run),
            "success" if not summary.error else "failed",
            summary.error,
        ),
    )
    summary.run_id = cur.lastrowid
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for provider, recipient, n in summary.sent_details:
        conn.execute(
            """INSERT INTO outreach_emails
               (run_id, provider, recipient, sent_at, flagged_creatives,
                subject, dry_run)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (summary.run_id, provider, recipient, now, n,
             f"flagged creatives on {provider}", int(summary.dry_run)),
        )
    conn.commit()

    print()
    print(f"Summary: {summary.emails_sent} emails "
          f"{'would be sent' if args.dry_run else 'sent'}, "
          f"{summary.emails_skipped_no_contact} skipped (no contact). "
          f"Run #{summary.run_id} recorded.")

    return 0 if not summary.error else 1


if __name__ == "__main__":
    sys.exit(main())
