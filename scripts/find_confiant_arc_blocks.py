"""List the GAM Ad Review Center blocks the Confiant cron reported in a window.

Read-only. `confiant_blocklist.py` (launchd on a Mac) blocks Cloaked
`Detail = ID xxxxx` creatives in GAM ARC and records them only in a local
`state.sqlite` — and, per run, in its daily summary email ("GAM blocklist · …"),
sent from the agentmail inbox. When the Mac's state file isn't reachable, those
sent emails are the remaining record. This script lists them and pulls the
"GAM Ad Review Center — manual blocks" table (Confiant ID → GPT Ad Response ID)
out of each.

ARC itself keeps no block history, so this is how to answer "did the
automation block that creative, and when": match a GPT Ad Response ID printed
here against the creative in ARC (filter "Ad response ID:").

Output is public (Actions logs on a public repo): subjects, send dates and the
IDs only — never recipients or other inbox mail.

Usage:
  AGENTMAIL_API_KEY=… AGENTMAIL_INBOX_ID=… \
      python scripts/find_confiant_arc_blocks.py --start 2026-09-01 --end 2026-10-01
"""

from __future__ import annotations

import argparse
import html as htmllib
import os
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ttd_client import _api_get, _messages_from, _msg_ts, get_message_detail  # noqa: E402

SUBJECT_NEEDLE = "GAM blocklist"
ARC_HEAD_RE = re.compile(r"GAM Ad Review Center\s*(?:&mdash;|—)\s*([^<]+)</h2>", re.I)
# One row of the ARC blocks table: Confiant ID cell, then GPT Ad Response ID cell.
ARC_ROW_RE = re.compile(
    r"<tr><td[^>]*>\s*([^<\s]+)\s*</td>\s*<td[^>]*word-break:break-all[^>]*>\s*([^<\s]+)\s*</td>\s*</tr>",
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")


def _list_all(api_key: str, inbox_id: str, start: str, max_pages: int) -> list[dict]:
    """Page through the inbox (subject-filtered server-side, re-checked here)
    until messages are older than `start`."""
    subj = urllib.parse.quote(SUBJECT_NEEDLE, safe="")
    out: dict[str, dict] = {}
    for extra, label in ((f"&subject={subj}", "subject-filtered"), ("", "unfiltered")):
        token = None
        for _ in range(max_pages):
            path = f"/inboxes/{inbox_id}/messages?limit=100{extra}"
            if token:
                path += f"&page_token={urllib.parse.quote(token, safe='')}"
            try:
                raw = _api_get(path, api_key=api_key)
            except Exception as exc:  # noqa: BLE001
                print(f"[{label}] listing failed: {exc}", file=sys.stderr)
                break
            msgs = _messages_from(raw)
            for m in msgs:
                if SUBJECT_NEEDLE in (m.get("subject") or ""):
                    out[m.get("message_id") or m.get("id")] = m
            token = raw.get("next_page_token") if isinstance(raw, dict) else None
            oldest = min((_msg_ts(m) for m in msgs if _msg_ts(m)), default="")
            if not token or not msgs or (oldest and oldest[:10] < start):
                break
        print(f"[{label}] {len(out)} '{SUBJECT_NEEDLE}' message(s) so far", file=sys.stderr)
        if out:
            break
    return list(out.values())


def _body(detail: dict) -> str:
    return detail.get("html") or detail.get("extracted_html") or detail.get("text") or ""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-09-01")
    p.add_argument("--end", default="2026-10-01", help="exclusive")
    p.add_argument("--max-pages", type=int, default=40)
    p.add_argument("--find", default="", help="optional string to search each email for")
    a = p.parse_args()

    key, inbox = os.environ["AGENTMAIL_API_KEY"], os.environ["AGENTMAIL_INBOX_ID"]
    msgs = [m for m in _list_all(key, inbox, a.start, a.max_pages)
            if a.start <= _msg_ts(m)[:10] < a.end]
    msgs.sort(key=_msg_ts)
    print(f"{len(msgs)} '{SUBJECT_NEEDLE}' email(s) sent {a.start} .. <{a.end}\n")

    blocks: list[tuple[str, str, str]] = []
    for m in msgs:
        mid = m.get("message_id") or m.get("id")
        ts = _msg_ts(m)[:16]
        subject = (m.get("subject") or "").strip()
        try:
            body = _body(get_message_detail(key, inbox, mid))
        except Exception as exc:  # noqa: BLE001
            print(f"{ts}  {subject}\n    (detail fetch failed: {exc})")
            continue
        head = ARC_HEAD_RE.search(body)
        arc = htmllib.unescape(head.group(1).strip()) if head else "no ARC section"
        rows = ARC_ROW_RE.findall(body) if head else []
        print(f"{ts}  {subject}\n    ARC: {arc}")
        for cid, gpt in rows:
            print(f"      Confiant {cid} -> GPT {gpt}")
            blocks.append((ts, cid, gpt))
        if a.find and a.find in htmllib.unescape(TAG_RE.sub(" ", body)):
            print(f"    !! contains {a.find!r}")

    print(f"\nTotal ARC blocks reported in window: {len(blocks)}")
    for ts, cid, gpt in blocks:
        print(f"{ts}\t{cid}\t{gpt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
