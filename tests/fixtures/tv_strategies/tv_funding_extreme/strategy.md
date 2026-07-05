# tv_funding_extreme

- id: tv_funding_extreme
- module: tradingview
- status: dry_run
- hipótese: funding rate em extremos (percentil alto/baixo histórico) indica
  posicionamento lotado; reversões de curto prazo têm expectância positiva ao
  operar contra o lado lotado.
- edge esperado: a validar em backtest/dry-run; o scanner (Fase 5) fornece a
  medição de anomalias de funding que embasa o alerta no TradingView.
- parâmetros-chave: notional default 20 USD (cap 80), alavancagem máx. 2x,
  símbolos BTC e ETH.
- thresholds: min_net_pnl -12 USD / 14 dias, mínimo 5 trades.

## Regras de decisão

1. Alerta `sell` quando o funding está extremo positivo (longs pagando caro);
   `buy` no extremo negativo — o gatilho vem do alerta do TradingView.
2. `close` fecha a posição da estratégia (reduce-only).
3. Mesmo contrato de webhook e mesmas rejeições logadas das demais
   sub-estratégias (token, schema, símbolo).

## Changelog de decisões

- 2026-07-02: criação como sub-estratégia de exemplo em dry-run.
