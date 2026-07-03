# Changelog da lógica do discovery (copy trade)

A coluna `traders.logic_version` indica qual versão produziu as métricas de
cada candidato. Registro retroativo + versões futuras.

## logic_version: 1 — "trivial" (2026-07-02, retroativo)

Lógica em produção desde a Fase 3 do build (registrada retroativamente):

- **Coleta**: leaderboard público da Hyperliquid (mainnet) + análise de
  carteira por candidato (fills recentes, portfólio, equity).
- **Métricas**: PnL 30d e ROI 30d (janelas do leaderboard), max drawdown
  (curva de equity do mês), win rate e consistência (top-2 trades vs. PnL
  positivo total) sobre fills, frequência (trades/dia) e hold mediano.
- **Coorte (unidimensional)**: estilo — `scalper` / `swing` / `position`,
  por frequência e hold mediano.
- **Filtros de exclusão**: scalpers (> 40 trades/dia ou hold < 30 min — o
  edge não sobrevive à latência do espelhamento), equity < US$ 10k, PnL 30d
  não positivo, resultado concentrado (consistência < 0,2 com amostra ≥ 10).
- **Score (0–100)**: 35% ROI 30d (clampado) + 25% drawdown invertido +
  20% consistência + 20% win rate. Ordenação sempre por score decrescente.
- **Aproximações conhecidas**: `twrr_30d` = ROI da janela (não é TWRR
  verdadeiro); `profit_factor` e `liq_distance` não calculados; coorte não é
  bidimensional.

## logic_version: 2 — spec PROMPT_DISCOVERY_TRADERS_v5 (EM IMPLEMENTAÇÃO)

- **Spec**: `docs/specs/PROMPT_DISCOVERY_TRADERS_v5.md` (recebida 2026-07-03).
  Substitui integralmente a spec breve da Fase 3. Funil de 3 estágios
  (coleta 4 janelas → 11 hard filters → scoring ponderado), coorte
  bidimensional (tamanho × PnL), coorte de controle rekt com teste de
  separação, TWRR neutro a aportes, CLI `scan/inspect/positioning/token/report`.
- **Patch de scoring (2026-07-03, humano)** — profit factor com crédito
  gradativo, já implementado em `engine/strategies/copy_trade/metrics.py`:
  - antes: cap duro em 3.0 → depois: integral até 3.0; meio-crédito 3.0–5.0
    APENAS se `n_trades ≥ 60` na janela; acima de 5.0 não pontua;
  - PF calculado incluindo PnL não realizado das posições abertas no
    fechamento da janela (PF só de realizados é inflável ao não fechar
    perdedores);
  - motivo: PF extremo com amostra pequena é variância, não habilidade;
  - resultado esperado: score deixa de premiar sortudos de poucas operações;
  - testes: PF 4.8/32 trades sem crédito estendido; PF 4.5/80 trades com.
- Mini-plano endpoint → métrica em `docs/discovery_v2_plan.md` (gate humano
  da spec antes do código do funil).
