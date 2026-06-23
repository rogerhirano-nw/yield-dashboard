"""Unit tests for health_check's pure logic (no DB / network)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from health_check import (CheckResult, _data_day, _eval_freshness,
                          _eval_rls_hygiene, build_report, should_send)

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
    assert "✅ 2/2 pass" in subject
    assert "2/2 checks pass" in body


def test_report_failure_verdict_and_detail():
    subject, body, all_ok = build_report(
        [CheckResult("a", True, "fine"),
         CheckResult("b", False, "max(date) 2026-06-08 < required 2026-06-10")],
        TODAY)
    assert not all_ok
    assert "❌ 1 of 2 FAILING" in subject
    assert "❌ FAIL" in body and "2026-06-08" in body


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
    assert "auto-remediated" in subject
    assert "Auto-remediation: re-ran refresh sweep" in body


def test_report_still_failing_after_remediation_flags_human():
    subject, body, all_ok = build_report(
        [CheckResult("a", False, "still stale")], TODAY,
        remediation="re-ran refresh sweep → success (url)")
    assert not all_ok
    assert "needs a human" in body


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


def test_rls_hygiene_passes_when_no_offenders():
    r = _eval_rls_hygiene([])
    assert r.ok
    # Must NOT be remediable: the sweep can't enable RLS (it's fixed in-place),
    # and marking it remediable would dispatch a pointless refresh.
    assert not r.remediable
    assert "all public tables" in r.detail.lower()


def test_rls_hygiene_fails_and_lists_each_offender_reason():
    r = _eval_rls_hygiene([("ttd_luckyland", True, False),
                           ("dv_ivt", False, True),
                           ("gam_creatives", True, True)])
    assert not r.ok
    assert not r.remediable
    assert "3 public table(s)" in r.detail
    assert "ttd_luckyland (RLS off)" in r.detail
    assert "dv_ivt (anon/authenticated grant)" in r.detail
    assert "gam_creatives (RLS off, anon/authenticated grant)" in r.detail
