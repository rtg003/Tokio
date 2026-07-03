-- 0002_traders — tabela ÚNICA de traders (candidatos + copiados), ADR 0008.
-- Fonte única de verdade: esta tabela. YAMLs por trader foram removidos.
-- Alterações de config/status só via API de controle do gateway (logadas em events).

CREATE TABLE IF NOT EXISTS traders (
    address TEXT PRIMARY KEY,             -- lowercase; chave de upsert
    name TEXT,
    status TEXT NOT NULL DEFAULT 'SUGERIDO'
        CHECK (status IN ('SUGERIDO','DRY_RUN','COPIANDO','PAUSADO','REJEITADO','ARQUIVADO')),

    -- config de execução (só usada quando DRY_RUN/COPIANDO)
    mode TEXT NOT NULL DEFAULT 'fixed_usdc' CHECK (mode IN ('fixed_usdc','percent')),
    value REAL NOT NULL DEFAULT 50.0,
    max_leverage REAL NOT NULL DEFAULT 3.0,
    blocked_assets TEXT NOT NULL DEFAULT '[]',   -- json array
    dry_run INTEGER NOT NULL DEFAULT 1,          -- default true, sem exceção
    thresholds TEXT NOT NULL DEFAULT '{}',       -- json (auto-pausa)

    -- dados do discovery (logic_version indica a lógica que os produziu)
    score REAL,
    cohort TEXT,                                 -- coorte bidimensional (spec v2)
    twrr_30d REAL,
    pnl_30d REAL,
    windows TEXT,                                -- json: janelas day/week/month/allTime
    profit_factor REAL,
    win_rate REAL,
    max_drawdown REAL,
    liq_distance REAL,                           -- distância de liquidação (spec v2)
    origin TEXT NOT NULL DEFAULT 'discovery',    -- discovery | manual
    logic_version INTEGER NOT NULL DEFAULT 1,

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_traders_status ON traders(status);
CREATE INDEX IF NOT EXISTS idx_traders_score ON traders(score DESC);

-- Snapshot agregado por coorte a cada scan do discovery (spec v2).
CREATE TABLE IF NOT EXISTS cohort_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    logic_version INTEGER NOT NULL,
    cohort TEXT NOT NULL,
    n_traders INTEGER NOT NULL DEFAULT 0,
    avg_score REAL,
    payload TEXT                              -- json: agregados extras da coorte
);
CREATE INDEX IF NOT EXISTS idx_cohort_snapshots_scan ON cohort_snapshots(scan_ts);
