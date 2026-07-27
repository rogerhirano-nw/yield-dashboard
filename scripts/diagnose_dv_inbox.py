"""One-off diagnostic: why do DV Attention emails no longer yield a CSV?

Since ~2026-06-29 the sweep logs "No DV Attention CSV attachments found in
inbox" while the sibling IVT mails (same DV pipeline, same inbox) still carry
their CSVs. This lists recent DV Attention messages — with the IVT messages as
the working control — and prints STRUCTURAL metadata only: timestamps, auth
folder, attachment filenames/content-types/sizes, body sizes, and the
registrable domains of any links. The repo (and its Actions logs) is public,
so no subjects beyond the known constants, no bodies, no full/signed URLs.

Run via .github/workflows/diagnose_dv_inbox.yml (needs AGENTMAIL_* secrets),
or locally with those vars in the env.
"""
from __future__ import annotations

import os
import re

import dv_attention_client as att_client
import dv_ivt_client as ivt_client


def _link_domains(*texts: str) -> list[str]:
    doms = set()
    for text in texts:
        for m in re.finditer(r"https?://([^/\s\"'>]+)", text or ""):
            doms.add(m.group(1).lower())
    return sorted(doms)


def describe(client, api_key: str, inbox_id: str, messages: list[dict],
             label: str, max_msgs: int = 6) -> None:
    print(f"\n== {label}: {len(messages)} matching message(s)")
    for m in messages[:max_msgs]:
        mid = m.get("id") or m.get("message_id") or ""
        rec = dict(m)
        try:
            rec.update(client.get_message_detail(api_key, inbox_id, mid) or {})
        except Exception as e:  # noqa: BLE001 — diagnostic, keep going
            print(f"  [detail fetch failed for one message: {e}]")
        atts = rec.get("attachments") or []
        print(f"- ts={rec.get('sent_at') or rec.get('created_at')}  "
              f"unauth={bool(m.get('_unauthenticated'))}  "
              f"attachments={len(atts)}")
        for a in atts:
            print(f"    att: filename={a.get('filename') or a.get('name')!r}  "
                  f"content_type={a.get('content_type') or a.get('type')}  "
                  f"size={a.get('size')}  "
                  f"has_id={bool(a.get('id') or a.get('attachment_id'))}")
        text, html = rec.get("text") or "", rec.get("html") or ""
        print(f"    body: text_len={len(text)}  html_len={len(html)}  "
              f"link_domains={_link_domains(text, html)}")


def main() -> None:
    api_key = os.environ["AGENTMAIL_API_KEY"]
    inbox_id = os.environ["AGENTMAIL_INBOX_ID"]

    attention = att_client.list_dv_attention_messages(api_key, inbox_id, limit=10)
    describe(att_client, api_key, inbox_id, attention, "DV Attention (broken since ~6/29)")

    ivt = ivt_client.list_dv_ivt_messages(api_key, inbox_id, limit=4)
    describe(ivt_client, api_key, inbox_id, ivt, "DV IVT (working control)")


if __name__ == "__main__":
    main()
