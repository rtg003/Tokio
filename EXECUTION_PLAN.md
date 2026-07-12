# EXECUTION_PLAN — Módulo TV-Executor (Trading View)

> Par normativo: `PROMPT-TV-EXECUTOR-v1.4.2.md` (comportamento) +
> `DESIGN-TV-DASHBOARD-v1.0.md` (apresentação). Em conflito, o PROMPT manda no
> comportamento e o DESIGN na tela. Este plano cumpre o §0 do PROMPT: é o
> artefato de aprovação que mapeia cada item das fases F0–F3 para
> arquivos/commits, produzido ANTES de qualquer código do módulo.
>
> Base verificada: repo em `37f59be` (UPDATE-0035 aplicado — Wallet + Ambiente
> globais na statusbar). Aprovado por Eduardo (rtg003) em 2026-07-12.

## 0. Decisões travadas (§12 + auditoria)

| Item | Decisão |
|---|---|
| §12.2.1 Trigger SL/TP | Campos **opcionais `stop_loss`/`take_profit` no `IntentRequest`**; a ausência dos campos percorre caminho de código IDÊNTICO ao atual via guard clause (§8.4.1 passo 2). |
| §12.2.2 Cadastro TV | **Tabela satélite `tv_strategy_meta`** (reusa `strategies` com `module='tradingview'`, nunca duplica cadastro); view `tv_strategies` une as duas. `strategies` fica intocada. |
| §12.2.3 `bbo(symbol)` | Implementação via **`l2Book`** do SDK (uma chamada retorna best bid/ask + profundidade). |
| §12.4.1 Notificação | **Fallback F0/F1**: evento `SYSTEM` no card Logs + resumo no `tv_daily_report`. Canal real (Telegram / e-mail / Hermes Gateway) definido por Eduardo antes de fechar a F1. |
| §12.4.2 `manual_hermes` | **Defaults conservadores do modal §5**; `allocation_usd` definido por Eduardo na F2. |
| Kill switch (desvio deliberado) | O PROMPT §8.2/§10.2 diz "flag DB". Hoje o kill switch é **file-based** (`settings.kill_file`), já imposto no `risk_enforcer`, com `/control/kill` e exposto em `/health.kill_switch`. **Reusamos essa fonte única**: o validator (check 3) lê `/health.kill_switch`; o header TV aciona via `/control/kill`. NÃO se cria flag DB divergente. Aprovado por Eduardo. |

## 1. Achados da auditoria do código (base `37f59be`)

- `strategies` (`db/migrations/0001_initial.sql:17`) já tem `module IN
  ('copy_trade','tradingview','standalone','dummy')`, enum de status completo
  (`draft|dry_run|active|paused|auto_paused|archived`), `config_snapshot` e
  `thresholds` (JSON). **Faltam** `environment` e `secret_hash` → satélite
  `tv_strategy_meta`. `orders.type` já aceita `'trigger'`.
- `events` (`0001:68`) = `(id, ts, strategy_id, event_type, level, payload json)`
  → base da view `tv_events`.
- Gateway `IntentRequest` (`engine/gateway/server.py:54-68`) já tem
  `environment`, `reduce_only`, `leverage`, `size`/`notional_usd`, `meta`,
  `dry_run`, legado `paper` (não usado pelo módulo). `_adapter_for(env)`
  (`server.py:245`) roteia por ambiente; signatário único. `handle_intent`
  (`server.py:389`) coloca a ordem em `server.py:491` — ponto do guard clause de
  bracket.
- Adapter expõe só `mid_price` (`engine/exchanges/hyperliquid/adapter.py:282`);
  **sem `bbo`, sem SL/TP**.
- Gate humano em `traders_store.set_status(..., human_gate=True)`; `/intent` e
  `/cancel` **sem gate** (INVARIANTE explícita em `server.py:502`). Não tocar.
- Runner de migrations idempotente em `engine/core/db.py:30` (`db migrate`);
  maior número atual **0018** → próxima **0019**. Sem `tv_*`, sem `symbol_map`,
  sem fila/worker.

## 2. Fases → arquivos/commits

Commits pequenos e frequentes na `main` (ritual AGENTS.md §2: `git pull` antes de
cada push). Regra do mesmo commit para o inbox `HERMES_UPDATES.md`. Não iniciar
fase com critérios da anterior abertos.

