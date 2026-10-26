# Supabase Disk IO Budget — diagnosis + runbook

**Trigger:** Supabase emailed a "your project is depleting its Disk IO
Budget" warning for the prod cache (project `ltavpsikmmqmracvjtvk`,
2026-08-03). The project runs **Micro compute**, and every Supabase
instance below 4XL has *burstable* disk: a baseline throughput/IOPS plus a
daily budget of above-baseline bursting. When the budget is spent,
queries queue on IO wait and the instance can become unresponsive.

Supabase's guide: <https://supabase.com/docs/guides/troubleshooting/exhaust-disk-io>
Daily consumption graph: project → Settings → Infrastructure → Disk IO.
Hourly: project → Reports → Database.

## Root cause found (2026-10-21, second warning): pg_stat_statements text bloat

The second "depleting its Disk IO Budget" email was measured directly through
the claude.ai Supabase connector. **None of the suspects in the table below was
the driver.** The numbers since the 2026-08-10 restart (72 days):

| Signal | Value | Reading |
|---|---|---|
| Database size | **89 MB** | Fits in RAM many times over |
| Cache-hit ratio | **100.00%** (8.6k blocks read vs 510M hit) | Table reads never touch disk |
| `pg_stat_database.temp_bytes` | **33 TB** across 189,692 temp files | **≈ 460 GB/day of temp-file writes — this is the budget** |
| `pg_stat_statements` query text | **464 MB** in 4,201 entries (3,266 over 10 KB, max 252 KB) | 5× the size of the database it describes |

The text was the sweep's own writes: `DataFrame.to_sql` on SQLAlchemy 2.0 sends
each append as multi-row `INSERT … VALUES ($1, …, $15000)` batches, and
pg_stat_statements normalizes constants but keeps **one entry per distinct
batch size**. The final batch's row count changes every run, so every sweep
added new ~250 KB entries (`gam_campaigns` 1,257 entries / 281 MB,
`dv_attention` 1,106 / 120 MB, `dv_ivt` 318 / 30 MB, …). Supabase's metrics
exporter and the dashboard's Query Performance page read that view (the
exporter about every minute), and each read sorted/hashed the 464 MB of text
and spilled ~300-600 MB to temp files. That spill is invisible to
pg_stat_statements itself (only ~3 GB of the 33 TB was attributed to tracked
statements), which is why the per-statement report never pointed at it, and
it grew every day with nothing in our stack reading it.

**Fix:**
1. `refresh_cache._to_sql_append` routes every sweep append through
   `_pg_copy`, a `to_sql(method=…)` callable that bulk-loads with
   `COPY … FROM STDIN (FORMAT csv, NULL '\N')`. That is one constant
   statement per table, however many rows, so the view stays a few KB.
   It is also fewer round trips and less parse/plan work than 1000-row INSERTs.
   On SQLite (local/demo) it falls back to pandas' default. Equivalence was
   proven on a real Postgres 16 (4,900 rows, every dtype the sweep writes, `''`
   vs NULL, quotes/commas/newlines, `inf`, tz-aware timestamps, Int64 NA,
   appending to an existing table): identical frames and column types.
   `tests/test_pg_copy.py` pins the statement shape and the NULL encoding.
2. `select pg_stat_statements_reset();` was run on prod (2026-10-21 12:55 UTC)
   to drop the 464 MB already stored. The fix stops new bloat, but only a reset
   removes what's there.

**How to check it's still healthy** (SQL editor or the connector):

```sql
select count(*), pg_size_pretty(sum(length(query))::bigint) from pg_stat_statements;
select pg_size_pretty(temp_bytes), temp_files from pg_stat_database where datname = current_database();
```

Query text should stay in the low MB, and `temp_bytes` should grow by
megabytes a day, not hundreds of GB. If the text grows again, look for a new
writer that isn't going through `_to_sql_append`, i.e. any `to_sql` or
`executemany` with a varying row count (the one-off `scripts/seed_*.py` still
use plain `to_sql`; that's fine for a run or two, but reset afterwards if one
is used heavily).

**Rule:** bulk writes to prod Postgres go through `_to_sql_append` (COPY).
Never a plain `df.to_sql(...)` in the sweep.

## What in this stack touches the disk

Everything below is per **day**, steady state:

