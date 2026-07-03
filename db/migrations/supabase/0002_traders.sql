-- 0002_traders (Supabase/Postgres) — réplica analítica da tabela traders.
-- Apply: psql "$DATABASE_URL" -f db/migrations/supabase/0002_traders.sql
-- (o bootstrap_vps.sh aplica automaticamente; idempotente)

CREATE TABLE IF NOT EXISTS traders (
    address TEXT PRIMARY KEY,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'SUGERIDO',
    mode TEXT NOT NULL DEFAULT 'fixed_usdc',
    value DOUBLE PRECISION NOT NULL DEFAULT 50.0,
    max_leverage DOUBLE PRECISION NOT NULL DEFAULT 3.0,
    blocked_assets JSONB NOT NULL DEFAULT '[]',
    dry_run BOOLEAN NOT NULL DEFAULT true,
    thresholds JSONB NOT NULL DEFAULT '{}',
    score DOUBLE PRECISION,
    cohort TEXT,
    twrr_30d DOUBLE PRECISION,
    pnl_30d DOUBLE PRECISION,
    windows JSONB,
    profit_factor DOUBLE PRECISION,
    win_rate DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    liq_distance DOUBLE PRECISION,
    origin TEXT NOT NULL DEFAULT 'discovery',
    logic_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_traders_status ON traders(status);
CREATE INDEX IF NOT EXISTS idx_traders_score ON traders(score DESC);

CREATE TABLE IF NOT EXISTS cohort_snapshots (
    id BIGINT PRIMARY KEY,
    scan_ts TIMESTAMPTZ,
    logic_version INTEGER NOT NULL,
    cohort TEXT NOT NULL,
    n_traders INTEGER NOT NULL DEFAULT 0,
    avg_score DOUBLE PRECISION,
    payload JSONB
);
CREATE INDEX IF NOT EXISTS idx_cohort_snapshots_scan ON cohort_snapshots(scan_ts);

ALTER TABLE traders ENABLE ROW LEVEL SECURITY;
ALTER TABLE cohort_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS read_authenticated ON traders;
CREATE POLICY read_authenticated ON traders FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS read_authenticated ON cohort_snapshots;
CREATE POLICY read_authenticated ON cohort_snapshots FOR SELECT TO authenticated USING (true);
