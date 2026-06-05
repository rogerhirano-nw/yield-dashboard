"""
Pull DoubleVerify Pinnacle "Attention" reports from the
newsweek@agentmail.to inbox.

DV's team emails the report daily (subject:
"Unified Analytics Report: Attention Metrics") with the CSV attached.
This module polls the agentmail inbox, finds unprocessed matching
emails, downloads the CSV, parses it into a DataFrame, and hands
back rows ready for refresh_cache.py to write to the `dv_attention`
table.

CSV format (DV Pinnacle export):
    Lines 1-4: "# Report:", "# Start Date:", "# End Date:", "# Submit Time:"
    Line 5: blank
    Line 6: header row (12 columns, all double-quoted)
    Lines 7+: data, double-quoted strings, comma-separated

Columns (verbatim from the CSV header):
    Date, Ad Interaction Index, Attention Index, Engagement Index,
    Exposure Index, Intensity Index, Prominence Index, User Presence Index,
    Valid & Viewable Rate, Viewability Measurement Rate, Order, Line Item

The 8 *Index metrics are 100-baseline scores: 100 = DV's industry
median, >100 = better than median, <100 = below. So a row with
Attention Index 163 is "63% better attention than the typical campaign".

Order and Line Item use the 14-field Newsweek GAM naming convention, so
they join cleanly to `gam_campaigns.line_item_name` / `gam_pmp_deals.deal_name`.

agentmail.to API used (no SDK — plain urllib HTTP):
    GET /v0/inboxes/{inbox_id}/messages?limit=N
    GET /v0/inboxes/{inbox_id}/messages/{id}
    GET /v0/inboxes/{inbox_id}/messages/{id}/attachments/{filename}
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
DV_SUBJECT = "Unified Analytics Report: Attention Metrics"

# Verbatim header → snake_case DB column. Keep these names stable;
# the dashboard's column-rendering code keys off them.
COLUMN_MAP = {
    "Date":                          "date",
    "Ad Interaction Index":          "ad_interaction_index",
    "Attention Index":               "attention_index",
    "Engagement Index":              "engagement_index",
    "Exposure Index":                "exposure_index",
    "Intensity Index":               "intensity_index",
    "Prominence Index":              "prominence_index",
    "User Presence Index":           "user_presence_index",
    "Valid & Viewable Rate":         "valid_viewable_rate",
    "Viewability Measurement Rate":  "viewability_measurement_rate",
    "Order":                         "order_name",
    "Line Item":                     "line_item_name",
}


def _api_get(path: str, *, api_key: str, raw: bool = False):
    """GET /v0{path} with bearer auth. Returns parsed JSON by default, or
    raw bytes when raw=True (used for attachment downloads)."""
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


def list_dv_attention_messages(api_key: str, inbox_id: str, limit: int = 30) -> list[dict]:
    """List recent messages matching the DV Attention subject.

    Tries authenticated inbox first — whitelisted senders land here and
    attachment downloads work. Falls back to include_unauthenticated=true if
    empty; those messages are visible but attachment downloads return 404.
    Messages from the unauthenticated fallback are tagged _unauthenticated=True
    so pull_dv_attention() can skip them rather than producing noisy 404s.
    """
    subject_enc = urllib.parse.quote(DV_SUBJECT, safe="")
    base_path = f"/inboxes/{inbox_id}/messages?limit={limit}&subject={subject_enc}"

    raw = _api_get(base_path, api_key=api_key)
    if isinstance(raw, dict):
        messages = raw.get("messages") or raw.get("data") or []
    else:
        messages = raw or []

    if messages:
        logger.info(
            "agentmail: %d DV Attention messages in authenticated inbox", len(messages)
        )
        for m in messages:
            logger.debug("  msg id=%s from=%s atts=%s",
                         m.get("id") or m.get("message_id"),
                         m.get("from") or m.get("sender"),
                         [a.get("filename") or a.get("name") or a.get("id")
                          for a in (m.get("attachments") or [])])
        return messages

    # Authenticated inbox empty — check unauthenticated folder
    raw2 = _api_get(f"{base_path}&include_unauthenticated=true", api_key=api_key)
    if isinstance(raw2, dict):
        messages = raw2.get("messages") or raw2.get("data") or []
    else:
        messages = raw2 or []
    logger.info(
        "agentmail: authenticated inbox empty; %d DV Attention messages in "
        "unauthenticated folder (attachment downloads blocked — whitelist DV's sender)",
        len(messages),
    )
    for m in messages:
        logger.debug("  unauth msg id=%s from=%s atts=%s",
                     m.get("id") or m.get("message_id"),
                     m.get("from") or m.get("sender"),
                     [a.get("filename") or a.get("name") or a.get("id")
                      for a in (m.get("attachments") or [])])
        m["_unauthenticated"] = True
    return messages


def get_message_detail(api_key: str, inbox_id: str, message_id: str) -> dict:
    """Get a single message's full record — needed because the list endpoint
    sometimes omits the attachments[] array."""
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


def parse_dv_csv(content: bytes) -> pd.DataFrame:
    """Parse a DV Pinnacle Attention CSV (bytes) into a DataFrame with the
    snake_case columns the dv_attention table expects.

    Handles the 4-line preamble + blank + header by scanning for the
    first line that starts with `"Date"` or `Date,`. Tolerates UTF-8 BOM.
    """
    text = content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        stripped = line.lstrip("﻿").strip()
        if stripped.startswith('"Date"') or stripped.startswith("Date,"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "DV Attention CSV: could not locate 'Date' header line. "
            f"First 5 non-empty lines: {[l for l in lines[:5] if l.strip()]}"
        )

    payload = "\n".join(lines[header_idx:])
    df = pd.read_csv(io.StringIO(payload))
    df = df.rename(columns=COLUMN_MAP)
    keep = [c for c in COLUMN_MAP.values() if c in df.columns]
    df = df[keep].copy()

    # Type coercion. Numeric for indices + rates; date proper; strings kept.
    numeric_cols = [c for c in df.columns
                    if c not in ("date", "order_name", "line_item_name")]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date

    # Open-exchange / open-auction rows have a blank Line Item — keep them
    # (joinable on Order alone) but flag clearly with NaN→None for the join.
    df["line_item_name"] = df["line_item_name"].replace("", None)
    df["order_name"]     = df["order_name"].replace("", None)

    return df


def pull_dv_attention(api_key: str, inbox_id: str, *, limit: int = 30) -> pd.DataFrame:
    """End-to-end: poll the inbox, fetch every matching CSV attachment,
    parse + concat. Returns one DataFrame with all rows from all matched
    emails, plus an `_email_message_id` column for downstream dedup.

    Empty DataFrame if no matches or all fetches failed.
    """
    matches = list_dv_attention_messages(api_key, inbox_id, limit=limit)
    if not matches:
        return pd.DataFrame()

    frames = []
    for m in matches:
        # agentmail messages have no `id` field — primary key is `thread_id`
        # (a UUID). `message_id` is the RFC822 Message-ID in angle brackets;
        # the attachment download endpoint returns 404 when the RFC822 form is
        # used. thread_id is the UUID that the download endpoint resolves.
        msg_id = m.get("thread_id") or m.get("id") or m.get("message_id")
        if not msg_id:
            logger.warning("Skipping message with no id: %r", m)
            continue
        # Strip RFC822 angle brackets defensively (message_id fallback).
        msg_id = str(msg_id).strip().lstrip("<").rstrip(">")

        # The list endpoint may omit attachments[]; fetch detail to be safe.
        attachments = m.get("attachments")
        if not attachments:
            try:
                detail = get_message_detail(api_key, inbox_id, msg_id)
                attachments = detail.get("attachments") or []
            except Exception as e:
                logger.warning("Couldn't fetch detail for %s: %s", msg_id, e)
                continue

        if m.get("_unauthenticated"):
            logger.warning(
                "msg %s is in the unauthenticated folder — skipping attachment "
                "download (whitelist DV's sender in agentmail to fix)",
                msg_id,
            )
            continue

        for att in attachments:
            fn     = att.get("filename") or att.get("name") or ""
            att_id = att.get("id") or att.get("attachment_id") or ""
            logger.debug("  att object: %r", att)
            if not fn.lower().endswith(".csv") or not att_id:
                continue
            try:
                content = fetch_attachment(api_key, inbox_id, msg_id, att_id)
                df = parse_dv_csv(content)
                df["_email_message_id"] = msg_id
                logger.info("Parsed %d rows from %s (msg %s)", len(df), fn, msg_id)
                frames.append(df)
            except Exception as e:
                logger.exception("Failed to process %s from msg %s: %s", fn, msg_id, e)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
