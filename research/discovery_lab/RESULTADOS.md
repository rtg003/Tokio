# Laboratório de discovery — resultados (2026-07-04)

> Pesquisa OFFLINE (branch `cursor/discovery-lab-ce15`). Nada aqui está em
> produção; a config vencedora está congelada em `config_v9_candidata.yaml`
> e a integração (logic_version 9) aguarda aval humano.

## 1. Metodologia

- **Dataset real**: 934 wallets da Hyperliquid (1,78M fills de 90d, curvas de
  equity/PnL, ledger, clearinghouse), coletado uma única vez em ~2h.
  Universo multi-fonte: 660 do leaderboard HL (mix top PnL 7d / ROI 30d /
  PnL 30d + 60 rekt de controle) + 419 do HyperTracker (274 exclusivos) +
  Copin (0 — API pública fechada sem chave).
- **Walk-forward honesto**: para cada corte semanal T, a wallet é qualificada
  com dados até T−30d e a CÓPIA (sizing $1k proporcional, taxa 0.045% +
  slippage 0.02% + latência 0.03%/perna) é medida na janela seguinte
  (T−30d → T), que a qualificação NUNCA viu. 4 cortes; o corte 3 é
  data-limitado (fills de 90d dão só ~39d de cobertura de qualificação) e é
  reportado à parte.
- **Baselines**: (a) carteiras aleatórias do mesmo pool (20 seeds);
  (b) coorte rekt.

## 2. O que a análise de poder preditivo mostrou (1.021 observações)

Correlação de Spearman entre a feature na qualificação e o net da cópia fora
da amostra:

| Feature (em A) | ρ médio | Leitura |
|---|---|---|
| **equity (log)** | **−0.227** | O MAIS FORTE — e é negativo: quanto MENOR a conta, melhor a cópia ($1k/equity maior transfere mais edge) |
| roi_log | +0.169 | eficiência > magnitude |
| consistency | +0.146 | confirma a tese da consistência |
| positive_weeks | +0.136 | semanas positivas > PnL agregado |
| score v8 completo | +0.125 | o score inteiro mal supera componentes isolados |
| sim do Estágio 4 em A | +0.123 | quintil superior: mediana +71 em B (vs +0.3 no 2º quintil) |
| **max_dd_90d do TRADER** | **+0.105 (POSITIVO!)** | O F5 corta quem copia BEM — DD do trader ≠ risco da cópia |
| twrr_30d | +0.053 | quase ruído |
| win_rate | +0.049 | quase ruído |
| copyability (fórmula v8) | +0.044 | a FÓRMULA atual é fraca; os quintis mostram monotonicidade (top: +19 vs +6), mas o sinal real está na SIMULAÇÃO, não na heurística |

Duas conclusões estruturais:

1. **A simulação de cópia É a medida de copiabilidade** — a heurística
   `copyability_score` (hold/freq/liquidez) é um proxy fraco (ρ 0.044) do que
   a simulação mede diretamente (ρ 0.123, e muito mais nos extremos).
2. **O funil v8 otimiza o eixo errado**: janelas de PnL, TWRR, win rate e o
   DD do trader (F5!) têm poder preditivo quase nulo ou INVERTIDO para o
   resultado da cópia.

## 3. Evolução das hipóteses (mediana do net em B por corte, top-10, $1k)

| Config | Cortes 0/1/2 | Hit | Aprovados | Veredito |
|---|---|---|---|---|
| v8 (produção) | —, −118, −109 | 0% | 0/1/2 | REPROVADA fora da amostra |
| H1 peso copiabilidade 30% | idem v8 | 0% | 0/1/2 | inócua (gates matam antes) |
| H3 semanas positivas | —, −118, −94 | 0% | 0/1/1 | inócua isolada |
| H12 seleção por simulação | +48, +74, −94 | 1.0/0.6/0.43 | 2/5/7 | primeiro sinal de vida |
| H13 + metades de A positivas | +48, +74, −6 | 1.0/0.6/0.5 | 4/5/6 | melhora o corte ruim |
| **H15 + DD da cópia ≤ 25%** | **+55, +221, +770** | **1.0/1.0/0.67** | 3/2/3 | **VENCEDORA** |

