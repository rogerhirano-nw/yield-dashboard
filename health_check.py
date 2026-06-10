"""Daily data-health check for the yield dashboard cache.

Verifies invariants on the prod DB *after* the morning refresh sweep, so
data problems surface in one glanceable email instead of someone having to
eyeball the dashboard every day. The subject line carries the verdict —
"✅ N/N pass" means there is nothing to open.

Checks (each fails independently; an exception in one check is reported as
that check failing, never as a crash of the whole run):

  1. DV id hygiene      — no float-suffixed line_item_ids ("7306352098.0")
                          in dv_attention / dv_ivt. Canary for the join-key
                          format bug fixed 2026-06-10 (#151): a regression
                          here silently blanks the dashboard's DV columns.
  2. DV ↔ GAM join rate — ≥90% of distinct DV line_item_ids (last 30 days)
                          must match gam_campaigns. Catches id-format drift
                          from either side, not just the known ".0" shape.
  3. Freshness          — per-table max(date) / max(_pulled_at) within the
                          age each source contracts: same-day pulls write
                          yesterday's date; DV exports lag ~2 days.
  4. Sweep liveness     — the latest completed "Refresh cache" workflow run
                          (refresh.yml) succeeded within the last 26h.
                          Requires GITHUB_TOKEN; skipped when absent (local).

Sends via agentmail.to, same outbound pattern as betting_daily_update.py.
Exits non-zero when any check fails so the Actions run goes red too.

Manual ad-hoc:
    python health_check.py --dry-run    # run checks + print, no send
    python health_check.py              # run checks + send

Env required:
    DATABASE_URL          Postgres (Supabase) — same as refresh_cache.py
    AGENTMAIL_API_KEY     Bearer token for agentmail.to (not needed --dry-run)
    AGENTMAIL_INBOX_ID    "newsweek@agentmail.to"   (not needed --dry-run)
Optional:
    HEALTH_DIGEST_TO             Default: roger.hirano@newsweek.com
    HEALTH_DIGEST_ONLY_FAILURES  "1"/"true" → send email only when a check
                                 fails (default: send the ✅ daily too)
    GITHUB_TOKEN / GITHUB_REPOSITORY  for the sweep-liveness check; both are
                                 present automatically in GitHub Actions
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import sqlalchemy
from sqlalchemy import text

logger = logging.getLogger(__name__)

AGENTMAIL_BASE = "https://api.agentmail.to/v0"
DEFAULT_RECIPIENT = "roger.hirano@newsweek.com"

JOIN_RATE_THRESHOLD_PCT = 90.0
SWEEP_MAX_AGE_HOURS = 26  # daily cadence + slack for cron-job.org jitter

# (check name, table, date-ish column, max age in days).
# Same-day sources write yesterday's date on every successful sweep, so
# max(date) older than yesterday means the last sweep didn't land for that
# source. DV exports lag ~2 days behind, hence the looser bound.
FRESHNESS_CHECKS = [
    ("magnite_site_daily fresh",  "magnite_site_daily", "date", 1),
    ("magnite_dsp_daily fresh",   "magnite_dsp_daily",  "date", 1),
    ("magnite_deal_daily fresh",  "magnite_deal_daily", "date", 1),
    # Pubmatic's report for D-1 isn't available yet at the 09:00 UTC sweep —
    # verified 2026-06-10: a healthy pull writes through D-2 only.
    ("pubmatic_deals fresh",      "pubmatic_deals",     "date", 2),
    ("gam_pmp_deals fresh",       "gam_pmp_deals",      "date", 1),
    ("dv_attention fresh",        "dv_attention",       "date", 3),
    ("dv_ivt fresh",              "dv_ivt",             "date", 3),
]

# (check name, table, timestamp column, max age in hours)
PULLED_AT_CHECKS = [
    ("gam_campaigns pulled",      "gam_campaigns",     "_pulled_at", SWEEP_MAX_AGE_HOURS),
    ("pmp_last_bid_date updated", "pmp_last_bid_date", "updated_at", SWEEP_MAX_AGE_HOURS),
]


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------

def _check_dv_id_format(conn, table: str) -> CheckResult:
    n = conn.execute(text(
        f"SELECT count(*) FROM {table} WHERE line_item_id LIKE '%.0'"
    )).scalar() or 0
    return CheckResult(
        f"{table} id format", n == 0,
        "no float-suffixed line_item_ids" if n == 0
        else f"{n} rows with float-suffixed line_item_id — DV↔GAM join is broken (see #151)",
    )


def _check_dv_join_rate(conn, table: str) -> CheckResult:
    row = conn.execute(text(
        f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM gam_campaigns g
                   WHERE g.line_item_id = d.line_item_id)) AS matched
        FROM (SELECT DISTINCT line_item_id FROM {table}
              WHERE date::date >= CURRENT_DATE - 30
                AND line_item_id IS NOT NULL) d
        """
    )).one()
    if not row.total:
        return CheckResult(f"{table} ↔ GAM join", False,
                           "no DV line_item_ids in the last 30 days")
    pct = 100.0 * row.matched / row.total
    return CheckResult(
        f"{table} ↔ GAM join", pct >= JOIN_RATE_THRESHOLD_PCT,
        f"{pct:.1f}% of {row.total} ids match gam_campaigns "
        f"(threshold {JOIN_RATE_THRESHOLD_PCT:.0f}%)",
    )


def _eval_freshness(name: str, observed: date | None, max_age_days: int,
                    today: date) -> CheckResult:
    """Pure comparison — split from the query so it's unit-testable."""
    required = today - timedelta(days=max_age_days)
    if observed is None:
        return CheckResult(name, False, "table is empty (max date is NULL)")
    return CheckResult(
        name, observed >= required,
        f"max(date) {observed.isoformat()}"
        + ("" if observed >= required else f" < required {required.isoformat()}"),
    )


