# tv_strategy_manage — ciclo de vida da estratégia TV

Criar, editar qualquer campo de risco/execução, ativar, pausar, **promover
testnet→mainnet** e **rotacionar o secret**. Sem fila de aprovação prévia
(autonomia §9), com auditoria HERMES em toda escrita.

## Endpoints (gateway interno, `X-Control-Token: $GATEWAY_CONTROL_TOKEN`)

Todos aceitam `"actor":"hermes"` no corpo JSON.

| Ação | Método · rota | Corpo | Efeito |
|------|---------------|-------|--------|
| criar | `POST /control/tv/strategies` | campos §5 + `strategy_id`,`name`,`environment` | nasce `draft`; devolve secret 1× |
| editar config | `POST /control/tv/strategies/{id}/config` | campos de risco/execução + `justification` | bumpa versão + diff auditado |
| ativar | `POST /control/tv/strategies/{id}/activate` | — | `draft/paused/auto_paused → active` |
| pausar | `POST /control/tv/strategies/{id}/pause` | — | `→ paused` (reversível por activate) |
| promover | `POST /control/tv/strategies/{id}/promote` | `environment` | muda o ambiente (fonte de verdade) |
| rotacionar secret | `POST /control/tv/strategies/{id}/rotate_secret` | — | novo webhook+secret 1×; antigo para de valer |

Exemplos:

```bash
# editar risco (testnet — autônomo)
curl -s -X POST http://127.0.0.1:8700/control/tv/strategies/tv_gap_fade/config \
  -H "X-Control-Token: $GATEWAY_CONTROL_TOKEN" -H "Content-Type: application/json" \
  -d '{"actor":"hermes","allocation_usd":500,"stop_loss_pct":0.9,
       "justification":"reduzir risco após 2 stops seguidos"}'

# pausar / ativar
curl -s -X POST http://127.0.0.1:8700/control/tv/strategies/tv_gap_fade/pause \
  -H "X-Control-Token: $GATEWAY_CONTROL_TOKEN" -d '{"actor":"hermes"}'
```

## Guardrails inegociáveis

- **Ambiente só muda por `promote`** — nunca por payload ou seletor de UI.
- **MAINNET tem gate humano preservado**: `activate`/`promote` para mainnet
  falham com `mainnet_nao_configurado` se as credenciais não estiverem no
  servidor. Toda mudança mainnet dispara notificação a Eduardo.
- **Confirme com Eduardo antes de promover para mainnet.** Edição de risco em
  testnet é autônoma; edição de estratégia mainnet notifica na hora.
- **Não desative caps globais nem toque no kill switch global** (perímetro,
  README). Isso não tem endpoint aqui.
- `desativar` = `pause` (reversível). Encerrar de vez é `engine.cli strategy
  archive <id> --yes` (cancela ordens, move a pasta) — só com pós-mortem.
- Toda escrita já grava `tv_strategy_versions` (diff + justificativa) → evento
  HERMES no Logs. Sempre passe uma `justification` clara na edição.
