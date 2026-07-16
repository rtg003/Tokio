-- 0024_metrics_confidence — UPDATE-0057 (Fase 2/3 da arquitetura definitiva p/
-- amostras truncadas). ADITIVA (só ADD COLUMN; nenhuma linha é reescrita).
--
-- A Fase 1 (UPDATE-0056) passou a classificar `metrics_confidence`
-- (complete|sampled|insufficient) e a separar os 3 conceitos que o
-- `coverage_days` misturava (idade da wallet × span da amostra × janela
-- pedida) — mas só em memória/API. Esta migration dá COLUNAS PRÓPRIAS a esses
-- sinais para que a persistência possa:
--   (Parte 8) proteger métricas COMPLETAS de serem sobrescritas por amostradas/
--     insuficientes num scan futuro (guarda anti-sobrescrita em persist_scan);
--   (Parte 2) guardar a idade autoritativa da wallet;
--   (Parte 7) guardar o enriquecimento AGREGADO do HyperTracker em campos
--     SEPARADOS — nunca substituindo as métricas de trading da Hyperliquid.
--
-- Linhas legadas ficam com `metrics_confidence` NULL: a guarda anti-sobrescrita
-- trata NULL como "desconhecido" e NUNCA bloqueia (permite atualização normal).
-- Só metadado/persistência — NÃO altera o caminho de ordem (INVARIANTE §8.4.1:
-- /intent e /cancel seguem sem gate novo).

ALTER TABLE traders ADD COLUMN metrics_confidence TEXT;         -- complete|sampled|insufficient
ALTER TABLE traders ADD COLUMN wallet_age_days REAL;            -- Parte 2: idade real da wallet
ALTER TABLE traders ADD COLUMN fills_sample_days REAL;          -- span coberto pela amostra de fills
ALTER TABLE traders ADD COLUMN fills_sample_count INTEGER;      -- nº de fills na amostra analisada

-- Parte 7: enriquecimento AGREGADO do HyperTracker (só a análise individual
-- popula; o scan em massa não toca estas colunas → nunca as zera).
ALTER TABLE traders ADD COLUMN ht_earliest_activity_ms REAL;    -- earliestActivityAt (ms epoch)
ALTER TABLE traders ADD COLUMN ht_total_equity REAL;            -- totalEquity agregado
ALTER TABLE traders ADD COLUMN ht_perp_pnl REAL;                -- perpPnl agregado
ALTER TABLE traders ADD COLUMN ht_exposure_ratio REAL;          -- exposureRatio agregado
