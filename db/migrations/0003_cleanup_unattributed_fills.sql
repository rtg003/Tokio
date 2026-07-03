-- 0003_cleanup_unattributed_fills — limpeza pontual (ADR 0010).
--
-- Remove fills SEM atribuição de estratégia (strategy_id NULL): resíduo do
-- bug do snapshot do WebSocket da Hyperliquid (corrigido em 2026-07-02), que
-- gravou trades manuais antigos da conta como se fossem fills do engine.
-- Não é histórico do engine — o histórico real permanece na corretora.
-- Fills legítimos SEMPRE têm strategy_id (atribuição via cloid no ledger).
--
-- Espelho no Supabase: DELETE equivalente executado diretamente (fills não
-- são reconciliados pelo replicator; a remoção lá é definitiva).

DELETE FROM fills WHERE strategy_id IS NULL;
