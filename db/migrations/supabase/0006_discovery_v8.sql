-- 0006_discovery_v8 (Supabase/Postgres) — espelho analítico da logic_version 8.
-- ATENÇÃO (UPDATE-0006 do Hermes): migration Supabase é passo MANUAL pós-deploy:
--   psql "$DATABASE_URL" -f db/migrations/supabase/0006_discovery_v8.sql

ALTER TABLE traders ADD COLUMN IF NOT EXISTS sim_expectancy_usd DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS sim_max_dd_pct DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS sim_factor DOUBLE PRECISION;
