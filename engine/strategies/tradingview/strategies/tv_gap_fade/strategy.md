# tv_gap_fade

- id: tv_gap_fade
- module: tradingview
- status: dry_run
- hipótese: o gap entre o fechamento do CME (sexta) e a reabertura (domingo)
  tende a ser preenchido em BTC — ineficiência recorrente entre mercado
  contínuo (cripto) e mercado com horário (CME).
- edge esperado: a validar no harness de backtest (Fase 6) e no dry-run —
  nenhuma ativação sem expectância positiva líquida de taxas registrada em docs/.
- parâmetros-chave: notional default 25 USD (cap 100), alavancagem máx. 2x,
  símbolo BTC apenas.
- thresholds: min_net_pnl -15 USD / 14 dias, mínimo 5 trades para avaliar.

## Regras de decisão

A lógica de sinal vive NO TradingView (alerta do usuário sobre o indicador de
gap do CME); este runner apenas executa o contrato do webhook:

1. Alerta `buy`/`sell` com `strategy_id: tv_gap_fade` → ordem market com o
   notional do sizing hint (limitado a `max_notional_usd`).
2. Alerta `close` → fecha a posição virtual da estratégia (reduce-only).
3. Payload malformado, token inválido ou símbolo fora da lista → rejeitado e
   logado; nunca vira ordem.

## Changelog de decisões

- 2026-07-02: criação como sub-estratégia de exemplo em dry-run.
