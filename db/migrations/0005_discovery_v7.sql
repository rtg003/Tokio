-- 0005_discovery_v7 — copiabilidade real (UPDATE-0007 do Hermes / logic_version 7).
-- Métricas de posição ABERTA no momento do scan + simulação retroativa de cópia.

ALTER TABLE traders ADD COLUMN max_current_leverage REAL;   -- F7b: max lev das posições abertas
ALTER TABLE traders ADD COLUMN available_margin_pct REAL;   -- F12: margem livre / accountValue
ALTER TABLE traders ADD COLUMN sim_net_pnl_usd REAL;        -- F15: net da cópia simulada 30d
