-- 0004_discovery_v2 (Supabase/Postgres) — espelho analítico da logic_version 2.
-- Aplicado automaticamente pelo bootstrap/autodeploy (idempotente).

ALTER TABLE traders ADD COLUMN IF NOT EXISTS n_trades_30d INTEGER;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS avg_holding_hours DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS avg_leverage DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS equity DOUBLE PRECISION;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS top_assets JSONB;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS last_activity TIMESTAMPTZ;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS windows_positive TEXT;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS reject_reason TEXT;
ALTER TABLE traders ADD COLUMN IF NOT EXISTS history_truncated BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE cohort_snapshots ADD COLUMN IF NOT EXISTS scan_id TEXT;
ALTER TABLE cohort_snapshots ADD COLUMN IF NOT EXISTS asset TEXT;
ALTER TABLE cohort_snapshots ADD COLUMN IF NOT EXISTS net_bias_pct DOUBLE PRECISION;
ALTER TABLE cohort_snapshots ADD COLUMN IF NOT EXISTS avg_leverage DOUBLE PRECISION;
ALTER TABLE cohort_snapshots ADD COLUMN IF NOT EXISTS n_wallets INTEGER;

-- discovery_cache é local-only (não replica): sem espelho aqui.
