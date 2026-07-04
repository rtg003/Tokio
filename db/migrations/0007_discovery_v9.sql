-- 0007_discovery_v9 — "copiar a CÓPIA": cobertura + consistência da simulação.
-- Referência canônica das variáveis: docs/discovery_logic_v9.md

ALTER TABLE traders ADD COLUMN coverage_days REAL;      -- F16: dias entre 1º e último fill
ALTER TABLE traders ADD COLUMN sim_half_old_net REAL;   -- F18: net da cópia na metade antiga
ALTER TABLE traders ADD COLUMN sim_half_new_net REAL;   -- F18: net da cópia na metade recente