### F0 — Contrato e recepção (sem execução)
- **Migração** `db/migrations/0019_tv_executor.sql`:
  - `tv_strategy_meta(strategy_id PK FK→strategies, environment CHECK(testnet|
    mainnet), secret_hash, url_secret_hash, version, updated_at)`.
  - `tv_strategy_versions(strategy_id, version, config json, changed_by,
    change_summary, created_at)`.
  - `tv_signals(id, signal_key UNIQUE, source, strategy_id, environment,
    raw_payload json, parsed json, state, received_at)`.
  - `tv_signal_decisions(signal_id, outcome, checks json, netting_plan json,
    computed_size_usd, created_at)`.
  - `tv_symbol_map(tv_ticker PK, hl_coin, enabled)`.
  - `tv_incidents(id, signal_id NULL, type, details json, resolved, created_at)`.
  - `tv_queue(id, signal_id, status, attempts, created_at)` — fila WAL.
  - VIEW `tv_events` = `events` ∪ `tv_signals`+`tv_signal_decisions` ∪
    `tv_incidents` ∪ `tv_strategy_versions`; colunas `ts, kind
    (SIGNAL|INCIDENT|HERMES|SYSTEM|USER), severity, summary, ref_id, detail`.
  - VIEW `tv_strategies` = `strategies` ⋈ `tv_strategy_meta` filtrando
    `module='tradingview'`.
- **Receiver** FastAPI novo `engine/tv/receiver.py` + container no
  `docker-compose.yml`/`docker-compose.prod.yml`, porta **8702 / 127.0.0.1**:
  - `POST /tv/{url_secret}` → `202` em <500ms, persiste `raw_payload`, enfileira
    em `tv_queue`.
  - `POST /signals/internal` → mesmo schema, token interno, `source:
    hermes|manual|test`.
  - `GET /tv/healthz` → receiver + fila + gateway.
  - Rate limit por IP (30/min) e por estratégia (10/min).
  - Caddy: bloco `tokio.bz/tv/*` → `127.0.0.1:8702` com precedência sobre o proxy
    do Next.js; allowlist de IPs do TradingView (lista oficial ao implementar,
    não hardcodar).
- **Validator** determinístico `engine/tv/validator.py`: checklist §8.2 (1–13),
  cada check com `required` vs `actual`, primeira falha encerra e restantes
  `skipped`; persiste array completo em `tv_signal_decisions`. Check 3 lê
  `/health.kill_switch`. Checks 8–9 dependem de market data / `bbo` (F1) — em F0
  ficam `skipped` (sem execução).
- **Netting** puro `engine/tv/netting.py`: `(sinal, posição, policy)` → plano de
  intenções; `flip` = duas intenções com dependência de fill.
- **Worker** da fila `engine/tv/worker.py` (SQLite WAL) + registro em
  `engine/supervisor.py` / `deploy/engine-processes.yaml`.
- **Symbol map** seed + kill switch (reuso) + logs JSON correlacionados por
  `signal_id`; nenhum secret em log.
- **Aceite F0**: sinal real do TradingView chega, é validado/deduplicado/
  persistido em <500ms; replay ⇒ `DUPLICATE`; T1–T9, T14, T16 passam.

### F1 — Execução (testnet primeiro) — SOB PROTOCOLO §8.4.1 (REGRESSÃO PRIMEIRO)
Cada passo é um commit distinto, nesta ordem inegociável:
1. **Baseline de regressão do Copy Trade** — `tests/gateway/test_intent_regression.py`:
   captura o comportamento ATUAL (`/intent` market e limit com `size` e
   `notional_usd`, `reduce_only`, `leverage`, `dry_run`, roteamento por
   `environment`, `/cancel` por cloid, parsing `on_own_fill`, erro de negócio sem
   retry vs transitório com retry, ordering de nonce sob intents concorrentes).
   Roda na testnet, congela como baseline. **PROIBIDO tocar gateway/adapter ou
   escrever código do módulo TV antes desta suite existir e estar verde.**
2. **Mudança aditiva backward-compatible** — `stop_loss`/`take_profit` opcionais
   em `IntentRequest` (`server.py:54`) + método `bbo(symbol)` via `l2Book` no
   adapter. A ausência dos campos percorre `handle_intent`/`place_order`
   idênticos (desvio por guard clause). PROIBIDO no mesmo change: refactor,
   rename, mover código, formatação de código existente.
3. **Regressão verde pós-mudança** — a MESMA suite do passo 1 passa sem editar
   nenhum teste. Editar baseline para "passar" = falha de processo → parar e
   reportar a Eduardo.
4. **Validação funcional nova** (testnet) — trigger orders (T10, T11), grupo
   entrada+SL+TP feliz, rollback (stop rejeitado ⇒ fechar + `INCIDENT_
   UNPROTECTED_POSITION`).
5. **Canário** — deploy com Copy Trade operando; ~24h sem divergência de
   reconciliação antes de ativar a 1ª estratégia TV na testnet.
6. **Falha de regressão em qualquer ponto ⇒ reverter o change** (nunca corrigir
   por cima com baseline vermelho).
- Executor `engine/tv/executor.py` via gateway (`origin_signal_id` em `meta`);
  netting em execução; promoção de ambiente (troca de `environment`, vale só para
  sinais futuros); reconciliação periódica por ambiente (ledger vs
  `Info.user_state`).
