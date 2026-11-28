"""Send an alert/digest as adops@newsweek.com through the AdOps Power Automate flow.

TEMPORARY bridge off agentmail (2026-11-28) until the reporting@ Graph app
(graph_mail.py, PR #430) gets IT's admin consent. The flow ("Report mail as
AdOps - apple-news (attachments)") sends with From = adops@newsweek.com under
Roger's Outlook connection, so it stops working when his account closes;
callers therefore fall back to agentmail on any error.

Env: MAIL_WEBHOOK_URL (the flow's trigger URL). Unset = not configured.
The flow answers 202 once it has accepted the request, before the email is
sent, so a failure inside the flow is not visible here.
"""
from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.request


def configured() -> bool:
    return bool(os.environ.get("MAIL_WEBHOOK_URL", "").strip())


def text_to_html(text: str) -> str:
    """Plain text → HTML that reads like a plain-text email (client default font)."""
    return '<div style="white-space:pre-wrap">' + html.escape(text) + "</div>"


def send(to: list[str], subject: str, text: str, timeout: int = 30) -> None:
    url = os.environ["MAIL_WEBHOOK_URL"].strip()
    body = json.dumps({"to": list(to), "cc": [], "subject": subject,
                       "html": text_to_html(text)}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status >= 300:
                raise RuntimeError(f"AdOps mail flow returned HTTP {r.status}")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode(errors="replace")[:300]
        except Exception:  # noqa: BLE001 — no body to read
            detail = ""
        raise RuntimeError(f"AdOps mail flow HTTP {e.code}: {detail}") from e
