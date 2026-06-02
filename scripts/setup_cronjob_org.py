#!/usr/bin/env python3
"""
One-shot: provision the cron-job.org jobs that trigger this repo's
scheduled GitHub Actions workflows.

Replaces GitHub's native `schedule:` cron, which was drifting 8+ hours
late because top-of-hour scheduled runs get deprioritized during the
system-wide load spike at :00. cron-job.org has no scheduling lag of
its own — workflow_dispatch fires within seconds of the API call.

Same pattern used by rogerhirano-nw/apple-news.

USAGE
-----
    export CRONJOB_API_KEY='...'        # cron-job.org → Account → API
    export GITHUB_PAT='ghp_...'         # fine-grained PAT, Actions: write,
                                         # repo: rogerhirano-nw/yield-dashboard
    python3 scripts/setup_cronjob_org.py

Re-running is safe: existing jobs with the same title get updated in
place rather than duplicated.

After provisioning the script fires each job once (smoke test) and
prints the GitHub workflow run URL for each, so you can confirm
end-to-end before relying on the cron tonight.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

# Per-job repo + workflow + schedule. Repo is per-entry so this script
# can provision crons across multiple repos owned by the same account
# (yield-dashboard + apple-news today; trivial to add more later).
JOBS = [
    {
        "repo":     "rogerhirano-nw/yield-dashboard",
        "title":    "yield-dashboard refresh ALL (daily 5 AM ET)",
        "workflow": "refresh.yml",
        # All-day, but hours/minutes pinned → fires once at 05:00 NY time.
        "schedule": {
            "timezone": "America/New_York",
            "hours":   [5],
            "minutes": [0],
            "mdays":   [-1],   # every day of month
            "months":  [-1],   # every month
            "wdays":   [-1],   # every day of week
        },
    },
    {
        "repo":     "rogerhirano-nw/yield-dashboard",
        "title":    "yield-dashboard refresh direct campaigns (business hours, hourly)",
        "workflow": "refresh_direct.yml",
        # Direct-only refresh keeps gam_campaigns fresh during the trading
        # day so seller-comms cards / Ivy's intraday view aren't stuck on
        # yesterday's numbers. Pairs with the 5 AM ET full sweep above.
        #
        # Hours pinned to 7 AM – 8 PM ET (14 fires/day). Overnight gap is
        # intentional — no one's looking at the dashboard at 3 AM and the
        # 5 AM ET full sweep refills the rest of the SSP feeds anyway.
        #
        # This SUPERSEDES the legacy 11 AM + 3 PM ET jobs that were
        # configured manually on cron-job.org's web UI. After this entry
        # is provisioned (run scripts/setup_cronjob_org.py), DELETE those
        # two manual jobs on cron-job.org to stop duplicate fires.
        "schedule": {
            "timezone": "America/New_York",
            "hours":   [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
            "minutes": [0],
            "mdays":   [-1],
            "months":  [-1],
            "wdays":   [-1],
        },
    },
    {
        "repo":     "rogerhirano-nw/yield-dashboard",
        "title":    "yield-dashboard weekly seller report (Wed 9 AM ET)",
        "workflow": "weekly_report.yml",
        "schedule": {
            "timezone": "America/New_York",
            "hours":   [9],
            "minutes": [0],
            "mdays":   [-1],
            "months":  [-1],
            "wdays":   [3],    # Wednesday only (Sun=0..Sat=6)
        },
    },
    {
        "repo":     "rogerhirano-nw/apple-news",
        "title":    "apple-news daily report (daily 10 AM ET)",
        "workflow": "daily-report.yml",
        "schedule": {
            "timezone": "America/New_York",
            "hours":   [10],
            "minutes": [0],
            "mdays":   [-1],
            "months":  [-1],
            "wdays":   [-1],
        },
    },
    {
        "repo":     "rogerhirano-nw/seller-comms",
        "title":    "seller-comms weekly deal health report (Mon 9 AM ET)",
        "workflow": "weekly_report.yml",
        "schedule": {
            "timezone": "America/New_York",
            "hours":   [9],
            "minutes": [0],
            "mdays":   [-1],
            "months":  [-1],
            "wdays":   [1],    # Monday only (Sun=0..Sat=6)
        },
    },
    # ── Intraday GAM refresh (feeds the 3-hourly cap digest with today's data) ─
    # Uses refresh_direct.yml (GAM direct + hourly breakdown only — much faster
    # than the full refresh.yml sweep which re-pulls all SSP feeds).
    # Fires at 10:30, 13:30, 16:30, 19:30 ET Mon–Fri — 30 min before each
    # cap-digest run — so each digest has data ≤ 30 minutes old.
    # The 5 AM full refresh covers overnight + the 8 AM digest run.
    {
        "repo":     "rogerhirano-nw/yield-dashboard",
        "title":    "yield-dashboard refresh DIRECT+HOURLY (intraday daily 10:30/13:30/16:30/19:30 ET)",
        "workflow": "refresh_direct.yml",
        "schedule": {
            "timezone": "America/New_York",
            "hours":   [10, 13, 16, 19],
            "minutes": [30],
            "mdays":   [-1],
            "months":  [-1],
            "wdays":   [-1],   # every day including weekends
        },
    },
    # ── Cap digest — every 3 hours, 7 days a week ───────────────────────────
    # 8 AM uses data from the 5 AM overnight refresh (full yesterday + early today).
    # 11 AM, 2 PM, 5 PM, 8 PM each use the intraday refresh 30 min prior.
    {
        "repo":     "rogerhirano-nw/seller-comms",
        "title":    "seller-comms cap digest (daily 8:00/11:00/14:00/17:00/20:00 ET)",
        "workflow": "cap_digest.yml",
        "schedule": {
            "timezone": "America/New_York",
            "hours":   [8, 11, 14, 17, 20],
            "minutes": [0],
            "mdays":   [-1],
            "months":  [-1],
            "wdays":   [-1],   # every day including weekends
        },
    },
]

CRONJOB_API = "https://api.cron-job.org"


def _api(method: str, path: str, *, api_key: str, body: dict | None = None) -> dict:
    """Call the cron-job.org REST API. Returns parsed JSON (empty dict on 204)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{CRONJOB_API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"\n✗ cron-job.org API error: {method} {path}\n"
            f"  HTTP {e.code}: {msg}\n"
            f"  Most likely cause: CRONJOB_API_KEY is wrong or revoked.\n"
        ) from e


