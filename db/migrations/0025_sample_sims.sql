-- 0025_sample_sims — UPDATE-0059 (métricas amostrais). ADITIVA (só ADD COLUMN;
-- nenhuma linha é reescrita).
--
-- Quando `metrics_confidence != complete` o gate (UPDATE-0056) nulifica as
-- sim_* LONGITUDINAIS (não afirmamos veredito de 30/60d sobre horas de dado —
-- invariante mantida). Mas a amostra recente CONHECE o span que DE FATO cobriu:
-- dá pra simular a cópia sobre ESSE span e reportar honestamente "SIM ~$X em
-- Yd" em vez de "—". Estas colunas guardam essa família AMOSTRAL, PARALELA às
-- sim_* longitudinais.
--
-- A guarda anti-sobrescrita (persist_scan) lê SÓ `metrics_confidence` — NÃO se
-- aplica a estes campos, que são sempre gravados quando presentes. Só metadado/
-- persistência — NÃO altera o caminho de ordem (INVARIANTE §8.4.1).

ALTER TABLE traders ADD COLUMN sample_sim_net_usd REAL;         -- net da cópia no span coberto
ALTER TABLE traders ADD COLUMN sample_sim_expectancy_usd REAL;  -- expectância por trade (amostra)
ALTER TABLE traders ADD COLUMN sample_sim_max_dd_pct REAL;      -- max drawdown da cópia (amostra)
ALTER TABLE traders ADD COLUMN sample_sim_window_days REAL;     -- span REAL coberto pela amostra
ALTER TABLE traders ADD COLUMN sample_sim_net_per_day REAL;     -- net/dia (base da projeção /30d informativa)
