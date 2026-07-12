# tv_strategy_import — material bruto → estratégia TV

Extrai uma estratégia de material não-estruturado (transcript, PDF, texto,
print de Pine) para o schema §6.1 e a cadastra **nascendo `draft`** (disabled-
first: o primeiro sinal de teste bate `STRATEGY_DISABLED`, provando o pipeline
com risco zero).

## Quando usar

"Importa essa estratégia do vídeo/artigo", "cadastra o setup que descrevi",
"transforma esse Pine em estratégia TV".

## Procedimento

1. Leia o material e mapeie para os campos §6.1. **Campos ausentes ficam `null`
   e são listados como pendências** — não invente números. Defaults conservadores
   do modal §5 só entram onde o material não fala de risco (o servidor aplica).
2. Escolha `strategy_id` no padrão `tv_<nome_curto>` (`^[a-z0-9_]{3,48}$`).
3. Crie (nasce draft):

   ```bash
   cd ${HERMES_SKILL_DIR}/..
   curl -s -X POST http://127.0.0.1:8700/control/tv/strategies \
     -H "X-Control-Token: $GATEWAY_CONTROL_TOKEN" -H "Content-Type: application/json" \
     -d '{"actor":"hermes","strategy_id":"tv_gap_fade","name":"Gap Fade 4h",
          "environment":"testnet","symbols_allowed":["BTC"],
          "timeframes_allowed":["4h"],"allocation_usd":1000,
          "risk_per_trade_pct":0.75,"stop_loss_pct":1.2,"take_profit_pct":2.4}'
   ```

   A resposta traz `webhook_url`, `secret` e `alert_json` **UMA vez** (só o hash
   persiste). Entregue-os a Eduardo para colar no alerta do TradingView.
4. Se **todos os campos obrigatórios** estiverem completos, pode ATIVAR na
   testnet (`tv_strategy_manage` → activate). Se houver pendências, deixe draft e
   liste o que falta.

## Guardrails

- NÃO ative mainnet no import (promoção é ato separado e notificado).
- Registre a hipótese e o edge esperado no `strategy.md` da pasta (template
  obrigatório). A criação já grava a versão 1 na auditoria como HERMES.
- Nunca preencha risco/sizing "no chute": pendência explícita > número inventado.
