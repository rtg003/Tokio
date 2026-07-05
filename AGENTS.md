# AGENTS.md — protocolo bilateral de coordenação Cursor ⇄ Hermes (ADR 0009)

> **LEIA E EXECUTE NO INÍCIO DE TODA SESSÃO.** Este repositório é trabalhado
> por DOIS agentes em paralelo: o **Cursor** (CONSTRUTOR — código,
> arquitetura, schema) e o **Hermes** (OPERADOR — produção, skill, crons).
> Sessões de agente NÃO têm memória entre si; este arquivo é o contrato
> persistente de coordenação. O ritual da seção 2 é a **primeira ação de
> qualquer sessão** que vá alterar algo.
>
> **LIMITE INVIOLÁVEL**: nada neste protocolo — inbox, PR, review ou
> desempate — autoriza violar gates ou caps (Gate 2 de traders, promoção
> para cópia, mainnet, caps de risco; ver `docs/HANDOFF_HERMES.md` §7).
> Desde 2026-07-05, a dashboard autenticada por senha é um caminho de ato
> humano para traders: mudar o combobox de Status para `TESTNET` ou `MAINNET`
> equivale à autorização humana explícita de rtg003. Caps de risco, mainnet
> sem credenciais configuradas e demais travas continuam invioláveis.

## 1. Inboxes (um por agente; cada um ESCREVE no do OUTRO)

| Arquivo | Direção | Quem escreve | Quem aplica e marca APLICADO |
|---|---|---|---|
| `docs/HERMES_UPDATES.md` | Cursor → Hermes | Cursor | Hermes |
| `docs/CURSOR_UPDATES.md` | Hermes → Cursor | Hermes | Cursor |

Formato comum aos dois: entradas `UPDATE-NNNN` sequenciais, **append-only**
(entradas publicadas nunca são editadas nem renumeradas), com
`Status: PENDENTE → APLICADO em <data>` como única edição permitida, feita
pelo destinatário após executar as ações e passar na validação da entrada.
Entradas NUNCA autorizam violar gates/caps.

## 2. Ritual pré-alteração (OBRIGATÓRIO, sem exceção)

Antes de INICIAR qualquer mudança em código/config/lógica:

1. **Sincronizar**: `git fetch origin main` + pull da `main`.
2. **Ler o próprio inbox** e aplicar/ackar as entradas `PENDENTE` ANTES de
   qualquer trabalho novo (Cursor lê `docs/CURSOR_UPDATES.md`; Hermes lê
   `docs/HERMES_UPDATES.md`).
3. **Trabalhar direto na `main`**: diretiva humana de rtg003 em 2026-07-05
   autoriza Cursor e Hermes a editar, commitar e pushar direto na `main` sem
   branch/PR. Antes de cada push, fazer novo `git pull origin main`; preferir
   commits pequenos e frequentes para reduzir conflito entre agentes.
4. **Respeitar áreas e inboxes**: o desempate da seção 4 continua valendo.
   Se a mudança afetar o outro agente, registrar entrada no inbox dele no
   mesmo commit (seção 3).

## 3. Regra do mesmo commit (bilateral)

- Commit do **Cursor** cujo merge/push exija ação, conhecimento novo ou
  mudança de comportamento do Hermes → entrada em `docs/HERMES_UPDATES.md`
  **no mesmo commit**.
- Commit do **Hermes** que afete o Cursor (mudança de código/config/convenção
  que sessões futuras do construtor precisam conhecer) → entrada em
  `docs/CURSOR_UPDATES.md` **no mesmo commit**.
- Em review/checagem de histórico, cada agente **EXIGE** do outro a entrada
  faltante: mudança aplicável sem entrada = mudança incompleta.

## 4. Desempate de área

| Área | Prioridade |
|---|---|
| Código do engine/web, arquitetura, schema/migrations | **Cursor** |
| Config operacional, `skill/`, crons, rotina de produção | **Hermes** |
| Conflito genuíno (a mesma mudança disputada pelos dois) | **Ambos PARAM e notificam o humano (rtg003)** |

Prioridade define quem conduz e quem revisa — não dispensa o ritual da
seção 2 nem a regra do mesmo PR da seção 3.

## 5. Regras centrais de produto (invioláveis para os DOIS agentes)

### 5.1 Isolamento de observabilidade (ADR 0010)

**Cada estratégia/módulo SÓ ENXERGA os próprios dados.** Sem exceção:

- Toda visão de estratégia ou módulo — dashboard, relatório da CLI, resposta
  de agente, briefing — exibe SOMENTE dados das estratégias daquele módulo.
  **Filtro por `strategy_id`/módulo é OBRIGATÓRIO em toda query de exibição**;
  "SELECT sem escopo" em tela/relatório de estratégia é bug crítico
  (incidente de 2026-07-03: dashboard de Copy Trade exibindo ordens/fills do
  módulo TradingView e dados sem atribuição).
- Dados sem atribuição (`strategy_id NULL`) só aparecem em **visões de
  sistema** (tela Logs, `report --daily` agregado) e são tratados como
  anomalia a investigar — nunca como dado de estratégia.
- Novas telas, relatórios e análises NASCEM com o filtro de escopo. Em
  review, ausência de filtro reprova o PR.
- A atribuição é sempre via `cloid` → `strategy_id` (ledger); nenhum dado
  entra numa visão de módulo por inferência ou "parece ser".

### 5.2 Estratégias não se misturam (diretiva rtg003, 2026-07-05)

Toda regra, ordem, trade e especificação de uma estratégia vale somente para
ela mesma. Isso inclui, sem exceção:

- ordens, filas, fills/trades, métricas, tabelas, cards, relatórios,
  briefings e respostas de agente;
- configurações, thresholds, limites, bloqueios de ativos, caps e parâmetros
  operacionais;
- qualquer dado derivado de execução, discovery, auditoria ou monitoramento.

É proibido exibir dados de uma estratégia na dashboard, relatório ou resposta
de outra estratégia. Configuração de uma estratégia nunca é herdada, aplicada
ou inferida para outra porque "parece semelhante".

### 5.3 Dashboards por estratégia e por funcionalidade

A única dashboard existente hoje é a de **Copy Trade**. Ela NÃO é dashboard
geral e exibe exclusivamente dados do módulo `copy_trade`: `strategy_id`
`ct_*` e a tabela `traders` (que é própria do módulo). Uma dashboard geral
de sistema poderá existir no futuro, mas será uma página separada.

A estrutura web deve seguir uma página por estratégia/módulo ou funcionalidade:
rotas próprias (por exemplo, `/copy-trade`), componentes próprios e camada de
dados própria. Código/queries de estratégias diferentes não devem se misturar
na mesma página. Telas de sistema como configurações, logs e futura dashboard
geral também devem ter páginas próprias.

### 5.4 Banco de dados único

Por diretiva humana de rtg003 em 2026-07-05, o SQLite local da VPS é o único
banco de dados do Tokio. A antiga réplica Supabase e o Supabase Auth foram
removidos da arquitetura; novas telas e relatórios devem ler do SQLite via
gateway interno. Backup local e offsite do SQLite é obrigatório.

## 6. Persistência deste protocolo

- Registrado como decisão em
  `docs/decisions/0009-protocolo-bilateral-cursor-hermes.md`.
- Referenciado no `README.md` e nos inboxes bilaterais.
- Sessões do Cursor carregam este `AGENTS.md` automaticamente: execute o
  ritual da seção 2 como primeira ação. O Hermes tem o mesmo dever via
  `docs/HANDOFF_HERMES.md` §8 e via a entrada correspondente em
  `docs/HERMES_UPDATES.md`.
