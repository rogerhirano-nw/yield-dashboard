"""
CLI entry point.

    python -m deal_health --output-dir reports --no-publish        # local dry
    python -m deal_health --output-dir reports                     # publish
    python -m deal_health --output-dir reports --no-redact         # internal-only
    python -m deal_health --output-dir reports --dry-run           # generate, don't commit/push
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import structlog

from .aggregate import build_payload
from .data import load_deals
from .publish import (
    build_csv_url,
    commit_and_push,
    csv_filename_for,
    verify_url,
    write_csv,
)
from .redact import is_public_safe_enabled, redact_csv
from .render import render_email


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.KeyValueRenderer(key_order=["event"]),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


log = structlog.get_logger(__name__)


def _load_dotenv() -> None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    import os
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deal_health")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"),
                        help="directory for CSV + HTML output")
    parser.add_argument("--report-date", default=None,
                        help="ISO date for the report header (default: today UTC)")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--no-publish", action="store_true",
                        help="skip git commit + push")
    parser.add_argument("--no-redact", action="store_true",
                        help="skip CSV redaction (overrides PUBLIC_SAFE env)")
    parser.add_argument("--dry-run", action="store_true",
                        help="generate files but never commit/push (alias for --no-publish)")
    parser.add_argument("--dashboard-url", default="https://newsweek-magnite.streamlit.app/")
    args = parser.parse_args(argv)

    _setup_logging()
    _load_dotenv()

    report_date = (
        date.fromisoformat(args.report_date)
        if args.report_date
        else datetime.now(timezone.utc).date()
    )

    deals = load_deals(report_date=report_date)
    log.info("loaded", deal_count=len(deals))
    if not deals:
        log.warning("no deals to report")
        return 0

    csv_filename = csv_filename_for(report_date)
    csv_url = build_csv_url(csv_filename)

    payload = build_payload(
        deals,
        report_date=report_date,
        lookback_days=args.lookback_days,
        csv_url=csv_url,
        dashboard_url=args.dashboard_url,
    )

    # 1. Render HTML email (always full, never redacted).
    html_path = args.output_dir / f"weekly_deal_health_{report_date.isoformat()}.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_email(payload), encoding="utf-8")
    log.info("html written", path=str(html_path), bytes=html_path.stat().st_size)

    # 2. Write full CSV locally (kept for debugging).
    full_csv_path = args.output_dir / f"_full_{csv_filename}"
    write_csv(payload, full_csv_path)

    # 3. Redact if PUBLIC_SAFE and --no-redact wasn't passed.
    if args.no_redact or not is_public_safe_enabled():
        published_path = args.output_dir / csv_filename
        full_csv_path.replace(published_path)
        log.info("redaction skipped")
    else:
        published_path = redact_csv(full_csv_path, args.output_dir / csv_filename)
        full_csv_path.unlink(missing_ok=True)

    # 4. Commit + push + verify URL (unless --dry-run / --no-publish).
    if args.dry_run or args.no_publish:
        log.info("publish skipped", flag="--dry-run" if args.dry_run else "--no-publish")
        return 0

    pushed = commit_and_push(
        published_path,
        f"chore: weekly deal health report — {report_date.isoformat()}",
    )
    if not pushed:
        log.info("nothing to publish (file unchanged)")
        return 0

    if verify_url(csv_url):
        log.info("publish verified", url=csv_url)
        return 0
    log.error("verify_url failed", url=csv_url)
    return 2


if __name__ == "__main__":
    sys.exit(main())