def _job_payload(*, repo: str, title: str, workflow: str, schedule: dict, github_pat: str) -> dict:
    """Build the cron-job.org job creation/update payload."""
    return {
        "job": {
            "url":           f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
            "enabled":       True,
            "title":         title,
            "saveResponses": True,         # keep response bodies for debugging
            "requestMethod": 1,            # 1 = POST
            "schedule":      schedule,
            "extendedData": {
                "headers": {
                    "Authorization":         f"Bearer {github_pat}",
                    "Accept":                "application/vnd.github+json",
                    "X-GitHub-Api-Version":  "2022-11-28",
                    "Content-Type":          "application/json",
                },
                "body": json.dumps({"ref": "main"}),
            },
            "notification": {
                "onFailure":      True,    # email me when GitHub dispatch fails
                "onSuccess":      False,
                "onDisable":      True,
            },
        }
    }


def _find_existing_job(api_key: str, title: str) -> int | None:
    """Return the jobId of an existing job with this title, or None."""
    resp = _api("GET", "/jobs", api_key=api_key)
    for j in resp.get("jobs", []):
        if j.get("title") == title:
            return j.get("jobId")
    return None


def _create_or_update(api_key: str, *, title: str, payload: dict) -> int:
    """Idempotent: PUT to create, PATCH to update an existing one."""
    existing = _find_existing_job(api_key, title)
    if existing:
        _api("PATCH", f"/jobs/{existing}", api_key=api_key, body=payload)
        print(f"  ↻ Updated existing job (id={existing})")
        return existing
    resp = _api("PUT", "/jobs", api_key=api_key, body=payload)
    job_id = resp.get("jobId")
    print(f"  ✓ Created new job (id={job_id})")
    return int(job_id)


def _fire_workflow_dispatch(github_pat: str, repo: str, workflow: str) -> int:
    """Smoke test: make the *exact same* call cron-job.org will make at
    schedule time, directly against GitHub. Returns the HTTP code.
    cron-job.org has no public 'run now' API endpoint — that button is
    UI-only — so we verify end-to-end against GitHub instead. This is
    actually a stronger test: if this 204s, cron-job.org will too."""
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    req = urllib.request.Request(
        url,
        data=json.dumps({"ref": "main"}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization":        f"Bearer {github_pat}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type":         "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"\n✗ GitHub workflow_dispatch failed: POST {url}\n"
            f"  HTTP {e.code}: {msg}\n"
            f"  Most likely cause: GITHUB_PAT is wrong, expired, or lacks\n"
            f"  Actions: write permission on this repo.\n"
        ) from e


def main() -> int:
    api_key    = os.environ.get("CRONJOB_API_KEY", "").strip()
    github_pat = os.environ.get("GITHUB_PAT",      "").strip()
    if not api_key or not github_pat:
        print(__doc__)
        print("✗ Missing CRONJOB_API_KEY and/or GITHUB_PAT env vars.")
        return 2

    repos = sorted({j["repo"] for j in JOBS})
    print(f"Provisioning {len(JOBS)} cron-job.org jobs across {len(repos)} repos:\n  - "
          + "\n  - ".join(repos) + "\n")
    created_ids: list[tuple[str, int]] = []
    for spec in JOBS:
        print(f"• {spec['title']}")
        payload = _job_payload(
            repo=spec["repo"],
            title=spec["title"],
            workflow=spec["workflow"],
            schedule=spec["schedule"],
            github_pat=github_pat,
        )
        job_id = _create_or_update(api_key, title=spec["title"], payload=payload)
        created_ids.append((spec["title"], job_id))

    print("\nSmoke test — making the same workflow_dispatch call directly\n"
          "against GitHub that cron-job.org will make at schedule time…\n")
    for spec in JOBS:
        print(f"• {spec['repo']} / {spec['workflow']}")
        code = _fire_workflow_dispatch(github_pat, spec["repo"], spec["workflow"])
        ok = "✓" if code == 204 else "✗"
        print(f"  {ok} GitHub returned HTTP {code} (expect 204)")

    print("\nGive GitHub ~10 seconds to register the dispatches, then "
          "verify with:\n")
    for spec in JOBS:
        print(f"  gh run list --workflow={spec['workflow']} --limit=1 -R {spec['repo']}")
    print("\nAll should show event=workflow_dispatch, status=queued or in_progress.\n")

    # Give GitHub a moment, then list the runs for the user.
    time.sleep(8)
    print("Recent runs (live from gh CLI):\n")
    for spec in JOBS:
        os.system(f"gh run list --workflow={spec['workflow']} --limit=1 -R {spec['repo']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
