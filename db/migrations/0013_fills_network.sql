-- 0013_fills_network — atribuição explícita de rede em fills + backfill.
-- Permite filtrar ordens/trades por testnet/mainnet sem depender só de JOIN
-- com orders.exchange_id (muitos registros legados tinham exchange_id NULL).

ALTER TABLE fills ADD COLUMN network TEXT CHECK (network IN ('testnet', 'mainnet'));

-- Backfill orders sem exchange_id → hyperliquid testnet (default histórico).
UPDATE orders
SET exchange_id = (
    SELECT id FROM exchanges
    WHERE name = 'hyperliquid' AND network = 'testnet'
    LIMIT 1
)
WHERE exchange_id IS NULL;

-- Backfill fills.network a partir da ordem vinculada.
UPDATE fills
SET network = (
    SELECT e.network
    FROM orders o
    JOIN exchanges e ON o.exchange_id = e.id
    WHERE o.cloid = fills.cloid
    LIMIT 1
)
WHERE network IS NULL;

-- Fills órfãos (sem ordem) → testnet.
UPDATE fills SET network = 'testnet' WHERE network IS NULL;

CREATE INDEX IF NOT EXISTS idx_fills_network ON fills(network, strategy_id, ts);
