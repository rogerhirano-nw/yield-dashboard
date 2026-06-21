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

Auto-remediation: when a *remediable* check fails (freshness / sweep
liveness — i.e. things a re-pull can fix), the script re-dispatches
refresh.yml via the GitHub API, waits for it to complete, re-runs every
check, and reports the final state — so a transient upstream failure heals
itself without anyone touching Actions. Code-level failures (DV id format,
join rate) are NOT remediable by re-running and are reported as such. One
remediation attempt per run, never loops. Requires `actions: write` on the
workflow's GITHUB_TOKEN; GITHUB_TOKEN-created workflow_dispatch events are
exempt from GitHub's recursive-trigger guard, so the dispatch works.

Sends via agentmail.to, same outbound pattern as betting_daily_update.py.
Exits non-zero when any check still fails so the Actions run goes red too.

Manual ad-hoc:
    python health_check.py --dry-run    # run checks + print; no send, no remediation
    python health_check.py              # run checks + remediate + send

Env required:
    DATABASE_URL          Postgres (Supabase) — same as refresh_cache.py
    AGENTMAIL_API_KEY     Bearer token for agentmail.to (not needed --dry-run)
    AGENTMAIL_INBOX_ID    "newsweek@agentmail.to"   (not needed --dry-run)
Optional:
    HEALTH_DIGEST_TO             Default: roger.hirano@newsweek.com
    HEALTH_DIGEST_ONLY_FAILURES  "1"/"true" → send only failures and
                                 remediation outcomes; quiet green runs send
                                 nothing (default: send the ✅ daily too).
                                 Independent of this flag, only the FIRST
                                 green run of the day emails the ✅ — later
                                 green re-runs are quiet automatically
                                 (run-history check, drift-proof).
    HEALTH_AUTO_REMEDIATE        default "1"; "0" disables the sweep re-run
    GITHUB_TOKEN / GITHUB_REPOSITORY  for the sweep-liveness check and the
                                 remediation dispatch; both are present
                                 automatically in GitHub Actions
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
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
    # TTD scheduled reports arrive via agentmail (same pipeline as DV).
    # Allow 2 days lag — TTD typically sends the report same-day but the
    # signed URL is valid 30 days so the report always reflects past data.
    ("ttd_luckyland fresh",       "ttd_luckyland",      "date", 2),
    ("ttd_chumba fresh",          "ttd_chumba",         "date", 2),
]

# (check name, table, timestamp column, max age in hours)
PULLED_AT_CHECKS = [
    ("gam_campaigns pulled",      "gam_campaigns",     "_pulled_at", SWEEP_MAX_AGE_HOURS),
    ("pmp_last_bid_date updated", "pmp_last_bid_date", "updated_at", SWEEP_MAX_AGE_HOURS),
    # OpenSincera: all four tables rewrite on every sweep (--mode=opensincera
    # job), so staleness means that job failed. The ecosystem snapshot date
    # lags a day by design — _pulled_at is the invariant, not max(date).
    ("opensincera_ecosystem pulled",  "opensincera_ecosystem",  "_pulled_at", SWEEP_MAX_AGE_HOURS),
    ("opensincera_publishers pulled", "opensincera_publishers", "_pulled_at", SWEEP_MAX_AGE_HOURS),
    ("opensincera_adsystems pulled",  "opensincera_adsystems",  "_pulled_at", SWEEP_MAX_AGE_HOURS),
    ("opensincera_modules pulled",    "opensincera_modules",    "_pulled_at", SWEEP_MAX_AGE_HOURS),
]


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    # True when a refresh re-run can plausibly fix a failure (stale data,
    # failed sweep). False for code/contract problems (id format, join
    # rate) where re-pulling would just rewrite the same bad rows.
    remediable: bool = False


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


# The sweep fires at 09:00 UTC and lands within ~15 min; give it slack.
SWEEP_LANDS_BY = timedelta(hours=9, minutes=30)


def _data_day(now: datetime) -> date:
    """The day whose D-1 data the cache should currently hold.

    Rolls over at 09:30 UTC — after the 09:00 sweep lands — instead of
    midnight. Between 00:00 UTC and the sweep, yesterday's pull is the
    freshest data that can exist; baselining on the calendar date there
    would fail every date-capped table and trigger a pointless
    remediation sweep."""
    return (now - SWEEP_LANDS_BY).date()


def _eval_freshness(name: str, observed: date | None, max_age_days: int,
                    today: date) -> CheckResult:
    """Pure comparison — split from the query so it's unit-testable."""
    required = today - timedelta(days=max_age_days)
    if observed is None:
        return CheckResult(name, False, "table is empty (max date is NULL)",
                           remediable=True)
    return CheckResult(
        name, observed >= required,
        f"max(date) {observed.isoformat()}"
        + ("" if observed >= required else f" < required {required.isoformat()}"),
        remediable=True,
    )


