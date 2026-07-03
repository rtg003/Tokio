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

## logic_version: 2 — spec PROMPT_DISCOVERY_TRADERS_v5 (EM PRODUÇÃO em 2026-07-03)

- **Autor**: Cursor (construtor), por diretiva humana; spec v5 + diagnóstico
  do humano sobre as 5 falhas da v1.
- **Motivo**: v1 restrita (top 30, 1 janela, 4 filtros) e enviesada (bug do
  hold via startPosition; score punitivo; PnL absoluto no ranking).
- **O que mudou (antes → depois)**:
  - Fonte: top 30 por PnL → **top 500**, corte barato por 30d>0 + equity ≥
    US$ 5k, aprofundamento priorizado por **ROI 30d** (PnL absoluto puro só
    trazia mega-baleias inativas — constatação da validação real).
  - Janelas: 30d apenas → **7d/30d/60d/90d** com regra de entrada "≥3/4
    positivas, 30d e 60d obrigatórias" (7d pode ser negativa).
  - Filtros: 4 → **F1–F11** (com F1 pre-check barato em janela própria de 7d
    — a paginação de fills enviesava o last_activity de hiperativos).
  - Hold: startPosition==0 → **episódios de posição** (início desconhecido =
    excluído da mediana; hold desconhecido NUNCA classifica scalper).
  - Trades fechados: episódios zerados → **fills de fechamento (closedPnl≠0)**
    (position traders reduzem sem zerar e eram punidos).
  - Retorno: ROI simples → **TWRR neutro a aportes** (+ F10 anti-aporte).
  - Score: 4 componentes punitivos → fórmula da spec (25/20/15/15/15/10) com
    PF gradativo (patch de 2026-07-03), ROI log, DD quality, copiabilidade,
    expectância líquida; ajustes +5/−10/−5.
  - Coortes: estilo unidimensional → **bidimensional** (tamanho × PnL) +
    coorte de controle **rekt** com snapshots de posicionamento por ativo.
  - Reprovados: descartados → persistidos **REJEITADO + reject_reason**.
- **Resultado esperado**: ranking maior e de qualidade auditável; scalpers e
  contas infladas por aporte fora; separação smart vs. rekt testada em CI.

## logic_version: 3 — afrouxamento de filtros (2026-07-03)

- **Autor**: humano (rtg003), implementado pelo Cursor.
- **Motivo (justificativa numérica)**: o primeiro scan real full-budget da v2
  (`b684b8bbe5f5`, 2026-07-03: 500 coletados, 100 aprofundados, 650 requests)
  aprovou **0 candidatos**. Reprovações: F3 anti-scalper **34**, F5 DD>25%
  **24**, F4 TWRR<5% **8**, entrada (30d+60d obrigatórias) **7**, F1 3, F2 2,
  F6 1, F7 3, interrompidos por orçamento 18. Os filtros estavam calibrados
  para um universo que não existe no leaderboard real.
- **O que mudou (antes → depois)**:
  - **F3 (anti-scalper): desabilitado** (`f3_*: null` no config; o código pula
    filtros com threshold null). Scalper deixa de ser eliminado e passa a ser
    penalizado pelo score de copiabilidade (frequência fora do sweet spot
    0.3–20 trades/dia e hold fora de 4h–72h derrubam o componente de 15%).
  - **F4 (TWRR 30d ≥ 5%): desabilitado** (`null`). O TWRR segue calculado,
    persistido e exibido no dossiê/rationale — só não é mais eliminatório.
  - **F5: teto de DD 90d 25% → 40%**. Efeito colateral documentado: o teto
    também é o cap do componente `drawdown_quality` do score, que fica
    proporcionalmente mais tolerante (DD 20% agora pontua 0.5 de magnitude,
    antes 0.2).
  - **Entrada: "≥3/4 janelas, 30d e 60d obrigatórias" → "≥2/4 janelas, só a
    30d obrigatória"**. Sem reduzir o mínimo para 2, os reprovados "2/4" do
    scan real continuariam fora e a flexibilização seria inócua.
- **O que NÃO mudou**: F1/F2 (atividade e amostra), F6–F11, pesos do score,
  coortes, orçamento de requests. A numeração F1–F11 é preservada (filtros
  desabilitados ficam no código; reativar = config + novo bump).
- **Resultado esperado**: tabela `traders` populada com aprovados reais; perfis
  de maior frequência/DD aparecem com score proporcionalmente menor em vez de
  sumirem do funil.

## Registro histórico da implementação

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
