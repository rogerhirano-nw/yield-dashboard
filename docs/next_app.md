# The next dashboard: FastAPI + React (`next/`)

Replacing the Streamlit dashboard (`dashboard.py`), view by view. Decided
2026-11-28 after measuring the old one: ~20 s to first load after the 2 h
cache expires (30 serial DB reads) and 1.5–2.7 s *per click* on a fast Mac
(the whole 9,450-line script reruns on every interaction; more on the B1
core). The new app, same data and same `dashboard_logic` decisions:

| | Streamlit | next |
|---|---|---|
| Cold start (all tables) | ~20 s, paid by a user | 4.6 s, in the background |
| Prep per data refresh | every click | once (~2 s), before anyone asks |
| Click (filter, triage, page) | 1.5–2.7 s (Mac) | ~25 ms API, ~80 ms in the browser |
| Open a row's drawer | full rerun | ~75 ms |

**Live side by side:** https://adops-next.azurewebsites.net (same Newsweek
login). Ported so far: **Campaigns → Direct** (filters, triage, KPI strip,
table, drawer) and **Priority flights** (TTD CPA monitor). Other tabs link to the old dashboard until ported.

## How it works
- `next/api/data.py` — keeps every cache table in memory. A background thread
  checks Postgres' per-table write counters (`pg_stat_user_tables`) every 60 s
  and re-reads only tables that changed, in parallel; then runs each view's
  *warmer* (its prepared frame) so no request pays for it.
- `next/api/settings.py` — settings exactly as dashboard.py builds them
  (copied; `dashboard_settings` row 'main', else settings.json).
- `next/api/campaigns.py` — `_build()` prepares one row per Direct line item
  with every value, band and DV join already decided (via `dashboard_logic`);
  `/api/campaigns` applies filters/triage and sums the KPIs;
  `/api/campaigns/{line_item_id}` serves the drawer's daily series.
- `next/web/` — React + TypeScript (Vite). One page component per view under
  `src/`, plain CSS with the Newsweek "Paper" tokens (`src/index.css`, same
  values as dashboard.py's `:root`). Filters live in the URL, so every view is
  a link. No UI framework, no state library — deliberately boring.

## Run locally
```bash
DATABASE_URL=... python -m uvicorn next.api.main:app --port 8000   # API (+ built web/dist)
cd next/web && npm install && npm run dev                           # React dev server, proxies /api
```

## Deploy
Push to `main` touching `next/**` (or dispatch `deploy_next.yml`): builds the
React app, zips it with the repo, deploys to web app **`adops-next`** (plan
`nw-adops-yield-plan`, startup `python -m uvicorn next.api.main:app --host
0.0.0.0 --port 8000`). App settings: `DATABASE_URL` is a Key Vault reference
to `yield-db-url`; Entra auth copied from `adops` (same app registration, its
redirect URIs include adops-next). Secret: `AZURE_NEXT_PUBLISH_PROFILE`.

## Porting a view (the checklist)
1. Spec the Streamlit view (every column, formula, setting, with line numbers).
2. Add `next/api/<view>.py`: a `_build(store)` registered as a warmer + routes.
   Keep decisions in `dashboard_logic`; never re-derive a band in TypeScript.
3. Add the React view; link the tab.
4. **Prove parity** on prod data with a script like
   `next/scripts/parity_campaigns.py` (runs the old view headless and compares).
   Campaigns: triage counts and all nine KPI values + trend chips identical
   (2026-11-28).
5. When every tab is ported: point `adops` at this app, retire `dashboard.py`.

Remaining: Campaigns → PMP section; By site/size;
By DSP; Magnite; Pubmatic; OpenSincera; Tools; Configure (settings editor).
