-- 0016_score_components — persistência dos componentes normalizados do score.
-- A régua de ranking (AJUSTES 2026-07-11, Parte 1) passou a usar a cópia
-- simulada líquida (sim_net) e rebalanceou os pesos. Para reclassificar TODOS
-- os traders já persistidos SEM refazer o deep dive (Parte 2 — `discovery
-- reclassify`), precisamos dos 7 componentes normalizados [0,1] que só existiam
-- em memória no momento do scan. Agora eles são gravados como JSON.
--
-- Linhas legadas (anteriores a esta migration) ficam NULL: o reclassify faz
-- best-effort recomputando os componentes a partir das métricas cruas já
-- persistidas (profit_factor, sim_net_pnl_usd, windows_positive, max_drawdown,
-- etc.), marcando approx=true no log. Só metadado/leitura — NÃO altera o
-- caminho de ordem (INVARIANTE: /intent e /cancel seguem sem gate novo).

ALTER TABLE traders ADD COLUMN score_components TEXT;

-- kv genérico de metadados do discovery (ex.: hash dos pesos p/ auto-trigger do
-- reclassify no startup quando a config de score muda).
CREATE TABLE IF NOT EXISTS discovery_meta (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TEXT
);
