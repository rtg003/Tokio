-- 0005_discovery_v7 (Supabase/Postgres) — espelho analítico da logic_version 7.
-- Aplicado automaticamente pelo bootstrap/autodeploy (idempotente).

ALTER TABLE traders ADD COLUMN IF NOT EXISTS max_current_leverage DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS available_margin_pct DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS sim_net_pnl_usd DOUBLE PRECISION;
