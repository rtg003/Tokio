# Discovery logic v9 — referência canônica

## Filosofia

A v9 não seleciona o melhor trader; seleciona a melhor CÓPIA. A régua decisiva é o resultado simulado de espelhar a wallet com nosso capital ($1k), nosso teto de alavancagem (3x), nossas taxas, slippage e latência. Score, TWRR, win rate e janelas de PnL continuam visíveis, mas são informativos.

## Pipeline

```mermaid
flowchart LR
    coleta["Coleta HL + HyperTracker"] --> deepDive["Deep dive: fills, portfolio, ledger, clearinghouse"]
    deepDive --> filtros["F1-F20"]
    filtros --> sim["Simulação da cópia com teto 3x"]
    sim --> ranking["Ranking por net simulado"]
    ranking --> gate2["Gate 2 humano"]
```

## Fórmulas principais

- `ratio = mirror_capital / trader_equity`.
- `copy_notional = min(trader_notional * ratio, mirror_capital * max_copy_leverage)`.
- Se o notional for capado, o PnL do fill é escalado pelo mesmo fator do corte.
- Custo por perna = `copy_notional * (taker_fee_pct + slippage_pct + latency_slippage_pct) / 100`.
- Equity da cópia = capital inicial + PnL realizado líquido acumulado; o DD da cópia vem dessa curva.

## Limitações honestas

- Latência é modelada em bps fixos, sem tick data.
- Funding é ignorado.
- Só PnL realizado conta; lucro 100% não-realizado não qualifica.
- Fills truncados pelo limite da API reduzem cobertura e podem reprovar no F16.
- Simulação não substitui Gate 2 humano nem dry-run.

## Tabela de variáveis

