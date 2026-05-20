"""URL construction tests for the published-CSV link."""

from __future__ import annotations

import re
from datetime import date

import pytest

from deal_health.publish import build_csv_url, csv_filename_for


def test_filename_is_date_stamped():
    assert csv_filename_for(date(2026, 5, 19)) == "weekly_deal_health_2026-05-19.csv"


def test_url_shape(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GH_ORG_REPO", "newsweek/yield-dashboard")
    monkeypatch.setenv("GH_BRANCH", "main")
    monkeypatch.setenv("REPORTS_PATH", "reports")
    url = build_csv_url("weekly_deal_health_2026-05-19.csv")
    assert url == (
        "https://raw.githubusercontent.com/newsweek/yield-dashboard/"
        "main/reports/weekly_deal_health_2026-05-19.csv"
    )


def test_url_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GH_ORG_REPO", raising=False)
    monkeypatch.delenv("GH_BRANCH", raising=False)
    monkeypatch.delenv("REPORTS_PATH", raising=False)
    url = build_csv_url("weekly_deal_health_2026-05-19.csv")
    # We just assert it's a well-formed raw.githubusercontent.com URL.
    assert re.match(
        r"^https://raw\.githubusercontent\.com/[^/]+/[^/]+/[^/]+/reports/weekly_deal_health_2026-05-19\.csv$",
        url,
    ), f"url shape unexpected: {url}"


def test_url_strips_leading_slash_in_reports_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REPORTS_PATH", "/reports/")
    url = build_csv_url("file.csv")
    assert "//reports//file.csv" not in url, "double slashes leaked into URL"
