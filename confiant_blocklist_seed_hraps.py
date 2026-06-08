"""Seed the GAM Protection blocklist from the persisted Confiant HRAP list.

Confiant maintains a "High Risk Ad Platforms" (HRAP) list — bidding/serving
platforms with persistent abnormal volumes of malicious campaigns. We persist
the list at data/confiant_hraps.json (canonical source-of-truth, updated when
Confiant ships their periodic notice email) and run this script to ensure
every HRAP domain is in the GAM Protection.

The script is **idempotent**: it diffs against the same `blocked_domains`
table the daily cron writes to and only pushes the delta. Safe to run on
every update or even daily.

Issue type column is set to `HRAP — <platform name>` so the weekly digest
distinguishes platform-blocks from per-creative blocks.

Typical usage:

    # Preview the diff against current state.
    python confiant_blocklist_seed_hraps.py --dry-run

    # Push new HRAPs to the prod Protection.
    python confiant_blocklist_seed_hraps.py \
        --protection-id 28044902 --protection-label Everything
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from confiant_blocklist import (
    _load_dotenv, _open_state, _state_path,
    RunSummary, record_run,
)
from gam_blocklist_ui import GAMBlocklistBrowser, default_profile_dir


_HRAP_FILE = Path(__file__).parent / "data" / "confiant_hraps.json"


def _load_hraps(path: Path) -> tuple[list[tuple[str, str]], dict]:
    """Returns (domain_with_label, meta).

    Each domain entry becomes a (domain, issue_type) tuple where issue_type is
    `HRAP — <platform>`. That namespacing is on purpose: it keeps HRAPs from
    being silently mixed with per-creative pushes in the weekly digest's
    issue-type grouping.
    """
    payload = json.loads(path.read_text())
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in payload.get("platforms", []):
        domain = entry["domain"].strip()
        platform = entry.get("name", "Unknown").strip()
        if domain.lower() in seen:
            continue
        seen.add(domain.lower())
        out.append((domain, f"HRAP — {platform}"))
    return out, payload


def _diff_against_state(conn, domain_tuples: list[tuple[str, str]]) -> tuple[list, list]:
    """Returns (to_push, already_blocked) — only the to_push list is
    written to GAM."""
    domains = [d for d, _ in domain_tuples]
    if not domains:
        return [], []
    existing = {
        row[0] for row in conn.execute(
            f"SELECT domain FROM blocked_domains WHERE domain IN ({','.join('?' * len(domains))})",
            domains,
        )
    }
    to_push = [(d, t) for d, t in domain_tuples if d not in existing]
    already = [(d, t) for d, t in domain_tuples if d in existing]
    return to_push, already


def _push_and_record(conn, summary: RunSummary, profile_dir: Path,
                     headless: bool, debug: bool, network_id: str) -> None:
    """Same shape as confiant_blocklist.push_and_record, but takes an
    arbitrary (domain, issue_type) list — not tied to the Confiant CSV row
    type."""
    browser = GAMBlocklistBrowser(
        profile_dir=profile_dir,
        network_id=network_id,
        headless=headless,
        debug=debug,
    )
    browser.append_to_protection(
        summary.protection_id,
        [d for d, _ in summary.new_domains],
    )

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


def _parse_args() -> argparse.Namespace:
    import os
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hrap-file", type=Path, default=_HRAP_FILE,
                   help=f"HRAP list JSON (default {_HRAP_FILE}).")
    p.add_argument("--protection-id", type=int, default=28044902,
                   help="GAM Protection ID (default 28044902 = Everything).")
    p.add_argument("--protection-label", default="Everything",
                   help="Human label for the Protection (used in summary email).")
    p.add_argument("--dry-run", action="store_true",
                   help="Diff against state.sqlite and print the plan; "
                        "don't open the browser, don't touch GAM, don't "
                        "write to state.sqlite.")
    p.add_argument("--batch-size", type=int, default=30,
                   help="Push at most this many domains per modal cycle. "
                        "GAM's Edit modal validates the textarea input "
                        "before enabling the Update button; with >40 entries "
                        "the validation often exceeds the 10s wait. 30 is a "
                        "comfortable batch (default).")
    p.add_argument("--headless", action="store_true", default=False,
                   help="Run Playwright headless (default: visible browser).")
    p.add_argument("--debug", action="store_true",
                   help="Playwright debug screenshots in ~/.confiant-blocklist/")
    return p.parse_args()


def _chunked(seq, size):
    """Yield successive size-chunks from seq."""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def main() -> int:
    _load_dotenv()
    args = _parse_args()

    import os
    network_id = os.environ.get("GAM_NETWORK_ID")
    if not network_id and not args.dry_run:
        print("ERROR: GAM_NETWORK_ID not set in env / .env", file=sys.stderr)
        return 2

    if not args.hrap_file.exists():
        print(f"ERROR: HRAP file not found: {args.hrap_file}", file=sys.stderr)
        return 2

    hraps, payload = _load_hraps(args.hrap_file)
    print(f"Loaded {len(hraps)} HRAP domains from {args.hrap_file}")
    print(f"  source:     {payload.get('source', '(unknown)')}")
    print(f"  updated_at: {payload.get('updated_at', '(unknown)')}")
    print()

    conn = _open_state()
    try:
        to_push, already = _diff_against_state(conn, hraps)
        print(f"Diff vs state.sqlite:")
        print(f"  already in GAM Protection: {len(already)}")
        print(f"  NEW to push:               {len(to_push)}")
        print()

        if not to_push:
            print("Nothing new to push. State and HRAP list are in sync.")
            return 0

        if args.dry_run:
            print("Would push (dry-run, GAM unchanged, state unchanged):")
            for d, t in to_push:
                print(f"  {d:40s}  {t}")
            return 0

        # Push in batches so the modal's input-validation step never has
        # to chew through more than --batch-size entries at once (the 10s
        # Update-button wait in gam_blocklist_ui starts to time out
        # somewhere around 40-50 entries in one shot).
        batches = list(_chunked(to_push, args.batch_size))
        print(f"Pushing {len(to_push)} new HRAP domain(s) to Protection "
              f"{args.protection_id} ({args.protection_label}) in "
              f"{len(batches)} batch(es) of up to {args.batch_size}...")

        total_pushed = 0
        for i, batch in enumerate(batches, start=1):
            print(f"\n  --- batch {i}/{len(batches)} ({len(batch)} domains) ---")
            # One RunSummary per batch so each batch records independently.
            # A failure halfway through doesn't roll back successful prior
            # batches — they stay in state.sqlite as truthful "already pushed."
            summary = RunSummary(
                source=f"file://{args.hrap_file}#batch={i}/{len(batches)}",
                categories=("HRAP",),
                protection_id=args.protection_id,
                protection_label=args.protection_label,
                total_google_rows=len(hraps),
                blockable_domains_in_csv=len(hraps),
                new_domains=batch,
                skipped_already_blocked=len(already),
                cloaked_for_review=[],
                dry_run=False,
                success=False,
                error=None,
            )
            try:
                _push_and_record(
                    conn, summary,
                    profile_dir=default_profile_dir(),
                    headless=args.headless,
                    debug=args.debug,
                    network_id=network_id,
                )
                summary.success = True
                total_pushed += len(batch)
                print(f"  ✓ batch {i}: pushed {len(batch)} domain(s)")
            except Exception as e:
                summary.error = repr(e)
                print(f"  ✗ batch {i} failed: {e}", file=sys.stderr)
                record_run(conn, summary)
                print(f"\nTotal pushed before failure: {total_pushed}/{len(to_push)}",
                      file=sys.stderr)
                print(f"Re-run the script — it will skip what already landed.",
                      file=sys.stderr)
                raise
            record_run(conn, summary)
        print(f"\nAll batches successful. Pushed {total_pushed} HRAP domain(s).")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
