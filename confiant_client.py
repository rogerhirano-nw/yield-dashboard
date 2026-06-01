"""
ConfiantClient — fetches and parses Confiant's flagged-ad reports.

Two CSV shapes are supported:

  1. The Confiant API's ``issue_type_by_domain`` report (preferred).
     Columns: Domain, Property, Provider, Issue Category, Issue Type,
              Detail, Flagged Impressions, Ad Trace, Last Seen.
     One row per ad-trace detection, so multiple rows can share a Detail.

  2. Legacy: the "Alert Log CSV By Provider" export that Confiant emails.
     Columns: Provider, Issue Category, Issue Type, Detail, Flagged
              Impressions, Blocked Impressions, Detected Only Impressions,
              Est. CPM, Last Seen, Current Rule Status, Ad Trace URL.
     Already grouped at the per-creative level.

The parser detects which shape it's looking at from the column set and
normalizes both into a single ``ConfiantReport``.

The Detail column holds the blockable identifier (same in both shapes):
  *.example.com      domain glob          -> push to GAM Protection
  example.com        bare domain          -> push to GAM Protection
  https://full/url   landing URL          -> hostname pushed to GAM Protection
  ID 17830           Confiant creative ID -> NOT blockable in GAM; surfaced for review
  DoubleVerify       brand/advertiser     -> NOT blockable here; surfaced for review
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


# ── data shapes ───────────────────────────────────────────────────────────────

@dataclass
class FlaggedRow:
    provider: str
    issue_category: str
    issue_type: str
    detail: str
    flagged_impressions: int
    last_seen: str
    adtrace_url: str


@dataclass
class ConfiantReport:
    rows: list[FlaggedRow]
    source: str  # path or "api://issue_type_by_domain/<hash>"

    @property
    def google_rows(self) -> list[FlaggedRow]:
        return [r for r in self.rows if r.provider == "Google"]

    def blockable_domains(
        self,
        categories: Iterable[str] = ("Security",),
    ) -> tuple[list[tuple[str, FlaggedRow]], list[FlaggedRow]]:
        """Split Google rows into (blockable, unblockable).

        Args:
          categories: Which Issue Category values to consider for blocking.
                      Defaults to Security only (matches what was historically
                      in the weekly outreach email). Pass ("Security", "Quality")
                      to also block annoying-but-not-malicious creatives.

        Returns:
          blockable:   list of (domain, source_row) pairs. Deduplicated by
                       domain — if the same domain appears in multiple rows
                       within the same category set, the first wins.
          unblockable: list of in-category rows whose Detail can't be turned
                       into a domain (creative IDs, brand names).
        """
        wanted = {c.lower() for c in categories}
        seen_domains: dict[str, FlaggedRow] = {}
        unblockable: list[FlaggedRow] = []
        for row in self.google_rows:
            if row.issue_category.lower() not in wanted:
                continue
            domain = _detail_to_domain(row.detail)
            if domain:
                seen_domains.setdefault(domain, row)
            else:
                unblockable.append(row)
        return [(d, r) for d, r in seen_domains.items()], unblockable


# ── CSV parsing ───────────────────────────────────────────────────────────────

# Either shape must contain these columns for the parser to accept the file.
_REQUIRED_COLS = {"Provider", "Issue Category", "Issue Type", "Detail"}

# Column-name aliases between the API and email CSV variants. Both are normalized
# to the API name on the right.
_COL_ALIASES = {
    "Ad Trace URL": "Ad Trace",  # email -> api
}


def parse_csv(path: str | Path) -> ConfiantReport:
    text = Path(path).read_text(encoding="utf-8-sig")
    return _parse_csv_text(text, source=str(path))


def _parse_csv_text(text: str, source: str) -> ConfiantReport:
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    # Normalize column-name aliases.
    aliased = {_COL_ALIASES.get(c, c) for c in fieldnames}

    missing = _REQUIRED_COLS - aliased
    if missing:
        raise ValueError(
            f"Confiant CSV at {source} missing required columns: {sorted(missing)}. "
            f"Got: {fieldnames}"
        )

    def _get(row: dict, key: str) -> str:
        """Read by canonical key, falling back to email-CSV alias."""
        if key in row:
            return (row[key] or "").strip()
        for alias, canonical in _COL_ALIASES.items():
            if canonical == key and alias in row:
                return (row[alias] or "").strip()
        return ""

    rows = [
        FlaggedRow(
            provider=_get(r, "Provider"),
            issue_category=_get(r, "Issue Category"),
            issue_type=_get(r, "Issue Type"),
            detail=_get(r, "Detail"),
            flagged_impressions=_to_int(_get(r, "Flagged Impressions")),
            last_seen=_get(r, "Last Seen"),
            adtrace_url=_get(r, "Ad Trace"),
        )
        for r in reader
    ]
    return ConfiantReport(rows=rows, source=source)


def _to_int(v: str | None) -> int:
    if not v:
        return 0
    try:
        return int(str(v).replace(",", ""))
    except ValueError:
        return 0


# ── Detail -> domain resolution ───────────────────────────────────────────────

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def _detail_to_domain(detail: str) -> str | None:
    """Turn a Confiant Detail value into a domain pushable to GAM Protections.

    Returns the bare domain (no scheme, no path, no leading '*.'), lowercased.
    Returns None if the Detail isn't a domain (e.g. 'ID 17830', 'DoubleVerify').
    """
    d = detail.strip()
    if not d:
        return None
    if d.startswith("ID ") or d.startswith("id "):
        return None
    if d.startswith("*."):
        d = d[2:]
    if d.startswith(("http://", "https://", "//")):
        host = urlparse(d if "://" in d else "https:" + d).hostname
        return host.lower() if host else None
    d = d.lower()
    if _DOMAIN_RE.match(d):
        return d
    return None


# ── Confiant REST API ────────────────────────────────────────────────────────

_API_BASE = "https://api.app.confiant.com/rest/1"
_POLL_INTERVAL_S = 3
_POLL_TIMEOUT_S = 180


def fetch_via_api(
    days: int = 7,
    report_type: str = "issue_type_by_domain",
    min_impressions: int = 1,
    property_id: str | None = None,
) -> ConfiantReport:
    """Pull a Confiant report via the REST API and parse it.

    Reads CONFIANT_API_KEY from env. Hits ``POST /reports/<type>``, polls
    ``GET /report/<type>/<hash>`` until ready, returns the parsed report.

    Args:
      days:            Lookback window in days (default 7 = last week).
      report_type:     One of the report types Confiant supports. Default
                       ``issue_type_by_domain`` returns one row per ad-trace
                       detection with Provider/Domain/Issue Type/Detail/etc.
      min_impressions: Filter param passed to the API. Default 1 = include
                       even single-impression detections.
      property_id:     Optional Confiant property ID filter; default None
                       returns all properties under the account.
    """
    import requests

    api_key = os.environ.get("CONFIANT_API_KEY")
    if not api_key:
        raise RuntimeError(
            "CONFIANT_API_KEY env var not set; can't pull from Confiant API. "
            "Add it to ~/code/yield-dashboard/.env or set in launchd plist."
        )

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    date_to = now.replace(hour=23, minute=59, second=59, microsecond=0)
    date_from = (date_to - timedelta(days=days)).replace(hour=0, minute=0, second=0)
    body = {
        "date_from": date_from.strftime("%Y-%m-%d %H:%M:%S"),
        "date_to": date_to.strftime("%Y-%m-%d %H:%M:%S"),
        "property_id": property_id,
        "min_impressions": min_impressions,
    }
    headers = {"X-Auth-Token": api_key, "Content-Type": "application/json"}

    # 1. Queue the report
    r = requests.post(
        f"{_API_BASE}/reports/{report_type}", json=body, headers=headers, timeout=30,
    )
    r.raise_for_status()
    pay = r.json()
    if not pay.get("success"):
        raise RuntimeError(f"Confiant API rejected request: {pay}")
    report_hash = pay["report_hash"]

    # 2. Poll until ready
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        rr = requests.get(
            f"{_API_BASE}/report/{report_type}/{report_hash}",
            headers=headers, timeout=30,
        )
        rr.raise_for_status()
        rep = rr.json().get("report", {})
        status = rep.get("status")
        if status == "ready":
            csv_text = rep.get("csv") or ""
            return _parse_csv_text(
                csv_text,
                source=f"api://{report_type}/{report_hash}",
            )
        if status not in ("pending", "queued", "running"):
            raise RuntimeError(f"unexpected Confiant report status: {status}")
        time.sleep(_POLL_INTERVAL_S)

    raise TimeoutError(
        f"Confiant report {report_type}/{report_hash} not ready after "
        f"{_POLL_TIMEOUT_S}s"
    )
