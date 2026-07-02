# ADR 0004 — Sinais do TradingView via webhook de alertas

- Status: aceito
- Data: 2026-07-02

## Contexto

Não existe API oficial de sinais do TradingView nem MCP oficial. O Strategy
Tester também não tem API (por isso o backtest é um harness local com candles
da Hyperliquid).

## Decisão

- Caminho de produção: **webhook de alertas** do TradingView → servidor FastAPI
  (`engine/strategies/tradingview/webhook_server.py`), HTTPS atrás do proxy,
  com token secreto obrigatório e payload JSON padronizado
  (`strategy_id`, `symbol`, `action`, sizing hint, `timestamp`).
- Payloads sem token válido ou malformados são rejeitados e logados.
- Roteamento por `strategy_id` para sub-estratégias declarativas dentro do
  runner TV, cada uma com fronteira de exceção própria.

## Consequências

- MCP de TradingView permanece fora do v1 (não-objetivo).
- Backtesting é feito localmente com candles históricos da HL (5.000/chamada).
