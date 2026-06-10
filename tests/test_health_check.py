"""Unit tests for health_check's pure logic (no DB / network)."""

from __future__ import annotations

from datetime import date

from health_check import CheckResult, _eval_freshness, build_report

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
