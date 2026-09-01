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

## What in this stack touches the disk

Everything below is per **day**, steady state:

| Consumer | Cadence | IO shape |
|---|---|---|
| Streamlit dashboard cold reload | every `_CACHE_TTL_SECONDS` (now **2h**, was 1h) | ~25 `SELECT *` table reads + 4 `GROUP BY` scans (`_load_dv_attention_agg` ×3, `_load_dv_ivt_agg`) + the `gam_lica × gam_creatives` join (`_load_li_max_duration`). Read-only; served from RAM while the working set fits. |
| Daily sweep (`refresh.yml`, 05:00 ET) | 1×/day | Rewrites nearly every table: DELETE+append (`gam_campaigns`, `magnite_*`, `pubmatic_deals`) or TRUNCATE+append (`_safe_replace` tables). Each DELETE-style rewrite turns the whole table into dead tuples that autovacuum must re-read and rewrite — write IO ≈ 2-3× the data size, plus WAL. |
| Intraday direct refresh (`refresh_direct.yml`) | hourly 07:00–20:00 ET (14×/day; was 4×/day when this doc was written) | `gam_campaigns` fully rewritten via DELETE+append each run — with the sweep, ~15 rewrites/day of that table and its 2 indexes, 3× the churn the 2026-08-03 numbers assumed. |
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
   *(Update 2026-08-17: the direct refresh is now hourly during business
   hours, so the dashboard can trail the freshest intraday pull by up to
   the full 2h TTL. The TTL choice stands — it just no longer surfaces
   every intraday point.)*
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

## Session note (2026-08-03)

The warning email was triaged from a cloud session with no `DATABASE_URL`
and the Supabase MCP connector unauthorized, so the measurement step
(dispatching the report workflow) is handed to the operator. Authorizing
the Supabase connector on claude.ai would let a future session read
`get_advisors` / infrastructure metrics directly.
