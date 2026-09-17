"""TTD report parsing + inbox-scan contract.

These pin the two things that silently broke the Chumba feed on 2026-09-06,
when TTD replaced the scheduled report ("Newsweek Automated report VGW Chumba
Casino" -> "Newsweek Chumba Casino Performance report"):

1. The replacement report headers its spend columns "(USD)" where the retired
   one said "(Adv Currency)".  Unmapped, both of the columns the dashboard
   reads for spend go missing and every CPA silently reads 0.
2. The inbox scan looked at the last 50 messages of the WHOLE inbox, so the
   still-matching notifications simply aged out behind the twice-daily DV
   reports (matches decayed 7 -> 6 -> 4 -> 2 -> 0) while the campaign was
   still delivering.

All values here are synthetic — the repo is public.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

import ttd_client as t


REGISTERED = (
    "usergenChumba Registered TDID & UID2 - ghjdk2k - "
    "IdentityAlliance - Total Click + View Conversions"
)
FIRST_PURCHASE = (
    "usergenChumba First Purchase TDID & UID2 - udkazz3 - "
    "IdentityAlliance - Total Click + View Conversions"
)
FIRST_PURCHASE_HH = (
    "usergenChumba First Purchase TDID & UID2 - udkazz3 - "
    "IdentityAllianceWithHousehold - Total Click + View Conversions"
)


def _csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue().encode()


def _replacement_report_row(**over) -> dict:
    """One row shaped like the post-2026-09-06 Chumba report."""
    row = {
        "Advertiser": "Chumba Casino",
        "Deal ID": 4286022,
        "Inventory Contract": "Newsweek_PG_Gambling_AdX_TTD_NA_NA_VGW_X_US_320x50_$6_Team-USA_ILee",
        "Ad Format": "320x50",
        "Date": "2026-09-07",
        "Creative": "ChumbaCasino_ACQ_TTD_CC328_BR_Prog_320x50",
        "Advertiser Cost (USD)": 35.0,
        "Clicks": 4,
        "Impressions": 5000,
        "Media Cost (USD)": 30.0,
        REGISTERED: 0,
        FIRST_PURCHASE: 1,
        FIRST_PURCHASE_HH: 7,
    }
    row.update(over)
    return row


# ── column mapping ─────────────────────────────────────────────────────────

def test_replacement_report_maps_usd_spend_columns():
    """"(USD)" spend headers must land on the same columns "(Adv Currency)" did.

    `ttd_cpa_summary` / `ttd_cpa_for_deal` read `media_spend_usd` and fall back
    to `spend_usd`; if neither exists, spend sums to 0 and CPA reads None.
    """
    df = t.parse_ttd_csv(_csv([_replacement_report_row()]))

    assert "media_spend_usd" in df.columns
    assert "spend_usd" in df.columns
    assert df["media_spend_usd"].sum() == pytest.approx(30.0)
    assert df["spend_usd"].sum() == pytest.approx(35.0)


def test_retired_report_spend_headers_still_map():
    """The "(Adv Currency)" headers keep working — the mapping is additive."""
    df = t.parse_ttd_csv(_csv([{
        "Date": "2026-08-01",
        "Advertiser Cost (Adv Currency)": 12.0,
        "Media Cost (Adv Currency)": 9.0,
        "Impressions": 100,
    }]))

    assert df["spend_usd"].sum() == pytest.approx(12.0)
    assert df["media_spend_usd"].sum() == pytest.approx(9.0)


def test_ad_format_becomes_creative_size():
    """The replacement report carries the size as its own column.

    `ttd_cpa_summary` prefers `creative_size` over parsing a WxH token out of
    the creative name.
    """
    df = t.parse_ttd_csv(_csv([_replacement_report_row()]))
    assert df["creative_size"].iloc[0] == "320x50"


# ── conversion KPI selection ───────────────────────────────────────────────

def test_primary_conv_col_accepts_candidates_and_takes_first_present():
    """A campaign spans a report changeover, so the KPI is a candidate list."""
    df = t.parse_ttd_csv(
        _csv([_replacement_report_row(**{FIRST_PURCHASE: 3})]),
        primary_conv_col=(FIRST_PURCHASE, "01 - Total Click + View Conversions"),
    )
    assert df["attributed_conversions"].sum() == 3


def test_primary_conv_col_falls_through_to_a_later_candidate():
    """The retired report's pixel name still resolves when it's the one present."""
    df = t.parse_ttd_csv(
        _csv([{
            "Date": "2026-08-01",
            "Impressions": 10,
            "01 - Total Click + View Conversions": 6,
            "03 - Total Click + View Conversions": 99,
        }]),
        primary_conv_col=(FIRST_PURCHASE, "01 - Total Click + View Conversions"),
    )
    # 6, not 105 — the designated pixel only, never the auto-sum.
    assert df["attributed_conversions"].sum() == 6


def test_primary_conv_col_still_accepts_a_bare_string():
    df = t.parse_ttd_csv(
        _csv([_replacement_report_row(**{FIRST_PURCHASE_HH: 7})]),
        primary_conv_col=FIRST_PURCHASE_HH,
    )
    assert df["attributed_conversions"].sum() == 7


def test_each_replacement_pixel_is_mapped_separately():
    """The three pixel columns must stay distinct.

    The two First Purchase columns are ONE pixel under two attribution models,
    so anything that adds them together counts FTPs twice.
    """
    df = t.parse_ttd_csv(_csv([_replacement_report_row()]))

    assert df["conversions_registered"].sum() == 0
    assert df["conversions_first_purchase"].sum() == 1
    assert df["conversions_first_purchase_household"].sum() == 7


def test_missing_candidates_warn_before_falling_back(caplog):
    with caplog.at_level("WARNING"):
        t.parse_ttd_csv(
            _csv([_replacement_report_row()]),
            primary_conv_col=("No Such Column",),
        )
    assert "falling back to the conversion auto-sum" in caplog.text


# ── inbox scan ─────────────────────────────────────────────────────────────

class _FakeInbox:
    """Stands in for agentmail: records the paths asked for, replies per path."""

    def __init__(self, by_kind: dict[str, list[dict]]):
        self.by_kind = by_kind
        self.paths: list[str] = []

    def __call__(self, path, *, api_key, raw=False):
        self.paths.append(path)
        if "include_unauthenticated=true" in path:
            kind = "unauthenticated"
        elif "subject=" in path:
            kind = "filtered"
        else:
            kind = "unfiltered"
        return {"messages": self.by_kind.get(kind, [])}


def _msg(subject, sender="noreply@thetradedesk.com"):
    return {"id": subject, "subject": subject, "from": sender}


def test_scan_uses_the_server_side_subject_filter_first(monkeypatch):
    """The window must be N MATCHING messages, not the last N of the inbox."""
    hit = _msg("Report Available: Newsweek Chumba Casino Performance report - Daily")
    fake = _FakeInbox({"filtered": [hit]})
    monkeypatch.setattr(t, "_api_get", fake)

    out = t.list_ttd_messages("k", "inbox", subject_needle="Chumba")

    assert out == [hit]
    assert "subject=Chumba" in fake.paths[0]
    assert len(fake.paths) == 1  # no unfiltered fallback needed


def test_scan_falls_back_to_unfiltered_then_unauthenticated(monkeypatch):
    """The subject filter is an optimization, never a hard dependency."""
    hit = _msg("Report Available: Newsweek Chumba Casino Performance report - Daily")
    fake = _FakeInbox({"unauthenticated": [hit]})
    monkeypatch.setattr(t, "_api_get", fake)

    out = t.list_ttd_messages("k", "inbox", subject_needle="Chumba")

    assert out == [hit]
    assert len(fake.paths) == 3
    assert "include_unauthenticated=true" in fake.paths[-1]


def test_scan_rechecks_the_needle_client_side(monkeypatch):
    """A server-side filter that over-returns must not widen the match."""
    fake = _FakeInbox({"filtered": [_msg("Report Available: Luckyland Casino TTD")]})
    monkeypatch.setattr(t, "_api_get", fake)

    assert t.list_ttd_messages("k", "inbox", subject_needle="Chumba") == []


def test_a_renamed_schedule_is_logged_not_silent(monkeypatch, caplog):
    """The tell for the 2026-09-06 failure: TTD mail that no longer matches."""
    fake = _FakeInbox({
        "unfiltered": [_msg("Report Available: Newsweek Chumba Casino Performance report")],
    })
    monkeypatch.setattr(t, "_api_get", fake)

    with caplog.at_level("WARNING"):
        out = t.list_ttd_messages("k", "inbox", subject_needle="No Longer Matches")

    assert out == []
    assert "renamed" in caplog.text
    assert "Newsweek Chumba Casino Performance report" in caplog.text


def test_unrelated_senders_are_never_logged(monkeypatch, caplog):
    """Only TTD-sender subjects may be printed — the Actions logs are public."""
    fake = _FakeInbox({
        "unfiltered": [_msg("Someone's private mail", sender="a@example.com")],
    })
    monkeypatch.setattr(t, "_api_get", fake)

    with caplog.at_level("WARNING"):
        t.list_ttd_messages("k", "inbox", subject_needle="Chumba")

    assert "private mail" not in caplog.text


def test_a_failing_listing_does_not_abort_the_scan(monkeypatch):
    """One bad endpoint must not cost us the report."""
    hit = _msg("Report Available: Newsweek Chumba Casino Performance report")
    calls = {"n": 0}

    def flaky(path, *, api_key, raw=False):
        calls["n"] += 1
        if "subject=" in path and "include_unauthenticated" not in path:
            raise RuntimeError("HTTP 400 — subject filter unsupported")
        return {"messages": [hit]}

    monkeypatch.setattr(t, "_api_get", flaky)

    assert t.list_ttd_messages("k", "inbox", subject_needle="Chumba") == [hit]
    assert calls["n"] == 2
