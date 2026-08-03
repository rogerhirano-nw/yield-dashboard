"""Diagnose what is consuming the Supabase project's Disk IO Budget.

Context: Supabase emailed a "your project is depleting its Disk IO Budget"
warning for the prod cache (project ltavpsikmmqmracvjtvk, Micro compute,
2026-08-03). Every instance below 4XL runs on burstable disk — a daily
budget of above-baseline IO — and the depletion signal doesn't say *which*
activity is spending it. This script reads Postgres's own IO accounting so
the driver can be named instead of guessed:

  1. Database / relation sizes           — is anything unexpectedly large?
  2. Cache hit ratios                    — reads served from RAM vs disk.
  3. Per-table disk blocks read + churn  — pg_statio_user_tables joined with
                                           pg_stat_user_tables (dead tuples,
                                           ins/del volume, autovacuum counts):
                                           the read-IO and the write/vacuum-IO
                                           story per table.
  4. Index sizes + usage                 — bloated or never-scanned indexes
                                           cost write IO on every rewrite.
  5. Temp files                          — sorts/hashes spilling to disk.
  6. Top statements by disk IO           — pg_stat_statements ordered by
                                           shared_blks_read and _written
                                           (skipped gracefully if the
                                           extension isn't available).

Counters are cumulative since `stats_reset`, so the report prints that
timestamp and per-day rates where it matters. Read-only by default.

Optional maintenance:
    --vacuum    run VACUUM (ANALYZE) on every public table after the report
                (plain VACUUM, not FULL — reclaims dead tuples without
                exclusive locks; safe while the dashboard is live).

Usage (locally with .env, or via the db_disk_io_report.yml workflow):
    python scripts/db_disk_io_report.py [--vacuum]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import sqlalchemy
from sqlalchemy import text


def _load_dotenv() -> None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _run(conn, title: str, sql: str, fmt) -> None:
    """Run one report query; a failure (missing view/column across PG
    versions) prints the error instead of aborting the whole report."""
    _section(title)
    try:
        rows = conn.execute(text(sql)).fetchall()
    except Exception as e:  # noqa: BLE001 — report and move on
        conn.rollback()  # clear the aborted tx so later sections still run
        print(f"  (query failed: {e})")
        return
    if not rows:
        print("  (no rows)")
        return
    for r in rows:
        print(fmt(r))


def report(conn) -> None:
    _run(conn, "Database size + stats window", """
        SELECT current_database(),
               pg_size_pretty(pg_database_size(current_database())),
               (SELECT stats_reset FROM pg_stat_database
                 WHERE datname = current_database()),
               now()
    """, lambda r: (f"  db={r[0]}  size={r[1]}\n"
                    f"  stats since={r[2]}  now={r[3]}\n"
                    "  (all counters below are cumulative since that reset)"))

    _run(conn, "Cache hit ratio (reads served from RAM vs disk)", """
        SELECT sum(heap_blks_read)::bigint, sum(heap_blks_hit)::bigint,
               sum(idx_blks_read)::bigint,  sum(idx_blks_hit)::bigint
        FROM pg_statio_user_tables
    """, lambda r: (
        f"  heap: {r[0] or 0:>12,} disk blks read   "
        f"{(100.0 * (r[1] or 0) / max((r[0] or 0) + (r[1] or 0), 1)):5.1f}% hit\n"
        f"  idx : {r[2] or 0:>12,} disk blks read   "
        f"{(100.0 * (r[3] or 0) / max((r[2] or 0) + (r[3] or 0), 1)):5.1f}% hit\n"
        "  (1 blk = 8 kB. Hit% well under ~99 on a steady workload means the\n"
        "   working set no longer fits in RAM — reads are hitting disk.)"))

    _run(conn, "Per-table: size, disk reads, write churn, autovacuum", """
        SELECT s.relname,
               pg_size_pretty(pg_total_relation_size(s.relid)),
               io.heap_blks_read + coalesce(io.idx_blks_read, 0)
                 + coalesce(io.toast_blks_read, 0),
               s.seq_scan, s.seq_tup_read,
               s.n_live_tup, s.n_dead_tup,
               s.n_tup_ins, s.n_tup_del, s.n_tup_upd,
               s.autovacuum_count, s.last_autovacuum::date
        FROM pg_stat_user_tables s
        JOIN pg_statio_user_tables io ON io.relid = s.relid
        ORDER BY io.heap_blks_read + coalesce(io.idx_blks_read, 0) DESC
    """, lambda r: (
        f"  {r[0]:<26} {r[1]:>9}  disk_blks_read={r[2] or 0:>10,}  "
        f"seq_scan={r[3] or 0:>7,} (tup {r[4] or 0:>12,})\n"
        f"  {'':<26} live={r[5] or 0:>8,} dead={r[6] or 0:>8,}  "
        f"ins={r[7] or 0:>10,} del={r[8] or 0:>10,} upd={r[9] or 0:>8,}  "
        f"autovac={r[10] or 0:>5,} last={r[11]}"))

    _run(conn, "Indexes: size + scans (unused indexes still cost write IO)", """
        SELECT i.relname, i.indexrelname,
               pg_size_pretty(pg_relation_size(i.indexrelid)),
               pg_relation_size(i.indexrelid), i.idx_scan
        FROM pg_stat_user_indexes i
        ORDER BY pg_relation_size(i.indexrelid) DESC
    """, lambda r: f"  {r[0]:<26} {r[1]:<38} {r[2]:>9}  scans={r[4] or 0:,}")

    _run(conn, "Temp files (sorts/hashes spilling to disk)", """
        SELECT temp_files, pg_size_pretty(temp_bytes),
               pg_size_pretty(coalesce(temp_bytes, 0)
                 / greatest(extract(day from now() - stats_reset), 1)::bigint)
        FROM pg_stat_database
        WHERE datname = current_database()
    """, lambda r: (f"  temp_files={r[0]:,}  temp_bytes={r[1]}  (~{r[2]}/day)\n"
                    "  (large values → queries spilling past work_mem; that IO\n"
                    "   comes straight out of the burst budget)"))

    # pg_stat_statements: enabled by default on Supabase, but guard anyway.
    # total_exec_time is PG13+; Supabase runs PG15+, fine.
    _run(conn, "Top statements by disk blocks READ (pg_stat_statements)", """
        SELECT calls, shared_blks_read, shared_blks_written,
               temp_blks_read + temp_blks_written,
               round(total_exec_time::numeric) AS ms,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 110)
        FROM pg_stat_statements
        WHERE shared_blks_read > 0
        ORDER BY shared_blks_read DESC
        LIMIT 15
    """, lambda r: (f"  calls={r[0]:>7,} read={r[1]:>9,} written={r[2]:>8,} "
                    f"temp={r[3]:>7,} time={r[4]:>9,}ms\n    {r[5]}"))

    _run(conn, "Top statements by disk blocks WRITTEN (pg_stat_statements)", """
        SELECT calls, shared_blks_written, shared_blks_read,
               round(total_exec_time::numeric) AS ms,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 110)
        FROM pg_stat_statements
        WHERE shared_blks_written > 0
        ORDER BY shared_blks_written DESC
        LIMIT 15
    """, lambda r: (f"  calls={r[0]:>7,} written={r[1]:>9,} read={r[2]:>8,} "
                    f"time={r[3]:>9,}ms\n    {r[4]}"))


def vacuum_all(engine) -> None:
    """VACUUM (ANALYZE) every public table. Plain VACUUM — no exclusive
    locks, safe against a live dashboard. Must run outside a transaction."""
    _section("VACUUM (ANALYZE) all public tables")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "ORDER BY tablename"))]
        for t in tables:
            ident = 'public."' + t.replace('"', '""') + '"'
            try:
                conn.execute(text(f"VACUUM (ANALYZE) {ident}"))
                print(f"  vacuumed {t}")
            except Exception as e:  # noqa: BLE001
                print(f"  FAILED   {t}: {e}")


def main(argv: list[str]) -> int:
    _load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2
    engine = sqlalchemy.create_engine(
        url, pool_size=1, max_overflow=0,
        connect_args={"connect_timeout": 10},
    )
    with engine.connect() as conn:
        report(conn)
    if "--vacuum" in argv:
        vacuum_all(engine)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
