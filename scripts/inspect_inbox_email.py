#!/usr/bin/env python
"""Inspect a forwarded email in the newsweek@agentmail.to inbox.

Diagnoses why the newsletter ad renders in Beehiv preview (a live browser
render) but not in a delivered test email (a static image the mail client
fetches, often through a proxy). Reads AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID
from env (the workflow sets them from repo secrets — cloud sessions hold no
creds, same pattern as the DV pull).

Lists recent messages, finds the one carrying the GAM newsletter ad tag
(gampad / securepubads / the-bulletin), prints the ad block, and parses the
tell-tale flags: the requested `sz`, whether `url=` is present, whether `clkk`
resolved (vs. literal {{merge_tags}}), and — the key one — whether the <img>
`src` is the raw ad-server host or a **proxied / rehosted** copy (Beehiv CDN,
googleusercontent, etc.), which is what silently breaks dynamic ad tags in
delivered mail.
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

BASE = "https://api.agentmail.to/v0"


def api_get(path: str, api_key: str):
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def msg_list(d):
    if isinstance(d, dict):
        return d.get("messages") or d.get("data") or []
    return d or []


def body_html(detail: dict) -> str:
    if not isinstance(detail, dict):
        return ""
    for k in ("html", "html_body", "body_html", "body", "text", "snippet", "preview"):
        v = detail.get(k)
        if isinstance(v, str) and v.strip():
            return v
    for k in ("message", "data"):
        sub = detail.get(k)
        if isinstance(sub, dict):
            r = body_html(sub)
            if r:
                return r
    return ""


def main() -> int:
    api_key = os.environ.get("AGENTMAIL_API_KEY")
    inbox = os.environ.get("AGENTMAIL_INBOX_ID")
    if not api_key or not inbox:
        print("::error::AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID not set", file=sys.stderr)
        return 2
    limit = int(os.environ.get("INSPECT_LIMIT", "30"))
    lst = msg_list(api_get(
        f"/inboxes/{inbox}/messages?limit={limit}&include_unauthenticated=true", api_key))
    print(f"== {len(lst)} recent message(s) in {inbox} ==")
    for m in lst:
        print(f"  - id={m.get('id') or m.get('message_id')}  "
              f"date={m.get('timestamp') or m.get('date') or m.get('created_at')}  "
              f"from={m.get('from') or m.get('sender')}  subject={m.get('subject')!r}")

    ad_re = re.compile(r"gampad|securepubads|doubleclick|the-bulletin", re.I)
    target = None
    target_html = ""
    previews = []  # (subject, snippet) for the no-match dump
    first_keys = None
    for m in lst:
        mid = m.get("id") or m.get("message_id")
        if not mid:
            continue
        try:
            detail = api_get(
                f"/inboxes/{inbox}/messages/{urllib.parse.quote(mid, safe='')}", api_key)
        except Exception as e:  # noqa: BLE001
            print(f"  (detail fetch failed for {mid}: {e})")
            continue
        if first_keys is None and isinstance(detail, dict):
            first_keys = sorted(detail.keys())
        blob = " ".join(str(detail.get(k) or "")
                        for k in ("html", "text", "extracted_text", "preview", "subject"))
        if len(previews) < 8:
            snip = (detail.get("preview") or detail.get("text") or "")[:160].replace("\n", " ")
            previews.append((detail.get("subject"), snip))
        if ad_re.search(blob):
            target = m
            target_html = body_html(detail) or str(detail.get("text") or blob)
            break

    if not target:
        print("::notice::No message with a GAM ad tag found yet — the forward may "
              "not have arrived, or its content is in a field/folder not scanned.")
        if first_keys:
            print("detail keys:", first_keys)
        print("-- newest message previews --")
        for subj, snip in previews:
            print(f"   subject={subj!r}\n     {snip}")
        return 0

    print("=" * 72)
    print(f"AD-BEARING MESSAGE  from={target.get('from') or target.get('sender')}  "
          f"subject={target.get('subject')!r}")

    blocks = re.findall(r"<a\b[^>]*gampad[^>]*>.*?</a>", target_html, re.I | re.S)
    if not blocks:
        blocks = re.findall(
            r"<img\b[^>]*(?:gampad|googleusercontent|beehiv|mailchimp)[^>]*>",
            target_html, re.I)
    print(f"-- {len(blocks)} ad block(s) --")
    for b in blocks[:4]:
        print(_html.unescape(b)[:1600])
        print("-" * 40)

    srcs = re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', target_html, re.I)
    print("all <img> src hosts:")
    for s in srcs[:20]:
        host = urllib.parse.urlparse(_html.unescape(s)).netloc
        print(f"   {host or '(relative)'}   {_html.unescape(s)[:130]}")

    def flag(name, pat):
        m = re.search(pat, target_html, re.I)
        print(f"{name}: {_html.unescape(m.group(0)) if m else 'NOT FOUND'}")

    flag("requested sz", r"sz=600x\d+")
    flag("url= param", r"[?&]url=[^\"'&]*")
    flag("clkk", r"clkk=[^\"'&]{0,80}")
    if re.search(r"\{\{[^}]+\}\}", target_html):
        print("note: literal {{merge_tags}} still present in body (unresolved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
