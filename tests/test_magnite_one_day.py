"""Every Magnite API call covers ONE day, and the tables still accumulate.

Magnite DV+ asked on 2026-11-02 that our reporting calls cover a single day
(the trigger was a daily 180-day `deal,deal_id,date × bid_requests` pull for
the deal-age proxy), on pain of API revocation. These tests pin:

1. the request side — every report the sweep sends is `date_range="yesterday"`
   with no start/end, so no future edit can quietly widen one again;
2. the write side — a one-day pull merges into the existing table instead of
   replacing it, re-runs are idempotent, and the deal-age table stays
   cumulative.

Runs against SQLite with a fake MagniteClient; no network.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import sqlalchemy as sa

refresh_cache = pytest.importorskip(
    "refresh_cache", reason="refresh_cache needs its runtime deps installed"
)


class FakeMagnite:
    """Records every run_report call; returns the queued frames in order."""

    calls: list[dict] = []
    responses: list[pd.DataFrame] = []

    def __init__(self, *a, **kw):
        pass

    def run_report(self, **kw):
        FakeMagnite.calls.append(kw)
        return FakeMagnite.responses.pop(0) if FakeMagnite.responses else pd.DataFrame()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    monkeypatch.setattr(refresh_cache, "_engine", lambda: engine)
    monkeypatch.setattr(refresh_cache, "MagniteClient", FakeMagnite)
    for k in ("MAGNITE_KEY", "MAGNITE_SECRET", "MAGNITE_PUBLISHER_ID"):
        monkeypatch.setenv(k, "x")
    FakeMagnite.calls = []
    FakeMagnite.responses = []
    return engine


def _read(engine, table) -> pd.DataFrame:
    with engine.connect() as c:
        return pd.read_sql(sa.text(f'SELECT * FROM "{table}"'), c)


def _assert_one_day(call: dict) -> None:
    assert call.get("date_range") == "yesterday", call
    assert not call.get("start") and not call.get("end"), call


# ── 1. request side ─────────────────────────────────────────────────────────

def test_report_configs_carry_no_multi_day_window():
    for table, cfg in refresh_cache.REPORTS.items():
        for k in ("date_range", "start", "end", "window_days", "retention_days"):
            assert k not in cfg, f"{table} sets {k!r} — Magnite pulls are one day only"


def test_full_magnite_refresh_sends_only_one_day_calls(db):
    FakeMagnite.responses = [pd.DataFrame()] * 10
    refresh_cache.refresh_magnite()
    refresh_cache.refresh_magnite_deal_metadata()
    assert len(FakeMagnite.calls) == len(refresh_cache.REPORTS) + 1
    for call in FakeMagnite.calls:
        _assert_one_day(call)


def test_refresh_one_report_rejects_a_widened_config(db):
    with pytest.raises(ValueError, match="one day"):
        refresh_cache.refresh_one_report(
            FakeMagnite(), "magnite_x",
            {"dimensions": ["date"], "metrics": ["impressions"], "date_range": "last_30"})
    assert FakeMagnite.calls == []


# ── 2a. date-keyed tables: append the day, idempotent ───────────────────────

def _site_day(day: str, impressions: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"date": day, "site": "newsweek.com", "impressions": impressions},
        {"date": day, "site": "newsweek.co.uk", "impressions": impressions // 2},
    ])


CFG = {"dimensions": ["date", "site"], "metrics": ["impressions"]}


def test_daily_pull_appends_to_history_and_reruns_replace(db):
    for d in ("2026-10-29", "2026-10-30", "2026-10-31"):
        FakeMagnite.responses = [_site_day(d, 100)]
        refresh_cache.refresh_one_report(FakeMagnite(), "magnite_site_daily", CFG)

    FakeMagnite.responses = [_site_day("2026-11-01", 100)]
    refresh_cache.refresh_one_report(FakeMagnite(), "magnite_site_daily", CFG)
    # Health-check same-day re-run with restated numbers: replaces, no dups.
    FakeMagnite.responses = [_site_day("2026-11-01", 400)]
    refresh_cache.refresh_one_report(FakeMagnite(), "magnite_site_daily", CFG)

    out = _read(db, "magnite_site_daily")
    assert sorted(out["date"].unique()) == ["2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]
    assert len(out) == 8
    assert out.loc[out["date"] == "2026-11-01", "impressions"].sum() == 600
    assert out.loc[out["date"] == "2026-10-29", "impressions"].sum() == 150
    for call in FakeMagnite.calls:
        _assert_one_day(call)


def test_missed_day_is_logged(db, caplog):
    FakeMagnite.responses = [_site_day("2026-10-28", 1), _site_day("2026-11-01", 1)]
    refresh_cache.refresh_one_report(FakeMagnite(), "magnite_site_daily", CFG)
    with caplog.at_level(logging.WARNING):
        refresh_cache.refresh_one_report(FakeMagnite(), "magnite_site_daily", CFG)
    assert any("3 day(s) missing" in r.getMessage() for r in caplog.records)


def test_schema_drift_widens_instead_of_dropping_history(db):
    FakeMagnite.responses = [_site_day("2026-10-31", 10)]
    refresh_cache.refresh_one_report(FakeMagnite(), "magnite_site_daily", CFG)
    wider = _site_day("2026-11-01", 20).assign(ecpm=1.5)
    FakeMagnite.responses = [wider]
    refresh_cache.refresh_one_report(FakeMagnite(), "magnite_site_daily", CFG)

    out = _read(db, "magnite_site_daily")
    assert sorted(out["date"].unique()) == ["2026-10-31", "2026-11-01"]
    assert out.loc[out["date"] == "2026-10-31", "ecpm"].isna().all()


# ── 2b. lookup table (magnite_deal_demand) ──────────────────────────────────

def test_lookup_merge_replaces_seen_keys_keeps_and_expires_the_rest():
    now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    iso = lambda days_ago: (now - timedelta(days=days_ago)).isoformat()  # noqa: E731
    existing = pd.DataFrame([
        {"deal_id": 111.0, "revenue_source": "Publisher Deals", "_pulled_at": iso(3)},   # re-seen
        {"deal_id": 222.0, "revenue_source": "Publisher Deals", "_pulled_at": iso(10)},  # kept
        {"deal_id": 333.0, "revenue_source": "Magnite Deals",   "_pulled_at": iso(31)},  # expired
    ])
    new = pd.DataFrame([
        {"deal_id": "111", "revenue_source": "Magnite Deals", "_pulled_at": now.isoformat()},
    ])
    out = refresh_cache.merge_lookup_rows(existing, new, "deal_id", 30, now)
    got = {refresh_cache._norm_key(k): v for k, v in zip(out["deal_id"], out["revenue_source"])}
    assert got == {"111": "Magnite Deals", "222": "Publisher Deals"}


def test_deal_demand_refresh_end_to_end(db):
    cfg = refresh_cache.REPORTS["magnite_deal_demand"]
    FakeMagnite.responses = [pd.DataFrame([
        {"deal": "A", "deal_id": 1, "demand_type_ad_resp": "PMP", "revenue_source": "Publisher Deals",
         "paid_impression": 5, "publisher_gross_revenue": 1.0},
        {"deal": "B", "deal_id": 2, "demand_type_ad_resp": "PMP", "revenue_source": "Publisher Deals",
         "paid_impression": 5, "publisher_gross_revenue": 1.0},
    ])]
    refresh_cache.refresh_one_report(FakeMagnite(), "magnite_deal_demand", cfg)
    FakeMagnite.responses = [pd.DataFrame([
        {"deal": "B", "deal_id": 2, "demand_type_ad_resp": "PA", "revenue_source": "Magnite Deals",
         "paid_impression": 7, "publisher_gross_revenue": 2.0},
    ])]
    refresh_cache.refresh_one_report(FakeMagnite(), "magnite_deal_demand", cfg)

    out = _read(db, "magnite_deal_demand").set_index("deal")
    assert sorted(out.index) == ["A", "B"]
    assert out.loc["B", "revenue_source"] == "Magnite Deals"
    for call in FakeMagnite.calls:
        _assert_one_day(call)


# ── 2c. deal-age metadata ───────────────────────────────────────────────────

def _meta_day(rows):
    return pd.DataFrame(rows, columns=["deal", "deal_id", "date", "bid_requests"])


def test_deal_age_is_cumulative_and_rerun_safe(db):
    # Pre-existing table as the old 180-day pull left it (deal_id read as float).
    seeded = pd.DataFrame([
        {"deal": "Old", "deal_id": 10.0, "first_seen": "2026-05-06", "last_seen": "2026-10-31",
         "days_seen": 150, "_pulled_at": "2026-11-01T09:05:00+00:00"},
        {"deal": "Idle", "deal_id": 20.0, "first_seen": "2026-06-01", "last_seen": "2026-07-01",
         "days_seen": 30, "_pulled_at": "2026-11-01T09:05:00+00:00"},
    ])
    with db.begin() as c:
        seeded.to_sql("magnite_deal_metadata", c, index=False)

    day = _meta_day([("Old", 10, "2026-11-01", 5), ("New", 30, "2026-11-01", 9)])
    FakeMagnite.responses = [day, day.copy()]
    refresh_cache.refresh_magnite_deal_metadata()
    refresh_cache.refresh_magnite_deal_metadata()   # same-day health-check re-run

    out = _read(db, "magnite_deal_metadata").set_index("deal")
    assert out.loc["Old", "first_seen"] == "2026-05-06"     # history kept
    assert out.loc["Old", "last_seen"] == "2026-11-01"
    assert out.loc["Old", "days_seen"] == 151               # +1 once, not twice
    assert out.loc["Idle", "last_seen"] == "2026-07-01"     # untouched deal kept
    assert out.loc["New", "first_seen"] == "2026-11-01"
    assert out.loc["New", "days_seen"] == 1
    assert len(out) == 3
    for call in FakeMagnite.calls:
        _assert_one_day(call)


def test_deal_age_first_run_on_empty_db(db):
    FakeMagnite.responses = [_meta_day([("A", 1, "2026-11-01", 3)])]
    refresh_cache.refresh_magnite_deal_metadata()
    out = _read(db, "magnite_deal_metadata")
    assert out[["deal", "first_seen", "last_seen", "days_seen"]].values.tolist() == [
        ["A", "2026-11-01", "2026-11-01", 1]]
