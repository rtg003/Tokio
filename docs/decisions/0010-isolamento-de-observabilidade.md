# ADR 0010 — Isolamento de observabilidade por estratégia/módulo

- Status: aceito
- Data: 2026-07-03
- Gatilho: incidente em produção — o dashboard de Copy Trade exibia ordens e
  fills do módulo TradingView (`tv_gap_fade`) e fills sem atribuição
  (`strategy_id NULL`, resíduo do bug do snapshot da HL), porque as queries
  de exibição não tinham filtro de módulo.

## Decisão

1. **Cada estratégia/módulo só enxerga os próprios dados.** Toda visão de
   estratégia/módulo (web, CLI de relatórios, briefings/respostas de agente)
   filtra por `strategy_id` das estratégias do módulo — obrigatório, sem
   fallback para "todas" quando a lista é vazia.
2. Dados sem atribuição (`strategy_id NULL`) pertencem apenas a **visões de
   sistema** (tela Logs; agregado do `report --daily`) e são anomalia a
   investigar.
3. A atribuição é exclusivamente estrutural (`cloid` → `strategy_id` no
   ledger/banco) — nunca heurística.
4. A regra vive centralizada em `AGENTS.md` §5.1 (com espelho em `CLAUDE.md`)
   e vale para os dois agentes (Cursor e Hermes). Ausência de filtro de
   escopo em tela/relatório novo reprova PR em review.
5. Limpeza do incidente: fills `strategy_id NULL` removidos (migration
   `0003_cleanup_unattributed_fills` no SQLite; DELETE espelhado no
   Supabase) — eram trades manuais antigos da conta trazidos pelo snapshot
   do WebSocket, não histórico do engine (o histórico real permanece na
   corretora). Dados de `tv_gap_fade` permanecem (histórico legítimo do
   módulo TV; apenas deixam de vazar para a visão de Copy Trade).

## Consequências

- Dashboard de Copy Trade passa a mostrar tabelas vazias até existirem
  trades `ct_*` — correto, não é regressão.
- Futuras telas por módulo (TradingView, standalone) nascem com o mesmo
  filtro; a tela Logs continua sendo a visão de sistema.
- Hermes notificado via `docs/HERMES_UPDATES.md` UPDATE-0004 (inclui refletir
  a regra na skill, área dele).
