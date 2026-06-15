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

    subject_match = os.environ.get("INSPECT_SUBJECT", "Infiniti Ad Test").lower()
    ad_re = re.compile(r"gampad|securepubads|doubleclick|the-bulletin", re.I)
    target_detail = None
    previews = []
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
        subj = str(detail.get("subject") or m.get("subject") or "")
        blob = " ".join(str(detail.get(k) or "")
                        for k in ("html", "text", "extracted_text", "preview", "subject"))
        if len(previews) < 8:
            snip = (detail.get("preview") or detail.get("text") or "")[:160].replace("\n", " ")
            previews.append((subj, snip))
        if subject_match in subj.lower() or ad_re.search(blob):
            target_detail = detail
            break

    if not target_detail:
        print(f"::notice::No message matching subject {subject_match!r} (or a GAM ad "
              "tag) yet — the forward may not have landed.")
        if first_keys:
            print("detail keys:", first_keys)
        print("-- newest message previews --")
        for subj, snip in previews:
            print(f"   subject={subj!r}\n     {snip}")
        return 0

    html_body = ""
    for k in ("html", "html_body", "body_html"):
        if isinstance(target_detail.get(k), str) and target_detail[k].strip():
            html_body = target_detail[k]
            break
    text_body = str(target_detail.get("text") or target_detail.get("extracted_text") or "")
    blob = html_body or text_body
    print("=" * 72)
    print(f"MATCHED MESSAGE  from={target_detail.get('from')}  "
          f"subject={target_detail.get('subject')!r}")
    print(f"has html: {bool(html_body)}  (html {len(html_body)} chars, text {len(text_body)} chars)")

    blocks = re.findall(r"<a\b[^>]*gampad[^>]*>.*?</a>", blob, re.I | re.S)
    if not blocks:
        blocks = re.findall(
            r"<img\b[^>]*src=[\"'][^\"']*(?:gampad|googleusercontent|beehiv)[^\"']*[\"'][^>]*>",
            blob, re.I)
    print(f"-- {len(blocks)} ad <a>/<img> block(s) --")
    for b in blocks[:4]:
        print(_html.unescape(b)[:1500])
        print("-" * 40)

    # any ad-server URL anywhere (catches plain-text jump links too)
    urls = re.findall(
        r"https?://[^\s\"'<>]*(?:gampad|securepubads|doubleclick|googleusercontent)[^\s\"'<>]*",
        blob, re.I)
    print(f"-- {len(urls)} ad-server URL(s) in body --")
    for u in list(dict.fromkeys(urls))[:8]:
        print("   " + _html.unescape(u)[:240])

    def flag(name, pat):
        m = re.search(pat, blob, re.I)
        print(f"{name}: {_html.unescape(m.group(0)) if m else 'NOT FOUND'}")

    flag("requested sz", r"sz=600x\d+")
    flag("url= param", r"[?&]url=[^\"'&\s]*")
    flag("clkk", r"clkk=[^\"'&\s]{0,80}")
    if re.search(r"\{\{[^}]+\}\}", blob):
        print("note: literal {{merge_tags}} still present (unresolved)")

    # newsletter background colors — to match the native-style backgrounds to
    from collections import Counter
    print("-- background colors in the email (to match the ad styles to) --")
    bm = re.search(r"<body[^>]*>", blob, re.I)
    if bm:
        print("  <body> tag:", _html.unescape(bm.group(0))[:240])
    bgcolor = Counter(re.findall(r'bgcolor=["\']?(#[0-9a-fA-F]{3,6})', blob))
    bgstyle = Counter(re.findall(r'background(?:-color)?:\s*(#[0-9a-fA-F]{3,6})', blob))
    print("  bgcolor= values (most common):", bgcolor.most_common(8))
    print("  background[-color]: hex (most common):", bgstyle.most_common(8))

    # Is the native ad a rasterized <img>, or live HTML the newsletter styles?
    print("-- ad embedding + newsletter link styles --")
    n_adimg = len(re.findall(r"<img[^>]*gampad/ad", blob, re.I))
    live_sc = bool(re.search(r'class=["\']sc[-"\']', blob))
    live_head = bool(re.search(r"sc-headline|sc-link|\[%Headline%\]", blob))
    print(f"  ad served as <img gampad/ad>: {n_adimg}  |  live .sc/.sc-link HTML in body: {live_sc or live_head}")
    styles_txt = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", blob, re.I | re.S))
    arules = re.findall(r"(a[a-zA-Z0-9_\-:.\s,>]*\{[^}]*\})", styles_txt)
    link_rules = [r.strip() for r in arules
                  if "color" in r.lower() or "decoration" in r.lower()][:8]
    print("  <style> link rules:", link_rules or "(none)")
    inline_a = re.findall(r'<a[^>]*style="[^"]*(?:color|text-decoration)[^"]*"[^>]*>', blob, re.I)[:4]
    for a in inline_a:
        print("   inline <a>:", _html.unescape(a)[:160])

    # newsletter article typography + red accents, to match the native to
    print("-- newsletter typography + red accents --")
    fs = Counter(re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", blob, re.I))
    print("  font-size px (most common):", fs.most_common(14))
    red_any = re.findall(r"((?:color|border[-a-z]*|background[-a-z]*)\s*:\s*#e91d0c)", blob, re.I)
    print(f"  #e91d0c usages: {len(red_any)} ->", list(dict.fromkeys(r.lower() for r in red_any))[:8])
    red_rule = re.findall(r"border[-a-z]*\s*:[^;\"{}]*#e91d0c[^;\"{}]*", blob, re.I)
    print("  red border/rule:", list(dict.fromkeys(red_rule))[:6] or "(none)")
    for kw in ("Mamdani", "Nightmare", "rundown", "Politics"):
        i = blob.find(kw)
        if i != -1:
            print(f"  ctx around {kw!r}:",
                  _html.unescape(blob[max(0, i - 300):i + 30]).replace("\n", " ")[-320:])
            break

    if not html_body:
        print("\n-- text/extracted_text (first 3000 chars) --")
        print(text_body[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
