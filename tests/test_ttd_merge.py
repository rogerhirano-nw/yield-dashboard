"""The TTD append path keeps a campaign's history across a report changeover.

When an upstream report is *replaced* rather than merely updated, its column
set changes. The old behaviour was to DROP and recreate the table, which
silently discarded every row the new export doesn't cover — and a replacement
report only reaches back to its own start date. The 2026-09 Chumba report was
exactly that case (the new export starts 09-06), so the table is now WIDENED to
the union of both schemas instead.

Runs against SQLite; `_widen_table_to` only issues ALTER TABLE ADD COLUMN, which
behaves the same on Postgres.
"""

from __future__ import annotations

import pandas as pd
import pytest
import sqlalchemy as sa

# refresh_cache pulls in the GAM SDK at import; CI installs requirements.txt,
# a bare checkout may not have it.
refresh_cache = pytest.importorskip(
    "refresh_cache", reason="refresh_cache needs the GAM SDK installed"
)

_widen_table_to = refresh_cache._widen_table_to
_sql_type_for = refresh_cache._sql_type_for
_warn_if_report_stale = refresh_cache._warn_if_report_stale


RETIRED_ERA = pd.DataFrame([{
    "date": "2026-08-20",
    "ad_group": "CC_ACQ_Display",          # only the retired report had these
    "supply_vendor": "Google",
    "conversions_pixel_01": 2,
    "impressions": 1000,
    "media_spend_usd": 9.0,
    "attributed_conversions": 2,
}])

REPLACEMENT_ERA = pd.DataFrame([{
    "date": "2026-09-07",
    "creative_size": "320x50",             # only the replacement report has these
    "deal_name": "Newsweek_PG_Gambling_…",
    "conversions_first_purchase_household": 7,
    "impressions": 5000,
    "media_spend_usd": 30.0,
    "attributed_conversions": 7,
}])


@pytest.fixture()
def conn(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path/'t.db'}")
    with engine.begin() as c:
        RETIRED_ERA.to_sql("ttd_x", c, if_exists="append", index=False)
        yield c


def _rows(conn) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM ttd_x", conn)


def test_widening_keeps_rows_the_new_report_does_not_cover(conn):
    """The whole point: a replaced report must not cost us the earlier era."""
    _widen_table_to(conn, "ttd_x", REPLACEMENT_ERA)
    REPLACEMENT_ERA.to_sql("ttd_x", conn, if_exists="append", index=False)

    got = _rows(conn)
    assert len(got) == 2
    august = got[got["date"] == "2026-08-20"].iloc[0]
    assert august["ad_group"] == "CC_ACQ_Display"
    assert august["media_spend_usd"] == 9.0


def test_the_table_becomes_the_union_of_both_schemas(conn):
    _widen_table_to(conn, "ttd_x", REPLACEMENT_ERA)

    cols = set(_rows(conn).columns)
    assert {"ad_group", "supply_vendor", "conversions_pixel_01"} <= cols
    assert {"creative_size", "deal_name",
            "conversions_first_purchase_household"} <= cols


def test_columns_absent_from_an_era_read_null_for_it(conn):
    _widen_table_to(conn, "ttd_x", REPLACEMENT_ERA)
    REPLACEMENT_ERA.to_sql("ttd_x", conn, if_exists="append", index=False)

    got = _rows(conn).set_index("date")
    assert pd.isna(got.loc["2026-09-07", "ad_group"])
    assert pd.isna(got.loc["2026-08-20", "creative_size"])


def test_widening_never_drops_or_retypes_an_existing_column(conn):
    before = {c["name"]: str(c["type"])
              for c in sa.inspect(conn).get_columns("ttd_x")}
    _widen_table_to(conn, "ttd_x", REPLACEMENT_ERA)
    after = {c["name"]: str(c["type"])
             for c in sa.inspect(conn).get_columns("ttd_x")}

    assert before.items() <= after.items()


def test_widening_is_idempotent(conn):
    _widen_table_to(conn, "ttd_x", REPLACEMENT_ERA)
    _widen_table_to(conn, "ttd_x", REPLACEMENT_ERA)  # must not raise

    names = [c["name"] for c in sa.inspect(conn).get_columns("ttd_x")]
    assert len(names) == len(set(names))


def test_an_unchanged_schema_adds_nothing(conn):
    before = [c["name"] for c in sa.inspect(conn).get_columns("ttd_x")]
    _widen_table_to(conn, "ttd_x", RETIRED_ERA)
    assert [c["name"] for c in sa.inspect(conn).get_columns("ttd_x")] == before


@pytest.mark.parametrize("values, expected", [
    ([1, 2], "bigint"),
    ([1.5, 2.5], "double precision"),
    ([True, False], "boolean"),
    (["a", "b"], "text"),
])
def test_sql_type_for(values, expected):
    assert _sql_type_for(pd.Series(values)) == expected


# ── the stale-report canary ────────────────────────────────────────────────

def test_a_frozen_report_warns(conn, caplog):
    """A report that doesn't advance the cache is the silent-freeze signature."""
    frozen = pd.DataFrame([{"date": "2026-08-20", "impressions": 1}])
    with caplog.at_level("WARNING"):
        _warn_if_report_stale(conn, "ttd_x", frozen)
    assert "NOT newer than the cache" in caplog.text


def test_an_advancing_report_does_not_warn(conn, caplog):
    with caplog.at_level("WARNING"):
        _warn_if_report_stale(conn, "ttd_x", REPLACEMENT_ERA)
    assert "NOT newer" not in caplog.text


def test_the_canary_never_breaks_the_write(conn):
    """Advisory only — a table it can't read must not abort the refresh."""
    _warn_if_report_stale(conn, "no_such_table", REPLACEMENT_ERA)
