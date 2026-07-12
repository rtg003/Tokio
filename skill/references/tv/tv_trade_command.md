# tv_trade_command — comando natural → sinal `source: hermes`

Traduz um comando em linguagem natural ("abre long de BTC na tv_gap_fade") num
sinal estruturado e o injeta na MESMA fila do webhook, com `source: hermes`.
O sinal passa pelo MESMO validator determinístico (§8.2) — a autonomia do Hermes
NÃO fura guardrail nenhum.

## Fluxo obrigatório

1. **Estruture** o sinal a partir do comando e **confirme com Eduardo** o sinal
   já montado ANTES de emitir. Nunca emita direto.
2. Envie ao receiver interno:

   ```bash
   curl -s -X POST http://127.0.0.1:8702/signals/internal \
     -H "X-Internal-Token: $TV_INTERNAL_TOKEN" -H "Content-Type: application/json" \
     -d '{"source":"hermes","strategy_id":"tv_gap_fade",
          "alert_id":"hermes-2026-07-12T14:03:00Z","ticker":"BTCUSDT",
          "action":"buy","market_position":"long","price":64250.0,
          "timeframe":"4h","bar_time":"2026-07-12T14:00:00Z"}'
   ```

   Campos obrigatórios: `strategy_id`, `alert_id`, `ticker`, `action`
   (`buy|sell`), `market_position` (`long|short|flat`), `bar_time`. `price` e
   `timeframe` são opcionais. Autenticação é o token interno — o sinal `hermes`
   não usa o secret por-estratégia do webhook.
3. **Se o validator BLOQUEAR, reporte a condição EXATA** (block_code + o check
   que falhou, via `tv_explain_decision`). **Proibido reformular o sinal para
   contornar o bloqueio** — o bloqueio é a resposta certa.

## Guardrails

- `alert_id` único por intenção (a idempotência §5.3 deduplica repetição dentro
  de 24h → `DUPLICATE`).
- Sizing é do servidor; não mande tamanho "na mão" esperando que valha.
- Ordem/flip/fechamento seguem o netting do módulo — descreva a INTENÇÃO
  (long/short/flat), não a mecânica.
- Confirmação humana é parte do contrato desta skill, mesmo em testnet.
