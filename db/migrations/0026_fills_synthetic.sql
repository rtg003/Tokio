-- 0026_fills_synthetic — fills de AJUSTE sintético (resync ledger↔venue).
-- Migração ADITIVA (só ADD COLUMN; nenhuma linha é reescrita).
--
-- O `Ledger` é 100% em memória e reconstruído de `fills` no boot. Quando a venue
-- vai a flat sem nos mandar um fill (fechamento manual/liquidação/reset), o book
-- fica com SIZE FANTASMA que infla o total_cap e bloqueia ordens reais. O resync
-- grava um fill sintético (`synthetic=1`) que SÓ reconstrói o size no hydrate.
--
-- Regra de ouro: `synthetic=1` NUNCA entra em métricas/PnL/relatórios/breaker —
-- é PnL-neutro (realized_pnl=0, fee=0). Toda query de PnL/breaker filtra
-- `synthetic=0`. Fills reais (ordem própria/paper/venue) ficam 0 (default).
-- Só persistência/reconstrução — NÃO altera o caminho de ordem (INVARIANTE §8.4.1).
ALTER TABLE fills ADD COLUMN synthetic INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_fills_synthetic ON fills (synthetic);
