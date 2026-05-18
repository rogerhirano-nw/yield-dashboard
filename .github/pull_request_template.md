<!--
  Title format (keep under 70 chars):
    fix: <what>     — for bug fixes
    feat: <what>    — for new behavior the user can see
    chore: <what>   — for refactors, deps, repo hygiene
    docs: <what>    — README / CLAUDE.md / templates only

  Delete any section below that doesn't apply.
-->

## Summary

<!-- One paragraph. State the problem first, then the fix. Be specific —
     name the file, function, table, or metric you touched.

     Good example:
     "gam_client.run_deals_report returned 0 Private Auction rows because
      AD_SERVER_IMPRESSIONS only counts Ad-Server-served traffic; PA serves
      through Ad Exchange. Switch to IMPRESSIONS / REVENUE_WITHOUT_CPD —
      the metric set the GAM UI's Programmatic report uses." -->

## Changes

<!-- Bullet per file or logical change. Don't restate what the diff already shows;
     do explain *why* if the diff alone wouldn't make it obvious. -->

- 

## Verification

<!-- How you know it works. Quote concrete numbers where you can.

     For data-pipeline changes: refresh log line + Postgres row count / sum.
     For dashboard changes: tab, filter state, what should appear, screenshot if useful.
     For diagnostics-only changes: which `tmp_*.yml` workflow ran, link to the run.

     Example:
     "Refresh log: `GAM deals report: 577 rows, channels={'Preferred Deals': 467,
      'Private Auction': 71, ...}`. Postgres `gam_pmp_deals` shows 71 PA rows /
      170,507 imps / \$930.15 over the last 7 days. Sample Ford-Always-On row
      matches a GAM UI export." -->

- 

## Schema / data impact

<!-- Required for anything that touches refresh_cache, settings.json, or a DB table. -->

- [ ] No schema change — existing tables/columns untouched
- [ ] Existing table changes shape (column added/removed/renamed). `refresh_one_report` drop-and-recreate handles the migration on the next refresh
- [ ] New table introduced: `<table_name>`
- [ ] `settings.json` field added → also added to `_DEFAULT_SETTINGS` so empty-DB envs work, and (if per-SSP / per-direct-source) backfilled in `_load_settings` via the relevant `_patch_*` helper so stale DB snapshots heal automatically
- [ ] Cache freshness: `<table>` will be refilled by the next scheduled refresh — no manual backfill needed
- [ ] Backfill IS needed because: <reason>

## Test plan

<!-- Specific, reviewer-actionable. At least one item per checkable behavior. -->

- [ ] `python3 -m py_compile dashboard.py gam_client.py refresh_cache.py` passes
- [ ] `refresh.yml` log shows no `ERROR Refresh failed for *`
- [ ] Postgres: <table query that confirms expected rows, e.g. counts by channel / SSP>
- [ ] Dashboard, **<tab name>** → **<section>**: <expected behavior / values>
- [ ] No regression: <existing-feature> still renders the same numbers as before

## Deploy

- [ ] Requires `main` → `mac-studio` merge so Streamlit Cloud picks this up
- [ ] Requires `refresh.yml` re-run after merge to repopulate `<table>` (otherwise stale cache hides the change until 09:00 UTC)
- [ ] No deploy action needed — governance / docs / refactor with no runtime change

## Notes for reviewer

<!-- Optional. Things this PR deliberately defers, multi-laptop coordination
     (e.g. "the other laptop has an in-flight PR touching settings.json — merge
     order matters"), or follow-ups already queued. -->
