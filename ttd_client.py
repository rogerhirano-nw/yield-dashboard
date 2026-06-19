"""Pull TTD (The Trade Desk) scheduled report CSVs from the
newsweek@agentmail.to inbox.

TTD sends a notification email when a scheduled report is ready.
The body contains a pre-signed download link valid for 30 days.
This module polls agentmail for the notification, extracts and
decodes the download URL (which may be wrapped in Microsoft
Outlook safelinks), downloads the CSV, and returns a DataFrame.

Email format (subject):
    Report Available: <report_name> - <schedule_name>
    e.g. "Report Available: Luckyland Casino TTD Newsweek MonthtoDate report v3 - ..."
Sender: noreply@thetradedesk.com

The download URL in the body is either:
    • A direct TTD URL:
        https://desk.thetradedesk.com/reports/view/<exec_id>?d=<partner>&s=<sig>&t=<ts>
    • Or the same URL wrapped in Microsoft safelinks:
        https://*.safelinks.protection.outlook.com/?url=<encoded-ttd-url>&...

The signed URL is valid 30 days and serves the CSV directly
(Content-Type: text/csv or application/octet-stream).

agentmail.to API (no SDK — plain urllib HTTP), same auth pattern as
dv_attention_client.py.
"""
from __future__ import annotations

import io
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

logger = logging.getLogger(__name__)

AGENTMAIL_BASE = "https://api.agentmail.to/v0"

# Match any TTD report-available notification.  The full subject
# includes the report name and schedule; we only need a stable prefix.
TTD_SUBJECT_NEEDLE = "Report Available: Luckyland Casino TTD"
TTD_SENDER_DOMAIN  = "thetradedesk.com"

# Extract the TTD report URL from an email body.  Two cases:
#   1. Safelinks wrapper  →  capture the encoded inner URL from ?url=
#   2. Bare TTD URL       →  match directly
_SAFELINKS_RE = re.compile(
    r'https://[^\s<>"]*safelinks\.protection\.outlook\.com/\?url=([^&\s<>"]+)',
    re.I,
)
_TTD_URL_RE = re.compile(
    r'https://desk\.thetradedesk\.com/reports/view/\d+[^\s<>"]*',
    re.I,
)

# TTD column names → snake_case DB columns.
# Covers the columns commonly present in a display+video delivery report.
# Unknown columns are auto-converted to snake_case by _snake().
COLUMN_MAP: dict[str, str] = {
    # Time
    "Date":                          "date",
    # Advertiser
    "Advertiser":                    "advertiser",
    "AdvertiserName":                "advertiser",
    "Advertiser ID":                 "advertiser_id",
    "AdvertiserId":                  "advertiser_id",
    # Campaign / IO
    "Campaign":                      "campaign",
    "CampaignName":                  "campaign",
    "Campaign ID":                   "campaign_id",
    "CampaignId":                    "campaign_id",
    # Ad Group
    "Ad Group":                      "ad_group",
    "AdGroupName":                   "ad_group",
    "Ad Group ID":                   "ad_group_id",
    "AdGroupId":                     "ad_group_id",
    # Supply
    "Supply Vendor":                 "supply_vendor",
    "SupplyVendor":                  "supply_vendor",
    "Site":                          "site",
    "Domain":                        "domain",
    # Delivery
    "Impressions":                   "impressions",
    "Clicks":                        "clicks",
    "CTR":                           "ctr",
    "Frequency":                     "frequency",
    "Reach":                         "reach",
    # Spend
    "Spend (USD)":                   "spend_usd",
    "Total Spend (USD)":             "spend_usd",
    "Media Spend (USD)":             "media_spend_usd",
    "Data Spend (USD)":              "data_spend_usd",
    "Fee Spend (USD)":               "fee_spend_usd",
    # CPM / CPC
    "eCPM (USD)":                    "ecpm_usd",
    "CPM (USD)":                     "ecpm_usd",
    "CPC (USD)":                     "cpc_usd",
    # Video
    "Video Completions":             "video_completions",
    "Video Completion Rate":         "vcr",
    "Video Views":                   "video_views",
    "25% Video Complete":            "video_q1",
    "50% Video Complete":            "video_q2",
    "75% Video Complete":            "video_q3",
    "100% Video Complete":           "video_complete",
    # Viewability
    "Viewability Rate":              "viewability_rate",
    "Viewable Impressions":          "viewable_impressions",
    "Measurable Impressions":        "measurable_impressions",
    # Conversions (acquisition)
    "Attributed Conversions":        "attributed_conversions",
    "Click Attributed Conversions":  "click_conversions",
    "View Attributed Conversions":   "view_conversions",
    "Total Attributed Revenue (USD)": "attributed_revenue_usd",
    "Revenue Per Conversion":        "revenue_per_conversion",
    # Media type / format
    "Media Type":                    "media_type",
    "Format":                        "format",
    "Creative Size":                 "creative_size",
    "Creative":                      "creative",
    "Creative ID":                   "creative_id",
    # Device / geo
    "Device Type":                   "device_type",
    "Country":                       "country",
    "Region":                        "region",
    # Report metadata
    "Report Schedule":               "report_schedule",
    "Report Type":                   "report_type",
}

