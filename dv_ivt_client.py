"""
Pull DoubleVerify Pinnacle "IVT" (invalid traffic) reports from the
newsweek@agentmail.to inbox.

DV's team emails the report daily (subject:
"Unified Analytics Report: IVT") with the CSV attached. This module
polls the agentmail inbox, finds unprocessed matching emails,
downloads the CSV, parses it into a DataFrame, and hands back rows
ready for refresh_cache.py to write to the `dv_ivt` table.

CSV format (DV Pinnacle export):
    Lines 1-4: "# Report:", "# Start Date:", "# End Date:", "# Submit Time:"
    Line 5: blank
    Line 6: header row (8 columns)
    Lines 7+: data rows. Each (Date, Line Item) appears as MULTIPLE rows:
        one "Valid Traffic" + one or more "Fraud/SIVT" + one or more
        "Fraud/GIVT" rows, depending on classifier output. The three
        rate columns are TAUTOLOGICAL — they're 1.0 / 0.0 labels of
        which bucket the row falls into, not impression-weighted rates.

Columns (verbatim from the CSV header):
    Traffic Validity, Date, Advertiser, Order, Line Item,
    Fraud/SIVT Rate, GIVT Rate, IVT-Rate

This export DOES NOT include impression counts, so a true
impression-weighted IVT% per line can't be computed from this file
alone. The dashboard derives a day-prevalence proxy instead — see
`refresh_cache.refresh_dv_ivt` + the dashboard's _ivt_html column for
how the rate-per-line gets rendered.

Order and Line Item use the 14-field Newsweek GAM naming convention,
so they join cleanly to `gam_campaigns.line_item_name` /
`gam_pmp_deals.deal_name` (same as DV Attention).

The polling helpers mirror dv_attention_client.py rather than sharing
them — keeping each DV report's logic in its own file is easier to
maintain than a polymorphic super-module, and the duplication is
~40 lines of plain urllib.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

logger = logging.getLogger(__name__)

AGENTMAIL_BASE = "https://api.agentmail.to/v0"
DV_SUBJECT = "Unified Analytics Report: IVT"

# Verbatim CSV header → snake_case DB column. Keep stable.
#
# 2026-05-24: DV added 3 impression-count columns (Monitored Ads /
# Eligible Impressions / Total Calls) at our request. `Monitored Ads`
# is DV's standard "DV-measured impressions" metric and is the right
# basis for impression-weighted IVT% (the dashboard divides
# Σ Monitored Ads(Fraud rows) / Σ Monitored Ads(all rows)).
COLUMN_MAP = {
    "Traffic Validity":     "traffic_validity",
    "Date":                 "date",
    "Advertiser":           "advertiser",
    "Order":                "order_name",
    "Line Item":            "line_item_name",
    "Fraud/SIVT Rate":      "fraud_sivt_rate",
    "GIVT Rate":            "givt_rate",
    "IVT-Rate":             "ivt_rate",
    "Monitored Ads":        "monitored_ads",
    "Eligible Impressions": "eligible_impressions",
    "Total Calls":          "total_calls",
}


def _api_get(path: str, *, api_key: str, raw: bool = False):
    """GET /v0{path} with bearer auth. JSON by default; raw bytes for
    attachment downloads."""
    url = f"{AGENTMAIL_BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json" if not raw else "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read()
            return content if raw else json.loads(content)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"agentmail.to GET {url} failed: HTTP {e.code} {e.reason} :: {body[:500]}"
        ) from e


def list_dv_ivt_messages(api_key: str, inbox_id: str, limit: int = 30) -> list[dict]:
    """List recent messages matching the DV IVT subject.
    include_unauthenticated=true is required — DV's noreply sender lands in
    agentmail's Unauthenticated folder, which the default list call excludes.
    Newest first."""
    subject_enc = urllib.parse.quote(DV_SUBJECT, safe="")
    raw = _api_get(
        f"/inboxes/{inbox_id}/messages?limit={limit}"
        f"&subject={subject_enc}&include_unauthenticated=true",
        api_key=api_key,
    )
    messages = raw.get("messages") or raw.get("data") or [] if isinstance(raw, dict) else (raw or [])
    logger.info(
        "agentmail: scanned inbox (unauthenticated included); %d match DV IVT subject",
        len(messages),
    )
    return messages


def get_message_detail(api_key: str, inbox_id: str, message_id: str) -> dict:
    return _api_get(f"/inboxes/{inbox_id}/messages/{message_id}", api_key=api_key)


def fetch_attachment(api_key: str, inbox_id: str, message_id: str, attachment_id: str) -> bytes:
    """Download one attachment as raw bytes using its UUID attachment_id.
    include_unauthenticated=true mirrors the list call — without it the server
    scopes the lookup to authenticated messages only and returns 404."""
    return _api_get(
        f"/inboxes/{inbox_id}/messages/{message_id}/attachments/{attachment_id}"
        "?include_unauthenticated=true",
        api_key=api_key, raw=True,
    )


def parse_dv_ivt_csv(content: bytes) -> pd.DataFrame:
    """Parse a DV Pinnacle IVT CSV into a DataFrame with snake_case columns.

    Scans for the 'Traffic Validity' header to skip the 4-line preamble.
    Tolerates UTF-8 BOM.
    """
    text = content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        stripped = line.lstrip("﻿").strip()
        if stripped.startswith('"Traffic Validity"') or stripped.startswith("Traffic Validity,"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "DV IVT CSV: could not locate 'Traffic Validity' header line. "
            f"First 5 non-empty lines: {[l for l in lines[:5] if l.strip()]}"
        )

    df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns=COLUMN_MAP)
    keep = [c for c in COLUMN_MAP.values() if c in df.columns]
    df = df[keep].copy()

    # Coerce numerics + date
    for col in ("fraud_sivt_rate", "givt_rate", "ivt_rate",
                "monitored_ads", "eligible_impressions", "total_calls"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date

    # Normalize the categorical column so downstream `startswith("Fraud")`
    # checks work reliably (DV uses both "Fraud/SIVT" and "Fraud/GIVT").
    df["traffic_validity"] = df["traffic_validity"].astype(str).str.strip()

    return df


def pull_dv_ivt(api_key: str, inbox_id: str, *, limit: int = 30) -> pd.DataFrame:
    """End-to-end: poll the inbox, fetch every matching CSV attachment,
    parse + concat. `_email_message_id` stamped per row for dedup."""
    matches = list_dv_ivt_messages(api_key, inbox_id, limit=limit)
    if not matches:
        return pd.DataFrame()

    frames = []
    for m in matches:
        msg_id = m.get("id") or m.get("message_id")
        if not msg_id:
            logger.warning("Skipping message with no id: %r", m)
            continue
        # The RFC822 Message-ID header is wrapped in <...> per spec; the
        # AgentMail attachment endpoint returns HTTP 400 if those brackets
        # are left in the URL path. Strip them defensively so either field
        # name on the message object works.
        msg_id = str(msg_id).strip().lstrip("<").rstrip(">")

        attachments = m.get("attachments")
        if not attachments:
            try:
                detail = get_message_detail(api_key, inbox_id, msg_id)
                attachments = detail.get("attachments") or []
            except Exception as e:
                logger.warning("Couldn't fetch detail for %s: %s", msg_id, e)
                continue

        for att in attachments:
            fn     = att.get("filename") or att.get("name") or ""
            att_id = att.get("id") or att.get("attachment_id") or ""
            if not fn.lower().endswith(".csv") or not att_id:
                continue
            try:
                content = fetch_attachment(api_key, inbox_id, msg_id, att_id)
                df = parse_dv_ivt_csv(content)
                df["_email_message_id"] = msg_id
                logger.info("Parsed %d IVT rows from %s (msg %s)", len(df), fn, msg_id)
                frames.append(df)
            except Exception as e:
                logger.exception("Failed to process %s from msg %s: %s", fn, msg_id, e)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
