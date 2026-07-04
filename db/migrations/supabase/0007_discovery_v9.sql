-- 0007_discovery_v9 (Supabase/Postgres) — espelho analítico da logic_version 9.
-- ATENÇÃO (UPDATE-0006 do Hermes): migration Supabase é passo MANUAL pós-deploy:
--   psql "$DATABASE_URL" -f db/migrations/supabase/0007_discovery_v9.sql
-- Sem isso o replicator falha com PGRST204 nas colunas novas.

ALTER TABLE traders ADD COLUMN IF NOT EXISTS coverage_days DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS sim_half_old_net DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS sim_half_new_net DOUBLE PRECISION;
