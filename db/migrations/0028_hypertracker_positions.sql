-- 0028_hypertracker_positions — Discovery v15 (HyperTracker como fonte primária
-- de POSIÇÕES + sourcing por cohort). Migração ADITIVA (coluna nova + tabela
-- nova; nenhuma linha existente é tocada).
--
-- (i) `traders.position_metrics_source`: de onde vieram as métricas de posição
--     do último scan/análise — 'hypertracker' (posições consolidadas, sem o teto
--     de ~2.000 fills) ou 'hl_fills' (reconstrução por fills HL, comportamento
--     legado). NULL em linhas legadas (pré-v15) = tratar como 'hl_fills'.
-- (ii) `market_bias`: snapshot informativo do heatmap de posicionamento do
--     HyperTracker (/positions/heatmap?openedWithin=7d) por scan. NUNCA alimenta
--     ranking/score — é só exibição na dashboard de Copy Trade.
ALTER TABLE traders ADD COLUMN position_metrics_source TEXT;  -- 'hypertracker' | 'hl_fills'

CREATE TABLE IF NOT EXISTS market_bias (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_ts       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    logic_version INTEGER NOT NULL,
    payload       TEXT                       -- json do heatmap (bias por ativo)
);
CREATE INDEX IF NOT EXISTS idx_market_bias_scan ON market_bias(scan_ts);
