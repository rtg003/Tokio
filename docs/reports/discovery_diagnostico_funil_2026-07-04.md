# Diagnóstico do funil de discovery — "poucos bons traders" (2026-07-04)

> Entregável do item 1 da diretiva humana de 2026-07-04. Dados: tabela
> `traders` de PRODUÇÃO (Supabase, replicada da VPS) + relatórios dos scans
> full-budget de validação (v2 `b684b8bbe5f5`, v3 `4439dbfd5038`,
> v7 `407e8caa996f`). Gerado ANTES de qualquer alteração de threshold.

## Estado da tabela de produção (277 wallets analisadas)

| Status | Qtde |
|---|---|
| REJEITADO | 274 |
| SUGERIDO | **3** |

Por `logic_version`: v7 = 145 · v5 = 130 · v1 = 2 (os 2 SUGERIDOs de coorte
"position" com score 33.8/17.2 são LEGADO v1 — não passaram pelo funil atual
e um deles tem DD 99.3%; candidatos a limpeza).

O único aprovado real da lógica atual: `0x35c5…bd29d` (score 71.2, 3/4,
TWRR 58.2%, DD 23.1%).

## Mortes por filtro (produção, todas as versões)

| Filtro | Mortes | % dos 274 | Leitura |
|---|---|---|---|
| **F5** DD 90d > 40% | **106** | 39% | MAIOR gargalo — e é um filtro de MÉRITO (proteção), não de calibração |
| **F2b** < 5 trades 30d | 52 | 19% | inativos recentes — morte correta |
| **F1** sem trade 7d | 39 | 14% | inativos — morte correta |
| **F8** volume ilíquido > 20% | 26 | 9% | 2º maior gargalo CALIBRÁVEL (ativos fora do top 25) |
| **F2** < 30 trades fechados | 14 | 5% | amostra curta |
| **F6** top-3 > 50% do PnL | 12 | 4% | sorte concentrada |
| F7/F7b (alavancagem) | 9 | 3% | v7 pegando os 20x |
| **F13** liq < 15% | 5 | 2% | v7 funcionando (dossiê #6 morre aqui) |
| F9/F11/entrada/score | 10 | 4% | cauda |

Só no scan v7 (`407e8caa996f`, 150 aprofundados): F5 42 · F1 26+21* · F2 18 ·
F2b 12 · F8 11 · F6 8 · F13 5 · F7b 4 · F11 3 (*47 no relatório do scan,
26 persistidos — parte veio do active_scan e não upsertou).

## Conclusões (sem mexer em threshold ainda)

1. **O funil não está "errado" — o universo é ruim.** 79% das mortes são
   mérito (DD alto, inatividade, amostra curta, sorte concentrada). Afrouxar
   F5 de novo (40% → mais) traria exatamente os perfis que o dossiê do
   Hermes reprovou.
2. **F8 (liquidez) é o único gargalo grande "calibrável"**: 26 mortes por
   operar ativos fora do top 25 por volume. Subir `f8_liquid_assets_top_n`
   de 25 → 40 é a alavanca de maior retorno com menor risco — decisão
   humana, NÃO aplicada neste PR.
3. **O problema real é FUNIL DE ENTRADA, não filtros**: 5000 coletados →
   150 aprofundados (3%) limitados por `request_budget`. Fontes adicionais
   (Nansen/Apify) e mais orçamento aumentam o numerador sem tocar no rigor.
4. **"Bom trader ≠ boa cópia"**: os 3 SUGERIDOs atuais nunca passaram por
   uma simulação de cópia com latência — o Estágio 4 (logic_version 8)
   fecha essa lacuna e vira o critério FINAL de ranking.

## Recomendações entregues ao humano

| # | Ação | Risco | Este PR? |
|---|---|---|---|
| 1 | Estágio 4 — simulação de cópia como ranking final | baixo | **SIM** |
| 2 | Fontes adicionais atrás de flag (Nansen/Apify) | baixo | **SIM** (desligadas) |
| 3 | `f8_liquid_assets_top_n` 25 → 40 | médio | NÃO — aguarda decisão humana |
| 4 | Limpar os 2 SUGERIDOs legado v1 (`trader reject`) | baixo | NÃO — operação (Hermes) |
| 5 | `request_budget` 1100 → 1500 (mais aprofundados/scan) | baixo | NÃO — aguarda decisão humana |
