# Moving the cache database from Supabase to Azure

The dashboard cache (~89 MB of Postgres) lives in Supabase, a third-party
account. This moves it to **Azure Database for PostgreSQL – Flexible Server**
in Newsweek's own Azure subscription. The code is plain SQLAlchemy/psycopg2
against Postgres, so the switch is **one connection string** (`DATABASE_URL`).

## What is Supabase-specific (and how it behaves on Azure)
- **RLS hygiene** (health check + `_lockdown_table`) exists only to close
  Supabase's public REST API. Azure has no such API and no `anon` /
  `authenticated` roles, so the health check reports it as passing
  ("not Supabase") and `_lockdown_table`'s RLS enable is a harmless no-op
  (the app connects as the table owner). `docs/supabase_rls_lockdown.sql`
  is not needed on Azure.
- The pooler connect retry in `refresh_cache` is generic and stays.
- `scripts/db_disk_io_report.py` reads `pg_stat_statements`; enable that
  extension on Azure (server parameter `azure.extensions`) if you want it.

## 1. Server (Newsweek IT / Azure admin)
- Azure Database for PostgreSQL **Flexible Server**, **PostgreSQL 17**.
- Size: **Burstable B1ms** (1 vCore, 2 GiB) + **32 GiB** storage is plenty
  for an ~89 MB database; roughly the price of the current Supabase plan.
  Step up to B2s if the dashboard feels slow.
- Database `yield`, an admin login for the app, **SSL required** (default).
- Networking: the daily jobs run on **GitHub-hosted runners** and the
  dashboard on **Streamlit Cloud**; neither has fixed IPs. Either allow
  public access (0.0.0.0–255.255.255.255) protected by SSL + a strong
  password — the same exposure Supabase's connection string has today — or
  move the jobs/dashboard into Azure too and use private networking.
- Connection string format for the secret:
  `postgresql://<user>:<password>@<server>.postgres.database.azure.com:5432/yield?sslmode=require`

## 2. Copy the data
1. Add repo secret **`AZURE_DATABASE_URL`** (the new server; prod still uses
   `DATABASE_URL` = Supabase).
2. Run the sweep-free window: after the daily refresh (09:00 UTC) and its
   health check have finished.
3. Actions → **Migrate DB to Azure (one-off)** → Run, type `copy`.
   It dumps Supabase's public schema (read-only), restores into Azure, and
   compares row counts per table — the run fails on any mismatch.

## 3. Cut over (same day as the copy)
Point every `DATABASE_URL` at the Azure string:
1. GitHub repo secret `DATABASE_URL`.
2. Streamlit Cloud → app → Settings → Secrets → `DATABASE_URL`.
3. The `.env` on the Mac that runs the Confiant jobs, if it uses the DB.
Then dispatch `refresh.yml` and `health_check.yml` — all green — and open
the dashboard. Rows written to Supabase between the copy and the cutover are
not carried over; the next daily sweep re-pulls those windows anyway.

## 4. Afterwards
- Keep Supabase **paused, not deleted**, for 2 weeks as a rollback (swap
  `DATABASE_URL` back). Then delete the project.
- Remove `AZURE_DATABASE_URL` once `DATABASE_URL` points at Azure.