def _check_freshness(conn, name: str, table: str, col: str,
                     max_age_days: int) -> CheckResult:
    observed = conn.execute(text(
        f"SELECT max({col}::date) FROM {table}"
    )).scalar()
    return _eval_freshness(name, observed, max_age_days,
                           datetime.now(timezone.utc).date())


def _check_pulled_at(conn, name: str, table: str, col: str,
                     max_age_hours: int) -> CheckResult:
    observed = conn.execute(text(
        f"SELECT max({col}::timestamptz) FROM {table}"
    )).scalar()
    if observed is None:
        return CheckResult(name, False, "table is empty (max timestamp is NULL)")
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - observed).total_seconds() / 3600
    return CheckResult(
        name, age_h <= max_age_hours,
        f"last write {age_h:.1f}h ago (max {max_age_hours}h)",
    )


def _check_sweep_workflow() -> CheckResult:
    """Latest completed refresh.yml run succeeded within SWEEP_MAX_AGE_HOURS."""
    name = "refresh sweep run"
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return CheckResult(name, True, "skipped — no GITHUB_TOKEN (local run)")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    req = urllib.request.Request(
        f"{api}/repos/{repo}/actions/workflows/refresh.yml/runs"
        "?status=completed&per_page=1",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        runs = json.loads(r.read()).get("workflow_runs") or []
    if not runs:
        return CheckResult(name, False, "no completed refresh.yml runs found")
    run = runs[0]
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
    ok = run["conclusion"] == "success" and age_h <= SWEEP_MAX_AGE_HOURS
    return CheckResult(
        name, ok,
        f"latest run {run['conclusion']} {age_h:.1f}h ago ({run['html_url']})",
    )


def run_checks() -> list[CheckResult]:
    """Run every check, isolating failures so one bad table can't hide the rest."""
    results: list[CheckResult] = []

    def _guard(name: str, fn) -> None:
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001 — a broken check IS the finding
            results.append(CheckResult(name, False, f"check errored: {e}"))

    engine = sqlalchemy.create_engine(
        os.environ["DATABASE_URL"], pool_size=1, max_overflow=0, pool_recycle=300,
    )
    with engine.connect() as conn:
        for table in ("dv_attention", "dv_ivt"):
            _guard(f"{table} id format", lambda t=table: _check_dv_id_format(conn, t))
            _guard(f"{table} ↔ GAM join", lambda t=table: _check_dv_join_rate(conn, t))
        for name, table, col, days in FRESHNESS_CHECKS:
            _guard(name, lambda n=name, t=table, c=col, d=days:
                   _check_freshness(conn, n, t, c, d))
        for name, table, col, hours in PULLED_AT_CHECKS:
            _guard(name, lambda n=name, t=table, c=col, h=hours:
                   _check_pulled_at(conn, n, t, c, h))
    _guard("refresh sweep run", _check_sweep_workflow)
    return results


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------

def build_report(results: list[CheckResult],
                 today: date) -> tuple[str, str, bool]:
    """Returns (subject, body, all_ok)."""
    n_fail = sum(1 for r in results if not r.ok)
    all_ok = n_fail == 0
    subject = (
        f"Yield health — ✅ {len(results)}/{len(results)} pass ({today.isoformat()})"
        if all_ok else
        f"Yield health — ❌ {n_fail} of {len(results)} FAILING ({today.isoformat()})"
    )
    width = max(len(r.name) for r in results)
    lines = [f"Yield-dashboard data health — {today.isoformat()} (UTC)", ""]
    for r in results:
        mark = "✅ PASS" if r.ok else "❌ FAIL"
        lines.append(f"{mark}  {r.name.ljust(width)}  {r.detail}")
    lines.append("")
    lines.append(f"{len(results) - n_fail}/{len(results)} checks pass.")
    if not all_ok:
        lines.append("Re-run the sweep: gh workflow run refresh.yml — "
                     "details in CLAUDE.md / docs.")
    lines.append("")
    lines.append(f"Generated by yield-dashboard.health_check at "
                 f"{datetime.now(timezone.utc).isoformat()}.")
    return subject, "\n".join(lines), all_ok


def send_via_agentmail(api_key: str, inbox_id: str, to: list[str],
                       subject: str, body: str) -> dict:
    """Same outbound pattern as betting_daily_update.send_via_agentmail."""
    req = urllib.request.Request(
        f"{AGENTMAIL_BASE}/inboxes/{inbox_id}/messages/send",
        data=json.dumps({"to": to, "subject": subject, "text": body}).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
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

    results = run_checks()
    subject, body, all_ok = build_report(
        results, datetime.now(timezone.utc).date())

    print(subject)
    print(body)

    only_failures = (os.environ.get("HEALTH_DIGEST_ONLY_FAILURES") or "").lower() \
        in ("1", "true", "yes")
    if dry_run:
        logger.info("--dry-run: not sending")
    elif all_ok and only_failures:
        logger.info("All checks pass and HEALTH_DIGEST_ONLY_FAILURES set — not sending")
    else:
        to = [a.strip() for a in
              (os.environ.get("HEALTH_DIGEST_TO") or DEFAULT_RECIPIENT).split(",")
              if a.strip()]
        resp = send_via_agentmail(
            os.environ["AGENTMAIL_API_KEY"], os.environ["AGENTMAIL_INBOX_ID"],
            to, subject, body,
        )
        logger.info("Sent to %s: %s", to, resp.get("message_id") or resp)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
