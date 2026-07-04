-- 0008_discovery_v10 — filtros de atividade recente e win rate 30d.
-- Referência canônica das variáveis: docs/discovery_logic_v9.md

ALTER TABLE traders ADD COLUMN n_trades_7d INTEGER;      -- v10: F2c trades fechados nos últimos 7d
ALTER TABLE traders ADD COLUMN win_rate_30d REAL;        -- v10: win rate só dos últimos 30d (não 60d)
