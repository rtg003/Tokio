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
- **Validação real (scan `4439dbfd5038`, 2026-07-03, full budget 650 req)**:
  **5 aprovados** (era 0 na v2), scores 57.6–91.3, janelas 3/4–4/4, nenhum
  aprovado com DD > 40%. Funil: F5 48 (maior gargalo mesmo a 40%), F8 13,
  F1/F6/F7 4 cada, F9 3, F2 1, entrada 1, interrompidos por orçamento 17.
  Ex-reprovados por F3/F4 foram triados pelos filtros seguintes e pelo score,
  como projetado.

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

## logic_version: 4 — refinamento do funil (2026-07-03)

- **Autor**: Hermes (operador), com autorização humana explícita (exceção ao
  desempate de área AGENTS.md §4 — código + config no mesmo PR).
- **Motivo (justificativa numérica)**: scan v3 real (`4439dbfd5038`) aprovou
  5 candidatos com scores 57.6–91.3, mas 3 dos 4 candidatos em produção (v1)
  tinham DD > 40% (incl. um com 99.3%). F5 (DD) reprovou 48% dos aprofundados
  — maior gargalo. 17 candidatos interrompidos por orçamento. min_equity
  $5k filtrava traders pequenos/médios potencialmente melhores.
- **O que mudou (antes → depois)**:
  - **F5 drawdown_quality piecewise** (código: `metrics.py`): magnitude decai
    por faixas em vez de linear. DD 0-20% = score cheio; 20-30% = ×0.7;
    30-40% = ×0.4; >40% = reprovado. Um trader com DD 25% agora pontua menos
    que um com DD 15% — antes tinham o mesmo quality.
  - **request_budget: 650 → 800** (config): elimina os 17 interrompidos por
    orçamento no scan v3.
  - **min_equity_usd: 5000 → 2000** (config): abre o funil para traders
    pequenos/médios ($2k–$50k) que podem ter edge melhor que baleias.
  - **min_score_for_suggestion: 60.0** (config): candidatos com score < 60
    viram REJEITADO (não SUGERIDO). Diferencia candidatos ruins de bons.
- **O que NÃO mudou**: F1–F4 (filtros), F6–F11, pesos do score, coortes,
  entry_rule, copyability, cost_of_copy.
- **Resultado esperado**: mais candidatos analisados (800 req vs 650),
  distribuição de score mais granular (faixas de DD), só SUGERIDO acima de
  60.

## logic_version: 5 — refinamento profundo + varredura ativa (2026-07-03)

- **Autor**: Hermes (operador), com autorização humana explícita (exceção ao
  desempate de área AGENTS.md §4 — código + config no mesmo PR).
- **Motivo (justificativa numérica)**: scan v4 (`fb138be7e938`) aprovou 6
  candidatos, mas 3 tinham 0 trades em 30d (#3, #5, #6) e 1 tinha PF de 5453
  (absurdo — ausência de perdas realizadas). Só 1 dos 6 (#4, score 71.24) era
  minimamente analisável. F5 reprovou 57/94 (61%) — o leaderboard é enviesado
  para sobreviventes de alto risco. 317 requests usados de 800 budget —
  deep_dive_max: 100 era o limitante real, não o orçamento.
- **O que mudou**:
  a) **F2b (min_trades_30d: 5)** — novo filtro: trader sem atividade recente
     não tem o que copiar. Elimina os 3 aprovados com 0 trades em 30d.
  b) **PF absurdo penalizado** — PF > 10 recebe -5 no score (config:
     pf_absurd_threshold: 10.0, pf_absurd_penalty: -5). PF exibido capado
     em 10.0 (PF de 5453 é enganoso).
  c) **deep_dive_max: 100 → 150** + **request_budget: 800 → 1100** — era o
     limitante real; agora 150 candidatos × ~7 req = 1050 < 1100.
  d) **Varredura ativa** — `active_addresses()` em `hl_data.py`: coleta
     endereços além do leaderboard (expandido + conhecidos). Integrado no
     `run_scan` como candidatos extras no deep dive. Window: 48h.
  e) **active_scan_window_hours: 48** — captura fim de semana + dia útil.
  f) **active_scan_max_addresses: 200** — limite de endereços novos por scan.
  g) **active_scan_min_notional_usd: 1000** — ignora fills poeira.
- **O que NÃO mudou**: F1, F3/F4 (off), F5 (40% + bands), F6–F11, pesos,
  coortes, entry_rule, copyability, cost_of_copy.
