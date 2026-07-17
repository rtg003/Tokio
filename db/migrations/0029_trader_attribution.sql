-- 0029_trader_attribution — UPDATE-0064 (invariante strategy↔trader). Migração
-- ADITIVA: atribuição EXPLÍCITA do trader-mestre em fills/orders, sem tocar em
-- `master_address` (wallet EXECUTORA da nossa conta, migration 0015 — alimenta o
-- filtro "por Wallet" da UI). São conceitos distintos e coexistem:
--   • trader_address  = endereço do trader-mestre COPIADO (externo).
--   • master_address  = wallet da NOSSA conta que executou a ordem/fill.
--
-- Backfill idempotente (roda 1x com a migration): deriva o trader das linhas
-- históricas via strategies.config_snapshot.$.address (mesmo vínculo que a UI
-- usava por strategy_id). Linhas sem strategy vinculada ficam NULL (a UI mostra
-- "—" · sem atribuição de trader).
ALTER TABLE fills  ADD COLUMN trader_address TEXT;
ALTER TABLE orders ADD COLUMN trader_address TEXT;

CREATE INDEX IF NOT EXISTS idx_fills_trader  ON fills(trader_address);
CREATE INDEX IF NOT EXISTS idx_orders_trader ON orders(trader_address);

UPDATE fills SET trader_address = (
    SELECT json_extract(s.config_snapshot, '$.address')
    FROM strategies s WHERE s.id = fills.strategy_id
) WHERE trader_address IS NULL;

UPDATE orders SET trader_address = (
    SELECT json_extract(s.config_snapshot, '$.address')
    FROM strategies s WHERE s.id = orders.strategy_id
) WHERE trader_address IS NULL;