| Chave | Significado | Valor | Por quê | Evidência/efeito |
|---|---|---|---|---|
| `logic_version` | versão da lógica de discovery que produziu as métricas | 9 | bump após laboratório offline e correção do modelo de cópia | go/no-go v9: medianas +54/+368/+336 nos 3 cortes válidos |
| `collection.leaderboard_top_n` | parâmetro de coleta/custo do scan | 5000 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.deep_dive_max` | parâmetro de coleta/custo do scan | 150 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.request_budget` | parâmetro de coleta/custo do scan | 1100 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.min_equity_usd` | parâmetro de coleta/custo do scan | 2000 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.sort_by` | parâmetro de coleta/custo do scan | pnl_7d | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.fills_window_days` | parâmetro de coleta/custo do scan | 60 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.fills_max_pages` | parâmetro de coleta/custo do scan | 4 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.cache_ttl_hours` | parâmetro de coleta/custo do scan | 20 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.rekt_sample` | parâmetro de coleta/custo do scan | 20 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.positioning_sample` | parâmetro de coleta/custo do scan | 15 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.active_scan_enabled` | parâmetro de coleta/custo do scan | true | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.active_scan_window_hours` | parâmetro de coleta/custo do scan | 48 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.active_scan_max_addresses` | parâmetro de coleta/custo do scan | 200 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `collection.active_scan_min_notional_usd` | parâmetro de coleta/custo do scan | 1000 | mantido da v6/v5 para controlar universo e rate-limit | sem alteração v9; documentado para cobertura |
| `sources.hypertracker.enabled` | ativar HyperTracker como feed de endereços | true | aumenta pool sem delegar decisão | +274 endereços exclusivos; mediana +$11.79, hit 67% |
| `sources.hypertracker.api_key_env` | nome da variável de ambiente da chave | HYPERTRACKER_API_KEY | segredo fora do repo; sem chave = off silencioso | teste de fonte sem chave |
| `sources.hypertracker.max_addresses` | limite de candidatos do HyperTracker por scan | 300 | ~3-5 requests/scan dentro do free tier | free tier 100 req/dia; uso estimado < 5 |
| `sources.nansen_leaderboard.enabled` | fonte externa opcional de endereços candidatos | false | terceiros só alimentam wallets; métricas são nossas | sem dependência dura; flags off exceto HyperTracker |
| `sources.nansen_leaderboard.api_key_env` | fonte externa opcional de endereços candidatos | NANSEN_API_KEY | terceiros só alimentam wallets; métricas são nossas | sem dependência dura; flags off exceto HyperTracker |
| `sources.nansen_leaderboard.max_addresses` | fonte externa opcional de endereços candidatos | 100 | terceiros só alimentam wallets; métricas são nossas | sem dependência dura; flags off exceto HyperTracker |
| `sources.nansen_leaderboard.window_days` | fonte externa opcional de endereços candidatos | 30 | terceiros só alimentam wallets; métricas são nossas | sem dependência dura; flags off exceto HyperTracker |
| `sources.apify_hl_scraper.enabled` | fonte externa opcional de endereços candidatos | false | terceiros só alimentam wallets; métricas são nossas | sem dependência dura; flags off exceto HyperTracker |
| `sources.apify_hl_scraper.api_key_env` | fonte externa opcional de endereços candidatos | APIFY_TOKEN | terceiros só alimentam wallets; métricas são nossas | sem dependência dura; flags off exceto HyperTracker |
| `sources.apify_hl_scraper.actor` | fonte externa opcional de endereços candidatos | null | terceiros só alimentam wallets; métricas são nossas | sem dependência dura; flags off exceto HyperTracker |
| `sources.apify_hl_scraper.max_addresses` | fonte externa opcional de endereços candidatos | 100 | terceiros só alimentam wallets; métricas são nossas | sem dependência dura; flags off exceto HyperTracker |
| `entry_rule.min_positive_windows` | mínimo de janelas PnL positivas | 0 janelas | desativado: janelas de PnL quase não predizem lucro da cópia | Spearman windows_pos +0.075 |
| `entry_rule.required_windows` | janelas obrigatórias de PnL positivo | nenhuma | desativado; a entrada agora é por simulação da cópia | v9: F16-F19 substituem regra de entrada |
| `hard_filters.f1_recent_activity_days` | atividade recente máxima para não considerar abandonado | 21 dias | atividade é julgada pela simulação; 21d só corta abandono real | h11/h12: afrouxar atividade aumentou pool sem quebrar direção |
| `hard_filters.f2_min_closed_trades` | amostra mínima de trades fechados | 15 trades | amostra suficiente para simulação sem matar demais o pool | lab: F2=30 matava candidatos antes do ranking |
| `hard_filters.f2_min_history_days` | filtro binário do funil | 60 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f2b_min_trades_30d` | atividade mínima nos 30d | 3 trades | mantém sinal recente mínimo | lab: simulação decide a qualidade |
| `hard_filters.f3_min_avg_holding_hours` | filtro binário do funil | null | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f3_max_trades_per_day` | filtro binário do funil | null | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f4_min_twrr_30d_pct` | filtro binário do funil | null | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f5_max_drawdown_90d_pct` | teto de sanidade do DD do trader | 80% | DD do trader não é o risco da cópia; 80% só corta quase-liquidado | Spearman max_dd_90d +0.105 (invertido) — F19 substitui risco |
| `hard_filters.f5_dd_quality_bands` | filtro binário do funil | `[[0, 20, 1.0], [20, 30, 0.7], [30, 40, 0.4]]` | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f6_max_top3_pnl_concentration` | filtro binário do funil | 0.5 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f7_max_avg_leverage` | filtro binário do funil | 15.0 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f7b_max_current_leverage` | filtro binário do funil | 10.0 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f12_min_available_margin_pct` | filtro binário do funil | null | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f13_min_liq_distance_pct` | filtro binário do funil | 15.0 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f8_min_liquid_volume_share` | filtro binário do funil | 0.8 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f8_liquid_assets_top_n` | filtro binário do funil | 25 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f9_mm_max_trades_per_day` | filtro binário do funil | 200.0 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f9_mm_max_pnl_over_volume` | filtro binário do funil | 0.0001 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f10_max_deposit_growth_share` | filtro binário do funil | 0.5 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f11_min_mirror_notional_usd` | filtro binário do funil | 10.0 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f11_mirror_capital_usd` | filtro binário do funil | 1000.0 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f15_sim_window_days` | filtro binário do funil | 30 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f15_min_net_pnl_usd` | filtro binário do funil | 0.0 | regra herdada/versionada; null desativa sem apagar código | evidência histórica no changelog v2-v8; não é foco novo v9 |
| `hard_filters.f16_min_coverage_days` | cobertura mínima entre primeiro e último fill | 30 dias | impede wallet de 5 dias parecer consistente | auditoria top 1: +250% com só 5 dias |
| `hard_filters.f17_min_sim_net_usd` | lucro líquido mínimo da cópia simulada | $10 | a cópia precisa pagar mais que ruído/custos | quintil superior de sim A: mediana +$71 em B |
| `hard_filters.f18_sim_positive_halves` | exigir edge nas metades da janela | true | mata sortudo de uma perna só | corte 2: mediana foi de -$94 para +$770 |
| `hard_filters.f19_max_sim_dd_pct` | drawdown máximo da curva da cópia simulada | 25% | risco que importa é o da cópia | perdedores tinham DD 56–75% já visível em A |
| `hard_filters.f20_max_trader_equity_usd` | teto de equity do trader para copiar com $1k | $150k | conta menor transfere mais edge para $1k | maior preditor: Spearman equity_log -0.227 |
| `copy_simulation.window_days` | janela de replay da cópia | 60 dias | usa histórico cheio de fills; metades de 30d | h13/h15: duas metades foram preditivas |
| `copy_simulation.latency_slippage_pct` | custo estimado de latência por perna | 0.03% | modelo simples para 200ms–2s sem tick data | laboratório desconta esse custo em todo replay |
| `copy_simulation.max_copy_leverage` | teto de alavancagem da nossa cópia | 3x | equilíbrio entre executabilidade e sobrevivência | corrige top 1: fill virava 128x sem teto |
| `copy_simulation.factor_floor` | parâmetro da simulação de cópia | 0.5 | modelo de replay da cópia com nosso sizing/custos | validado no laboratório walk-forward |
| `copy_simulation.factor_cap` | parâmetro da simulação de cópia | 1.2 | modelo de replay da cópia com nosso sizing/custos | validado no laboratório walk-forward |
| `score_weights.consistency` | peso do score informativo | 0.25 | score preservado para análise, não ranking final v9 | score v8 Spearman +0.125 |
| `score_weights.profit_factor` | peso do score informativo | 0.2 | score preservado para análise, não ranking final v9 | score v8 Spearman +0.125 |
| `score_weights.roi_log` | peso do score informativo | 0.15 | score preservado para análise, não ranking final v9 | score v8 Spearman +0.125 |
| `score_weights.drawdown_quality` | peso do score informativo | 0.15 | score preservado para análise, não ranking final v9 | score v8 Spearman +0.125 |
| `score_weights.copyability` | peso do score informativo | 0.15 | score preservado para análise, não ranking final v9 | score v8 Spearman +0.125 |
| `score_weights.net_expectancy` | peso do score informativo | 0.1 | score preservado para análise, não ranking final v9 | score v8 Spearman +0.125 |
| `score_adjustments.full_consistency_bonus` | ajuste pós-score informativo | 5 | mantido para dossiê e auditoria histórica | score não decide aprovação v9 |
| `score_adjustments.liq_distance_penalty` | ajuste pós-score informativo | -10 | mantido para dossiê e auditoria histórica | score não decide aprovação v9 |
| `score_adjustments.liq_distance_threshold_pct` | ajuste pós-score informativo | 20.0 | mantido para dossiê e auditoria histórica | score não decide aprovação v9 |
| `score_adjustments.crowding_penalty` | ajuste pós-score informativo | -5 | mantido para dossiê e auditoria histórica | score não decide aprovação v9 |
| `score_adjustments.crowding_top_n` | ajuste pós-score informativo | 20 | mantido para dossiê e auditoria histórica | score não decide aprovação v9 |
| `score_adjustments.min_score_for_suggestion` | piso mínimo do score para sugerir | 0 | score vira informativo; simulação decide | score completo Spearman +0.125, inferior à simulação nos extremos |
| `score_adjustments.pf_absurd_penalty` | ajuste pós-score informativo | -5 | mantido para dossiê e auditoria histórica | score não decide aprovação v9 |
| `score_adjustments.pf_absurd_threshold` | ajuste pós-score informativo | 10.0 | mantido para dossiê e auditoria histórica | score não decide aprovação v9 |
| `copyability.hold_sweet_spot_hours` | heurística de copiabilidade informativa | `[4, 72]` | proxy mantido, mas simulação é a métrica real | copyability Spearman +0.044 |
| `copyability.freq_sweet_spot_trades_day` | heurística de copiabilidade informativa | `[0.3, 20.0]` | proxy mantido, mas simulação é a métrica real | copyability Spearman +0.044 |
| `cost_of_copy.taker_fee_pct` | taxa taker por perna | 0.045% | custo da HL usado na simulação | descontado em simulate_copy |
| `cost_of_copy.slippage_pct` | slippage base por perna | 0.02% | custo conservador base | descontado em simulate_copy |
| `cohorts.size_bands.Shrimp` | faixa de coorte/rotulagem | 250 | rótulo informativo e comparável ao HyperTracker | não decide aprovação v9 |
| `cohorts.size_bands.Fish` | faixa de coorte/rotulagem | 10000 | rótulo informativo e comparável ao HyperTracker | não decide aprovação v9 |
| `cohorts.size_bands.Dolphin` | faixa de coorte/rotulagem | 100000 | rótulo informativo e comparável ao HyperTracker | não decide aprovação v9 |
| `cohorts.size_bands.Whale` | faixa de coorte/rotulagem | 5000000 | rótulo informativo e comparável ao HyperTracker | não decide aprovação v9 |
| `cohorts.size_bands.Leviathan` | faixa de coorte/rotulagem | inf | rótulo informativo e comparável ao HyperTracker | não decide aprovação v9 |
| `cohorts.pnl_bands.Rekt` | faixa de coorte/rotulagem | 0 | rótulo informativo e comparável ao HyperTracker | não decide aprovação v9 |
| `cohorts.pnl_bands.Flat` | faixa de coorte/rotulagem | 1000 | rótulo informativo e comparável ao HyperTracker | não decide aprovação v9 |
| `cohorts.pnl_bands.Printer` | faixa de coorte/rotulagem | inf | rótulo informativo e comparável ao HyperTracker | não decide aprovação v9 |

## Como reproduzir a validação

```bash
.venv/bin/python -m research.discovery_lab.evaluate --label v9_final --cuts 4 --top-k 10
.venv/bin/python -m research.discovery_lab.analyze
.venv/bin/python -m research.discovery_lab.select_now --config /dev/null
```
