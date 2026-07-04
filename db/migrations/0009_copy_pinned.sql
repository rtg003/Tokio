-- 0009_copy_pinned — flag inviolável de trader copiado (Bloco 3).
-- copy_pinned = 1 impede que um re-scan rebaixe/rejeite o trader: o funil
-- atualiza métricas mas NUNCA mexe em status ou reject_reason. Setado
-- automaticamente ao entrar em DRY_RUN/COPIANDO via gate humano; só removido
-- por unpin explícito (human_gate=True) com a cópia pausada.

ALTER TABLE traders ADD COLUMN copy_pinned INTEGER NOT NULL DEFAULT 0;
