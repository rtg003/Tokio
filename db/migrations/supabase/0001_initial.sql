-- 0001_initial (Supabase/Postgres) — analytics replica of the local schema.
-- Apply with: psql "$DATABASE_URL" -f db/migrations/supabase/0001_initial.sql
-- RLS: read-only for authenticated users; writes only via service_role
-- (which bypasses RLS and exists ONLY inside engine containers).

CREATE TABLE IF NOT EXISTS exchanges (
    id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    network TEXT NOT NULL CHECK (network IN ('testnet', 'mainnet')),
    status TEXT NOT NULL DEFAULT 'active',
    UNIQUE (name, network)
);

CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    module TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    config_snapshot JSONB,
    thresholds JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS orders (
    id BIGINT PRIMARY KEY,
    cloid TEXT UNIQUE,
    strategy_id TEXT,
    exchange_id BIGINT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,
    size DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    acked_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    latency_ms DOUBLE PRECISION,
    reject_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id, created_at);

CREATE TABLE IF NOT EXISTS fills (
    id BIGINT PRIMARY KEY,
    order_id BIGINT,
    cloid TEXT,
    strategy_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    size DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION NOT NULL DEFAULT 0,
    fee_asset TEXT DEFAULT 'USDC',
    realized_pnl DOUBLE PRECISION,
    ts TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_fills_strategy ON fills(strategy_id, ts);

-- Operational/decision events only; high-volume debug stays in local JSONL.
-- Retention: 90 days (scheduled delete, see HANDOFF).
CREATE TABLE IF NOT EXISTS events (
    id BIGINT PRIMARY KEY,
    ts TIMESTAMPTZ,
    strategy_id TEXT,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    payload JSONB
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS strategy_metrics_daily (
    strategy_id TEXT NOT NULL,
    day DATE NOT NULL,
    net_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
    expectancy DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    win_rate DOUBLE PRECISION,
    n_trades INTEGER NOT NULL DEFAULT 0,
    fees DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy_id, day)
);

-- ---------------------------------------------------------------------------
-- RLS: enabled on ALL tables; authenticated users may only read.
-- ---------------------------------------------------------------------------
ALTER TABLE exchanges ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE fills ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_metrics_daily ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['exchanges','strategies','orders','fills','events','strategy_metrics_daily']
    LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS read_authenticated ON %I;
             CREATE POLICY read_authenticated ON %I FOR SELECT TO authenticated USING (true);',
            t, t);
    END LOOP;
END $$;
