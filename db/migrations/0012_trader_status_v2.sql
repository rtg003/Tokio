-- 0012_trader_status_v2 — diretiva rtg003 (2026-07-05).
-- Novo ciclo: SUGERIDO, SALVO, TESTNET, MAINNET, REJEITADO.
-- Mapeamento legado:
--   DRY_RUN  -> TESTNET
--   COPIANDO -> TESTNET (produção atual opera testnet)
--   PAUSADO  -> SALVO
--   ARQUIVADO -> REJEITADO

PRAGMA foreign_keys=OFF;

CREATE TABLE traders_new (
    address TEXT PRIMARY KEY,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'SUGERIDO'
        CHECK (status IN ('SUGERIDO','SALVO','TESTNET','MAINNET','REJEITADO')),

    mode TEXT NOT NULL DEFAULT 'fixed_usdc' CHECK (mode IN ('fixed_usdc','percent')),
    value REAL NOT NULL DEFAULT 50.0,
    max_leverage REAL NOT NULL DEFAULT 3.0,
    blocked_assets TEXT NOT NULL DEFAULT '[]',
    dry_run INTEGER NOT NULL DEFAULT 1,
    thresholds TEXT NOT NULL DEFAULT '{}',

    score REAL,
    cohort TEXT,
    twrr_30d REAL,
    pnl_30d REAL,
    windows TEXT,
    profit_factor REAL,
    win_rate REAL,
    max_drawdown REAL,
    liq_distance REAL,
    origin TEXT NOT NULL DEFAULT 'discovery',
    logic_version INTEGER NOT NULL DEFAULT 1,

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    n_trades_30d INTEGER,
    avg_holding_hours REAL,
    avg_leverage REAL,
    equity REAL,
    top_assets TEXT,
    last_activity TEXT,
    windows_positive TEXT,
    reject_reason TEXT,
    history_truncated INTEGER NOT NULL DEFAULT 0,
    max_current_leverage REAL,
    available_margin_pct REAL,
    sim_net_pnl_usd REAL,
    sim_expectancy_usd REAL,
    sim_max_dd_pct REAL,
    sim_factor REAL,
    coverage_days REAL,
    sim_half_old_net REAL,
    sim_half_new_net REAL,
    n_trades_7d INTEGER,
    win_rate_30d REAL,
    copy_pinned INTEGER NOT NULL DEFAULT 0
);

INSERT INTO traders_new (
    address, name, status, mode, value, max_leverage, blocked_assets, dry_run,
    thresholds, score, cohort, twrr_30d, pnl_30d, windows, profit_factor,
    win_rate, max_drawdown, liq_distance, origin, logic_version, created_at,
    updated_at, n_trades_30d, avg_holding_hours, avg_leverage, equity,
    top_assets, last_activity, windows_positive, reject_reason,
    history_truncated, max_current_leverage, available_margin_pct,
    sim_net_pnl_usd, sim_expectancy_usd, sim_max_dd_pct, sim_factor,
    coverage_days, sim_half_old_net, sim_half_new_net, n_trades_7d,
    win_rate_30d, copy_pinned
)
SELECT
    address,
    name,
    CASE status
        WHEN 'DRY_RUN' THEN 'TESTNET'
        WHEN 'COPIANDO' THEN 'TESTNET'
        WHEN 'PAUSADO' THEN 'SALVO'
        WHEN 'ARQUIVADO' THEN 'REJEITADO'
        ELSE status
    END,
    mode,
    value,
    max_leverage,
    blocked_assets,
    CASE WHEN status IN ('DRY_RUN','COPIANDO') THEN 0 ELSE dry_run END,
    thresholds,
    score,
    cohort,
    twrr_30d,
    pnl_30d,
    windows,
    profit_factor,
    win_rate,
    max_drawdown,
    liq_distance,
    origin,
    logic_version,
    created_at,
    updated_at,
    n_trades_30d,
    avg_holding_hours,
    avg_leverage,
    equity,
    top_assets,
    last_activity,
    windows_positive,
    reject_reason,
    history_truncated,
    max_current_leverage,
    available_margin_pct,
    sim_net_pnl_usd,
    sim_expectancy_usd,
    sim_max_dd_pct,
    sim_factor,
    coverage_days,
    sim_half_old_net,
    sim_half_new_net,
    n_trades_7d,
    win_rate_30d,
    copy_pinned
FROM traders;

DROP TABLE traders;
ALTER TABLE traders_new RENAME TO traders;

CREATE INDEX IF NOT EXISTS idx_traders_status ON traders(status);
CREATE INDEX IF NOT EXISTS idx_traders_score ON traders(score DESC);

PRAGMA foreign_keys=ON;