- **Resultado esperado**: menos falsos positivos (F2b elimina inativos),
  scores mais justos (PF penalizado), mais candidatos (150 vs 100), fonte
  menos enviesada (varredura ativa além do leaderboard).

## logic_version: 6 — coleta por atividade recente + leaderboard expandido (2026-07-03)

- **Autor**: Hermes (operador), com autorização humana explícita.
- **Motivo (justificativa numérica)**: varredura ativa manual revelou que o
  leaderboard tem 40.191 rows (não 500), com 2.277 candidatos realistas
  (PnL 7d>$200, equity $5k-$500k, ROI 10-100%). O discovery pegava só os
  primeiros 500 (por PnL all-time) — majoritariamente baleias inativas ou
  com DD absurdo. Apenas 3 dos 100 aprofundados no v5 tinham trades reais
  em 48h. Deep dive manual encontrou 10 traders ativos em 48h com perfil
  copiável que não estavam sendo coletados.
- **O que mudou**:
  a) **leaderboard_top_n: 500 → 5000** — coleta 10x mais candidatos do
     leaderboard (40k+ rows disponíveis).
  b) **sort_by: "pnl_7d"** — ordena coleta por PnL 7d (atividade recente)
     em vez de PnL all-time. Baleias inativas que dominavam o topo por
     PnL acumulado agora ficam no fim da fila.
- **O que NÃO mudou**: F1-F11, F2b, score, entry_rule, deep_dive_max,
  request_budget, varredura ativa (v5).
- **Resultado esperado**: candidatos aprofundados são majoritariamente
  ativos nesta semana, não baleias inativas. Mais traders copiáveis
  (equity $5k-$500k) entram no funil.

## logic_version: 7 — copiabilidade real (2026-07-04)

- **Autor**: Cursor (construtor), por diretiva humana; spec no UPDATE-0007 do
  Hermes (`docs/CURSOR_UPDATES.md`) — dossiê profundo dos 2 melhores do scan v6.
- **Motivo (justificativa numérica)**: o scan v6 aprovou 7, mas o dossiê do
  Hermes concluiu que NENHUM era copiável. O #1 (score 91.84): BTC LONG 20x,
  $0 de margem disponível, PnL concentrado em não-realizado ($63K), dia de
  −$16K na semana. O #6 (77.91, o melhor): 100% em margem, SOL a 7.5% da
  liquidação, equity $56K → cópia com $1K geraria trades de ~$1.80. O score
  media desempenho histórico; nenhum filtro olhava as posições ABERTAS no
  momento do scan, e o F11 tinha bug (assumia trade = 5% do equity em vez de
  medir os fills — estimava $50 de cópia onde o real era $1.80).
