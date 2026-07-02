# copy_trade (módulo)

- id: ct_* (uma estratégia por trader copiado — ver `traders/*.yaml`)
- module: copy_trade
- status: por trader (default: dry_run — sem exceção)
- hipótese: traders com histórico auditável de expectância positiva em swing/
  posição mantêm o edge quando espelhados com segundos de atraso; o edge de
  scalpers NÃO sobrevive à latência estrutural do espelhamento (por isso o
  discovery os filtra).
- edge esperado: definido por trader no relatório do discovery (PnL 30/90d,
  consistência, drawdown) — a decisão de copiar é sempre humana.
- parâmetros-chave (por trader, em `traders/<nome>.yaml`):
  - `address`: endereço-alvo na Hyperliquid
  - `mode`: `fixed_usdc` (notional fixo por posição) | `percent`
    (proporcional: equity do alvo vs. a sua)
  - `value`: USDC (fixed) ou fração (percent)
  - `max_leverage`, `blocked_assets`, `active`, `dry_run`
- thresholds: por trader (`min_net_pnl`, `min_trades`, `eval_window_days`)

## Regras de decisão (100% determinísticas)

1. WebSocket nos fills do endereço-alvo (leitura pública; ordens SEMPRE via
   gateway).
2. Espelhar aberturas, aumentos, reduções e fechamentos mantendo a proporção
   da posição do alvo:
   - alvo abre do zero → abrimos com notional `value` (fixed) ou
     `value × equity_própria / equity_alvo × notional_alvo` (percent);
   - alvo varia a posição por fator k → nossa posição varia pelo mesmo k;
   - alvo zera → fechamos tudo (reduce-only).
3. Ordens abaixo de US$ 10 notional: pular e logar (`decision.skipped_min_notional`).
4. Ativos em `blocked_assets`: pular e logar.
5. Latência alvo→espelho logada em todo trade (`latency_ms`).
6. Drift check periódico: posição espelhada esperada vs. ledger real; desvio
   > 5% gera `drift.detected` (alerta).

## Changelog de decisões

- 2026-07-02: implementação inicial (espelhamento por proporção de posição;
  startPosition do fill da HL como âncora anti-perda-de-evento).
