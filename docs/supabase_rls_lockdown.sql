-- ============================================================================
-- Supabase RLS lockdown for the yield-dashboard project.
--
-- Run once in Supabase → SQL Editor.
--
-- What it does:
--   1. Enables Row-Level Security on every public-schema table.
--   2. Adds no policies — so the anon + authenticated roles get nothing
--      via the PostgREST REST API (https://*.supabase.co/rest/v1/...).
--   3. The dashboard's DATABASE_URL connection (postgres / service_role)
--      bypasses RLS for table owners, so it keeps working unchanged.
--
-- Why:
--   Supabase's auto-security check flagged rls_disabled_in_public +
--   sensitive_columns_exposed. The dashboard uses direct postgres
--   connections, not the REST API — but the REST API was still wide open
--   because RLS was off. This closes the REST surface without touching
--   the dashboard.
--
-- Verification:
--   - After running, hit https://ltavpsikmmqmracvjtvk.supabase.co/rest/v1/gam_campaigns
--     with the anon key — should return [] (empty), not the table.
--   - Reload the dashboard — should still render data as before.
-- ============================================================================

-- Enable RLS on every existing user table in the public schema, all in one go.
DO $$
DECLARE
    t record;
BEGIN
    FOR t IN
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          -- Skip Supabase-internal tables if any have leaked in.
          AND tablename NOT LIKE 'pg_%'
          AND tablename NOT LIKE '_realtime%'
    LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY;',
                       t.schemaname, t.tablename);
        RAISE NOTICE 'RLS enabled: %.%', t.schemaname, t.tablename;
    END LOOP;
END $$;

-- Belt-and-suspenders: revoke all REST API privileges from anon +
-- authenticated. Even if a future schema change forgets RLS, these
-- roles can't reach the data.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated;

-- For any tables created in the future, default-revoke the same way.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM anon, authenticated;
