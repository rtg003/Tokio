# Skills do módulo Trading View (TV-Executor) — camada Hermes (§9)

O Hermes tem **autonomia total sobre estratégias TV** (nunca no hot path), pela
MESMA API de controle da dashboard. Estas 5 skills são o contrato de operação.
Toda escrita se identifica com `"actor": "hermes"` — isso vira `changed_by:
hermes` na auditoria e, pela view `tv_events`, aparece como evento **HERMES** no
Logs (controle compensatório: nada do Hermes é invisível).

## Fronteiras internas (nunca expostas ao browser)

| Serviço | Base | Auth |
|---------|------|------|
| Gateway (controle + leitura) | `http://127.0.0.1:8700` | header `X-Control-Token: $GATEWAY_CONTROL_TOKEN` (só escrita) |
| Receiver (sinais internos) | `http://127.0.0.1:8702` | header `X-Internal-Token: $TV_INTERNAL_TOKEN` |

Segredos vêm do `.env` (600, owner tokio). Valide só a PRESENÇA — NUNCA
imprima/ecoe/logue valores. `cd ${HERMES_SKILL_DIR}/..` para chegar à raiz.

## Perímetro — FORA do alcance do Hermes (config de sistema, não estratégia)

- **Kill switch global**: acionar em emergência é permitido (`engine.cli kill`);
  **DESLIGAR (`unkill`) é exclusivo de Eduardo**.
- **Caps globais de risco**, gestão de **wallets/credenciais**, allowlist/infra.
- Estes não têm endpoint no módulo TV — a recusa é por construção. Se um pedido
  cair aqui, PARE e explique que está fora do seu alcance.

## As 5 skills

| Skill | Arquivo | O que faz |
|-------|---------|-----------|
| `tv_strategy_import` | [tv_strategy_import.md](tv_strategy_import.md) | material bruto → schema §6.1; cria draft |
| `tv_strategy_manage` | [tv_strategy_manage.md](tv_strategy_manage.md) | criar/editar/ativar/pausar/promover/rotacionar secret |
| `tv_trade_command` | [tv_trade_command.md](tv_trade_command.md) | comando natural → sinal `source: hermes` |
| `tv_explain_decision` | [tv_explain_decision.md](tv_explain_decision.md) | explica qualquer evento do `tv_events` |
| `tv_daily_report` | [tv_daily_report.md](tv_daily_report.md) | relatório diário do módulo + alterações do Hermes |

## Regras comuns a toda mudança

1. **Ritual pré-alteração** (SKILL.md §protocolo): `git pull`, ler
   `HERMES_UPDATES.md`, checar PR aberto do Cursor na mesma área.
2. **Ambiente é fonte de verdade em `tv_strategy_meta`** — nunca vem do payload
   nem de seletor de UI. Só muda por `promote`.
3. **Sizing é sempre no servidor** — `position_size` de qualquer payload é
   informativo.
4. **Mudança que afeta MAINNET** (editar estratégia mainnet, promover, rotacionar
   secret mainnet) dispara notificação a Eduardo (hoje evento SYSTEM no Logs,
   `tv.notify.mainnet_change`; canal real §12.6 pluga depois).
5. **Confirme com Eduardo antes de emitir ordem/sinal** (`tv_trade_command`) e
   antes de promover para mainnet. Editar config de risco/execução em testnet é
   autônomo.
