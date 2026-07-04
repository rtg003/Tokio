# PLANO — Discovery v9: "copiar a CÓPIA", com tudo documentado

Data: 2026-07-04 · Autor: Cursor (construtor) · Status: AGUARDANDO APROVAÇÃO HUMANA
Branch de trabalho: `cursor/discovery-v9-integration-ce15` (a criar, a partir de `main`)
Origem da evidência: laboratório offline `research/discovery_lab/` (branch `cursor/discovery-lab-ce15`)

---

## 0. Contexto — por que a v9 existe

O laboratório walk-forward (934 wallets reais, 1,78M fills, 4 cortes temporais) provou:

- A lógica v8 em produção, medida fora da amostra, aprova 0–2 wallets por corte e
  TODAS perdem dinheiro na janela seguinte (medianas −$118/−$109).
- Os critérios herdados (janelas de PnL positivas, TWRR, win rate, DD do trader) têm
  poder preditivo quase nulo — ou INVERTIDO — sobre o lucro da cópia:

| Feature medida na qualificação | Correlação (Spearman) com lucro da cópia depois | Leitura |
|---|---|---|
| equity do trader (log) | −0.227 | MAIS FORTE do estudo — e negativa: conta menor copia melhor |
| ROI 30d (log) | +0.169 | eficiência importa |
| consistência | +0.146 | confirmada |
| semanas positivas | +0.136 | melhor que PnL agregado |
| score v8 completo | +0.125 | o score inteiro mal supera componentes isolados |
| simulação de cópia na janela A | +0.123 | quintil superior: mediana +$71 fora da amostra |
| DD 90d do TRADER (base do F5) | +0.105 POSITIVA | o F5 corta quem copia BEM |
| TWRR 30d | +0.053 | ruído |
| win rate | +0.049 | ruído |
| copyability (fórmula heurística v8) | +0.044 | proxy fraco — a SIMULAÇÃO é a medida real |

- A config vencedora do lab (gates sobre a CÓPIA, não sobre o trader) entregou, fora
  da amostra: medianas +$54,54 / +$220,95 / +$770,08 por corte, hit-rate 100%/100%/67%,
  batendo baseline aleatório (~0 a +18) e coorte rekt (~0), validada também em split
  transversal even/odd.

- PORÉM a auditoria do "top 1" da seleção atual achou 2 defeitos no modelo de
  simulação que INFLAM resultados individuais (não invalidam a direção, mas precisam
  de correção ANTES da integração):
  1. A simulação espelha qualquer tamanho de fill — um fill de $1,47M numa conta de
     $11k viraria "cópia" de $128k de notional sobre capital de $1k (128x de
     alavancagem, inexecutável).
  2. Wallet com só 5 dias de histórico qualificou (o gate de "duas metades" tratou a
     metade antiga vazia como "sem evidência" e deixou passar).

---

## 1. REGRA CENTRAL DA ENTREGA (diretiva humana): TUDO DOCUMENTADO

Nenhuma variável da lógica v9 entra em produção sem registro de:

- (a) o que significa, em linguagem simples;
- (b) valor e unidade;
- (c) por que ESSE valor e não outro;
- (d) qual evidência do laboratório o sustenta (nº de Spearman, tabela de quintis ou
  resultado de walk-forward — com link para o run);
- (e) o efeito esperado de subir/descer o valor.

Critério de aceite MECÂNICO: `tests/test_docs_coverage.py` faz o parse de
`config/discovery_config.yaml` e de `docs/discovery_logic_v9.md` e FALHA se existir
chave do YAML sem linha correspondente na tabela de referência do doc. Isso vale para
sempre: quem criar variável nova sem documentar quebra a suíte.

---

## 2. FASE 1 — Correções no laboratório e revalidação (GO/NO-GO)

### 2.1 Teto de alavancagem da cópia — `engine/strategies/copy_trade/metrics.py`

`simulate_copy()` ganha o parâmetro `max_copy_leverage: float = 3.0`:

- Por fill: `copy_notional = min(notional_trader × ratio, mirror_capital × max_copy_leverage)`.
- O PnL espelhado do fill é escalado pelo MESMO fator de corte (se copiamos só 40% do
  tamanho proporcional, recebemos só 40% do closedPnl proporcional).
