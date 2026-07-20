-- 0032_copy_existing_positions — UPDATE-0084.
-- Flag por-trader: ao ativar a cópia (TESTNET/MAINNET), 1 = espelha as posições
-- JÁ ABERTAS do trader (comportamento atual); 0 = começa do baseline atual (não
-- copia o legado) e só espelha fills NOVOS. Aditiva; default 1 preserva o
-- comportamento existente para todas as linhas.

ALTER TABLE traders ADD COLUMN copy_existing_positions INTEGER NOT NULL DEFAULT 1;
