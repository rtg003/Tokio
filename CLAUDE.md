# CLAUDE.md

Este repositório tem UM contrato central de coordenação e regras de produto:
**[`AGENTS.md`](AGENTS.md)** (ADR 0009 + ADR 0010 + diretivas rtg003 de
2026-07-05). Leia-o integralmente e execute o ritual pré-alteração (§2) como
PRIMEIRA ação de qualquer sessão que vá alterar algo — vale para qualquer
agente/ferramenta que carregue este arquivo (Claude Code, **Hermes**, Cursor
ou outros).

> Hermes: além deste arquivo (carregado quando sua sessão roda com cwd no
> repo), a skill `trade` e o `docs/HANDOFF_HERMES.md` §8 apontam para o mesmo
> contrato — os três caminhos levam ao `AGENTS.md`. Configure seu runtime
> para carregá-lo automaticamente (UPDATE-0005 do seu inbox).

Destaques inegociáveis (detalhes no AGENTS.md):

- **Gates humanos** (promoção de traders para TESTNET/MAINNET, mainnet, caps
  de risco): nunca contornáveis. A dashboard autenticada é ato humano para o
  combobox de status; mainnet sem credenciais configuradas é recusada.
- **Isolamento de observabilidade** (§5.1): cada estratégia/módulo só enxerga
  os próprios dados; filtro por `strategy_id`/módulo é obrigatório em toda
  query de exibição; dados sem atribuição só em visão de sistema.
- **Estratégias não se misturam** (§5.2): regras, ordens, trades, filas,
  tabelas, métricas, configurações e relatórios de uma estratégia só valem
  para ela mesma.
- **Dashboards por estratégia/funcionalidade** (§5.3): a única dashboard
  existente hoje é a de Copy Trade e ela não é dashboard geral; cada nova tela
  deve ter rota, componentes e camada de dados próprios.
- **SQLite único BD** (§5.4): o SQLite local da VPS é a fonte de verdade; a
  camada Supabase e Supabase Auth foram removidas; leituras passam pelo
  gateway interno e backup offsite do SQLite é obrigatório.
- **Inboxes bilaterais**: `docs/HERMES_UPDATES.md` (→ operador) e
  `docs/CURSOR_UPDATES.md` (→ construtor); regra do mesmo commit.