_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")


def _snake(col: str) -> str:
    """Convert an arbitrary column header to snake_case."""
    s = col.lower().strip()
    s = _NON_ALPHANUM.sub("_", s)
    return s.strip("_")


def _api_get(path: str, *, api_key: str, raw: bool = False):
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


def list_ttd_messages(api_key: str, inbox_id: str, limit: int = 10) -> list[dict]:
    """List recent messages whose subject contains the TTD report needle."""
    raw = _api_get(f"/inboxes/{inbox_id}/messages?limit={limit}", api_key=api_key)
    messages = raw.get("messages", raw.get("data", [])) if isinstance(raw, dict) else (raw or [])
    matches = []
    for m in messages:
        subj = (m.get("subject") or "").strip()
        if TTD_SUBJECT_NEEDLE in subj:
            matches.append(m)
    logger.info(
        "agentmail: scanned %d message(s); %d match TTD report notification",
        len(messages), len(matches),
    )
    return matches


def get_message_detail(api_key: str, inbox_id: str, message_id: str) -> dict:
    encoded = urllib.parse.quote(message_id, safe="")
    return _api_get(f"/inboxes/{inbox_id}/messages/{encoded}", api_key=api_key)


def extract_ttd_download_url(body: str) -> str | None:
    """Extract the TTD report download URL from the email body.

    Handles two cases:
    1. Safelinks-wrapped: decode the `url=` parameter.
    2. Bare TTD URL: match directly.

    Returns the raw TTD URL, or None if not found.
    """
    # Case 1: safelinks wrapper — the URL is percent-encoded in the ?url= param.
    m = _SAFELINKS_RE.search(body)
    if m:
        encoded = m.group(1)
        # Unquote once; safelinks encodes `%` as `%25` in some clients
        decoded = urllib.parse.unquote(encoded)
        # Verify it's actually a TTD URL after decoding
        if "thetradedesk.com" in decoded:
            logger.debug("Extracted TTD URL via safelinks decode: %s", decoded[:80])
            return decoded

    # Case 2: bare TTD URL in body
    m2 = _TTD_URL_RE.search(body)
    if m2:
        url = m2.group(0)
        logger.debug("Extracted bare TTD URL: %s", url[:80])
        return url

    return None


