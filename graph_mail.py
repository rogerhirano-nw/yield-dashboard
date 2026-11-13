"""Read and send mail through a Newsweek Microsoft 365 mailbox (Graph API).

This is the Microsoft replacement for the agentmail.to inbox
(`newsweek@agentmail.to`). It talks to one shared mailbox — intended to be
**reporting@newsweek.com** — with an app-only (client-credentials) token, so
nothing depends on any one person's login.

It is OPT-IN. Every caller checks `enabled()` first and, when it is False,
runs its existing agentmail code unchanged. `enabled()` is True only when
`GRAPH_MAILBOX` and a full set of app credentials are present, so adding the
secrets to a workflow is what switches that workflow over — and removing
`GRAPH_MAILBOX` switches it back.

Env:
    GRAPH_MAILBOX        the shared mailbox, e.g. reporting@newsweek.com
    GRAPH_TENANT_ID      Entra tenant  (falls back to ENTRA_TENANT_ID)
    GRAPH_CLIENT_ID      app (client) id (falls back to ENTRA_CLIENT_ID)
    GRAPH_CLIENT_SECRET  client secret   (falls back to ENTRA_CLIENT_SECRET)

Permissions the app needs (granted by a Newsweek M365 admin):
    Microsoft Graph → Application → Mail.Read  (incoming reports)
    Microsoft Graph → Application → Mail.Send  (digests / alerts)
  scoped to reporting@ only with an Exchange Application Access Policy
  (New-ApplicationAccessPolicy -AccessRight RestrictAccess), so the app
  cannot read or send as any other mailbox.

Reads never modify the mailbox: nothing is moved, flagged, marked read or
deleted. Sent mail is saved to the mailbox's Sent Items.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
_SELECT = "id,subject,from,receivedDateTime,sentDateTime,hasAttachments,internetMessageId"

_token: tuple[str, float] | None = None  # (access_token, expires_at)


# ── config ───────────────────────────────────────────────────────────────────

def _env(name: str) -> str:
    return (os.environ.get(f"GRAPH_{name}") or os.environ.get(f"ENTRA_{name}") or "").strip()


def mailbox() -> str:
    return (os.environ.get("GRAPH_MAILBOX") or "").strip()


def enabled() -> bool:
    """True when the shared mailbox and app credentials are all configured."""
    return bool(mailbox() and _env("TENANT_ID") and _env("CLIENT_ID") and _env("CLIENT_SECRET"))


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _access_token() -> str:
    global _token
    if _token and _token[1] - 60 > time.time():
        return _token[0]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": _env("CLIENT_ID"),
        "client_secret": _env("CLIENT_SECRET"),
        "scope": "https://graph.microsoft.com/.default",
    }).encode()
    url = f"https://login.microsoftonline.com/{_env('TENANT_ID')}/oauth2/v2.0/token"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Graph token request failed: HTTP {e.code} :: "
            f"{e.read().decode(errors='replace')[:400]}"
        ) from e
    _token = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)))
    return _token[0]


def _request(method: str, path: str, *, body: dict | None = None,
             headers: dict | None = None, raw: bool = False, retries: int = 3):
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    hdrs = {"Authorization": f"Bearer {_access_token()}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                content = r.read()
                if raw:
                    return content
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            # Graph throttles with 429 / 503 + Retry-After; back off and retry.
            if e.code in (429, 503, 504) and attempt < retries:
                wait = int(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                logger.warning("Graph %s %s → HTTP %s, retrying in %ss", method, path[:80], e.code, wait)
                time.sleep(min(wait, 60))
                continue
            raise RuntimeError(
                f"Graph {method} {url} failed: HTTP {e.code} {e.reason} :: "
                f"{e.read().decode(errors='replace')[:500]}"
            ) from e


def _mbx_path() -> str:
    return f"/users/{urllib.parse.quote(mailbox())}"


def _q(msg_id: str) -> str:
    return urllib.parse.quote(msg_id, safe="")


# ── read ─────────────────────────────────────────────────────────────────────

def _normalize(m: dict) -> dict:
    """Shape a Graph message like the agentmail records the clients expect."""
    sender = ((m.get("from") or {}).get("emailAddress") or {})
    return {
        "id": m.get("id"),
        "message_id": m.get("internetMessageId"),
        "subject": m.get("subject") or "",
        "from": sender.get("address") or "",
        "received_at": m.get("receivedDateTime") or "",
        "sent_at": m.get("sentDateTime") or m.get("receivedDateTime") or "",
        "has_attachments": bool(m.get("hasAttachments")),
    }


def list_messages(subject_contains: str, limit: int = 30) -> list[dict]:
    """Newest-first messages (any folder) whose subject contains the needle.

    Uses Graph's $search for the phrase, then re-checks the needle against
    the subject client-side ($search also matches body/sender). If $search
    errors or finds nothing, falls back to scanning the newest messages, so
    the search is an optimization, never a hard dependency.
    """
    needle = subject_contains.replace('"', "").strip()
    base = f"{_mbx_path()}/messages"
    attempts = [
        (f'{base}?$search={urllib.parse.quote(chr(34) + needle + chr(34))}'
         f'&$top={min(max(limit * 3, 25), 250)}&$select={_SELECT}', "search"),
        (f"{base}?$orderby=receivedDateTime desc&$top=250&$select={_SELECT}", "newest-250"),
    ]
    for path, label in attempts:
        try:
            raw = _request("GET", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graph %s listing failed (%s) — trying next", label, exc)
            continue
        msgs = [_normalize(m) for m in (raw.get("value") or [])]
        matches = [m for m in msgs if needle.lower() in m["subject"].lower()]
        logger.info("Graph %s: %d message(s) scanned in %s; %d match %r",
                    label, len(msgs), mailbox(), len(matches), needle)
        if matches:
            matches.sort(key=lambda m: m["received_at"], reverse=True)
            return matches[:limit]
    return []


def list_attachments(msg_id: str) -> list[dict]:
    """[{id, name, size}] for the message's file attachments."""
    raw = _request("GET", f"{_mbx_path()}/messages/{_q(msg_id)}/attachments"
                          f"?$select=id,name,size,contentType")
    return [{"id": a.get("id"), "name": a.get("name") or "", "size": a.get("size")}
            for a in (raw.get("value") or [])
            if a.get("@odata.type", "").endswith("fileAttachment")]


