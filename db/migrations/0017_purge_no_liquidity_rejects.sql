-- 0017_purge_no_liquidity_rejects — pedido rtg003 (2026-07-11).
-- Remove as linhas de `orders` que ficaram como REJECTED/ERROR por ausência de
-- liquidez (IOC agressivo não cruzou o book — ex.: "CASHCAT: Order could not
-- immediately match against any resting orders. asset=209"). Não são falha
-- operacional; poluem a tabela de Trades da dashboard.
--
-- A prevenção já entrou no gateway (UPDATE-0029): agora um IOC sem match apaga a
-- própria linha `created` e devolve status "skipped" (nunca vira `rejected`).
-- Esta migration limpa o histórico anterior a essa correção. Idempotente.
--
-- Escopo cirúrgico: só toca em orders REJECTED/ERROR cujo motivo é o no-match do
-- IOC. Fills nunca existem para essas ordens (não cruzaram), então não há órfãos.

DELETE FROM orders
WHERE status IN ('rejected', 'error')
  AND reject_reason IS NOT NULL
  AND lower(reject_reason) LIKE '%could not immediately match%';