- **Aceite F1 (testnet)**: baseline verde antes e depois; sinal aprovado abre
  posição com SL+TP visíveis na exchange; short abre short; fechamento
  reduce-only; flip em 2 etapas; stop rejeitado ⇒ incidente; T10–T13 passam.

### F2 — Hermes
As 5 skills da §9 (`tv_strategy_import`, `tv_strategy_manage`, `tv_trade_command`,
`tv_explain_decision`, `tv_daily_report`) via API interna autenticada (mesmas
validações de schema/limites do modal), autonomia total sem fila de aprovação.
Controles compensatórios: toda escrita gera `tv_strategy_versions` (diff +
`changed_by: hermes` + justificativa) e evento `HERMES` no Logs; mudança que
afete mainnet dispara notificação (fallback definido). Perímetro fora do Hermes:
kill switch global (desligar exclusivo de Eduardo), caps globais, wallets/
credenciais. `manual_hermes` com defaults do modal §5.
- **Aceite F2**: comando natural vira sinal confirmado sob os mesmos guardrails;
  ações do Hermes aparecem no Logs com diff e versão (T15); tentativa do Hermes de
  alterar caps globais ou desligar kill switch é recusada.

### F3 — Dashboard (`DESIGN-TV-DASHBOARD-v1.0.md`)
- Item de nav "Trading View" (ícone `TV`) ACIMA de "Copy Trade" em
  `web/components/Shell.tsx` (nav ~L163-169); rota nova
  `web/app/(app)/trading-view/page.tsx` (server component, `force-dynamic`, lê
  `readEnv/readWallet` de `web/lib/prefs.ts`). O botão fantasma "+ nova
  estratégia" abre o wizard só na rota `/trading-view`.
- Camada de dados própria `web/lib/trading-view/data.ts` via `gatewayGet`, SEMPRE
  filtrando por módulo + ambiente global (isolamento §5.1 AGENTS.md).
- Componentes novos em `web/components/trading-view/*`, derivados de
  `copy-trade/*` (KpiRow, PositionsTable, TradesOrdersTable, TradersTable,
  DashboardControls reduzido, CopyConfigModal como base do wizard/modal). Ordem
  dos cards: header operacional → filtros (Estratégia + Período) → 6 KPIs →
  Estratégias → Posições → Trades → **Logs (último, `tv_events`, minimalista,
  expansível, paginado por cursor)**. Tokens de `web/app/globals.css` só reuso.
- Wizard 4 passos (design §4) com handshake fim-a-fim: estratégia nasce
  `disabled`; sinal de teste chega `BLOCKED · STRATEGY_DISABLED`; "Concluir" ativa
  na testnet. Modal de ativação/edição (design §5) com preview de sizing e
  validações §6.3. Nenhuma tela existente alterada.
- **Aceite F3**: critérios §7 do design (item acima de Copy Trade; troca de
  ambiente atualiza tudo e não altera execução; isolamento bidirecional; Logs
  último e unificado; wizard só ativa após sinal de teste; modal valida limites e
  mostra preview).

## 3. Invariantes preservadas (todos os sources)
- Gate humano de status (`set_status(human_gate=True)`; MAINNET exige
  credenciais) — nunca contornar.
- Nunca adicionar gate a `/intent` nem `/cancel` (`server.py:502`).
- Isolamento de observabilidade (§5.1 AGENTS.md): toda query de exibição filtra
  por `strategy_id`/módulo + ambiente global.
- Sizing sempre no servidor; `position_size` do payload é informativo. O ambiente
  de execução vem de `tv_strategy_meta.environment`, nunca do payload nem do
  seletor global de UI.
- Regra do mesmo commit para os inboxes.
- Toda estratégia nova nasce `disabled` e ativa primeiro na testnet.
- "default" = configurável com esse default, nunca constante hardcoded (§0).

## 4. Verificação end-to-end
- **Migração**: `make migrate` (ou `python -m engine.cli db migrate`); conferir
  `schema_migrations` e as tabelas/views novas.
- **Regressão gateway (§8.4.1)**: suite baseline verde ANTES e DEPOIS da mudança
  (testnet).
- **Casos T1–T16** (§11) como testes automatizados; T10–T13 na testnet real.
- **F0**: enviar sinal real do TradingView + replay; conferir `DUPLICATE`,
  latência <500ms, decisão persistida com checklist completo.
- **F3**: subir o dev server, abrir `/trading-view`, exercitar golden path e edge
  cases (ambiente sem dados = zeros; troca de seletor não dispara execução — T16),
  verificar isolamento (Copy Trade inalterado) e o wizard fim-a-fim no browser.

## 5. Pendências de Eduardo (não bloqueiam F0)
1. Canal de notificação definitivo (§12.4.1) — necessário até o fim da F1.
2. `allocation_usd` default do `manual_hermes` (§12.4.2) — necessário na F2.