def _check_freshness(conn, name: str, table: str, col: str,
                     max_age_days: int) -> CheckResult:
    observed = conn.execute(text(
        f"SELECT max({col}::date) FROM {table}"
    )).scalar()
    return _eval_freshness(name, observed, max_age_days,
                           _data_day(datetime.now(timezone.utc)))


def _check_pulled_at(conn, name: str, table: str, col: str,
                     max_age_hours: int) -> CheckResult:
    observed = conn.execute(text(
        f"SELECT max({col}::timestamptz) FROM {table}"
    )).scalar()
    if observed is None:
        return CheckResult(name, False, "table is empty (max timestamp is NULL)",
                           remediable=True)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - observed).total_seconds() / 3600
    return CheckResult(
        name, age_h <= max_age_hours,
        f"last write {age_h:.1f}h ago (max {max_age_hours}h)",
        remediable=True,
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
        return CheckResult(name, False, "no completed refresh.yml runs found",
                           remediable=True)
    run = runs[0]
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
    ok = run["conclusion"] == "success" and age_h <= SWEEP_MAX_AGE_HOURS
    return CheckResult(
        name, ok,
        f"latest run {run['conclusion']} {age_h:.1f}h ago ({run['html_url']})",
        remediable=True,
    )


# ----------------------------------------------------------------------
# Auto-remediation
# ----------------------------------------------------------------------

REMEDIATION_POLL_SECONDS = 30
REMEDIATION_TIMEOUT_MINUTES = 25


def _gh_api(path: str, *, token: str, method: str = "GET",
            payload: dict | None = None) -> dict:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    req = urllib.request.Request(
        f"{api}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        return json.loads(body) if body else {}


def remediate_with_sweep() -> tuple[bool, str]:
    """Re-run the refresh sweep and wait for it.

    If a refresh.yml run is already queued/in progress (e.g. the morning
    sweep overlaps this check), wait on that instead of dispatching a
    duplicate. Returns (sweep_succeeded, human-readable description).
    GITHUB_TOKEN-created workflow_dispatch events are exempt from GitHub's
    recursive-trigger guard, so the dispatch fires normally.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False, "cannot remediate — no GITHUB_TOKEN (local run)"
    wf = f"/repos/{repo}/actions/workflows/refresh.yml"

    run = None
    for status in ("in_progress", "queued"):
        runs = _gh_api(f"{wf}/runs?status={status}&per_page=1",
                       token=token).get("workflow_runs") or []
        if runs:
            run = runs[0]
            logger.info("refresh.yml already %s — waiting on %s",
                        status, run["html_url"])
            break
    if run is None:
        ref = os.environ.get("GITHUB_REF_NAME") or "main"
        _gh_api(f"{wf}/dispatches", token=token, method="POST",
                payload={"ref": ref})
        logger.info("Dispatched refresh.yml on %s — waiting for the run to appear", ref)
        for _ in range(10):
            time.sleep(10)
            runs = _gh_api(f"{wf}/runs?event=workflow_dispatch&per_page=1",
                           token=token).get("workflow_runs") or []
            if runs and runs[0]["status"] != "completed":
                run = runs[0]
                break
        if run is None:
            return False, "dispatched refresh.yml but no run appeared within 100s"

    deadline = time.monotonic() + REMEDIATION_TIMEOUT_MINUTES * 60
    while time.monotonic() < deadline:
        cur = _gh_api(f"/repos/{repo}/actions/runs/{run['id']}", token=token)
        if cur.get("status") == "completed":
            ok = cur.get("conclusion") == "success"
            return ok, f"re-ran refresh sweep → {cur.get('conclusion')} ({cur['html_url']})"
        time.sleep(REMEDIATION_POLL_SECONDS)
    return False, f"refresh sweep still running after {REMEDIATION_TIMEOUT_MINUTES}min ({run['html_url']})"


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
        connect_args={"connect_timeout": 10},
    )
    # Retry the initial connect — a transient pooler blip would otherwise
    # error every SQL check at once and email a false ❌ (the same failure
    # mode that killed the gam sweep job on 2026-06-11). After the retry
    # budget, fall through and let the per-check guard report it.
    for _attempt in range(3):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except sqlalchemy.exc.OperationalError as exc:
            if _attempt == 2:
                break
            logger.info("DB connect failed (attempt %d/3): %s — retrying", _attempt + 1, exc)
            time.sleep(15 * (_attempt + 1))
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

def build_report(results: list[CheckResult], today: date,
                 remediation: str | None = None) -> tuple[str, str, bool]:
    """Returns (subject, body, all_ok). `remediation` is a one-line account
    of an auto-remediation attempt; results should then be the re-check."""
    n_fail = sum(1 for r in results if not r.ok)
    all_ok = n_fail == 0
    if all_ok:
        subject = f"Yield health — ✅ {len(results)}/{len(results)} pass"
        if remediation:
            subject += " (auto-remediated)"
    else:
        subject = f"Yield health — ❌ {n_fail} of {len(results)} FAILING"
    subject += f" ({today.isoformat()})"
    width = max(len(r.name) for r in results)
    lines = [f"Yield-dashboard data health — {today.isoformat()} (UTC)", ""]
    for r in results:
        mark = "✅ PASS" if r.ok else "❌ FAIL"
        lines.append(f"{mark}  {r.name.ljust(width)}  {r.detail}")
    lines.append("")
    lines.append(f"{len(results) - n_fail}/{len(results)} checks pass.")
    if remediation:
        lines.append(f"Auto-remediation: {remediation}")
    if not all_ok:
        lines.append(
            "Still failing after remediation — needs a human."
            if remediation else
            "Re-run the sweep: gh workflow run refresh.yml — details in CLAUDE.md / docs."
        )
    lines.append("")
    lines.append(f"Generated by yield-dashboard.health_check at "
                 f"{datetime.now(timezone.utc).isoformat()}.")
    return subject, "\n".join(lines), all_ok


def _already_verdicted_today() -> bool:
    """True when an earlier run of this workflow already completed
    successfully today (UTC) — i.e. a ✅ verdict has been emailed.

    Used to quiet green re-runs WITHOUT assuming punctual scheduling:
    GitHub cron drifts hours (observed 6-8h on 2026-06-10/11), so the
    previous hour-of-day gate pushed every run past the cutoff and green
    days emailed nothing at all. Failed runs exit non-zero → conclusion
    'failure' → don't count, so the next run still reports."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        data = _gh_api(
            f"/repos/{repo}/actions/workflows/health_check.yml/runs"
            f"?status=success&created=%3E%3D{today}&per_page=10",
            token=token,
        )
    except Exception as exc:  # noqa: BLE001 — fail open: a duplicate ✅ beats silence
        logger.warning("Could not check today's run history (%s) — sending anyway", exc)
        return False
    current = str(os.environ.get("GITHUB_RUN_ID") or "")
    return any(str(r.get("id")) != current
               for r in (data.get("workflow_runs") or []))


def should_send(all_ok: bool, only_failures: bool,
                remediation: str | None) -> bool:
    """Failures always send. Remediation outcomes always send — you want to
    know the system healed itself even on a quiet day. A green run with
    nothing to report sends unless suppressed: by repo var
    HEALTH_DIGEST_ONLY_FAILURES, or because today's ✅ verdict already went
    out on an earlier run (_already_verdicted_today)."""
    return (not all_ok) or remediation is not None or not only_failures


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

    # Auto-remediate: a failing *remediable* check (stale table / failed
    # sweep) triggers one sweep re-run, then a full re-check. Code-level
    # failures (id format, join rate) skip straight to the report — a
    # re-pull can't fix those.
    remediation = None
    remediate_enabled = (os.environ.get("HEALTH_AUTO_REMEDIATE") or "1").lower() \
        not in ("0", "false", "no")
    if (not dry_run and remediate_enabled
            and any(not r.ok and r.remediable for r in results)):
        failing_before = {r.name for r in results if not r.ok}
        _, remediation = remediate_with_sweep()
        results = run_checks()
        recovered = failing_before - {r.name for r in results if not r.ok}
        if recovered:
            remediation += f"; recovered: {', '.join(sorted(recovered))}"

    subject, body, all_ok = build_report(
        results, datetime.now(timezone.utc).date(), remediation=remediation)

    print(subject)
    print(body)

    only_failures = (os.environ.get("HEALTH_DIGEST_ONLY_FAILURES") or "").lower() \
        in ("1", "true", "yes")
    # Quiet green re-runs once today's ✅ has gone out — keyed on the
    # workflow's own run history rather than the clock, so scheduling
    # drift can't silence the first verdict of the day.
    if not only_failures and not dry_run and all_ok:
        only_failures = _already_verdicted_today()
    if dry_run:
        logger.info("--dry-run: not sending")
    elif not should_send(all_ok, only_failures, remediation):
        logger.info("Quiet green run (verdict already sent today) — not sending")
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
