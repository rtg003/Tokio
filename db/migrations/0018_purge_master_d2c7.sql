-- 0018_purge_master_d2c7 — pedido rtg003 (2026-07-11).
-- Apaga definitivamente os registros ligados à wallet (conta MASTER de trading)
-- 0xd2c7… — a que aparece no filtro "Wallet" da dashboard de Copy Trade
-- (orders/fills.master_address, migration 0015).
--
-- IMPORTANTE — casamento por PREFIXO: o operador identificou a wallet pela forma
-- curta exibida na UI (`0xd2c7…XXXX` = 6 primeiros chars). Não recebi o endereço
-- completo, então casamos por `lower(master_address) LIKE '0xd2c7%'`. Isso é
-- seguro enquanto o prefixo for único entre as contas master do banco — se
-- houver colisão, troque pelo endereço completo antes de aplicar.
--
-- NÃO toca em `hl_agents`: remover o agent (chave cifrada) poderia derrubar a
-- assinatura se esse master ainda estiver ativo. Se a intenção for também sumir
-- com a wallet do dropdown, faça isso num passo separado e consciente.
-- Idempotente.

DELETE FROM fills
WHERE master_address IS NOT NULL
  AND lower(master_address) LIKE '0xd2c7%';

DELETE FROM orders
WHERE master_address IS NOT NULL
  AND lower(master_address) LIKE '0xd2c7%';
