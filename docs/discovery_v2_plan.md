# Mini-plano — Discovery v2 (spec v5) · mapeamento endpoint → métrica

> Gate da spec: aguarda aprovação humana antes do código do funil.
> Endpoints validados na documentação oficial em 2026-07-03.

## Endpoints e limitações (ADR ao implementar)

| Endpoint | Payload | Uso | Limitações |
|---|---|---|---|
| Leaderboard `stats-data.hyperliquid.xyz/Mainnet/leaderboard` | GET | Estágio 1: coleta ampla (top 500), janelas day/week/month/allTime com PnL/ROI/volume | Endpoint de stats não documentado (estável desde 2023, mas monitorar); janelas fixas: 7d≈week, 30d≈month; 60d/90d serão derivadas do `portfolio` de cada candidato |
| `info type=userFillsByTime` | `{user, startTime, endTime}` | F1–F3, F6–F9, holding médio, trades/dia, top ativos, expectância, PF realizado | Máx. 2.000 fills/resposta; retenção upstream ~10.000 fills — traders hiperativos têm histórico truncado (registrar no dossiê) |
| `info type=clearinghouseState` | `{user}` | Equity atual, posições abertas (F11), alavancagem (F7), **distância de liquidação** (ajuste −10), PnL não realizado p/ o PF do patch | Snapshot em tempo real (sem histórico); `liquidationPx` pode ser `null` (cross com folga) |
| `info type=portfolio` | `{user}` | Curvas de equity/PnL por janela (day/week/month/allTime): TWRR (F4), max DD 90d (F5), estabilidade semanal (consistência) | Janelas fixas da API; 60d/90d interpolados da série `allTime` quando necessário |
| `info type=userNonFundingLedgerUpdates` | `{user, startTime}` | F10 anti-aporte: depósitos/saques p/ TWRR e p/ separar crescimento por aporte | Paginado por tempo; mesma retenção do histórico |
| `info type=metaAndAssetCtxs` | — | Lista top liquidez (F8) por `dayNtlVlm`; book p/ F11 (dominância) | Snapshot; lista de liquidez cacheada por varredura |

Orçamento de requests por varredura (config): coleta 4 GETs + ~4 requests/candidato aprofundado. Com 500 coletados → ~80 sobrevivem ao corte barato → ~320 requests + cache SQLite por varredura (tabela `discovery_cache`, TTL 20h). Backoff exponencial em 429.

## Funil → fonte de dados

- **Estágio 1**: leaderboard (7d/30d) + `portfolio` (60d/90d) → regra "PnL ≥ 3 de 4 janelas, 30d e 60d obrigatórias". Fonte secundária (fills grandes do feed público) fica atrás de flag `enable_tape_source` (custo alto; default off na 1ª versão — registrado no plano como fase 2.1).
- **Estágio 2 (ordem de custo)**: F1/F2/F3 via `userFillsByTime` → F4 TWRR (portfolio + ledger) → F5 DD (portfolio) → F6 concentração (fills) → F7 alavancagem (clearinghouse + fills) → F8 liquidez (fills × metaAndAssetCtxs) → F9 anti-MM (fills: trades/dia, delta-neutro, PnL/volume) → F10 anti-aporte (ledger) → F11 espelhabilidade (clearinghouse + capital configurado, mínimo US$ 10).
- **Estágio 3 (score 0–100)**: consistência 25% · **PF 20% com o crédito gradativo do patch (já em `metrics.py`, incl. não realizado)** · ROI 30d log 15% · qualidade do DD 15% · copiabilidade 15% · expectância líquida de custo de cópia 10%. Ajustes: +5 (4/4 janelas), −10 (liq < 10%), −5 (top 20 all-time).
- **Coortes**: bidimensional tamanho (Shrimp <250 / Fish <10k / Dolphin <100k / Whale <5M / Leviathan ≥5M) × PnL (Rekt <0 / Flat / Printer >0 consistente) — faixas em `discovery_config.yaml`. Coorte de controle rekt (PnL negativo ≥ 3 janelas) alimenta `cohort_snapshots` (por ativo: viés líquido, alavancagem média, n wallets) e o teste automatizado de separação smart vs. rekt.

## Saídas

- Tabela `traders` (já existe, ADR 0008) ganha colunas: `n_trades_30d`, `avg_holding_hours`, `avg_leverage`, `equity`, `top_assets`, `last_activity`, `windows_positive` (ex. `3/4`), `reject_reason` — migration `0003_discovery_v2`.
- `cohort_snapshots` estendida: `scan_id`, `asset`, `net_bias_pct`, `avg_leverage`, `n_wallets` (migration `0003`).
- `discovery_config.yaml` com thresholds/pesos + `logic_version: 2`; CLI `scan` / `inspect <addr>` / `positioning` / `token <ativo>` / `report --last`.
- Autoridade do Hermes p/ evoluir a lógica (PR + bump + changelog): entra no `strategy.md` do copy trade e no `SKILL.md`.

## Testes (fixtures sintéticas)

TWRR (neutro a aporte), PF com não realizado + crédito gradativo (já verdes), concentração top-3, DD de curva, holding médio, anti-MM (delta-neutro), **separação smart vs. rekt** (score médio da coorte smart ≫ rekt em dataset sintético), armadilhas: sortudo de 1 trade, scalper lucrativo, inflado por depósito.