| Consumer | Cadence | IO shape |
|---|---|---|
| Streamlit dashboard cold reload | every `_CACHE_TTL_SECONDS` (now **2h**, was 1h) | ~25 `SELECT *` table reads + 4 `GROUP BY` scans (`_load_dv_attention_agg` ×3, `_load_dv_ivt_agg`) + the `gam_lica × gam_creatives` join (`_load_li_max_duration`). Read-only; served from RAM while the working set fits. |
| Daily sweep (`refresh.yml`, 05:00 ET) | 1×/day | Rewrites nearly every table: DELETE+append (`gam_campaigns`, `magnite_*`, `pubmatic_deals`) or TRUNCATE+append (`_safe_replace` tables). Each DELETE-style rewrite turns the whole table into dead tuples that autovacuum must re-read and rewrite — write IO ≈ 2-3× the data size, plus WAL. |
| Intraday direct refresh (`refresh_direct.yml`) | 4×/day | `gam_campaigns` fully rewritten via DELETE+append each run — 5 total rewrites/day of that table and its 2 indexes. |
| Health check | 2-3×/day | Cheap `MAX()` freshness probes + two `LIKE '%.0'` scans of the (now small) DV tables + the RLS catalog query. When a *remediable* check fails it re-runs the **whole sweep** (max 2×/day) — a failing-source incident doubles or triples the sweep's write IO for as long as it lasts (the 6/29→7/27 `ttd_luckyland` RLS loop did exactly this daily). |
| Supabase platform | continuous | Daily backup reads the whole disk; logs/metrics services have their own floor. Fixed cost — only compute upgrade changes it. |

None of these is individually huge — the cache is a few hundred MB at
most — which is exactly why the answer has to be **measured, not
guessed**: on a 1 GB-RAM Micro the difference between "reads come from
page cache" and "reads hit disk" is whether the working set + bloat still
fits in memory.

## How to find the actual driver

Dispatch **`db_disk_io_report.yml`** (Actions tab → "DB disk-IO report").
It runs `scripts/db_disk_io_report.py` against prod and prints, per table
and per statement, Postgres's own IO accounting. How to read it:

- **Cache hit ratio < ~99%** → the working set no longer fits in RAM;
  reads are hitting disk on every dashboard reload. Fix: shrink/bloat-fix
  the hot tables, or upgrade compute (more RAM is the real product of the
  upgrade, not the IOPS).
- **A table with `dead` ≫ `live`, or huge `ins`/`del` counts** → churn
  bloat from the DELETE+append pattern. Fix: dispatch the workflow again
  with `vacuum=1` (plain `VACUUM (ANALYZE)`, lock-free); if size doesn't
  come down, a one-off `VACUUM FULL <table>` in the SQL editor during a
  quiet hour rewrites it compact (takes an exclusive lock — seconds on
  these table sizes).
- **`temp_bytes` growing by GBs/day** → some query spills past `work_mem`;
  the top-statements section names it.
- **Top statements by blocks read/written** → names the exact SQL spending
  the budget. Dashboard queries show up as the `pd.read_sql` text; sweep
  writes as `INSERT INTO ...`.
- Counters are cumulative since `stats_reset` (printed in the header) —
  divide by the elapsed days for rates.

## Levers, cheapest first

1. **Done (this PR): dashboard TTL 1h → 2h** — halves the recurring read
   load. The intraday direct refreshes land 3h apart, so ≤2h staleness
   doesn't lose a data point; Settings-save and the debug "Clear cache +
   re-query" button still force an immediate reload.
2. **Vacuum the churn tables** (`db_disk_io_report.yml` with `vacuum=1`)
   whenever the report shows dead-tuple pileup. Months of daily
   DELETE+append had never been followed by a manual vacuum.
3. **Fix failing sources promptly** — every day a freshness check fails,
   auto-remediation re-runs the full sweep (up to 2×), multiplying write
   IO. The health-check email is the tell.
4. **Convert `refresh_gam`'s DELETE+append to `_safe_replace`**
   (TRUNCATE+append) if the report fingers `gam_campaigns` churn: 5
   rewrites/day × (table + 2 indexes) of dead tuples vs. a single WAL
   record per TRUNCATE. Not done pre-emptively — verify on prod first
   that no consumer depends on rows surviving a missed sweep.
5. **Upgrade compute Micro → Small** (project → Settings → Compute and
   Disk). Doubles RAM (the page cache) and the disk baseline. This is
   Supabase's own recommendation when the budget binds structurally and
   costs ~$15/mo on top of the Pro credit. If the report shows a healthy
   cache-hit ratio and modest churn yet the budget still depletes, the
   floor is platform overhead and this is the only lever left.

## Session note (2026-08-03, superseded by the root cause above)

The warning email was triaged from a cloud session with no `DATABASE_URL`
and the Supabase MCP connector unauthorized, so the measurement step
(dispatching the report workflow) is handed to the operator. Authorizing
the Supabase connector on claude.ai would let a future session read
`get_advisors` / infrastructure metrics directly.
