-- 0001_initial — core operational schema (SQLite local, source of truth).
-- Mirrored in db/migrations/supabase/0001_initial.sql (Postgres) for analytics.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    network TEXT NOT NULL CHECK (network IN ('testnet', 'mainnet')),
    status TEXT NOT NULL DEFAULT 'active',
    UNIQUE (name, network)
);

CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,                  -- e.g. ct_whale01, tv_gap_fade, sa_dca_btc
    module TEXT NOT NULL CHECK (module IN ('copy_trade', 'tradingview', 'standalone', 'dummy')),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'dry_run', 'active', 'paused', 'auto_paused', 'archived')),
    config_snapshot TEXT,                 -- json
    thresholds TEXT,                      -- json: performance thresholds for auto-pause
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cloid TEXT UNIQUE,                    -- client order id: strategy attribution
    strategy_id TEXT NOT NULL REFERENCES strategies(id),
    exchange_id INTEGER REFERENCES exchanges(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    type TEXT NOT NULL,                   -- market | limit | trigger
    size REAL NOT NULL,
    price REAL,
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'sent', 'acked', 'partially_filled',
                          'filled', 'cancelled', 'rejected', 'error', 'dry_run')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    sent_at TEXT,
    acked_at TEXT,
    closed_at TEXT,
    latency_ms REAL,
    reject_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id, created_at);

-- Orders != fills: one order has N fills; net PnL is born from fills + fees.
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER REFERENCES orders(id),
    cloid TEXT,
    strategy_id TEXT REFERENCES strategies(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    fee_asset TEXT DEFAULT 'USDC',
    realized_pnl REAL,                    -- set on position close
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_fills_strategy ON fills(strategy_id, ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    strategy_id TEXT,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    payload TEXT                          -- json
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_strategy ON events(strategy_id, ts);

-- Dashboards read from here — they NEVER scan `events` to compute metrics.
CREATE TABLE IF NOT EXISTS strategy_metrics_daily (
    strategy_id TEXT NOT NULL REFERENCES strategies(id),
    day TEXT NOT NULL,                    -- YYYY-MM-DD (UTC)
    net_pnl REAL NOT NULL DEFAULT 0,
    expectancy REAL,
    profit_factor REAL,
    max_drawdown REAL,
    win_rate REAL,
    n_trades INTEGER NOT NULL DEFAULT 0,
    fees REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy_id, day)
);

-- Local outbox for async batch replication to Supabase (local-first, ADR 0005).
CREATE TABLE IF NOT EXISTS replication_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    op TEXT NOT NULL DEFAULT 'upsert',
    payload TEXT NOT NULL,                -- json row
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
