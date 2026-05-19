"""
ConfiantClient — parses Confiant's "Alert Log CSV By Provider" export and
optionally pulls the latest one out of the agentmail.to inbox where Confiant
forwards them weekly.

Confiant CSV columns (UTF-8 BOM):
  Provider, Issue Category, Issue Type, Detail, Flagged Impressions,
  Blocked Impressions, Detected Only Impressions, Est. CPM, Last Seen,
  Current Rule Status, Ad Trace URL

The Detail column holds the blockable identifier:
  *.example.com      domain glob          -> push to GAM Protection
  example.com        bare domain          -> push to GAM Protection
  https://full/url   landing URL          -> hostname pushed to GAM Protection
  ID 17830           Confiant creative ID -> NOT blockable in GAM; surfaced for review
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


# ── data shapes ───────────────────────────────────────────────────────────────

@dataclass
class FlaggedRow:
    provider: str
    issue_type: str
    detail: str
    flagged_impressions: int
    blocked_impressions: int
    last_seen: str
    rule_status: str
    adtrace_url: str


@dataclass
class ConfiantReport:
    rows: list[FlaggedRow]
    csv_path: str

    @property
    def google_rows(self) -> list[FlaggedRow]:
        return [r for r in self.rows if r.provider == "Google"]

    def blockable_domains(self) -> tuple[list[tuple[str, FlaggedRow]], list[FlaggedRow]]:
        """Split Google rows into (blockable, unblockable).

        Returns:
          blockable:   list of (domain, source_row) pairs, one per row that resolved
          unblockable: list of rows whose Detail can't be turned into a domain
                       (typically Confiant-internal creative IDs from Cloaked ads)
        """
        blockable: list[tuple[str, FlaggedRow]] = []
        unblockable: list[FlaggedRow] = []
        for row in self.google_rows:
            domain = _detail_to_domain(row.detail)
            if domain:
                blockable.append((domain, row))
            else:
                unblockable.append(row)
        return blockable, unblockable


# ── CSV parsing ───────────────────────────────────────────────────────────────

_EXPECTED_COLS = {
    "Provider", "Issue Type", "Detail",
    "Flagged Impressions", "Blocked Impressions",
    "Last Seen", "Current Rule Status", "Ad Trace URL",
}


def parse_csv(path: str | Path) -> ConfiantReport:
    path = Path(path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = _EXPECTED_COLS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Confiant CSV at {path} missing expected columns: {sorted(missing)}. "
                f"Got: {reader.fieldnames}"
            )
        rows = [
            FlaggedRow(
                provider=r["Provider"],
                issue_type=r["Issue Type"],
                detail=r["Detail"].strip(),
                flagged_impressions=_to_int(r.get("Flagged Impressions")),
                blocked_impressions=_to_int(r.get("Blocked Impressions")),
                last_seen=r.get("Last Seen", ""),
                rule_status=r.get("Current Rule Status", ""),
                adtrace_url=r.get("Ad Trace URL", ""),
            )
            for r in reader
        ]
    return ConfiantReport(rows=rows, csv_path=str(path))


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
    Returns None if the Detail isn't a domain (e.g. 'ID 17830').
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


# ── optional: pull latest Confiant CSV from agentmail.to inbox ────────────────

def fetch_latest_csv_from_inbox(
    save_dir: str | Path,
    subject_contains: str = "Confiant",
    sender_contains: str = "confiant.com",
) -> Path:
    """Find the most recent Confiant email in the agentmail inbox and download
    its CSV attachment to `save_dir`. Returns the saved path.

    Env vars: AGENTMAIL_API_KEY, AGENTMAIL_INBOX_ID.

    Raises FileNotFoundError if no matching email is found.
    """
    from agentmail import AgentMail  # noqa: import deferred for fast --dry-run paths

    client = AgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
    inbox_id = os.environ["AGENTMAIL_INBOX_ID"]

    messages = client.inboxes.messages.list(inbox_id, limit=50)
    match = None
    for msg in _iter_messages(messages):
        subject = (getattr(msg, "subject", "") or "").lower()
        from_addr = (getattr(msg, "from_", None) or getattr(msg, "from", "") or "").lower()
        if subject_contains.lower() in subject and (
            not sender_contains or sender_contains.lower() in from_addr
        ):
            match = msg
            break

    if match is None:
        raise FileNotFoundError(
            f"No agentmail message found in inbox {inbox_id} matching "
            f"subject~={subject_contains!r}, from~={sender_contains!r}"
        )

    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    full = client.inboxes.messages.get(inbox_id, match.id)
    for att in getattr(full, "attachments", []) or []:
        filename = getattr(att, "filename", "") or ""
        if filename.lower().endswith(".csv"):
            data = client.inboxes.messages.attachments.download(
                inbox_id, match.id, att.id
            )
            out = save_dir / filename
            out.write_bytes(data)
            return out

    raise FileNotFoundError(
        f"Confiant message {match.id} found but has no .csv attachment"
    )


def _iter_messages(messages_response) -> Iterable:
    """Agentmail's list response shape varies; normalize to an iterable of messages."""
    for attr in ("messages", "data", "items"):
        v = getattr(messages_response, attr, None)
        if v is not None:
            return v
    return messages_response
