"""
CSV writing, GitHub publish (git commit + push), and URL verification.
"""

from __future__ import annotations

import csv
import hashlib
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests
import structlog

from .models import Payload, UnhealthyDeal

log = structlog.get_logger(__name__)

# ── CSV columns (full, unredacted) ─────────────────────────────────────────

CSV_COLUMNS = (
    "Seller", "SSP", "DSP", "Agency", "Advertiser",
    "Deal Type", "Vertical", "Geo", "Format", "Floor",
    "Bid Requests", "Days in Data", "First Seen", "Raw Deal Name",
)


def _row_for(d: UnhealthyDeal) -> dict[str, str]:
    p = d.parsed
    return {
        "Seller":         p.seller or "Unknown",
        "SSP":            d.source_ssp,
        "DSP":            p.dsp or "",
        "Agency":         p.agency or p.agency_holdco or "",
        "Advertiser":     p.advertiser or "",
        "Deal Type":      p.deal_type or "",
        "Vertical":       p.vertical or "",
        "Geo":            p.geo or "",
        "Format":         p.ad_format or "",
        "Floor":          p.floor or "",
        "Bid Requests":   str(d.bid_requests),
        "Days in Data":   str(d.days_in_data),
        "First Seen":     d.first_seen or "",
        "Raw Deal Name":  p.raw,
    }


def _sort_key(r: dict[str, str]) -> tuple[str, str, str, int]:
    # seller, ssp, dsp, -bid_requests
    return (r["Seller"], r["SSP"], r["DSP"], -int(r["Bid Requests"] or 0))


# ── public CSV writer ──────────────────────────────────────────────────────

def write_csv(payload: Payload, output_path: Path) -> Path:
    """Write the full unredacted CSV to output_path. Returns the path."""
    rows = [_row_for(d) for d in payload.deals]
    rows.sort(key=_sort_key)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    log.info("csv written", path=str(output_path), rows=len(rows))
    return output_path


def csv_filename_for(report_date) -> str:
    return f"weekly_deal_health_{report_date.isoformat()}.csv"


# ── GitHub URL construction ────────────────────────────────────────────────

def build_csv_url(filename: str) -> str:
    """raw.githubusercontent.com URL for the published CSV. Org/repo, branch,
    and path come from env vars (see README)."""
    org_repo = os.environ.get("GH_ORG_REPO", "rogerhirano-nw/yield-dashboard")
    branch   = os.environ.get("GH_BRANCH",  "main")
    path     = os.environ.get("REPORTS_PATH", "reports").strip("/")
    return f"https://raw.githubusercontent.com/{org_repo}/{branch}/{path}/{filename}"


# ── git commit + push ──────────────────────────────────────────────────────

def _file_sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_git(*args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


def commit_and_push(
    filepath: Path,
    commit_message: str,
    *,
    repo_dir: Optional[Path] = None,
    user_email: str = "yield-dashboard@newsweek.com",
    user_name: str  = "yield-dashboard",
    dry_run: bool = False,
) -> bool:
    """
    Stage and commit the file via subprocess git. Returns True if a commit
    was made, False if nothing changed (idempotent on identical content).

    A service-account identity is set per-commit (-c flag) so the global
    config is untouched.
    """
    repo_dir = repo_dir or filepath.parent.parent
    if dry_run:
        log.info("dry_run skip commit", path=str(filepath))
        return False

    # If the file is unchanged in the index, there's nothing to commit.
    diff = subprocess.run(
        ["git", "diff", "--quiet", "--", str(filepath)],
        cwd=str(repo_dir),
        check=False,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(filepath)],
        cwd=str(repo_dir),
        check=False,
        capture_output=True,
    )
    file_is_new = untracked.returncode != 0
    if diff.returncode == 0 and not file_is_new:
        log.info("no change, skipping commit", path=str(filepath))
        return False

    _run_git("add", str(filepath), cwd=repo_dir)
    _run_git(
        "-c", f"user.email={user_email}",
        "-c", f"user.name={user_name}",
        "commit", "-m", commit_message,
        cwd=repo_dir,
    )
    # Push is best-effort — branch protection or a transient network issue
    # shouldn't take down the rest of the report (email send, etc.). The
    # commit stays on the runner's local clone; we surface the push outcome
    # via the return value.
    try:
        _run_git("push", "origin", "HEAD", cwd=repo_dir)
    except subprocess.CalledProcessError as e:
        log.warning(
            "push failed (commit kept locally; CSV won't be at raw URL until pushed)",
            path=str(filepath),
            stderr=(e.stderr or "")[:300],
        )
        return False
    log.info("pushed", path=str(filepath))
    return True


# ── verify the raw URL responds 200 ────────────────────────────────────────

def verify_url(url: str, *, max_attempts: int = 3, base_delay: float = 5.0) -> bool:
    """HEAD the raw URL up to max_attempts times with exponential backoff
    (5s, 15s, 45s). Returns True on first 200 hit. GitHub's raw CDN takes
    a few seconds to propagate after a push."""
    delay = base_delay
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.head(url, allow_redirects=True, timeout=15)
            log.info("verify attempt", attempt=attempt, status=resp.status_code, url=url)
            if 200 <= resp.status_code < 300:
                return True
        except requests.RequestException as e:
            log.warning("verify exception", attempt=attempt, error=str(e))
        if attempt < max_attempts:
            time.sleep(delay)
            delay *= 3
    return False
