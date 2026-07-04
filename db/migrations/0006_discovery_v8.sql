-- 0006_discovery_v8 — Estágio 4: simulação de cópia como ranking final.

ALTER TABLE traders ADD COLUMN sim_expectancy_usd REAL;  -- net / trade fechado (replay)
ALTER TABLE traders ADD COLUMN sim_max_dd_pct REAL;      -- max DD da curva da cópia
ALTER TABLE traders ADD COLUMN sim_factor REAL;          -- multiplicador do ranking final