- **O que mudou (antes → depois)**:
  - **F7b (novo)**: alavancagem ATUAL — max da lev das posições abertas
    ≤ 10x (o F7 mede a média histórica; o trader pode estar 20x agora com
    média 13x). Sem posição aberta = sem evidência, passa.
  - **F12 (novo)**: margem disponível ≥ 10% do accountValue
    (`totalMarginUsed` do clearinghouse). Available $0 = qualquer movimento
    contra liquida — os DOIS dossiês tinham $0.
    **Desabilitado pós-validação** (`null` no config): scan `407e8caa996f`
    reprovou 7/150 só no F12 com 0 aprovados no total — gate humano.
  - **F13 (novo)**: distância de liquidação ≥ 15%, medida do **MARK price**
    (`positionValue/|szi|`, fallback entry). Correção embutida: o cálculo
    antigo usava a ENTRADA como referência — posição que já andou muito
    escondia o risco real. A penalidade de score (−10) subiu de <10% para
    <20% (abaixo de 15% o F13 já rejeita; a penalidade cobre a faixa 15–20%).
  - **F15 (novo)**: simulação retroativa de cópia — "copiando este trader
    com $1K nos últimos 30d, qual o PnL líquido de taxa (0.045%) + slippage
    (0.02%) por perna?" Se ≤ 0, rejeita. Nota matemática: o net escala
    linearmente com o capital, então o SINAL independe do valor copiado —
    o capital afeta a executabilidade (F11). Usa só PnL REALIZADO: rejeitar
    lucro 100% não-realizado é intencional (perfil do dossiê #1).
  - **F11 corrigido** (o "F14" do UPDATE-0007): notional mediano REAL dos
    fills × (mirror_capital/equity) ≥ $10. Antes: placeholder `equity × 5%`
    nos dois ramos de um if — bug desde a v2.
  - Novas colunas persistidas (migration 0005): `max_current_leverage`,
    `available_margin_pct`, `sim_net_pnl_usd` — visíveis no dashboard
    (modo expandido) e no rationale/report dos aprovados.
- **O que NÃO mudou**: coleta (v6), entry_rule, pesos do score, F1–F10,
  orçamento. Filtros novos usam dados já buscados — zero requests extras.
- **Resultado esperado**: aprovado = copiável por construção (margem livre,
  lev sã, longe da liquidação, cópia simulada lucrativa e executável).
  Fixture de teste: os perfis dos 2 dossiês são rejeitados (F7b/F12/F13/F11).
- **Validação real (scan `407e8caa996f`, 2026-07-04, budget 1100, 710 req,
  934s)**: **0 aprovados** — conforme o plano, sem auto-afrouxar (F12 a 10%
  é agressivo para o perfil do leaderboard). Funil: 5000 coletados, 150
  aprofundados; novos gargalos v7: F7b 4 · F12 7 · F13 3 · F11 1; legados:
  F1 47 · F5 42 · F6 11 · F2 18 · F2b 7 · F8 7 · F7 3. Checagem dos 2
  wallets do dossiê Hermes (deep dive isolado): `0x1aa5…95cb` reprovaria em
  **F7b** (lev atual 20x; margem 34%, liq 70%, sim +$139); `0x5d8f…7927`
  reprovaria em **F12** (margem 0%) e **F13** (liq 8.2% do mark; lev 10x
  no limite do F7b). Decisão de threshold = gate humano.

## logic_version: 8 — Estágio 4: simulação de cópia como ranking final (2026-07-04)

- **Autor**: Cursor (construtor), por diretiva humana de 2026-07-04.
- **Motivo (diagnóstico primeiro)**: relatório completo em
  `docs/reports/discovery_diagnostico_funil_2026-07-04.md` (entregue ao
  humano ANTES desta mudança). Produção: 277 wallets analisadas, 274
  REJEITADO, só 3 SUGERIDO (2 dos quais legado v1). 79% das mortes são
  mérito (F5 106, F2b 52, F1 39) — o funil não está errado, mas "bom
  trader ≠ boa cópia": nenhum aprovado passou por simulação com latência.
- **O que mudou**:
  - **ESTÁGIO 4 (novo — critério FINAL de ranking)**: para os sobreviventes
    do score, replay dos fills (janela `copy_simulation.window_days: 60`)
    com nosso sizing (ratio $1K/equity), taxas taker + slippage E custo de
    latência (`latency_slippage_pct: 0.03`/perna ≈ deslocamento de preço em
    200ms–2s, aproximado por bps fixos na ausência de tick data). Saídas:
    PnL líquido, **expectância por trade** e **max DD da curva da cópia**.
    - `ranking final = score × fator` onde `fator = 1 + ROI da cópia`,
      clampado em [0.5, 1.2] (config).
    - Net simulado ≤ 0 → **rebaixado a REJEITADO com motivo
      `copy_sim_negativa`** mesmo com score alto (stats:
      `rebaixados_copy_sim`).
    - Relação com o F15 (v7): o F15 continua como hard filter barato SEM
      latência na janela de 30d; o Estágio 4 é o veredito final com
      latência em 60d. Se o F15 for desabilitado (null), o Estágio 4
      segura a linha sozinho.
  - **Fontes adicionais de candidatos (config `sources`, flags OFF por
    default)**: `nansen_leaderboard` (API paga, janela de datas arbitrária,
    exige `NANSEN_API_KEY`) e `apify_hl_scraper` (backup, exige
    `APIFY_TOKEN` + actor). SEM dependência dura: sem flag/chave → lista
    vazia. Terceiros só alimentam ENDEREÇOS; a HL pública continua a fonte
    de verdade de todas as métricas e filtros.
  - Novas colunas persistidas (migration 0006): `sim_expectancy_usd`,
    `sim_max_dd_pct`, `sim_factor`. Migration Supabase é passo MANUAL
    pós-deploy (incidente 1 do UPDATE-0006 do Hermes).
- **O que NÃO mudou**: nenhum threshold de F1–F15 foi alterado (diretiva:
  diagnóstico antes de calibração — recomendações 3/4/5 do relatório
  aguardam decisão humana).
- **Resultado esperado**: o topo do ranking passa a ser "melhor CÓPIA", não
  "melhor trader"; sugestões manuais (Hermes, Copin/HyperX) entram via
  `discovery inspect` e passam pela MESMA simulação.

## logic_version: 9 — copiar a CÓPIA, não o trader (2026-07-04)

- **Autor**: Cursor (construtor), por diretiva humana explícita. Referência
  canônica: `docs/discovery_logic_v9.md` (toda variável documentada; teste
  automatizado trava config sem doc).
- **Motivo (evidência do laboratório)**: laboratório offline com 934 wallets
  reais, 1,78M fills e walk-forward fora da amostra mostrou que a v8 aprova
  0–2 wallets por corte e perde dinheiro (medianas −$118/−$109). O DD do
  trader (F5) tem sinal invertido para lucro da cópia (ρ +0.105), janelas de
  PnL/TWRR/win rate são quase ruído, e a maior variável preditiva é equity do
  trader (ρ −0.227: conta menor copia melhor). A simulação da cópia na janela
  A é o melhor qualificador observável.
- **Correções pré-integração**:
  - Teto de alavancagem da cópia: `max_copy_leverage: 3.0`. Sem ele, o antigo
    top 1 virava uma cópia de $128k de notional sobre $1k (128x) e inflava
    +250% em 5 dias. Agora o fill é capado em $3k e o PnL escala junto.
  - Cobertura mínima: F16 exige 30 dias entre primeiro e último fill. Wallet
    de 5 dias não qualifica.
- **O que mudou**:
  - Novos gates formais sobre a CÓPIA: F16 cobertura, F17 net da cópia > $10,
    F18 metades positivas, F19 DD da cópia ≤ 25%, F20 equity do trader ≤ $150k.
  - Ranking final = `sim_stage4_net_usd` (score vira informativo).
  - Entry rule por janelas de PnL e min score desligados; F5 vira teto de
    sanidade em 80% (não aceita DD 99%+ mesmo se a cópia simular bem).
  - HyperTracker ON como feed de endereços (sem chave = off silencioso); HL
    pública continua fonte da verdade de métricas.
  - Novas colunas persistidas (migration 0007): `coverage_days`,
    `sim_half_old_net`, `sim_half_new_net`.
- **Validação pós-correção**: `v9_final` no laboratório: cortes válidos com
  medianas +$54.54, +$367.66, +$335.59; hit-rate 100%, 100%, 50%; todos batem
  baselines em mediana/soma. Seleção atual: 10 aprovados com cobertura ≥30d,
  metades positivas e cópia capada a 3x. Go controlado: PR draft e recomendação
  de 1–2 semanas em sombra antes de qualquer Gate 2.

## logic_version: 10 — filtros de atividade + win_rate realista + F20 ajustado (2026-07-04)

- **Autor**: Hermes (operador), com autorização humana explícita.
- **Motivo**: dossiê do top 1 do scan v9 revelou que o trader PAROU de operar
  há 7+ dias mas ainda era SUGERIDO. Win rate na tabela era 100% mas a
  realidade é 64% (calculado sobre janela de 60d, não 30d). F20 a $150K
  deixava passar traders grandes demais para copiar com $1K.

### Mudanças:

a) **F1: 21d → 7d** — voltou para 7 dias (v9 tinha afrouxado para 21).
   Trader sem fill em 7 dias = inativo.
b) **F2c (NOVO)**: min_trades_7d: 5 — trader sem 5 trades fechados nos
   últimos 7 dias é rejeitado como inativo. O F2b (30d) continua, mas
   não captura quem parou recentemente.
c) **win_rate_30d (NOVO)**: win rate calculado só sobre closing fills dos
   últimos 30 dias, não 60d. O win_rate original (60d) permanece mas
   win_rate_30d é a métrica correta para exibir/analisar.
d) **F20: $150K → $50K** — análise: com $1K de capital, ratio de cópia
   é 0.02x. Trader de $50K gera notional de ~$15-50 (acima do mínimo $10).
   Trader de $150K gera notional de ~$5-15 (no limite ou abaixo). Sweet
   spot para copiabilidade real é equity ≤ $50K.
e) **n_trades_7d (NOVO)**: calculado no deep_dive, persistido na tabela.
f) **Migration 0008**: ALTER TABLE traders ADD n_trades_7d, win_rate_30d.

### O que NÃO mudou: F2-F20 (exceto F1 e F20), simulação, score, entry_rule.
