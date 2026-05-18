"""The Trade Desk VGW conversion report — reads the daily CSV from the AgentMail inbox."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests
from agentmail import AgentMail

_COLUMN_MAP = {
    "Campaign":                                                "campaign",
    "Ad Group":                                                "ad_group",
    "Date":                                                    "date",
    "Advertiser Currency Code":                                "currency",
    "Deal ID":                                                 "deal_id",
    "Creative":                                                "creative",
    "Impressions":                                             "impressions",
    "Clicks":                                                  "clicks",
    "Advertiser Cost (Adv Currency)":                          "advertiser_cost",
    "Media Cost (Adv Currency)":                               "media_cost",
    "01 - Total Click + View Conversions":                     "registrations",
    "03 - Total Click + View Conversions":                     "first_deposits",
    "01 - Total Click + View Conversions CPA (Adv Currency)":  "registration_cpa",
    "03 - Total Click + View Conversions CPA (Adv Currency)":  "first_deposit_cpa",
}


def run_ttd_report(
    inbox_id: str,
    agentmail_api_key: str,
    *,
    after: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Find the most recent TTD report email in the AgentMail inbox, download its
    CSV attachment, and return a normalized DataFrame.

    The inbox should be configured in TTD's scheduler as the report recipient.
    TTD sends from *@thetradedesk.com; falls back to subject-line matching.
    """
    client = AgentMail(api_key=agentmail_api_key)
    response = client.inboxes.messages.list(inbox_id, after=after, ascending=False)
    messages = response.messages or []

    for msg in messages:
        sender = (msg.from_ or "").lower()
        subject = (msg.subject or "").lower()
        if (
            "thetradedesk.com" not in sender
            and "trade desk" not in subject
            and "ttd" not in subject
        ):
            continue

        full_msg = client.inboxes.messages.get(inbox_id, msg.message_id)
        if not full_msg.attachments:
            continue

        csv_att = next(
            (a for a in full_msg.attachments if (a.filename or "").lower().endswith(".csv")),
            None,
        )
        if csv_att is None:
            continue

        att = client.inboxes.messages.get_attachment(inbox_id, msg.message_id, csv_att.attachment_id)
        resp = requests.get(att.download_url, timeout=60)
        resp.raise_for_status()
        return _parse(resp.content)

    raise RuntimeError("No TTD report email with CSV attachment found in AgentMail inbox")


def _parse(content: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(content))
    df = df.rename(columns=_COLUMN_MAP)

    known = list(_COLUMN_MAP.values())
    df = df[[c for c in known if c in df.columns]]

    df["date"] = pd.to_datetime(df["date"]).dt.date

    for col in ("impressions", "clicks", "registrations", "first_deposits"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    for col in ("advertiser_cost", "media_cost", "registration_cpa", "first_deposit_cpa"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    return df