def download_ttd_csv(url: str) -> bytes:
    """Download the CSV from a TTD signed report URL.

    TTD's signed /reports/view/{id}?s=... URL serves the CSV directly
    (Content-Type: text/csv or application/octet-stream).  The link
    is valid for 30 days from the notification email.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "yield-dashboard/ttd-report-client"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            ct = r.headers.get("Content-Type", "")
            content = r.read()
            logger.debug(
                "TTD download: %d bytes, Content-Type: %s", len(content), ct
            )
            if "text/html" in ct.lower() and len(content) < 50_000:
                # Likely a login page; log a snippet to help debug
                snippet = content[:500].decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"TTD URL returned HTML (likely requires authentication). "
                    f"Snippet: {snippet!r}"
                )
            return content
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"TTD CSV download failed: HTTP {e.code} {e.reason} :: {body[:300]}"
        ) from e


def _execution_id_from_url(url: str) -> str | None:
    """Extract the ScheduleExecutionId from a TTD report URL.
    e.g. .../reports/view/202866221?... → '202866221'
    """
    m = re.search(r"/reports/view/(\d+)", url)
    return m.group(1) if m else None


def parse_ttd_csv(content: bytes, execution_id: str | None = None) -> pd.DataFrame:
    """Parse a TTD report CSV into a DataFrame with standardized columns.

    TTD reports have no metadata preamble — the first row is the header.
    Applies COLUMN_MAP for known columns; auto-converts unknown columns
    to snake_case.  Date and numeric columns are coerced.
    """
    text = content.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(text))

    # Rename using COLUMN_MAP, then snake-case unknowns
    rename = {}
    for col in df.columns:
        rename[col] = COLUMN_MAP.get(col) or _snake(col)
    df = df.rename(columns=rename)

    # Drop fully-empty columns that sometimes appear at the CSV tail
    df = df.dropna(how="all", axis=1)

    # Date coercion
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        df = df.dropna(subset=["date"])

    # Numeric coercion for everything that isn't clearly a string identity column
    _str_cols = {"date", "advertiser", "campaign", "ad_group", "supply_vendor",
                 "site", "domain", "media_type", "format", "creative",
                 "creative_size", "device_type", "country", "region",
                 "advertiser_id", "campaign_id", "ad_group_id", "creative_id",
                 "report_schedule", "report_type"}
    for col in df.columns:
        if col not in _str_cols:
            coerced = pd.to_numeric(df[col], errors="coerce")
            # Only replace if the coercion didn't introduce NaN where there
            # were real non-numeric values (e.g. string identity columns that
            # didn't make it into _str_cols).
            if coerced.notna().sum() > 0 or df[col].isna().all():
                df[col] = coerced

    if execution_id:
        df["_execution_id"] = execution_id

    return df


def pull_ttd(api_key: str, inbox_id: str) -> tuple[pd.DataFrame, dict]:
    """End-to-end: poll inbox for latest TTD report notification,
    extract the download URL, download the CSV, and parse it.

    Returns (df, meta) where meta includes execution_id, subject,
    and received_at.  Empty DataFrame if no matching message found
    or download fails.
    """
    messages = list_ttd_messages(api_key, inbox_id)
    if not messages:
        return pd.DataFrame(), {}

    # Newest first
    messages.sort(
        key=lambda m: m.get("sent_at") or m.get("created_at") or "",
        reverse=True,
    )

    for m in messages:
        msg_id = m.get("id") or m.get("message_id") or ""
        subj   = m.get("subject") or ""

        # Fetch full message to get the body (list response may be truncated)
        try:
            detail = get_message_detail(api_key, inbox_id, msg_id)
        except Exception as exc:
            logger.warning("Could not fetch TTD message detail %s: %s", msg_id, exc)
            continue

        body = detail.get("text") or detail.get("body") or ""
        if not body:
            # Fall back to HTML body, strip tags
            html = detail.get("html") or ""
            body = re.sub(r"<[^>]+>", " ", html)

        url = extract_ttd_download_url(body)
        if not url:
            logger.warning(
                "TTD notification message %s has no recognizable download URL "
                "(subject: %r) — skipping",
                msg_id, subj,
            )
            continue

        exec_id = _execution_id_from_url(url)
        meta = {
            "message_id":   msg_id,
            "subject":      subj,
            "received_at":  m.get("sent_at") or m.get("created_at"),
            "execution_id": exec_id,
            "download_url": url,
        }

        try:
            content = download_ttd_csv(url)
        except Exception as exc:
            logger.warning("TTD CSV download failed for exec %s: %s", exec_id, exc)
            return pd.DataFrame(), meta

        try:
            df = parse_ttd_csv(content, execution_id=exec_id)
        except Exception as exc:
            logger.warning("TTD CSV parse failed for exec %s: %s", exec_id, exc)
            return pd.DataFrame(), meta

        logger.info(
            "Parsed %d rows from TTD report (exec_id=%s, subject=%r)",
            len(df), exec_id, subj,
        )
        return df, meta

    return pd.DataFrame(), {}
