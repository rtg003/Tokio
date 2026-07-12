# tv_daily_report — relatório diário do módulo TV

Resumo diário por exceção: sinais/execuções/bloqueios, PnL por estratégia, drift
testnet vs mainnet da mesma família, anomalias e — obrigatório — o resumo das
**alterações do próprio Hermes** no dia.

## Fontes (read-only, gateway interno)

```bash
# estratégias do ambiente (isolado ao módulo TV)
curl -s "http://127.0.0.1:8700/api/tv/strategies?environment=testnet"
curl -s "http://127.0.0.1:8700/api/tv/strategies?environment=mainnet"

# eventos do dia (filtrar por kind conforme a seção)
curl -s "http://127.0.0.1:8700/api/tv/events?limit=200"
```

PnL/trades por estratégia vêm dos endpoints compartilhados JÁ escopados aos ids
do módulo (`/api/pnl/summary`, `/api/fills/summary`, `/api/metrics` com
`strategy_id=` das estratégias TV + `network=<env>`). NUNCA varra `events` para
calcular métrica — use os agregados.

## Estrutura do relatório

1. **Sinais**: recebidos, aprovados, bloqueados (por `block_code`), duplicados.
2. **Execuções**: ordens/fills, PnL líquido por estratégia (taxas + slippage).
3. **Drift**: para famílias com par testnet+mainnet, compare comportamento;
   divergência inesperada é anomalia.
4. **Anomalias**: incidentes abertos, auto-pausas, bloqueios recorrentes.
5. **Alterações do Hermes**: liste os eventos `kind=HERMES` do dia (estratégia,
   versão, `change_summary`) — transparência total das suas próprias ações.

## Guardrails

- Por EXCEÇÃO: destaque o que exige atenção, não despeje tudo.
- Isolamento §5.1: só dados do módulo TV.
- Notificação de mudança mainnet (evento `tv.notify.mainnet_change`) entra no
  topo do relatório se houver.