- Racional do 3.0 (documentado no doc canônico):
  - risco: DD da cópia escala ~linear com a alavancagem — o critério de aceite (DD ≤ 25%)
    viraria ~75% em 10x; em 3x, uma queda de mercado de 10% custa 30% da conta (dói,
    recupera; em 10x liquidaria);
  - custo: 0,13% ida-e-volta sobre notional 10x = 1,3% do capital POR trade; em 3x, 0,39%;
  - piso: com $1k e teto 1x, muitos fills espelhados cairiam abaixo do mínimo de $10/ordem
    da Hyperliquid — 3x mantém a maioria executável;
  - coerência: exigimos lev atual ≤ 10x dos traders (F7b); nossa cópia ser mais
    conservadora que o copiado é intencional.

### 2.2 Cobertura mínima de histórico — `research/discovery_lab/qualify.py`

Gate `min_coverage_days: 30`: dias entre o PRIMEIRO e o ÚLTIMO fill dentro da janela de
qualificação. Menos que isso = "sem histórico para julgar" (wallet nova fica de fora até
acumular). Mata o caso real do top 1 (5 dias, 86 trades, +250% irreproduzível).

### 2.3 Revalidação (o go/no-go)

- Re-rodar `research.discovery_lab.evaluate` com a config candidata corrigida:
  4 cortes semanais + split transversal even/odd (6 execuções).
- Re-rodar `research.discovery_lab.analyze` (poder preditivo com o teto aplicado).
- Re-rodar `select_now` (a lista de aprovados de HOJE muda — o top 1 atual deve REPROVAR).
- Critério de GO (nos 3 cortes válidos — o corte 3 é data-limitado e reportado à parte):
  - mediana do net da cópia fora da amostra > 0 em TODOS;
  - hit-rate ≥ 60% em todos;
  - carteira bate os DOIS baselines (aleatório de mesmo tamanho, 20 seeds; coorte rekt);
  - ≥ 3 aprovados por corte no walk-forward e ≥ 8 na seleção atual.
- NO-GO: paro, reporto os números e AGUARDO decisão humana (sem auto-recalibrar).
- Atualizar `research/discovery_lab/RESULTADOS.md` (nova seção "pós-correções") e
  congelar `config_v9_candidata.yaml` revisada.

## 3. FASE 2 — Integração no engine (`logic_version: 9`)

### 3.1 Novos filtros formais no funil — `engine/strategies/copy_trade/funnel.py`

Os gates `lab.*` viram filtros F16–F20, com `reject_reason` padronizado e guarda
`null = desabilitado` (padrão v3):

| Filtro | O que faz | Chave no config | Valor | Evidência |
|---|---|---|---|---|
| F16 | cobertura mínima de fills | `f16_min_coverage_days` | 30 dias | auditoria top 1 (5 dias) |
| F17 | net da cópia simulada na janela A, com teto de alavancagem | `f17_min_sim_net_usd` | > $10 | quintis: top +$71 vs +$0,3 |
| F18 | edge nas DUAS metades de A (recente obrigatória; antiga quando há cobertura) | `f18_sim_positive_halves` | true | corte 2 foi de −$94 para +$770 no lab |
| F19 | max DD da curva da CÓPIA | `f19_max_sim_dd_pct` | ≤ 25% | perdedores de B tinham DD de cópia 56–75% visível em A |
| F20 | teto de equity do trader | `f20_max_trader_equity_usd` | ≤ $150k | preditor nº 1 (ρ −0.227) |

### 3.2 Critérios herdados — desativação documentada

- `entry_rule` (janelas de PnL): `min_positive_windows: 0`, `required_windows: []`
  (poder preditivo ~0; código preservado para reativação).
- `min_score_for_suggestion: 0` — o score continua CALCULADO e exibido, mas informativo.
- F5 (DD do trader): NÃO desliga — vira **teto de sanidade em 80%** (decisão humana:
  trader com DD 99% é bomba-relógio, mesmo que a cópia simule bem).
- Ranking final = `sim_stage4_net_usd` (desc). `sim_factor` deixa de ordenar.

### 3.3 Uma implementação só

O cálculo de metades + cobertura entra no `deep_dive()`; o `qualify.py` do laboratório
passa a IMPORTAR essas funções do funil (o lab vira consumidor da lógica de produção —
zero divergência entre pesquisa e produção daqui pra frente).

### 3.4 Fonte HyperTracker ON — `engine/strategies/copy_trade/hl_data.py`

- Novo adapter em `external_candidates`: `GET /api/external/leaderboards/perp-pnl`
  (rankBy pnlMonth + pnlWeek, ~5 requests/scan, dentro do budget existente).
- Config: `sources.hypertracker: {enabled: true, api_key_env: HYPERTRACKER_API_KEY, max_addresses: 300}`.
- Sem chave no ambiente → fonte silenciosamente OFF (zero erro, padrão v8).
- Evidência: +274 endereços exclusivos (+53% de pool), qualidade fora da amostra igual
  ou melhor (mediana +$11,79 vs +$10,45 do leaderboard HL).
