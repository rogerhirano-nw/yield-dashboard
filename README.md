# yield-dashboard

A Python toolkit for pulling ad revenue data from multiple sources into a local
cache and serving it through a fast Streamlit dashboard.

**Sources currently integrated:**
- **Magnite DV+** — programmatic (General + Prebid Analytics datasets)
- **Google Ad Manager (GAM)** — direct campaigns and PMP deals
- **Pubmatic** — PMP deal reporting

Structured for the live-dashboard use case: scheduled pull → local cache →
fast dashboard read. No source is queried at render time.

## Files

- `client.py` — `MagniteClient`: auth, create/poll/paginate loop, 429 backoff.
- `gam_client.py` — `GAMClient`: GAM delivery, pacing, and PMP deal reports.
- `pubmatic_client.py` — `PubmaticClient`: Pubmatic deal report.
- `refresh_cache.py` — scheduled-job entrypoint. Pulls all sources into SQLite.
  Wire to cron / Airflow / systemd timer.
- `dashboard.py` — minimal Streamlit dashboard reading from the cache.

## Quickstart

```bash
pip install requests pandas streamlit

# Magnite
export MAGNITE_KEY=...
export MAGNITE_SECRET=...
export MAGNITE_PUBLISHER_ID=...

# GAM (service account JSON path or inline credentials)
export GAM_NETWORK_CODE=...
export GAM_KEY_FILE=...

# Pubmatic
export PUBMATIC_API_KEY=...
export PUBMATIC_PUBLISHER_ID=...

# 1. populate the cache
python refresh_cache.py

# 2. run the dashboard
streamlit run dashboard.py
```

## Adding a new SSP

1. Create `<ssp>_client.py` with a `run_*_report()` method returning a DataFrame.
2. Add a `refresh_<ssp>()` function in `refresh_cache.py` following the same
   pattern: pull → add `_pulled_at` → DELETE stale rows → `to_sql(..., if_exists="append")`.
3. Call it from `main()` in `refresh_cache.py`.

## Magnite: switching from General to Prebid Analytics

The client is parametrized on dataset. The default is the General dataset
(`"default"` in the URL path). Two changes needed for Prebid:

1. In `client.py`, confirm the Prebid path slug from the logged-in docs at
   <https://help.magnite.com/help/prebid-analytics-api> and update the
   `Dataset` literal type if it's not `"prebid"`.
2. In `refresh_cache.py`, pass `dataset="prebid"` in each Magnite report config
   and replace the dimension/metric lists with the Prebid-specific column keys.

Pattern is identical (POST create, GET status, GET paginated data), so the
client code itself doesn't need to change.

## Magnite: things that will bite you

- **Pull yesterday, not today.** Same-day data has latency.
- **500K row cap per report.** High-cardinality dims (zone_id, hour) blow
  through this fast. Break by date range or pre-filter.
- **5 reports in parallel max** — beyond that you get 429s. The client retries
  on 429 with backoff, but if you're running this hourly across many reports
  you'll want to serialize them rather than fire in parallel.
- **Datasets are siloed.** You can't mix General + First Party + Prebid dims
  in one call. One client call per dataset.
- **The 429 can lie.** The doc warns it sometimes means a system-wide issue,
  not actual queue pressure. If 429s persist for more than an hour on a single
  report, escalate to Magnite support.

## Production hardening to consider

- Move the cache from SQLite to Postgres / BigQuery if more than one person
  reads the dashboard concurrently.
- Secrets to a vault / env injection in your orchestrator rather than shell env.
- Structured logging (JSON) + alerting on `MagniteReportFailed` / `MagniteReportTimeout`.
- Cross-reference the bidder dimension against your `Newsweek_TAM_Bidder_Reference.xlsx`
  short codes to break down Prebid metrics by partner the way Confiant will eventually
  report it.
