"""
confiant_blocklist — weekly job that pulls Confiant's flagged-ad data via
their REST API, picks the Google-served Security-category creatives, and
appends their landing-page domains to a GAM Protection's Advertiser URLs
blocklist.

Why this is a local-only script rather than a GitHub Actions cron:
GAM does not expose Protections via API. Driving the GAM UI with Playwright
requires a logged-in Google session with 2FA, which can't be done from a
headless CI runner. See docs/confiant_blocklist.md for the full design note.

Typical use (API mode — pulls last 7 days from Confiant):
  python confiant_blocklist.py \\
      --protection-id 28044902 --protection-label Everything --dry-run

Real run (remove --dry-run; browser will open):
  python confiant_blocklist.py --protection-id 28044902

Manual CSV mode (fallback if Confiant API is down):
  python confiant_blocklist.py --csv ~/Downloads/Alert\\ Log\\ CSV.csv \\
      --protection-id 28044902

Other modes:
  --inspect          Open the GAM Protections page in a visible browser, wait.
  --print-existing   Dump the current Advertiser URLs for the target Protection.
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
            protection_id       INTEGER NOT NULL,
            protection_label    TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at               TEXT NOT NULL,
            source               TEXT,
            categories           TEXT,
            protection_id        INTEGER,
            protection_label     TEXT,
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
    source: str                # CSV path or 'api://<endpoint>/<hash>'
    categories: tuple[str, ...]
    protection_id: int
    protection_label: str
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
    browser.append_to_protection(summary.protection_id, [d for d, _ in summary.new_domains])

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today_date = now[:10]
    conn.executemany(
        """INSERT OR IGNORE INTO blocked_domains
           (domain, issue_type, first_seen_in_csv, first_pushed_to_gam,
            protection_id, protection_label)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [(d, t, today_date, now, summary.protection_id, summary.protection_label)
         for d, t in summary.new_domains],
    )
    conn.commit()