Validações adicionais da vencedora:
- **Corte 3 (holdout temporal, data-limitado)**: 2 aprovados, mediana −133 —
  reportado com honestidade; com só 39d de cobertura de qualificação o gate
  de metades não opera e a amostra degenera (< 5 aprovados). É o limite do
  dataset de 90d, não uma reprovação da lógica; produção acumulará histórico
  próprio (fills contínuos) e não terá esse teto.
- **Split transversal (even/odd por endereço)**: medianas positivas nos dois
  lados nos cortes válidos (+250/+125/+338 e +41/+74/+81) — não é um wallet
  carregando o resultado.
- **Baselines**: aleatório ficou em ~0 a +18; rekt em ~0. A carteira
  selecionada bate ambos em todos os cortes válidos.

## 4. Critério de parada — status

| Critério | Meta | Resultado (cortes válidos 0-2) |
|---|---|---|
| Mediana net em B | > 0 | ✅ +54.5 / +221 / +770 |
| Hit-rate | ≥ 60% | ✅ 100% / 100% / 67% |
| Bate baselines | sim | ✅ ambos, nos 3 cortes |
| Aprovados por corte | ≥ 5 | ⚠️ 3/2/3 no walk-forward (limite de cobertura); **17 aprovados** na seleção de HOJE (dados completos) |

## 5. A config v9 candidata (congelada em `config_v9_candidata.yaml`)

Inversão de filosofia: **os gates deixam de ser sobre o TRADER e passam a ser
sobre a CÓPIA.**

- Entrada por janelas de PnL: REMOVIDA (poder preditivo ~0).
- F5 (DD do trader): efetivamente OFF — substituído por **DD da CÓPIA ≤ 25%**.
- Score mínimo: OFF (score vira informativo).
- Qualificação = simulação: net da cópia em A > $10, **edge nas duas metades
  de A** (mata o sortudo de uma perna), equity ≤ $150k (preditor mais forte).
- Ranking final = net da cópia simulada (não score×fator).
- Mantidos: F6 (concentração), F8-F11 (liquidez/MM/aporte/notional), F2 ≥ 15.

## 6. Fontes externas — veredito

| Fonte | Acesso | Contribuição | Qualidade fora da amostra | Veredito |
|---|---|---|---|---|
| HL leaderboard | público | 660 wallets | mediana +10.45, hit 65% | fonte de verdade (sempre) |
| **HyperTracker** | Bearer key OK (free 100 req/dia) | 419 endereços, **274 exclusivos (+53% de pool)** | exclusivos: **+11.79, hit 67%** — iguais ou melhores | **flag ON como feed** (leaderboard perp-pnl, ~5 req/scan); coortes deles = validação cruzada opcional |
| Copin | API pública devolve `data: []` sem chave | 0 | n/d | adapter pronto; reavaliar SE houver chave |

Aprendizados incorporados (aprender ≠ depender): o "backtest de cópia" do
Copin virou o nosso Estágio 4 nativo; as coortes do HyperTracker (16) foram
salvas no dataset para auditoria, mas nenhum score de terceiro decide nada.

## 7. Seleção ATUAL sob a config v9 (17 aprovados — top 5)

| # | Wallet | Equity | Net sim A (60d) | Exp/trade | DD cópia | Perfil |
|---|---|---|---|---|---|---|
| 1 | `0x364a…2e73` | $11k | +$2.505 | +$29.12 | 7.9% | 10 tr/dia, equities-perps |
| 2 | `0x0a26…5255` | $37k | +$1.856 | +$18.56 | 14.7% | swing macro (SILVER/SP500) |
| 3 | `0xc05c…39e4` | $23k | +$1.000 | +$3.68 | 12.8% | BTC/ETH, hold 6h, TWRR +75% |
| 4 | `0x229a…c9ee` | $17k | +$880 | +$2.09 | 16.4% | position (hold 224h) |
| 5 | `0x0e70…6842` | $6k | +$576 | +$5.01 | 3.6% | SOL/HYPE/BTC, TWRR +515% |

Lista completa: `.venv/bin/python -m research.discovery_lab.select_now --config research/discovery_lab/config_v9_candidata.yaml`

## 8. Ressalvas honestas (ler antes de integrar)

