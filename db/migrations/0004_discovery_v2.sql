-- 0004_discovery_v2 — colunas da logic_version 2 (spec PROMPT_DISCOVERY_TRADERS_v5).

ALTER TABLE traders ADD COLUMN n_trades_30d INTEGER;
ALTER TABLE traders ADD COLUMN avg_holding_hours REAL;
ALTER TABLE traders ADD COLUMN avg_leverage REAL;
ALTER TABLE traders ADD COLUMN equity REAL;
ALTER TABLE traders ADD COLUMN top_assets TEXT;            -- json: top 3 por volume
ALTER TABLE traders ADD COLUMN last_activity TEXT;         -- ts do último fill
ALTER TABLE traders ADD COLUMN windows_positive TEXT;      -- consistência, ex. "3/4"
ALTER TABLE traders ADD COLUMN reject_reason TEXT;         -- filtro + valores (REJEITADO)
ALTER TABLE traders ADD COLUMN history_truncated INTEGER NOT NULL DEFAULT 0;

-- Snapshot de posicionamento por coorte e ATIVO (divergência smart vs. rekt)
ALTER TABLE cohort_snapshots ADD COLUMN scan_id TEXT;
ALTER TABLE cohort_snapshots ADD COLUMN asset TEXT;
ALTER TABLE cohort_snapshots ADD COLUMN net_bias_pct REAL;   -- + long / - short
ALTER TABLE cohort_snapshots ADD COLUMN avg_leverage REAL;
ALTER TABLE cohort_snapshots ADD COLUMN n_wallets INTEGER;

-- Cache local de respostas da API (TTL controlado em código; rate-limit friendly)
CREATE TABLE IF NOT EXISTS discovery_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