def record_run(conn: sqlite3.Connection, summary: RunSummary) -> None:
    conn.execute(
        """INSERT INTO runs
           (run_at, source, categories, protection_id, protection_label,
            total_google_rows, blockable_domains, new_domains_added,
            cloaked_rows_skipped, dry_run, status, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            summary.source,
            ",".join(summary.categories),
            summary.protection_id,
            summary.protection_label,
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
  <p style='color:#666'>Source: <code>{summary.source}</code>
     &middot; Categories: <strong>{', '.join(summary.categories)}</strong>
     &middot; Protection: <strong>{summary.protection_label}</strong>
     <span style='color:#999'>(ID {summary.protection_id})</span></p>

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
    import json
    import urllib.error
    import urllib.request
    from datetime import date

    api_key   = os.environ.get("BREVO_API_KEY")
    from_addr = os.environ.get("BREVO_FROM") or "roger.hirano@newsweek.com"
    from_name = os.environ.get("BREVO_FROM_NAME") or "Newsweek yield-dashboard"
    recipient = os.environ.get("CONFIANT_REPORT_TO_EMAIL") or os.environ.get("REPORT_TO_EMAIL")
    if not (api_key and recipient):
        print("Skipping email — BREVO_API_KEY / CONFIANT_REPORT_TO_EMAIL "
              "not both set", file=sys.stderr)
        return

    subject_tag = " (DRY RUN)" if summary.dry_run else (
        "" if summary.success else " (FAILED)"
    )
    payload = {
        "sender":      {"email": from_addr, "name": from_name},
        "to":          [{"email": recipient}],
        "subject":     f"Confiant -> GAM blocklist{subject_tag} — {date.today().strftime('%b %d, %Y')}",
        "htmlContent": _build_email_html(summary),
    }
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode(),
        headers={
            "api-key":      api_key,
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   "yield-dashboard/confiant-blocklist",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"brevo.com send failed: HTTP {e.code} {e.reason} :: "
            f"{e.read().decode(errors='replace')}"
        ) from e
    print(f"Summary email sent to {recipient}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Append Confiant-flagged Google domains to a GAM Protection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true",
                     help="Open the GAM Protections page in a visible browser "
                          "and wait — used for first-time login and selector "
                          "verification. No CSV processed.")
    mode.add_argument("--print-existing", action="store_true",
                     help="Open the target Protection, print the current "
                          "Advertiser URLs to stdout, and exit. No CSV "
                          "processed, no modification. Use this before the "
                          "first real run to see what's already in the prod "
                          "Protection.")

    p.add_argument("--csv",
                   help="Path to a Confiant CSV (manual fallback). If omitted, "
                        "pulls the issue_type_by_domain report from the Confiant "
                        "API using CONFIANT_API_KEY.")
    p.add_argument("--api-days", type=int, default=7,
                   help="When pulling from the API, lookback window in days. "
                        "Default 7. Ignored if --csv is given.")
    p.add_argument("--categories", default="Security",
                   help="Comma-separated Issue Category values to block. "
                        "Default 'Security'. Pass 'Security,Quality' to also "
                        "block annoying-but-not-malicious creatives.")
    p.add_argument("--protection-id", type=int,
                   help="GAM Protection ID to append to (e.g. 28044902). "
                        "Required for normal runs and --print-existing.")
    p.add_argument("--protection-label", default=None,
                   help="Human-readable name for the Protection (e.g. "
                        "'Everything'). Used only for email subject lines and "
                        "state-table display; the script navigates by ID. "
                        "Defaults to 'Protection #<id>'.")
    p.add_argument("--dry-run", action="store_true",
                   help="Pull report, diff against state, send summary email, "
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


def _label_for(args: argparse.Namespace) -> str:
    return args.protection_label or f"Protection #{args.protection_id}"


def _run_inspect(args, profile_dir, network_id) -> int:
    GAMBlocklistBrowser(profile_dir, network_id, headless=False, debug=True) \
        .inspect(protection_id=args.protection_id)
    return 0


def _run_print_existing(args, profile_dir, network_id) -> int:
    if not args.protection_id:
        print("--protection-id is required for --print-existing.", file=sys.stderr)
        return 2
    urls = GAMBlocklistBrowser(profile_dir, network_id,
                                headless=args.headless, debug=args.debug) \
        .read_existing_urls(args.protection_id)
    print(f"# {len(urls)} Advertiser URLs currently in "
          f"{_label_for(args)} (ID {args.protection_id}):")
    for u in urls:
        print(u)
    return 0


def main() -> int:
    _load_dotenv()
    args = _parse_args()
    profile_dir = args.profile_dir or default_profile_dir()
    network_id = os.environ.get("GAM_NETWORK_ID")

    if args.inspect or args.print_existing:
        if not network_id:
            print("GAM_NETWORK_ID env var required.", file=sys.stderr)
            return 2
        if args.inspect:
            return _run_inspect(args, profile_dir, network_id)
        return _run_print_existing(args, profile_dir, network_id)

    if not args.protection_id:
        print("--protection-id is required (or pass --inspect / --print-existing).",
              file=sys.stderr)
        return 2

    categories = tuple(c.strip() for c in args.categories.split(",") if c.strip())
    if args.csv:
        report = confiant_client.parse_csv(args.csv)
        print(f"Loaded Confiant CSV from {args.csv}")
    else:
        report = confiant_client.fetch_via_api(days=args.api_days)
        print(f"Pulled Confiant API report ({len(report.rows)} rows, "
              f"source={report.source})")

    blockable, cloaked = report.blockable_domains(categories=categories)
    print(f"Google rows ({'+'.join(categories)}): {len(report.google_rows)}  "
          f"blockable={len(blockable)}  cloaked={len(cloaked)}")

    conn = _open_state()
    new_domains, skipped = diff_new_domains(conn, blockable)
    print(f"New domains to push: {len(new_domains)}  "
          f"(already in state: {skipped})")

    summary = RunSummary(
        source=report.source,
        categories=categories,
        protection_id=args.protection_id,
        protection_label=_label_for(args),
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
