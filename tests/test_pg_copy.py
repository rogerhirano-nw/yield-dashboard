"""The sweep's Postgres appends go through COPY, not multi-row INSERT.

pg_stat_statements kept one ~250 KB entry per distinct `INSERT … VALUES`
batch size — 464 MB of query text against an 89 MB database — and Supabase's
per-minute metrics scrape spilled ~460 GB/day of temp files reading it (the
2026-10 Disk IO Budget warning, docs/supabase_disk_io.md). COPY is one
constant statement per table, so the view stays small.

Equivalence against a real Postgres (4,900 rows, every dtype the sweep writes,
'' vs NULL, quotes/commas/newlines, inf, tz-aware timestamps, Int64 NA) was
proven when this landed; these tests pin the statement shape and the NULL
encoding without needing a server.
"""

from __future__ import annotations

import csv
import io
from types import SimpleNamespace

import pandas as pd
import pytest
import sqlalchemy as sa

refresh_cache = pytest.importorskip(
    "refresh_cache", reason="refresh_cache needs the GAM SDK installed"
)


class _FakeCursor:
    def __init__(self, calls):
        self.calls = calls

    def copy_expert(self, sql, buf):
        self.calls.append((sql, buf.read()))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run_copy(rows, keys=("a", "Mixed Case"), schema=None, name="t"):
    calls = []
    conn = SimpleNamespace(connection=SimpleNamespace(cursor=lambda: _FakeCursor(calls)))
    refresh_cache._pg_copy(SimpleNamespace(schema=schema, name=name), conn, list(keys), iter(rows))
    return calls


def test_copy_statement_is_constant_regardless_of_row_count():
    one = _run_copy([("x", 1)])
    many = _run_copy([("x", i) for i in range(2500)])
    assert one[0][0] == many[0][0]
    assert one[0][0] == """COPY "t" ("a", "Mixed Case") FROM STDIN WITH (FORMAT csv, NULL '\\N')"""


def test_copy_keeps_empty_string_distinct_from_null():
    (_, payload), = _run_copy([("", None), (None, 'q"uote, comma')])
    rows = list(csv.reader(io.StringIO(payload)))
    assert rows == [["", r"\N"], [r"\N", 'q"uote, comma']]


def test_copy_qualifies_schema():
    (sql, _), = _run_copy([("x", 1)], schema="public")
    assert sql.startswith('COPY "public"."t" ')


def test_sqlite_append_keeps_pandas_default():
    eng = sa.create_engine("sqlite://")
    df = pd.DataFrame({"a": ["x", "", None], "n": [1.0, None, 3.0]})
    with eng.begin() as conn:
        refresh_cache._to_sql_append(df, "t", conn)
        refresh_cache._to_sql_append(df, "t", conn)
        out = pd.read_sql("SELECT * FROM t", conn)
    assert len(out) == 6
    assert out["a"].tolist()[:2] == ["x", ""]
    assert pd.isna(out["a"][2])
