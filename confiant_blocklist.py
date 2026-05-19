"""
confiant_blocklist — weekly job that takes a Confiant "Alert Log CSV By
Provider" export, picks the Google-served bad creatives, and appends their
landing-page domains to a named GAM Protection's Advertiser URLs blocklist.

Why this is a local-only script rather than a GitHub Actions cron:
GAM does not expose Protections via API. Driving the GAM UI with Playwright
requires a logged-in Google session with 2FA, which can't be done from a
headless CI runner. See docs/confiant_blocklist.md for the full design note.

Typical use:
  python confiant_blocklist.py \\
      --csv ~/Downloads/Alert\\ Log\\ CSV\\ By\\ Provider_20260513_20260519.csv \\
      --protection-name 'Confiant auto-blocklist' \\
      --dry-run

Real run (remove --dry-run, prepare for browser to open):
  python confiant_blocklist.py --csv <path> --protection-name '<name>'

First-time setup (log in to Google, dial in selectors):
  python confiant_blocklist.py --inspect
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import confiant_client
from gam_blocklist_ui import GAMBlocklistBrowser, default_profile_dir


# ── env / state setup ─────────────────────────────────────────────────────────

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
        CREATE TABLE IF NOT EXISTS blocked_domains (
            domain              TEXT PRIMARY KEY,
            issue_type          TEXT NOT NULL,
            first_seen_in_csv   TEXT NOT NULL,
            first_pushed_to_gam TEXT NOT NULL,
            protection_name     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at               TEXT NOT NULL,
            csv_filename         TEXT,
            protection_name      TEXT,
            total_google_rows    INTEGER,
            blockable_domains    INTEGER,
            new_domains_added    INTEGER,
            cloaked_rows_skipped INTEGER,
            dry_run              INTEGER,
            status               TEXT,
            error                TEXT
        );
    """)
    return conn


# ── main flow ─────────────────────────────────────────────────────────────────

@dataclass
class RunSummary:
    csv_path: str
    protection_name: str
    total_google_rows: int
    blockable_domains_in_csv: int
    new_domains: list[tuple[str, str]]  # (domain, issue_type)
    skipped_already_blocked: int
    cloaked_for_review: list[confiant_client.FlaggedRow]
    dry_run: bool
    success: bool
    error: str | None = None


def diff_new_domains(
    conn: sqlite3.Connection,
    blockable: list[tuple[str, confiant_client.FlaggedRow]],
) -> tuple[list[tuple[str, str]], int]:
    """Return (new_domains_with_issue_type, count_already_known).

    new_domains_with_issue_type is unique by domain — if the same domain
    appears under multiple issue types in this CSV, the first wins.
    """
    seen_in_csv: dict[str, str] = {}
    for domain, row in blockable:
        seen_in_csv.setdefault(domain, row.issue_type)

    existing = {
        d for (d,) in conn.execute(
            f"SELECT domain FROM blocked_domains WHERE domain IN ({','.join('?' * len(seen_in_csv))})",
            list(seen_in_csv.keys()),
        )
    } if seen_in_csv else set()

    new = [(d, t) for d, t in seen_in_csv.items() if d not in existing]
    return new, len(seen_in_csv) - len(new)


def push_and_record(
    conn: sqlite3.Connection,
    summary: RunSummary,
    protection_name: str,
    profile_dir: Path,
    headless: bool,
    debug: bool,
    network_id: str,
) -> None:
    """Push new domains via Playwright, then record them in state.

    State is updated only on successful UI save (Playwright raises on save
    failure). The full batch is recorded together so partial pushes don't
    leave state out of sync with GAM.
    """
    browser = GAMBlocklistBrowser(
        profile_dir=profile_dir,
        network_id=network_id,
        headless=headless,
        debug=debug,
    )
    browser.append_to_protection(protection_name, [d for d, _ in summary.new_domains])

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today_date = now[:10]
    conn.executemany(
        """INSERT OR IGNORE INTO blocked_domains
           (domain, issue_type, first_seen_in_csv, first_pushed_to_gam, protection_name)
           VALUES (?, ?, ?, ?, ?)""",
        [(d, t, today_date, now, protection_name) for d, t in summary.new_domains],
    )
    conn.commit()


