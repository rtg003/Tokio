# Post-mortem — <strategy_id>

- Período operado: <início> → <arquivamento>
- Módulo: copy_trade | tradingview | standalone
- Motivo do arquivamento: <threshold violado / hipótese invalidada / decisão humana>

## Números finais (líquidos de taxas)

| Métrica | Valor |
|---|---|
| PnL líquido | |
| Expectância por trade | |
| Profit factor | |
| Max drawdown | |
| Win rate | |
| Nº de trades | |
| Taxas totais | |

Fonte: `python -m engine.cli report --strategy <id>` (histórico permanece no banco).

## O que a hipótese previa vs. o que aconteceu

<análise objetiva, com números>

## Lição acionável

<1–3 linhas: o que muda na próxima estratégia. Copiar para
`skill/references/lessons.md` via PR.>
