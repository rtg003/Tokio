-- 0008_discovery_v10 (Supabase/Postgres) — espelho das colunas v10 (F2c, win_rate_30d).
-- Passo MANUAL pós-deploy:
--   psql "$DATABASE_URL" -f db/migrations/supabase/0008_discovery_v10.sql

ALTER TABLE traders ADD COLUMN IF NOT EXISTS n_trades_7d INTEGER;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS win_rate_30d DOUBLE PRECISION;
