-- 0020_fills_idempotency — idempotência de fills contra re-entrega do WS.
-- Migração ADITIVA. Fills da Hyperliquid trazem `tid` (id único do trade) e
-- `hash` (tx L1). Persistir o `tid` permite pular fills reentregues numa
-- reconexão do websocket (o snapshot já é filtrado no adapter, mas re-deliveries
-- mid-stream ainda podem ocorrer) — sem isso o ledger dobraria a posição.
--
-- Fills de paper/teste NÃO têm `tid` ⇒ ficam NULL; no SQLite NULLs são distintos
-- num índice UNIQUE, então múltiplos fills sem `tid` continuam válidos.
ALTER TABLE fills ADD COLUMN tid TEXT;
ALTER TABLE fills ADD COLUMN fill_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_tid ON fills(tid) WHERE tid IS NOT NULL;