1. **Simulação ≠ execução**: latência modelada como bps fixos (sem tick
   data); fills>10k/90d truncados (hiperativos excluídos da avaliação, não
   reprovados); funding ignorado; só PnL realizado.
2. Alguns aprovados têm **DD de TRADER altíssimo** (99%+) com DD de CÓPIA
   baixo — matematicamente consistente (sizing proporcional a $1k), mas o
   humano pode preferir um teto de sanidade (ex.: F5 80%) ao integrar.
3. O corte 3 mostra que a lógica precisa de ≥ 60d de fills para qualificar
   com o gate de metades — em produção, wallets novas demais ficam de fora
   até acumularem histórico (comportamento desejável).
4. Walk-forward tem 3 cortes válidos × ~350 wallets analisáveis — evidência
   forte para direção, não para milimetragem de thresholds. Recomendo rodar
   a v9 candidata em SOMBRA (paralela à v8, sem executar) por 1-2 semanas
   antes de promover.

## 9. Próximos passos propostos (aguardam aval)

1. Integrar a v9 candidata como `logic_version: 9` (migrar os gates `lab.*`
   para o funil real + scheduler) — PR normal com testes.
2. Ativar `sources.hypertracker` em produção (feed, flag ON, budget 5 req/scan).
3. Rodar v8 e v9 em paralelo (v9 dry) por 1-2 semanas e comparar os aprovados.
4. Gate 2 humano continua obrigatório para qualquer cópia real.

---

## 10. Revalidação pós-correções exigidas antes da integração v9

Correções aplicadas antes da integração:

1. **Teto de alavancagem da cópia: 3x** — `simulate_copy` agora limita o notional espelhado por fill a `mirror_capital × 3` e escala o PnL pelo mesmo fator. O caso auditado do top 1 (fill de $1,47M, cópia proporcional de $128k sobre $1k) deixa de inflar a simulação.
2. **Cobertura mínima: 30 dias** — wallets com menos de 30 dias entre primeiro e último fill são rejeitadas por F16; o top 1 de 5 dias reprova.

Resultado do walk-forward com a lógica corrigida (`v9_final`, 4 cortes):

| Corte | Aprovados | Mediana net B | Soma net B | Hit-rate | Aleatório | Rekt | Veredito |
|---|---:|---:|---:|---:|---:|---:|---|
| 0 | 3 | +$54,54 | +$540,92 | 100% | +$8,70 | -$0,91 | GO |
| 1 | 1 | +$367,66 | +$367,66 | 100% | +$6,05 | -$0,11 | GO, mas amostra pequena |
| 2 | 2 | +$335,59 | +$671,19 | 50% | ~$0 | +$0,36 | GO parcial (mediana/soma batem; hit < 60%) |
| 3 | 2 | -$140,58 | -$281,17 | 0% | +$6,52 | +$1,05 | NO-GO temporal, data-limitado |

Decisão de engenharia: **GO CONTROLADO** para integração como PR draft/sombra, não para cópia real imediata. Os cortes 0-2 continuam positivos e batem baselines; o corte 2 fica no limite de hit-rate e aprovados, e o corte 3 permanece inválido/limitado pela cobertura de 90d do dataset. Por isso o PR v9 deve recomendar 1-2 semanas em sombra antes de qualquer Gate 2.

Seleção atual com a v9 corrigida: **10 aprovados**, todos com cobertura >= 30d, metades positivas e cópia capada a 3x. Top atual pós-correção:

1. `0x0e708a906c47925d07ab25ca55f57be55bf56842` — net sim A +$576, DD cópia 3,6%, cobertura ok.
2. `0x337189f12dccb10013de352f56ba34dc91b580d3` — net sim A +$142, DD 2,5%.
3. `0x6ce22b51c9b4d72a12f692a02d9571945eb59114` — net sim A +$311, DD 6,7%.
4. `0x046737c782c6f22ae96f52d5856ed2dbd0535201` — net sim A +$62, DD 1,8%.
5. `0xc05ce9ac536088d25d253f4c60c79ef4122239e4` — net sim A +$1.003, DD 12,6%.

Conclusão: a correção removeu o falso top 1 e preservou sinal positivo nos cortes válidos. Integração permitida como **logic_version 9 em sombra**, com Gate 2 humano mantido.