- PASSO MANUAL SEU: adicionar `HYPERTRACKER_API_KEY` ao `.env` da VPS.

### 3.5 Persistência e dashboard

- Migration `db/migrations/0007_discovery_v9.sql` + espelho
  `db/migrations/supabase/0007_discovery_v9.sql` (aplicação MANUAL no Supabase,
  documentada no UPDATE): colunas `sim_half_old_net`, `sim_half_new_net`,
  `coverage_days` em `traders`.
- `persist_scan()` grava os extras novos; `render_report()`/rationale citam cobertura
  e metades.
- Dashboard `web/app/(app)/page.tsx`: modo expandido ganha "Cobertura" e "Metades A";
  ordenação segue o ranking novo (net simulado).

## 4. FASE 3 — Documentação (entregável de primeira classe)

### 4.1 `docs/discovery_logic_v9.md` — A REFERÊNCIA CANÔNICA (novo)

Estrutura obrigatória:

1. Filosofia (1 parágrafo): não selecionamos o melhor TRADER; selecionamos a melhor
   CÓPIA — todo gate decisivo mede o resultado simulado de espelhar, não a glória do
   histórico alheio.
2. Diagrama do pipeline (mermaid): coleta multi-fonte → pré-corte → F1–F20 →
   simulação (teto 3x) → ranking por net → SUGERIDO/REJEITADO → Gate 2 humano.
3. TABELA DE TODAS AS VARIÁVEIS do `discovery_config.yaml` — uma linha por chave:
   nome · seção · significado em linguagem simples · valor · unidade · por quê ·
   evidência (com número: ρ, quintil ou corte do walk-forward) · efeito de
   subir/descer · versão que introduziu.
4. Modelo da simulação (fórmulas): ratio = capital/equity_trader; custo por perna =
   notional × (taker 0,045% + slippage 0,02% + latência 0,03%); teto por fill =
   capital × 3; metades; DD da curva da cópia; expectância.
5. LIMITAÇÕES HONESTAS: latência modelada como bps fixos (sem tick data); funding
   ignorado; só PnL realizado (intencional); fills > 10k/90d truncados; simulação ≠
   execução.
6. Como reproduzir: comandos do laboratório (harvest/evaluate/analyze/select_now) e
   onde estão os runs salvos.

### 4.2 Governança e changelog

- `docs/discovery_changelog.md`: entrada `logic_version: 9` (antes→depois, números do
  go/no-go, link para o doc canônico).
- `tests/test_docs_coverage.py`: o teste-trava do "tudo documentado" (seção 1).

### 4.3 HERMES — como o operador ENTENDE e passa a operar a v9

O Hermes não executa o scan (o scheduler do engine roda sozinho às 05:00 SP e
re-escaneia no deploy ao detectar o bump 8→9). O papel dele é LER, INTERPRETAR e
SUGERIR — e a v9 muda a linguagem que ele lê. O plano de entendimento tem 4 camadas:

**a) UPDATE-0010 em `docs/HERMES_UPDATES.md`** (o canal formal — status PENDENTE até
ele aplicar). Conteúdo obrigatório:

1. A MUDANÇA DE FILOSOFIA em linguagem de operador: "o ranking não é mais 'melhor
   trader', é 'melhor cópia'. Score alto com net simulado baixo = NÃO sugerir."
2. Dicionário dos motivos novos de rejeição que vão aparecer em `reject_reason`:
   F16 (histórico curto), F17 (cópia não rende), F18 (edge só numa metade — sortudo),
   F19 (cópia com drawdown alto), F20 (conta grande demais para espelhar com $1k).
3. Dicionário das colunas novas da tabela `traders`: `coverage_days`,
   `sim_half_old_net`, `sim_half_new_net` (+ as v8: `sim_net_pnl_usd`,
   `sim_expectancy_usd`, `sim_max_dd_pct`) — com o exemplo de leitura de um dossiê
   real aprovado e um rejeitado.
4. O que citar ao sugerir wallet para Gate 2 (ordem obrigatória): net simulado,
   expectância/trade, DD da cópia, cobertura, metades — SÓ DEPOIS score e métricas
   de trader.
5. Passos manuais do deploy que são dele: aplicar a migration Supabase 0007 via
   psql (comando pronto no UPDATE) e validar que o replicator não acusa PGRST204;
   confirmar que `HYPERTRACKER_API_KEY` está no `.env` (a chave em si é passo do
   humano).