def record_run(conn: sqlite3.Connection, summary: RunSummary) -> None:
    conn.execute(
        """INSERT INTO runs
           (run_at, csv_filename, protection_name, total_google_rows,
            blockable_domains, new_domains_added, cloaked_rows_skipped,
            dry_run, status, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            Path(summary.csv_path).name,
            summary.protection_name,
            summary.total_google_rows,
            summary.blockable_domains_in_csv,
            len(summary.new_domains),
            len(summary.cloaked_for_review),
            int(summary.dry_run),
            "success" if summary.success else "failed",
            summary.error,
        ),
    )
    conn.commit()


# ── summary email ─────────────────────────────────────────────────────────────

def _build_email_html(summary: RunSummary) -> str:
    title = "Confiant -> GAM blocklist (DRY RUN)" if summary.dry_run \
        else ("Confiant -> GAM blocklist" if summary.success
              else "Confiant -> GAM blocklist (FAILED)")
    color = "#888" if summary.dry_run else ("#27ae60" if summary.success else "#c0392b")

    def _domain_table(rows: list[tuple[str, str]]) -> str:
        if not rows:
            return "<p><em>None — nothing new this week.</em></p>"
        body = "".join(
            f"<tr>"
            f"<td style='padding:4px 12px;border-bottom:1px solid #eee'>{d}</td>"
            f"<td style='padding:4px 12px;border-bottom:1px solid #eee;color:#666'>{t}</td>"
            f"</tr>"
            for d, t in sorted(rows)
        )
        return (
            "<table style='border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;width:100%'>"
            "<thead><tr>"
            "<th style='padding:6px 12px;text-align:left;background:#f0f4f8;border-bottom:2px solid #ccc'>Domain</th>"
            "<th style='padding:6px 12px;text-align:left;background:#f0f4f8;border-bottom:2px solid #ccc'>Issue Type</th>"
            "</tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )

    def _cloaked_table(rows: list[confiant_client.FlaggedRow]) -> str:
        if not rows:
            return "<p><em>No cloaked rows this week.</em></p>"
        by_type: dict[str, list[confiant_client.FlaggedRow]] = {}
        for r in rows:
            by_type.setdefault(r.issue_type, []).append(r)
        chunks = []
        for issue_type, items in sorted(by_type.items()):
            links = "".join(
                f"<li><a href='{r.adtrace_url}'>{r.detail}</a> "
                f"<span style='color:#888'>({r.flagged_impressions:,} imps, last seen {r.last_seen})</span></li>"
                for r in items
            )
            chunks.append(
                f"<h4 style='margin-bottom:4px'>{issue_type} ({len(items)})</h4>"
                f"<ul style='margin-top:0'>{links}</ul>"
            )
        return "".join(chunks)

    error_block = ""
    if summary.error:
        error_block = (
            f"<div style='background:#fee;border:1px solid #c00;padding:12px;"
            f"margin:12px 0;font-family:monospace;font-size:12px'>"
            f"<strong>Error:</strong><br>{summary.error}</div>"
        )

    return f"""
<html><body style='font-family:Arial,sans-serif;color:#333;max-width:900px;margin:auto;padding:20px'>
  <h2 style='color:{color}'>{title}</h2>
  <p style='color:#666'>CSV: <code>{Path(summary.csv_path).name}</code>
     &middot; Protection: <strong>{summary.protection_name}</strong></p>

  {error_block}

  <table style='border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;margin-bottom:20px'>
    <tr><td style='padding:4px 12px'>Google flagged rows in CSV</td>
        <td style='padding:4px 12px;text-align:right'><strong>{summary.total_google_rows}</strong></td></tr>
    <tr><td style='padding:4px 12px'>Resolved to blockable domain</td>
        <td style='padding:4px 12px;text-align:right'><strong>{summary.blockable_domains_in_csv}</strong></td></tr>
    <tr><td style='padding:4px 12px'>Already in GAM Protection (skipped)</td>
        <td style='padding:4px 12px;text-align:right'><strong>{summary.skipped_already_blocked}</strong></td></tr>
    <tr><td style='padding:4px 12px'><strong>New domains {"WOULD BE " if summary.dry_run else ""}pushed to GAM</strong></td>
        <td style='padding:4px 12px;text-align:right'><strong style='color:{color}'>{len(summary.new_domains)}</strong></td></tr>
    <tr><td style='padding:4px 12px'>Cloaked rows for manual review</td>
        <td style='padding:4px 12px;text-align:right'><strong>{len(summary.cloaked_for_review)}</strong></td></tr>
  </table>

  <h3>New domains {"that would be added" if summary.dry_run else "added to GAM Protection"}</h3>
  {_domain_table(summary.new_domains)}

  <h3>Cloaked rows — manual review needed</h3>
  <p style='color:#666'>These Google-served creatives are cloaked, so Confiant can't
     see the destination domain. They can't be auto-blocked via the URL list.
     Open each adtrace to decide on a per-creative block in GAM, or include
     them in your Confiant outreach to Google.</p>
  {_cloaked_table(summary.cloaked_for_review)}

  <hr style='margin-top:30px'>
  <p style='font-size:11px;color:#999'>
    Generated by confiant_blocklist.py &middot;
    State: <code>{_state_path()}</code>
  </p>
</body></html>"""


def _send_email(summary: RunSummary) -> None:
    from agentmail import AgentMail
    from datetime import date

    api_key = os.environ.get("AGENTMAIL_API_KEY")
    inbox_id = os.environ.get("AGENTMAIL_INBOX_ID")
    recipient = os.environ.get("CONFIANT_REPORT_TO_EMAIL") or os.environ.get("REPORT_TO_EMAIL")
    if not (api_key and inbox_id and recipient):
        print("Skipping email — AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID / "
              "CONFIANT_REPORT_TO_EMAIL not all set", file=sys.stderr)
        return

    subject_tag = " (DRY RUN)" if summary.dry_run else (
        "" if summary.success else " (FAILED)"
    )
    AgentMail(api_key=api_key).inboxes.messages.send(
        inbox_id,
        to=recipient,
        subject=f"Confiant -> GAM blocklist{subject_tag} — {date.today().strftime('%b %d, %Y')}",
        html=_build_email_html(summary),
    )
    print(f"Summary email sent to {recipient}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Append Confiant-flagged Google domains to a GAM Protection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--csv", help="Path to Confiant Alert Log CSV. "
                     "If omitted, fetches the latest from the agentmail inbox.")
    src.add_argument("--inspect", action="store_true",
                     help="Open the GAM Protections page in a visible browser "
                          "and wait — used for first-time login and selector "
                          "verification. No CSV processed.")
    p.add_argument("--protection-name",
                   help="Exact name of the existing GAM Protection to append to. "
                        "Required unless --inspect.")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse CSV, diff against state, send summary email, "
                        "but do not launch the browser or modify GAM.")
    p.add_argument("--headless", action="store_true",
                   help="Run Playwright headlessly. NOT recommended — Google "
                        "login may not survive headless mode.")
    p.add_argument("--debug", action="store_true",
                   help="Verbose logging + screenshots saved to the profile dir.")
    p.add_argument("--profile-dir", type=Path, default=None,
                   help="Override the Playwright persistent-profile directory. "
                        "Default: ~/.confiant-blocklist/playwright-profile")
    p.add_argument("--no-email", action="store_true",
                   help="Skip the summary email even if env vars are set.")
    return p.parse_args()


def main() -> int:
    _load_dotenv()
    args = _parse_args()
    profile_dir = args.profile_dir or default_profile_dir()
    network_id = os.environ.get("GAM_NETWORK_ID")

    if args.inspect:
        if not network_id:
            print("GAM_NETWORK_ID env var required for --inspect", file=sys.stderr)
            return 2
        GAMBlocklistBrowser(profile_dir, network_id, headless=False, debug=True).inspect()
        return 0

    if not args.protection_name:
        print("--protection-name is required (or pass --inspect).", file=sys.stderr)
        return 2

    csv_path = args.csv
    if not csv_path:
        save_dir = Path(os.environ.get(
            "CONFIANT_CSV_CACHE_DIR", "~/.confiant-blocklist/csv-cache"
        )).expanduser()
        csv_path = str(confiant_client.fetch_latest_csv_from_inbox(save_dir))
        print(f"Fetched Confiant CSV from inbox -> {csv_path}")

    report = confiant_client.parse_csv(csv_path)
    blockable, cloaked = report.blockable_domains()
    print(f"Google rows: {len(report.google_rows)}  "
          f"blockable={len(blockable)}  cloaked={len(cloaked)}")

    conn = _open_state()
    new_domains, skipped = diff_new_domains(conn, blockable)
    print(f"New domains to push: {len(new_domains)}  "
          f"(already in state: {skipped})")

    summary = RunSummary(
        csv_path=csv_path,
        protection_name=args.protection_name,
        total_google_rows=len(report.google_rows),
        blockable_domains_in_csv=len({d for d, _ in blockable}),
        new_domains=new_domains,
        skipped_already_blocked=skipped,
        cloaked_for_review=cloaked,
        dry_run=args.dry_run,
        success=True,
    )

    if not args.dry_run and not network_id:
        summary.success = False
        summary.error = "GAM_NETWORK_ID env var not set"
    elif not args.dry_run and new_domains:
        try:
            push_and_record(
                conn=conn,
                summary=summary,
                protection_name=args.protection_name,
                profile_dir=profile_dir,
                headless=args.headless,
                debug=args.debug,
                network_id=network_id,
            )
        except Exception as e:
            summary.success = False
            summary.error = f"{type(e).__name__}: {e}"
            print(f"GAM push failed: {summary.error}", file=sys.stderr)

    record_run(conn, summary)
    if not args.no_email:
        _send_email(summary)

    return 0 if summary.success else 1


if __name__ == "__main__":
    sys.exit(main())
