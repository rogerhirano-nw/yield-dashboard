"""refresh_cache._tracked_line_items: the single-source feed list.

Primary source is the tracked_line_items table in the shared cache (written
by seller-comms); the legacy GAM_HOURLY_LINE_ITEMS env var is the fallback
when the table is unreachable or empty. These tests cover the fallback path
(no DATABASE_URL → engine fails → env var) without a database.
"""

from __future__ import annotations

import refresh_cache as rc


def test_falls_back_to_env_var_without_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GAM_HOURLY_LINE_ITEMS", " 111 ,222,, 333 ")
    assert rc._tracked_line_items() == ["111", "222", "333"]


def test_empty_everywhere_returns_empty(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GAM_HOURLY_LINE_ITEMS", raising=False)
    assert rc._tracked_line_items() == []
