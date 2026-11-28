"""Unit tests for health_check's pure logic (no DB / network)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from health_check import (CheckResult, _ago, _data_day, _eval_freshness,
                          build_report, should_send)

TODAY = date(2026, 6, 11)


def test_freshness_pass_on_boundary():
    r = _eval_freshness("magnite fresh", date(2026, 6, 10), 1, TODAY)
    assert r.ok
    assert "2026-06-10" in r.detail


def test_freshness_fail_when_stale():
    r = _eval_freshness("magnite fresh", date(2026, 6, 8), 1, TODAY)
    assert not r.ok
    assert "required 2026-06-10" in r.detail


def test_freshness_fail_on_empty_table():
    r = _eval_freshness("magnite fresh", None, 1, TODAY)
    assert not r.ok


def test_report_all_pass_verdict_in_subject():
    subject, body, all_ok = build_report(
        [CheckResult("a", True, "fine"), CheckResult("b", True, "fine")], TODAY)
    assert all_ok
    assert "✅ all 2 checks passed" in subject
    assert "(Thu Jun 11)" in subject
    assert "Everything is up to date." in body


def test_report_failure_verdict_and_detail():
    subject, body, all_ok = build_report(
        [CheckResult("a", True, "fine"),
         CheckResult("b", False, "max(date) 2026-06-08 < required 2026-06-10")],
        TODAY)
    assert not all_ok
    assert "❌ 1 of 2 need attention" in subject
    assert "❌ b: max(date)" in body and "2026-06-08" in body
    # failures are listed before the passing checks
    assert body.index("❌") < body.index("✅")
    assert "Technical details" in body


def test_freshness_failures_are_remediable():
    assert _eval_freshness("x", date(2026, 6, 1), 1, TODAY).remediable
    assert _eval_freshness("x", None, 1, TODAY).remediable


def test_id_format_style_failures_are_not_remediable_by_default():
    assert not CheckResult("dv id format", False, "boom").remediable


def test_report_notes_remediation():
    subject, body, all_ok = build_report(
        [CheckResult("a", True, "fine")], TODAY,
        remediation="re-ran refresh sweep → success (url); recovered: a")
    assert all_ok
    assert "after an automatic fix" in subject
    assert "Automatic fix tried: re-ran refresh sweep" in body


def test_report_still_failing_after_remediation_flags_human():
    subject, body, all_ok = build_report(
        [CheckResult("a", False, "still stale")], TODAY,
        remediation="re-ran refresh sweep → success (url)")
    assert not all_ok
    assert "a person needs to look" in body


def test_data_day_rolls_over_after_the_sweep_not_at_midnight():
    # 02:00 UTC: the new calendar day's sweep hasn't run yet — still the
    # prior data-day, so pre-sweep runs don't demand data that can't exist.
    assert _data_day(datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc)) == date(2026, 6, 11)
    # 09:45 UTC: sweep has landed — the morning check must require D-1.
    assert _data_day(datetime(2026, 6, 12, 9, 45, tzinfo=timezone.utc)) == date(2026, 6, 12)


def test_should_send_matrix():
    assert should_send(False, True, None)          # failures always send
    assert should_send(True, True, "remediated")   # auto-fix outcome sends
    assert should_send(True, False, None)          # green morning verdict sends
    assert not should_send(True, True, None)       # quiet green follow-up


def test_freshness_friendly_lines():
    ok = _eval_freshness("magnite_site_daily fresh", date(2026, 6, 10), 1, TODAY)
    assert ok.friendly == "Magnite by site: data through Wed Jun 10"
    bad = _eval_freshness("dv_ivt fresh", date(2026, 6, 5), 3, TODAY)
    assert bad.friendly == ("DoubleVerify invalid traffic: data stops at Fri Jun 5, "
                            "should reach Mon Jun 8")
    empty = _eval_freshness("ttd_chumba fresh", None, 2, TODAY)
    assert empty.friendly == "Trade Desk (Chumba): no data at all"


def test_report_uses_friendly_line_when_present():
    _, body, _ = build_report(
        [CheckResult("gam_campaigns pulled", True, "last write 0.1h ago (max 26h)",
                     friendly="GAM direct campaigns: updated 6 minutes ago")], TODAY)
    assert "✅ GAM direct campaigns: updated 6 minutes ago" in body
    assert "gam_campaigns pulled" in body  # still in the technical section


def test_ago_wording():
    assert _ago(0.1) == "6 minutes ago"
    assert _ago(1) == "1 hour ago"
    assert _ago(6.2) == "6 hours ago"
    assert _ago(72) == "3 days ago"