def attachment_bytes(msg_id: str, attachment_id: str) -> bytes:
    return _request("GET", f"{_mbx_path()}/messages/{_q(msg_id)}/attachments/"
                           f"{_q(attachment_id)}/$value", raw=True)


def message_text(msg_id: str) -> str:
    """Plain-text body. Asks Graph to convert HTML → text; strips tags as a
    fallback if it doesn't."""
    raw = _request("GET", f"{_mbx_path()}/messages/{_q(msg_id)}?$select=body",
                   headers={"Prefer": 'outlook.body-content-type="text"'})
    body = (raw.get("body") or {})
    content = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        content = re.sub(r"<[^>]+>", " ", content)
    return content


# ── send ─────────────────────────────────────────────────────────────────────

def _recipients(addrs: str | Iterable[str] | None) -> list[dict]:
    if not addrs:
        return []
    if isinstance(addrs, str):
        addrs = addrs.split(",")
    out = []
    for a in addrs:
        a = a.strip()
        if not a:
            continue
        # Accept RFC 5322 display names: "Jane Doe <jane@x.com>"
        mt = re.match(r'^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$', a)
        addr = {"address": mt.group(2).strip(), "name": mt.group(1).strip()} if mt else {"address": a}
        out.append({"emailAddress": addr})
    return out


def send_mail(to: str | Iterable[str], subject: str, *, html: str | None = None,
              text: str | None = None, cc: str | Iterable[str] | None = None,
              attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    """Send from the shared mailbox. `attachments` = [(filename, bytes, mime)]."""
    if html is None and text is None:
        raise ValueError("send_mail needs html or text")
    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML" if html is not None else "Text",
                 "content": html if html is not None else text},
        "toRecipients": _recipients(to),
    }
    if cc:
        message["ccRecipients"] = _recipients(cc)
    if attachments:
        message["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": mime,
            "contentBytes": base64.b64encode(data).decode(),
        } for name, data, mime in attachments]
    _request("POST", f"{_mbx_path()}/sendMail",
             body={"message": message, "saveToSentItems": True})
    logger.info("Graph: sent %r from %s", subject, mailbox())
