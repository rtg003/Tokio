# Discovery calibration playbook — Hermes

Este playbook traduz pedidos do humano em ajustes do
`config/discovery_config.yaml`. Ele não autoriza violar gates/caps: Gate 2 de
traders, TESTNET/MAINNET, mainnet e caps de risco continuam humanos.

## Fluxo recomendado

1. Rode ou leia o último `discovery scan`.
2. Observe `funnel_stats`, especialmente:
   - `hypertracker_coletados`
   - `hypertracker_aprofundados`
   - `fontes_externas_aprofundados`
   - `fallback_leaderboard_extra`
   - `corte_barato_f20`
   - mortes por filtro (`reprovados_F*`)
3. Leia a seção NEAR-MISS do relatório: ela mostra candidatos reprovados por
   exatamente um filtro e a chave YAML correspondente.
4. Teste hipóteses sem persistir:

```bash
python -m engine.strategies.copy_trade.discovery replay \
  --set hard_filters.f2c_min_trades_7d=5 \
  --set hard_filters.f20_max_trader_equity_usd=150000
```

5. Se a mudança for adotada: editar YAML, bump de `logic_version`, changelog,
   doc canônica e inbox bilateral no mesmo commit.

## Pedidos comuns → chaves

### "Quero mais opções válidas"

- `collection.deep_dive_max` ↑
- `collection.external_dive_quota` ↑
- `collection.request_budget` ↑ junto do deep dive
- `hard_filters.f8_liquid_assets_top_n` ↑
- Banda F20 mais ampla:
  - `hard_filters.f20_min_trader_equity_usd` ↓ ou `null`
  - `hard_filters.f20_max_trader_equity_usd` ↑ ou `null`

Regra prática de budget: `request_budget ≈ deep_dive_max × 7 +
external_dive_quota × 7 + 100`.

### "Quero menos opções, só as melhores"

- `hard_filters.f17_min_sim_net_usd` ↑
- `hard_filters.f19_max_sim_dd_pct` ↓
- `hard_filters.f18_sim_positive_halves: true`
- `hard_filters.f13_min_liq_distance_pct` ↑
- `hard_filters.f7b_max_current_leverage` ↓

### "Quero perfis swing/position"

- `hard_filters.f2c_min_trades_7d` ↓ ou `null`
- Reativar F3 com cuidado:
  - `hard_filters.f3_min_avg_holding_hours` ↑
  - `hard_filters.f3_max_trades_per_day` ↓
- `collection.deep_sort_by: equity_asc` para priorizar contas menores e
  potencialmente mais copiáveis.

### "Quero perfis mais ativos"

- `hard_filters.f2c_min_trades_7d` ↑
- `collection.deep_sort_by: pnl_7d`
- `hard_filters.f1_recent_activity_days` ↓

### "Quero contas menores"

- `hard_filters.f20_max_trader_equity_usd` ↓
- `collection.deep_sort_by: equity_asc`
- Manter `hard_filters.f11_min_mirror_notional_usd` ligado para garantir
  executabilidade real dos fills copiados.

### "Quero contas maiores"

- `hard_filters.f20_max_trader_equity_usd` ↑
- Verificar NEAR-MISS de F11: contas grandes podem falhar por notional copiado
  abaixo de US$10 mesmo com bom histórico.

### "Quero ser mais conservador em risco atual"

- `hard_filters.f7b_max_current_leverage` ↓
- `hard_filters.f13_min_liq_distance_pct` ↑
- Reativar `hard_filters.f12_min_available_margin_pct`

### "Quero ser mais conservador na cópia simulada"

- `hard_filters.f17_min_sim_net_usd` ↑
- `hard_filters.f18_sim_positive_halves: true`
- `hard_filters.f19_max_sim_dd_pct` ↓
- `copy_simulation.latency_slippage_pct` ↑ se quiser modelar execução mais cara

## Chaves especiais

- `null` em qualquer hard filter F1–F20 desliga aquele filtro.
- `collection.min_request_interval_s` controla o throttle HTTP. Reduzir acelera
  o scan, mas aumenta risco de rate limit.
- `collection.external_dive_quota` reserva vagas para HyperTracker/Nansen/Apify.
  Se as fontes vierem vazias, o fallback usa mais linhas do leaderboard.
- `collection.active_scan_enabled` fica `false` por default: a implementação
  atual é stub e não deve ser considerada fonte real de atividade.

## Leitura de sinais

- `hypertracker_aprofundados = 0` e `fallback_leaderboard_extra > 0`: a fonte
  externa não contribuiu; verificar chave/API antes de concluir que não há edge.
- `corte_barato_f20` alto: a banda de equity está moldando fortemente o funil.
- Muitos NEAR-MISS no mesmo filtro: esse filtro é o melhor candidato a replay.
- Aprovados > 15 em um scan: auditar antes de recomendar traders; volume
  anômalo pode indicar threshold amplo demais ou fonte externa enviesada.
