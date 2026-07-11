-- 0015_orders_fills_master — atribuição real de wallet por ordem/trade.
-- O filtro por Wallet da dashboard de Copy Trade (AJUSTES 2026-07-09, item 2)
-- exige saber QUAL master (conta de trading do ambiente) executou cada ordem/
-- fill. Antes só tínhamos strategy_id + network; a conta viva do adapter podia
-- mudar (novo master via keyring hl-auth v2.0), então gravamos o master_address
-- no momento do insert — é o account_address do adapter do `network` resolvido.
--
-- Nullable: linhas históricas (anteriores a esta migration) ficam NULL e só
-- aparecem sob "Todas as wallets" na UI. Só metadado — NÃO altera o caminho de
-- ordem (INVARIANTE Hermes: /intent e /cancel seguem sem gate novo).

ALTER TABLE orders ADD COLUMN master_address TEXT;
ALTER TABLE fills  ADD COLUMN master_address TEXT;

CREATE INDEX IF NOT EXISTS idx_orders_master ON orders (master_address);
CREATE INDEX IF NOT EXISTS idx_fills_master  ON fills  (master_address);
