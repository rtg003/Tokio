# ADR 0008 — Tabela `traders` única (candidatos + copiados) e Gate 2

- Status: aceito
- Data: 2026-07-03
- Substitui: YAMLs por trader em `engine/strategies/copy_trade/traders/`

## Contexto

O copy trade nasceu com 1 YAML por trader copiado, e o discovery gerava
relatórios sem persistência estruturada. Isso criava duas fontes de verdade
(arquivos + relatórios) e nenhum ciclo de vida formal entre "candidato
descoberto" e "trader copiado".

## Decisão

- **Tabela `traders` única** (migration `0002_traders`): candidatos e
  copiados juntos, upsert por `address` (lowercase). Config de execução como
  colunas (`mode` fixed_usdc|percent, `value`, `max_leverage`,
  `blocked_assets`, `dry_run` default true, `thresholds`), métricas do
  discovery (`score`, `cohort`, `twrr_30d`, `pnl_30d`, `windows`,
  `profit_factor`, `win_rate`, `max_drawdown`, `liq_distance`), `origin`
  (discovery|manual) e `logic_version` (versão da lógica de discovery que
  produziu as métricas).
- **Ciclo de vida** (`status`): `SUGERIDO | DRY_RUN | COPIANDO | PAUSADO |
  REJEITADO | ARQUIVADO`.
- **Gate 2 (humano)**: `SUGERIDO → DRY_RUN/COPIANDO` e `DRY_RUN → COPIANDO`
  só pela CLI com confirmação (`trader approve`, `--live` exige `--evidence`).
  A API de controle do gateway recusa essas transições por construção; ela
  cobre apenas operação (`pausar/retomar/rejeitar`) e config (nunca
  `dry_run=false`).
- **Toda mudança é logada em `events`** (`trader.status_changed`,
  `trader.config_changed`), com autor (`by`).
- Re-scan do discovery faz upsert de métricas **sem rebaixar status** de quem
  já opera. Snapshots agregados por coorte a cada scan em `cohort_snapshots`.
- Os YAMLs foram migrados pela CLI (`db migrate` importa e remove os
  arquivos) e o diretório eliminado do repo. O executor lê a tabela e
  recarrega a cada 30 s — mudanças entram sem restart.

## Consequências

- Fonte única consultável pelo dashboard (réplica no Supabase com RLS).
- `logic_version` permite evoluir a lógica do discovery (v1 trivial → v2 na
  spec PROMPT_DISCOVERY_TRADERS_v4) preservando dados históricos
  (`docs/discovery_changelog.md`).
