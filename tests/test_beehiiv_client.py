"""Unit tests for beehiiv_client's pure transforms (no network).

The network layer is deliberately untested here — what these pin is the
shape contract with the beehiiv v2 API: the flattening of the nested
`stats` object into flat columns, and the epoch->Eastern date conversion
the whole dashboard's date semantics depend on.
"""

from __future__ import annotations

from datetime import date

from beehiiv_client import (epoch_to_et_date, flatten_post,
                            flatten_publication, post_within_window)

# 2026-06-15 12:00:00 UTC == 08:00 ET on the same day.
NOON_UTC = 1781524800
# 2026-06-16 02:00:00 UTC == 22:00 ET on 2026-06-15 — the rollover case.
LATE_UTC = 1781575200


# ----------------------------------------------------------------------
# epoch_to_et_date
# ----------------------------------------------------------------------

def test_epoch_to_et_date_basic():
    assert epoch_to_et_date(NOON_UTC) == "2026-06-15"


def test_epoch_to_et_date_uses_eastern_not_utc():
    """The evening-rollover case: 02:00 UTC is still the previous day in ET.

    This is the #339 hazard — a UTC read would label this 2026-06-16 while
    the rest of the dashboard (and Roger's wall clock) says the 15th."""
    assert epoch_to_et_date(LATE_UTC) == "2026-06-15"


def test_epoch_to_et_date_accepts_numeric_strings():
    assert epoch_to_et_date(str(NOON_UTC)) == "2026-06-15"
    assert epoch_to_et_date(float(NOON_UTC)) == "2026-06-15"


def test_epoch_to_et_date_none_on_junk():
    for bad in (None, "", "not-a-timestamp", {}, []):
        assert epoch_to_et_date(bad) is None


# ----------------------------------------------------------------------
# flatten_publication
# ----------------------------------------------------------------------

def _pub(**over) -> dict:
    base = {
        "id": "pub_123",
        "name": "Newsweek Daily",
        "organization_name": "Newsweek",
        "referral_program_enabled": True,
        "created": NOON_UTC,
        "stats": {
            "active_subscriptions": 100_000,
            "active_premium_subscriptions": 2_500,
            "active_free_subscriptions": 97_500,
            "average_open_rate": 42.5,
            "average_click_rate": 3.1,
            "total_sent": 5_000_000,
            "total_unique_opened": 2_000_000,
            "total_clicked": 150_000,
        },
    }
    base.update(over)
    return base


def test_flatten_publication_maps_stats_and_stamps_date():
    row = flatten_publication(_pub(), "2026-06-15")
    assert row["publication_id"] == "pub_123"
    assert row["name"] == "Newsweek Daily"
    assert row["active_subscriptions"] == 100_000
    assert row["active_free_subscriptions"] == 97_500
    assert row["average_open_rate"] == 42.5
    assert row["total_clicked"] == 150_000
    assert row["created_date"] == "2026-06-15"
    assert row["date"] == "2026-06-15"


def test_flatten_publication_without_stats_yields_none_not_keyerror():
    """A key lacking the stats scope still produces an identifiable row."""
    row = flatten_publication(_pub(stats=None), "2026-06-15")
    assert row["publication_id"] == "pub_123"
    assert row["active_subscriptions"] is None
    assert row["average_open_rate"] is None


# ----------------------------------------------------------------------
# flatten_post
# ----------------------------------------------------------------------

def _post(**over) -> dict:
    base = {
        "id": "post_abc",
        "title": "Morning Briefing",
        "subtitle": "What matters today",
        "slug": "morning-briefing",
        "authors": ["Jane Doe", "John Roe"],
        "content_tags": ["politics"],
        "status": "confirmed",
        "platform": "both",
        "audience": "free",
        "subject_line": "Your Tuesday briefing",
        "web_url": "https://news.example.com/p/morning-briefing",
        "split_tested": False,
        "created": NOON_UTC,
        "publish_date": NOON_UTC,
        "displayed_date": NOON_UTC,
        "recipient_count": 90_000,
        "stats": {
            "email": {
                "recipients": 90_000,
                "delivered": 89_500,
                "opens": 50_000,
                "unique_opens": 40_000,
                "open_rate": 44.7,
                "clicks": 6_000,
                "unique_clicks": 4_500,
                "verified_clicks": 5_800,
                "unique_verified_clicks": 4_400,
                "click_rate": 5.0,
                "unsubscribes": 120,
                "spam_reports": 3,
            },
            "web": {"views": 12_000, "clicks": 800},
            "clicks": [{"url": "https://x.example", "total_clicks": 10}],
            "upgrades": 17,
        },
    }
    base.update(over)
    return base


def test_flatten_post_flattens_email_and_web_stats():
    row = flatten_post(_post(), "pub_123")
    assert row["post_id"] == "post_abc"
    assert row["publication_id"] == "pub_123"
    assert row["email_delivered"] == 89_500
    assert row["email_unique_opens"] == 40_000
    assert row["email_open_rate"] == 44.7
    assert row["email_unique_verified_clicks"] == 4_400
    assert row["email_spam_reports"] == 3
    assert row["web_views"] == 12_000
    assert row["web_clicks"] == 800
    assert row["upgrades"] == 17


def test_flatten_post_date_is_publish_date():
    """`date` is the send date — the column the freshness/window logic keys on."""
    row = flatten_post(_post(publish_date=LATE_UTC), "pub_123")
    assert row["date"] == "2026-06-15"


def test_flatten_post_json_encodes_list_fields():
    """List columns round-trip through SQLite/Postgres as text, not as lists."""
    row = flatten_post(_post(), "pub_123")
    assert row["authors"] == '["Jane Doe", "John Roe"]'
    assert row["content_tags"] == '["politics"]'


def test_flatten_post_drops_per_url_click_breakdown():
    """stats.clicks is variable-length; it would need its own table."""
    row = flatten_post(_post(), "pub_123")
    assert "clicks" not in row
    assert not any(k.endswith("_url_clicks") for k in row)


def test_flatten_post_without_stats_yields_none_not_keyerror():
    row = flatten_post(_post(stats=None), "pub_123")
    assert row["post_id"] == "post_abc"
    assert row["email_opens"] is None
    assert row["web_views"] is None


def test_flatten_post_column_set_is_stable():
    """The row shape must not drift silently — _safe_replace treats a changed
    column set as a schema change and drops the table."""
    with_stats = set(flatten_post(_post(), "pub_123"))
    without_stats = set(flatten_post(_post(stats=None), "pub_123"))
    assert with_stats == without_stats


# ----------------------------------------------------------------------
# post_within_window
# ----------------------------------------------------------------------

def test_post_within_window_includes_cutoff_day():
    assert post_within_window(_post(), date(2026, 6, 15)) is True


def test_post_within_window_excludes_older():
    assert post_within_window(_post(), date(2026, 6, 16)) is False


def test_post_within_window_keeps_undated_posts():
    """No publish_date -> kept, so a missing timestamp can't silently shrink
    the table."""
    assert post_within_window(_post(publish_date=None), date(2026, 6, 16)) is True