6. Regra reforçada: sugestões manuais dele (ou do humano via Copin/HyperX) entram
   por `discovery inspect <address>` e passam pela MESMA régua F1–F20 + simulação —
   nenhuma via lateral de aprovação.

**b) Skill dele (`skill/SKILL.md` — área do Hermes, atualizada por PR DELE)**: o
UPDATE-0010 instrui a atualização com: o funil F1–F20 resumido, o novo formato do
briefing matinal (ranking por net simulado + estatísticas do funil + positioning) e
o link para `docs/discovery_logic_v9.md` como fonte canônica quando ele tiver dúvida
sobre qualquer variável.

**c) Autoload já existente**: o `AGENTS.md`/`CLAUDE.md` (UPDATE-0005, aplicado)
garante que toda sessão do Hermes carrega o contrato central — o doc canônico da v9
entra como referência citada ali, então ele "nasce" com o contexto em toda sessão.

**d) Validação de que ele ENTENDEU (critérios do UPDATE-0010)**:

- UPDATE-0010 marcado `APLICADO` por ele em `docs/HERMES_UPDATES.md`;
- skill atualizada via PR dele (com entrada espelho em `docs/CURSOR_UPDATES.md` se
  exigir ação nossa);
- primeiro briefing pós-deploy citando net simulado/cobertura/metades dos candidatos
  (e NÃO ordenando por score);
- migration 0007 aplicada no Supabase sem PGRST204 no replicator.

Só depois desses 4 checks o ciclo de entendimento é considerado fechado.

## 5. FASE 4 — Testes e validação real

- Testes novos: F16–F20 unitários e no funil sintético (incluindo o caso real do
  top 1 — 5 dias/128x TEM que reprovar); teto de alavancagem no `simulate_copy`
  (fill gigante → cópia capada + PnL escalado); HyperTracker on/off; docs coverage.
  Suíte completa verde.
- Scan real full-budget no workspace (tmux, ~20 min): aprovados devem ter cobertura
  ≥ 30d, cópia executável (≤ 3x) e metades positivas; estatísticas do funil e motivos
  registrados no PR.
- Commits lógicos separados (lab-fix / funil / fontes+migration / docs / validação),
  push, PR DRAFT para `main` com a evidência completa.
- MERGE E DEPLOY SÓ COM SEU AVAL. Pós-merge, o scheduler re-escaneia sozinho
  (logic_version bump). Recomendação registrada no PR: 1–2 semanas observando os
  SUGERIDOs da v9 antes de qualquer Gate 2.

## 6. O que NÃO muda (invariantes)

- Gate 2 humano obrigatório para qualquer cópia real (nenhum UPDATE/config substitui).
- Caps de risco do executor e kill switch.
- Isolamento de observabilidade (ADR 0010).
- Protocolo bilateral Cursor/Hermes (ADR 0009) — inbox, ritual, áreas.

## 7. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Walk-forward reprova após o teto 3x (os retornos eram alavancagem disfarçada) | é exatamente o que o go/no-go detecta; NO-GO = paro e reporto |
| Menos aprovados que o esperado em produção | cobertura de 30d exclui wallets novas por desenho; o pool cresce com HyperTracker e com o tempo |
| Chave HyperTracker ausente/expirada na VPS | fonte degrada para OFF silencioso; scan segue só com HL |
| Migration Supabase esquecida no deploy | UPDATE-0010 lista o comando psql; replicator acusa PGRST204 se faltar (sintoma documentado) |
| Overfit dos thresholds do lab | valores redondos, validação em split transversal, doc registra a evidência de cada um; sombra de 1–2 semanas antes do Gate 2 |

## To-dos

- [ ] v9-lab-fix — Fase 1: teto 3x no simulate_copy + cobertura 30d no lab; re-rodar
      evaluate/analyze/select_now; GO/NO-GO; atualizar RESULTADOS.md e config candidata
- [ ] v9-funnel — Fase 2: F16–F20 no funil, herdados desativados via config (F5 teto
      80%), ranking por net simulado, metades/cobertura no deep_dive, lab importa do funil
- [ ] v9-sources — Fase 2: HyperTracker ON no hl_data + migration 0007 (local+Supabase)
      + persist/report/dashboard
- [ ] v9-docs — Fase 3: docs/discovery_logic_v9.md (tabela completa), changelog v9,
      tests/test_docs_coverage.py
- [ ] v9-hermes — Fase 3: UPDATE-0010 completo (dicionário de motivos/colunas, ordem
      de citação no Gate 2, passos manuais, regra do inspect) + instrução de skill +
      critérios de validação do entendimento
- [ ] v9-validate — Fase 4: suíte verde + scan real + commits/push + PR draft com evidência
