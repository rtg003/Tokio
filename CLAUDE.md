# CLAUDE.md

Este repositório tem UM contrato central de coordenação e regras de produto:
**[`AGENTS.md`](AGENTS.md)** (ADR 0009 + ADR 0010). Leia-o integralmente e
execute o ritual pré-alteração (§2) como PRIMEIRA ação de qualquer sessão que
vá alterar algo — vale para qualquer agente/ferramenta que carregue este
arquivo (Claude Code, Hermes, Cursor ou outros).

Destaques inegociáveis (detalhes no AGENTS.md):

- **Gates humanos** (Gate 2 de traders, dry_run→active, mainnet, caps de
  risco): nunca contornáveis — `docs/HANDOFF_HERMES.md` §7.
- **Isolamento de observabilidade** (§5.1): cada estratégia/módulo só enxerga
  os próprios dados; filtro por `strategy_id`/módulo é obrigatório em toda
  query de exibição; dados sem atribuição só em visão de sistema.
- **Inboxes bilaterais**: `docs/HERMES_UPDATES.md` (→ operador) e
  `docs/CURSOR_UPDATES.md` (→ construtor); regra do mesmo PR.
