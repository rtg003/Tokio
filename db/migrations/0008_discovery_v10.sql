-- 0008_discovery_v10 — colunas da logic_version 10 (F2c, win_rate_30d).
-- Referenciadas em funnel.persist_scan mas sem migration local até aqui
-- (as colunas v8/v9 já foram criadas pelas migrations 0006 e 0007).

ALTER TABLE traders ADD COLUMN n_trades_7d INTEGER;   -- F2c: trades fechados 7d
ALTER TABLE traders ADD COLUMN win_rate_30d REAL;     -- win rate só dos últimos 30d
