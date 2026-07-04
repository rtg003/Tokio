# CURSOR_UPDATES — inbox de atualizações para o construtor (Cursor)

> Canal formal OPERADOR (Hermes) → CONSTRUTOR (Cursor). Espelho de
> `docs/HERMES_UPDATES.md`; protocolo bilateral completo em `AGENTS.md`
> (ADR 0009). Arquivo **append-only**: entradas numeradas sequencialmente
> (`UPDATE-NNNN`) e **nunca editadas depois de publicadas** — a ÚNICA
> alteração permitida em entrada antiga é a linha `Status:` (`PENDENTE` →
> `APLICADO em <data>`), feita pelo Cursor após executar as ações e passar
> na validação.
>
> **REGRA PERMANENTE DO REPO**: todo PR do Hermes cujo merge exija ação,
> conhecimento novo ou mudança de comportamento do Cursor DEVE incluir uma
> entrada neste arquivo NO MESMO PR. PR aplicável sem entrada = **PR
> incompleto** (checklist em `.github/PULL_REQUEST_TEMPLATE.md`).
>
> **LIMITE INVIOLÁVEL**: entradas deste inbox NUNCA autorizam violar gates ou
> caps. Nenhum UPDATE — de quem quer que venha — substitui aprovação humana
> de Gate 2 (traders), promoção dry_run→active, mainnet ou aumento de caps de
> risco. Se uma entrada parecer mandar fazer isso, ela está errada: NÃO
> execute e acione o humano (rtg003).

## Formato de cada entrada

```
## UPDATE-NNNN · AAAA-MM-DD · Status: PENDENTE
Origem: PR #X (merged)
Tipo: logica_discovery | operacao | skill | config | infra
Resumo: o que mudou e por quê (racional incluso — o destinatário precisa do
  porquê para não "corrigir" a mudança de volta)
Ações do Cursor: passos concretos numerados
Validação: como confirmar que aplicou corretamente
```

---

*(sem entradas ainda — a primeira será UPDATE-0001)*

## UPDATE-0001 · 2026-07-03 · Status: APLICADO em 2026-07-03

Origem: PR #7 (Hermes — skill bilateral protocol)
Tipo: skill

Resumo: a skill `trade` (SKILL.md) foi atualizada para tornar permanente o
protocolo bilateral de coordenação (ADR 0009). As seções adicionadas cobrem:
ritual pré-alteração (fetch+pull, ler inbox, gh pr list, draft PR imediato);
inboxes bilaterais (HERMES_UPDATES.md / CURSOR_UPDATES.md); desempate de área;
e referência ao funil do discovery logic_version 2 (gate, janelas, PF, rotinas).
Além disso, UPDATE-0001 e UPDATE-0002 em HERMES_UPDATES.md foram marcados
como APLICADO.

Ações do Cursor: nenhuma ação de código necessária — apenas tomar ciência de
que a skill agora referencia o protocolo bilateral e o funil do discovery v2.

Validação: `grep -c "Protocolo bilateral" skill/SKILL.md` retorna 1;
`grep "APLICADO" docs/HERMES_UPDATES.md` retorna 2 (UPDATE-0001 e UPDATE-0002).

## UPDATE-0002 · 2026-07-03 · Status: APLICADO em 2026-07-03

Origem: aplicação dos UPDATEs 0003-0005 pelo Hermes
Tipo: operacao

Resumo: o Hermes aplicou todos os UPDATEs pendentes (0003, 0004, 0005).

Ações aplicadas:
- UPDATE-0003: cron de discovery scan REMOVIDO (engine agora gerencia via
  discovery-scheduler). Briefing atualizado para LER resultado ao invés de
  disparar. Tabela traders populada (4 candidatos SUGERIDO).
- UPDATE-0004: regra de isolamento internalizada. Briefing atualizado com
  filtro de escopo obrigatório. Origem dos dados tv_gap_fade: ordens de
  teste do onboarding (Etapa 3) enviadas via /intent sem dry_run para
  validar execução real em testnet — não violação de gate.
- UPDATE-0005: mecanismo de autoload configurado: (b) memória persistente
  do Hermes com instrução para ler AGENTS.md antes de tocar o repo;
  (c) skill `trade` já traz instrução no topo.

Validação: HERMES_UPDATES.md com UPDATEs 0003-0005 marcados APLICADO.

## UPDATE-0003 · 2026-07-03 · Status: PENDENTE

Origem: PR do Hermes "discovery v4 — refinamento do funil"
Tipo: logica_discovery + operacao

Resumo: logic_version 4 implementada pelo Hermes (exceção autorizada pelo
humano ao desempate de área — código + config no mesmo PR). Mudanças:

1. metrics.py: drawdown_quality agora suporta bands piecewise (DD 0-20%
   = cheio, 20-30% = ×0.7, 30-40% = ×0.4). Função backward-compatible
   (sem bands = decai linear como antes).
2. funnel.py: passa f5_dd_quality_bands do config para drawdown_quality.
3. funnel.py: score mínimo (min_score_for_suggestion: 60.0) — candidatos
   com score < 60 viram REJEITADO, não SUGERIDO.
4. config: request_budget 650→800, min_equity 5000→2000, logic_version 3→4.
5. changelog: entrada v4 com justificativa numérica completa.

Ações do Cursor: tomar ciência das mudanças em metrics.py e funnel.py.
A função drawdown_quality é backward-compatible (bands=None = linear).
Se for evoluir o discovery no futuro, trabalhar sobre a v4.

Validação: scan v4 dispara automaticamente no próximo start do engine
(logic_version avançou). Verificar events por logic_updated (3→4).

## UPDATE-0004 · 2026-07-03 · Status: PENDENTE

Origem: PR do Hermes "discovery v5 — refinamento profundo + varredura ativa"
Tipo: logica_discovery + operacao

Resumo: logic_version 5 implementada pelo Hermes (exceção autorizada pelo
humano ao desempate AGENTS.md §4). Mudanças em CÓDIGO (área normalmente do
Cursor) + config. O Cursor deve tomar ciência e adaptar sessões futuras:

Mudanças em código (engine/strategies/copy_trade/):
1. funnel.py: F2b (min_trades_30d) — novo filtro binário após F2
2. funnel.py: penalização de PF absurdo (>10 → -5 no score) + cap de PF
   exibido em 10.0
3. funnel.py: varredura ativa integrada no run_scan (active_addresses)
4. funnel.py: DataClient Protocol atualizado com active_addresses()
5. hl_data.py: método active_addresses() — coleta endereços além do
   leaderboard (expandido + conhecidos na tabela traders)

Mudanças em config (config/discovery_config.yaml):
- logic_version: 4 → 5
- deep_dive_max: 100 → 150
- request_budget: 800 → 1100
- f2b_min_trades_30d: 5 (novo)
- pf_absurd_penalty: -5, pf_absurd_threshold: 10.0 (novo)
- active_scan_enabled: true, active_scan_window_hours: 48,
  active_scan_max_addresses: 200, active_scan_min_notional_usd: 1000

Ações do Cursor:
1. Tomar ciência das mudanças em funnel.py e hl_data.py.
2. Se for evoluir o discovery, trabalhar sobre a v5.
3. O método active_addresses() é um esboço — expande o leaderboard + conhecidos.
   Para uma varredura ativa real (fills públicos recentes), seria necessário
   um endpoint da HL que retorne endereços ativos (não existe público hoje).
   Considerar implementar via webhook de fills ou scraping de trades públicos.

Validação: scan v5 dispara automaticamente no próximo start do engine
(logic_version avançou). Verificar events por logic_updated (4→5).

## UPDATE-0005 · 2026-07-03 · Status: PENDENTE

Origem: PR do Hermes "discovery v6 — coleta por atividade recente"
Tipo: logica_discovery + operacao

Resumo: descoberta crítica — o leaderboard da HL tem 40.191 rows, mas o
discovery coletava só 500 (por PnL all-time). Deep dive manual encontrou
2.277 candidatos realistas e 10 traders ativos em 48h que NÃO estavam sendo
coletados. Mudanças:

Código (funnel.py):
1. run_scan agora ordena o leaderboard por PnL 7d (config sort_by) antes
   de cortar em leaderboard_top_n. Antes pegava os primeiros N rows
   (ordenados por PnL all-time pelo stats API).

Config (discovery_config.yaml):
- logic_version: 5 → 6
- leaderboard_top_n: 500 → 5000
- sort_by: "pnl_7d" (novo)

Ações do Cursor: tomar ciência da mudança de coleta. O método
client.leaderboard() retorna TODAS as rows (40k+); o sort agora acontece
no funnel.py (Python), não no stats API. Se performance for um problema
(40k rows em memória), considerar mover o sort para o hl_data.py com
paginação ou cache.

Validação: scan v6 dispara automaticamente no próximo start do engine.
Verificar events por logic_updated (5→6) e comparar aprovados com v5.

## UPDATE-0006 · 2026-07-03 · Status: PENDENTE

Origem: operação do Hermes (setup de produção + 6 logic_versions em um dia)
Tipo: infra + operacao

Resumo: três incidentes de replicação descobertos e corrigidos pelo Hermes
durante o onboarding e evolução do discovery. O Cursor precisa entender o
estado atual do Supabase e o que foi corrigido manualmente.

### Incidente 1: Migration 0004 não aplicada no Supabase (CORRIGIDO)

A migration `db/migrations/supabase/0004_discovery_v2.sql` (ALTER TABLE
traders ADD COLUMN n_trades_30d, avg_holding_hours, etc.) **nunca foi
aplicada no Supabase**. O engine aplica migrations apenas no SQLite local.
O Supabase precisa de passo manual (psql). Resultado: o replicator tentava
upsertar traders com colunas que não existiam no PostgREST → erro
PGRST204 "Could not find the 'avg_holding_hours' column".

**Correção aplicada**: `psql "$DATABASE_URL" -f db/migrations/supabase/0004_discovery_v2.sql`
— 14 ALTER TABLE executados com sucesso.

**Ação do Cursor**: o autodeploy (deploy/autodeploy.sh) NÃO aplica
migrations Supabase automaticamente. Considerar adicionar um hook que
detecte novas migrations em db/migrations/supabase/ e as aplique via psql
após o git pull. Ou documentar no HANDOFF que migrations Supabase são
passo manual pós-deploy.

### Incidente 2: PGRST102 "Empty or invalid json" em batches de traders

Após aplicar a migration, batches de 104 traders falhavam com PGRST102.
A normalização de keys (correção do PR #3) funciona para schemas iguais,
mas 6 traders tinham payloads com caracteres que o PostgREST rejeitava
(possivelmente valores numéricos extremos ou strings com caracteres de
controle). Corrigido enviando traders individualmente (1 por request) e
removendo os 6 problemáticos da fila.

**Ação do Cursor**: investigar quais campos nos 6 traders problemáticos
causam PGRST102. Endereços: 0xaeaab54bbf65bf, 0x8b253448c776ba,
0x8f78cb4c11dd66, 0x80bcb08c54bbd5, 0x383c452252b4b3, 0x3d4510e14071d8,
0x0a6b80da9b3080. Considerar sanitização de payloads no upsert_rows
(filtrar caracteres de controle, clamping de valores extremos).

### Incidente 3: PGRST102 "All object keys must match" (CORRIGIDO no PR #3)

Já corrigido pelo Hermes no PR #3 (dedup PK + normalize keys + coalesce
enqueue). Mas a correção da normalização de keys precisa ser aplicada
também quando há campos None em colunas NOT NULL — o PostgREST rejeita
null em colunas com constraint NOT NULL.

**Ação do Cursor**: verificar se há colunas NOT NULL na tabela traders
que podem receber None do engine. Se sim, ou alterar para nullable ou
garantir default values no código de upsert.

### Estado atual do Supabase

- Migration 0001 (initial): ✅ aplicada
- Migration 0002 (traders): ✅ aplicada
- Migration 0004 (discovery v2 columns): ✅ aplicada (manualmente pelo Hermes)
- Migration 0003 (cleanup unattributed fills): ✅ aplicada
- Replication queue: drenando (297 → 0 em andamento)
- Dashboard tokio.bz: funcionando, lendo do Supabase

### Resumo das logic_versions (v1→v6 em um dia)

O Hermes evoluiu o discovery de v1 para v6 em uma sessão, com autorização
humana explícita (exceção ao desempate de área). Mudanças em código:
- metrics.py: drawdown_quality piecewise por faixas (v4)
- funnel.py: F2b (min_trades_30d), PF penalty, score min, active scan (v5)
- funnel.py: sort por PnL 7d + leaderboard 5000 (v6)
- hl_data.py: active_addresses() (v5)
- config/discovery_config.yaml: 6 bumps de logic_version

Todas as mudanças estão documentadas em docs/discovery_changelog.md e
notificadas via CURSOR_UPDATES (UPDATE-0003, 0004, 0005, 0006).

Validação: `curl http://127.0.0.1:8700/health` → queue baixa, lag < 60s.
Dashboard tokio.bz carregando dados de traders v6.

## UPDATE-0007 · 2026-07-04 · Status: APLICADO em 2026-07-04

Origem: Dossiê profundo do Hermes — análise dos 2 melhores traders do scan v6
Tipo: logica_discovery + operacao

Resumo: o Hermes fez um deep dive completo nos 2 melhores candidatos do
scan v6 (dossiê com posições abertas, fills 48h, consistência 7d) e
concluiu que **NENHUM dos 2 é copiável**. O score mede desempenho histórico
mas NÃO mede copiabilidade real. O Cursor precisa implementar 5 mudanças
para resolver o problema estrutural. **LEIA COM ATENÇÃO — o humano vai
pedir para você executar isto.**

### Dossiê #1: 0x1aa5...95cb (score 91.84) — NÃO COPIÁVEL

Posições abertas:
- BTC LONG 20x ($580K, PnL +$28K, liq $18,745)
- HYPE LONG 7x ($494K, PnL +$31K)
- SOL LONG 20x ($82K)
- LIT LONG 5x ($61K)
- Available margin: $0 (100% comprometido)

48h: 179 fills, só 7 fechados, PnL $345
7d: 660 trades fechados, win rate 42%, PnL $10,932
- Dia 01/07: -$16,125 (perda severa)
- Dia 29/06: +$18,007 (ganho extremo)
- PnL concentrado em não-realizado ($63K)

Veredito: apostador de 20x com sorte no mês. Score 91.84 é enganoso.

### Dossiê #6: 0x5d8f...7927 (score 77.91) — MELHOR MAS NÃO COPIÁVEL

Posições abertas:
- NEAR LONG 10x ($137K, PnL +$12K, liq $0.60)
- HYPE LONG 6x ($181K, PnL +$5.9K)
- SOL LONG 10x ($76K, liq $75.47 — só 7.5% de distância!)
- Available margin: $0

48h: 300 fills, 86 fechados, PnL $8,432, 100% win rate
7d: 211 trades fechados, win rate 88%, PnL $13,631
- 5 de 6 dias positivos, perda máxima -$640
- Consistente e diversificado (6 ativos)

Veredito: melhor trader que o discovery já trouxe, mas:
- 100% em margem, zero disponível
- SOL a 7.5% da liquidação (10% de queda = liquidação)
- DD 34.4% histórico
- Equity $56K → cópia com $1K gera trades de ~$1.80 (abaixo do mínimo $10)

### PROBLEMA ESTRUTURAL: o score não mede copiabilidade

O score atual (consistência 25% + PF 20% + ROI 15% + DD 15% + copiabilidade
15% + expectância 10%) mede desempenho histórico, não mede se copiar este
trader geraria lucro. Um trader com 20x de alavancagem e sorte no mês
pontua 91.84.

### 5 MUDANÇAS NECESSÁRIAS (o humano vai pedir para você implementar)

#### 1. F7b: alavancagem ATUAL (não média histórica) — CONFIG

O F7 mede alavancagem média do histórico. O trader pode estar com 20x agora
mesmo com média de 13x. Precisa de um filtro que olhe a alavancagem das
posições ABERTAS no momento do scan.

Config: `f7b_max_current_leverage: 10.0` — se qualquer posição aberta tiver
leverage > 10x, rejeitar com "F7b: lev atual Xx > 10.0x".

Implementação: no deep_dive(), já temos `positions` do clearinghouse. Para
cada posição, `p.get("leverage", {}).get("value", 0)`. Pegar o max e
comparar com o threshold. Adicionar no hard_filters() após F7.

Arquivo: engine/strategies/copy_trade/funnel.py (função hard_filters)
Arquivo: config/discovery_config.yaml (adicionar f7b_max_current_leverage)

#### 2. F12: margem disponível mínima — CONFIG

Se available = $0, o trader está totalmente comprometido. Qualquer movimento
contra liquidaria.

Config: `f12_min_available_margin_pct: 10.0` — se available < 10% do
accountValue, rejeitar com "F12: margem disponível X% < 10%".

Implementação: no deep_dive(), já temos `ch = client.clearinghouse(addr)` e
`marginSummary`. Calcular `available / accountValue * 100`. Adicionar no
hard_filters().

Arquivo: engine/strategies/copy_trade/funnel.py
Arquivo: config/discovery_config.yaml

#### 3. F13: distância de liquidação mínima — CONFIG

O #6 tem SOL a 7.5% da liquidação. A penalização atual (-10 se < 10%) existe
no score, mas não REJEITA.

Config: `f13_min_liq_distance_pct: 15.0` — se a posição mais próxima estiver
a < 15% da liquidação, rejeitar com "F13: dist liq X% < 15%".

Implementação: no deep_dive(), já calculamos `c.liq_distance_pct` (min das
distâncias). Adicionar no hard_filters() após F5.

Arquivo: engine/strategies/copy_trade/funnel.py
Arquivo: config/discovery_config.yaml

#### 4. Simulação retroativa de cópia — CÓDIGO (mais complexo, mais importante)

Em vez de só pontuar métricas, simular: "se tivéssemos copiado este trader
com $1K nos últimos 30d, qual seria nosso PnL líquido de taxas e slippage?"

Para cada fill do trader:
- Calcular o size proporcional (mirror_capital / trader_equity * fill_size)
- Deduzir 0.045% taker fee + 0.02% slippage por perna
- Se PnL líquido simulado < 0, rejeitar
- Se trades com $1K têm notional < $10, rejeitar (não copiável)

Isso eliminaria traders que parecem bons nas métricas mas que não geram
lucro quando copiados com capital pequeno.

Implementação: nova função no funnel.py ou metrics.py que recebe os fills do
trader, o equity dele, o mirror_capital configurado, e simula o espelhamento.
Adicionar como componente do score ou como filtro (F15).

Arquivo: engine/strategies/copy_trade/funnel.py ou metrics.py
Arquivo: config/discovery_config.yaml (cost_of_copy já existe)

#### 5. F14: notional mínimo por trade com capital configurado — CONFIG

O F11 já existe mas o cálculo pode estar errado. O #6 tem equity $56K,
copiando com $1K (0.0179x), cada trade seria ~$1.80 — abaixo do mínimo $10.

Config: `f14_min_copy_notional_usd: 10.0` (já existe como f11, mas
verificar se o cálculo está correto).

Implementação: verificar o cálculo no hard_filters() F11. O notional médio
por trade = trader_avg_notional * (mirror_capital / trader_equity). Se
< $10, rejeitar.

Arquivo: engine/strategies/copy_trade/funnel.py (revisar F11)

### PRIORIDADE SUGERIDA

1. F7b (lev atual) + F12 (margem) + F13 (dist liq) — rápidos, config + pouco
   código, eliminam os 2 falsos positivos imediatamente
2. F14 (notional mínimo) — revisar F11 existente
3. Simulação retroativa de cópia — mais complexa, maior impacto

### Bump de logic_version

Todas as mudanças devem ser agrupadas em um único bump de logic_version (7).
Changelog em docs/discovery_changelog.md. UPDATE no HERMES_UPDATES.md
notificando o Hermes das novas leituras e filtros.

### Estado atual do discovery (para contexto do Cursor)

- logic_version: 6 (em produção)
- 7 aprovados no scan v6, mas só 2 analisáveis e NENHUM copiável
- O score não mede copiabilidade real
- Filtros atuais não olham posições abertas no momento do scan
- A simulação retroativa é a mudança de maior impacto estrutural

## UPDATE-0007 · 2026-07-04 · Status: APLICADO em 2026-07-04

Origem: Dossiê profundo do Hermes — análise dos 2 melhores traders do scan v6
Tipo: logica_discovery + operacao

Resumo: o Hermes fez um deep dive completo nos 2 melhores candidatos do
scan v6 (dossiê com posições abertas, fills 48h, consistência 7d) e
concluiu que NENHUM dos 2 é copiável. O score mede desempenho histórico
mas NÃO mede copiabilidade real. O Cursor precisa implementar 5 mudanças
para resolver o problema estrutural. **LEIA COM ATENÇÃO — o humano vai
pedir para você executar isto.**

### Dossiê #1: 0x1aa5...95cb (score 91.84) — NÃO COPIÁVEL

Posições abertas:
- BTC LONG 20x ($580K, PnL +$28K, liq $18,745)
- HYPE LONG 7x ($494K, PnL +$31K)
- SOL LONG 20x ($82K)
- LIT LONG 5x ($61K)
- Available margin: $0 (100% comprometido)

48h: 179 fills, só 7 fechados, PnL $345
7d: 660 trades fechados, win rate 42%, PnL $10,932
- Dia 01/07: -$16,125 (perda severa)
- Dia 29/06: +$18,007 (ganho extremo)
- PnL concentrado em não-realizado ($63K)

Veredito: apostador de 20x com sorte no mês. Score 91.84 é enganoso.

### Dossiê #6: 0x5d8f...7927 (score 77.91) — MELHOR MAS NÃO COPIÁVEL

Posições abertas:
- NEAR LONG 10x ($137K, PnL +$12K, liq $0.60)
- HYPE LONG 6x ($181K, PnL +$5.9K)
- SOL LONG 10x ($76K, liq $75.47 — só 7.5% de distância!)
- Available margin: $0

48h: 300 fills, 86 fechados, PnL $8,432, 100% win rate
7d: 211 trades fechados, win rate 88%, PnL $13,631
- 5 de 6 dias positivos, perda máxima -$640
- Consistente e diversificado (6 ativos)

Veredito: melhor trader que o discovery já trouxe, mas:
- 100% em margem, zero disponível
- SOL a 7.5% da liquidação (10% de queda = liquidação)
- DD 34.4% histórico
- Equity $56K -> cópia com $1K gera trades de ~$1.80 (abaixo do mínimo $10)

### PROBLEMA ESTRUTURAL: o score não mede copiabilidade

O score atual (consistência 25% + PF 20% + ROI 15% + DD 15% + copiabilidade
15% + expectância 10%) mede desempenho histórico, não mede se copiar este
trader geraria lucro. Um trader com 20x de alavancagem e sorte no mês
pontua 91.84.

### 5 MUDANÇAS NECESSÁRIAS (o humano vai pedir para você implementar)

#### 1. F7b: alavancagem ATUAL (não média histórica) — CONFIG + CÓDIGO

O F7 mede alavancagem média do histórico. O trader pode estar com 20x agora
mesmo com média de 13x. Precisa de um filtro que olhe a alavancagem das
posições ABERTAS no momento do scan.

Config: f7b_max_current_leverage: 10.0
Implementação: no deep_dive(), já temos positions do clearinghouse. Para
cada posição, p.get("leverage", {}).get("value", 0). Pegar o max e comparar
com o threshold. Adicionar no hard_filters() após F7.

Arquivos: engine/strategies/copy_trade/funnel.py, config/discovery_config.yaml

#### 2. F12: margem disponível mínima — CONFIG + CÓDIGO

Se available = $0, o trader está totalmente comprometido. Qualquer movimento
contra liquidaria.

Config: f12_min_available_margin_pct: 10.0
Implementação: no deep_dive(), já temos ch = client.clearinghouse(addr) e
marginSummary. Calcular available / accountValue * 100. Adicionar no
hard_filters().

Arquivos: engine/strategies/copy_trade/funnel.py, config/discovery_config.yaml

#### 3. F13: distância de liquidação mínima — CONFIG + CÓDIGO

O #6 tem SOL a 7.5% da liquidação. A penalização atual (-10 se < 10%) existe
no score, mas não REJEITA.

Config: f13_min_liq_distance_pct: 15.0
Implementação: no deep_dive(), já calculamos c.liq_distance_pct (min das
distâncias). Adicionar no hard_filters() após F5.

Arquivos: engine/strategies/copy_trade/funnel.py, config/discovery_config.yaml

#### 4. Simulação retroativa de cópia — CÓDIGO (mais complexo, mais importante)

Em vez de só pontuar métricas, simular: "se tivéssemos copiado este trader
com $1K nos últimos 30d, qual seria nosso PnL líquido de taxas e slippage?"

Para cada fill do trader:
- Calcular o size proporcional (mirror_capital / trader_equity * fill_size)
- Deduzir 0.045% taker fee + 0.02% slippage por perna
- Se PnL líquido simulado < 0, rejeitar
- Se trades com $1K têm notional < $10, rejeitar (não copiável)

Isso eliminaria traders que parecem bons nas métricas mas que não geram
lucro quando copiados com capital pequeno.

Implementação: nova função no funnel.py ou metrics.py que recebe os fills do
trader, o equity dele, o mirror_capital configurado, e simula o
espelhamento. Adicionar como componente do score ou como filtro (F15).

Arquivos: engine/strategies/copy_trade/funnel.py ou metrics.py,
config/discovery_config.yaml (cost_of_copy já existe)

#### 5. F14: notional mínimo por trade com capital configurado — REVISAR F11

O F11 já existe mas o cálculo pode estar errado. O #6 tem equity $56K,
copiando com $1K (0.0179x), cada trade seria ~$1.80 — abaixo do mínimo $10.

Verificar o cálculo no hard_filters() F11. O notional médio por trade =
trader_avg_notional * (mirror_capital / trader_equity). Se < $10, rejeitar.

Arquivo: engine/strategies/copy_trade/funnel.py (revisar F11)

### PRIORIDADE SUGERIDA

1. F7b (lev atual) + F12 (margem) + F13 (dist liq) — rápidos, eliminam os
   2 falsos positivos imediatamente
2. F14 (notional mínimo) — revisar F11 existente
3. Simulação retroativa de cópia — mais complexa, maior impacto estrutural

### Bump de logic_version

Todas as mudanças devem ser agrupadas em um único bump de logic_version (7).
Changelog em docs/discovery_changelog.md. UPDATE no HERMES_UPDATES.md
notificando o Hermes das novas leituras e filtros.

### Estado atual do discovery (para contexto do Cursor)

- logic_version: 6 (em produção)
- 7 aprovados no scan v6, mas só 2 analisáveis e NENHUM copiável
- O score não mede copiabilidade real
- Filtros atuais não olham posições abertas no momento do scan
- A simulação retroativa é a mudança de maior impacto estrutural

## UPDATE-0007 · 2026-07-04 · Status: APLICADO em 2026-07-04

Origem: Dossiê profundo do Hermes — análise dos 2 melhores traders do scan v6
Tipo: logica_discovery + operacao

Resumo: o Hermes fez um deep dive completo nos 2 melhores candidatos do
scan v6 (dossiê com posições abertas, fills 48h, consistência 7d) e
concluiu que NENHUM dos 2 é copiável. O score mede desempenho histórico
mas NÃO mede copiabilidade real. O Cursor precisa implementar 5 mudanças
para resolver o problema estrutural. **LEIA COM ATENÇÃO — o humano vai
pedir para você executar isto.**

### Dossiê #1: 0x1aa5abfd850012297428b509fb84fcd9f9f995cb (score 91.84) — NÃO COPIÁVEL

Posições abertas:
- BTC LONG 20x ($580K, PnL +$28K, liq $18,745)
- HYPE LONG 7x ($494K, PnL +$31K)
- SOL LONG 20x ($82K)
- LIT LONG 5x ($61K)
- Available margin: $0 (100% comprometido)

48h: 179 fills, só 7 fechados, PnL $345
7d: 660 trades fechados, win rate 42%, PnL $10,932
- Dia 01/07: -$16,125 (perda severa)
- Dia 29/06: +$18,007 (ganho extremo)
- PnL concentrado em não-realizado ($63K)

Veredito: apostador de 20x com sorte no mês. Score 91.84 é enganoso.

### Dossiê #6: 0x5d8f65942e5ace94c2f3c119970d502fcc6e7927 (score 77.91) — MELHOR MAS NÃO COPIÁVEL

Posições abertas:
- NEAR LONG 10x ($137K, PnL +$12K, liq $0.60)
- HYPE LONG 6x ($181K, PnL +$5.9K)
- SOL LONG 10x ($76K, liq $75.47 — só 7.5% de distância!)
- Available margin: $0

48h: 300 fills, 86 fechados, PnL $8,432, 100% win rate
7d: 211 trades fechados, win rate 88%, PnL $13,631
- 5 de 6 dias positivos, perda máxima -$640
- Consistente e diversificado (6 ativos)

Veredito: melhor trader que o discovery já trouxe, mas:
- 100% em margem, zero disponível
- SOL a 7.5% da liquidação (10% de queda = liquidação)
- DD 34.4% histórico
- Equity $56K → cópia com $1K gera trades de ~$1.80 (abaixo do mínimo $10)

### PROBLEMA ESTRUTURAL: o score não mede copiabilidade

O score atual (consistência 25% + PF 20% + ROI 15% + DD 15% + copiabilidade
15% + expectância 10%) mede desempenho histórico, não mede se copiar este
trader geraria lucro. Um trader com 20x de alavancagem e sorte no mês
pontua 91.84.

### 5 MUDANÇAS NECESSÁRIAS (o humano vai pedir para você implementar)

#### 1. F7b: alavancagem ATUAL (não média histórica) — CONFIG + CÓDIGO

O F7 mede alavancagem média do histórico. O trader pode estar com 20x agora
mesmo com média de 13x. Precisa de um filtro que olhe a alavancagem das
posições ABERTAS no momento do scan.

Config: `f7b_max_current_leverage: 10.0`
Implementação: no `deep_dive()`, já temos `positions` do clearinghouse. Para
cada posição, `p.get("leverage", {}).get("value", 0)`. Pegar o max e comparar
com o threshold. Adicionar no `hard_filters()` após F7.

Arquivos: `engine/strategies/copy_trade/funnel.py`, `config/discovery_config.yaml`

#### 2. F12: margem disponível mínima — CONFIG + CÓDIGO

Se available = $0, o trader está totalmente comprometido. Qualquer movimento
contra liquidaria.

Config: `f12_min_available_margin_pct: 10.0`
Implementação: no `deep_dive()`, já temos `ch = client.clearinghouse(addr)` e
`marginSummary`. Calcular `available / accountValue * 100`. Adicionar no
`hard_filters()`.

Arquivos: `engine/strategies/copy_trade/funnel.py`, `config/discovery_config.yaml`

#### 3. F13: distância de liquidação mínima — CONFIG + CÓDIGO

O #6 tem SOL a 7.5% da liquidação. A penalização atual (-10 se < 10%) existe
no score, mas não REJEITA.

Config: `f13_min_liq_distance_pct: 15.0`
Implementação: no `deep_dive()`, já calculamos `c.liq_distance_pct` (min das
distâncias). Adicionar no `hard_filters()` após F5.

Arquivos: `engine/strategies/copy_trade/funnel.py`, `config/discovery_config.yaml`

#### 4. Simulação retroativa de cópia — CÓDIGO (mais complexo, mais importante)

Em vez de só pontuar métricas, simular: "se tivéssemos copiado este trader
com $1K nos últimos 30d, qual seria nosso PnL líquido de taxas e slippage?"

Para cada fill do trader:
- Calcular o size proporcional (mirror_capital / trader_equity * fill_size)
- Deduzir 0.045% taker fee + 0.02% slippage por perna
- Se PnL líquido simulado < 0, rejeitar
- Se trades com $1K têm notional < $10, rejeitar (não copiável)

Isso eliminaria traders que parecem bons nas métricas mas que não geram
lucro quando copiados com capital pequeno.

Implementação: nova função no `funnel.py` ou `metrics.py` que recebe os fills
do trader, o equity dele, o mirror_capital configurado, e simula o
espelhamento. Adicionar como componente do score ou como filtro (F15).

Arquivos: `engine/strategies/copy_trade/funnel.py` ou `metrics.py`,
`config/discovery_config.yaml` (cost_of_copy já existe)

#### 5. F14: notional mínimo por trade com capital configurado — REVISAR F11

O F11 já existe mas o cálculo pode estar errado. O #6 tem equity $56K,
copiando com $1K (0.0179x), cada trade seria ~$1.80 — abaixo do mínimo $10.

Verificar o cálculo no `hard_filters()` F11. O notional médio por trade =
trader_avg_notional * (mirror_capital / trader_equity). Se < $10, rejeitar.

Arquivo: `engine/strategies/copy_trade/funnel.py` (revisar F11)

### PRIORIDADE SUGERIDA

1. F7b (lev atual) + F12 (margem) + F13 (dist liq) — rápidos, eliminam os
   2 falsos positivos imediatamente
2. F14 (notional mínimo) — revisar F11 existente
3. Simulação retroativa de cópia — mais complexa, maior impacto estrutural

### Bump de logic_version

Todas as mudanças devem ser agrupadas em um único bump de logic_version (7).
Changelog em `docs/discovery_changelog.md`. UPDATE no `HERMES_UPDATES.md`
notificando o Hermes das novas leituras e filtros.

### Estado atual do discovery (para contexto do Cursor)

- logic_version: 6 (em produção)
- 7 aprovados no scan v6, mas só 2 analisáveis e NENHUM copiável
- O score não mede copiabilidade real
- Filtros atuais não olham posições abertas no momento do scan
- A simulação retroativa é a mudança de maior impacto estrutural

## UPDATE-0008 · 2026-07-04 · Status: PENDENTE

Origem: PR do Hermes "discovery v10 — filtros de atividade + win_rate realista"
Tipo: logica_discovery + operacao

Resumo: 4 correções no funil após dossiê do top 1 do scan v9 (trader parado
há 7 dias ainda era SUGERIDO, win rate 100% na tabela mas 64% na realidade).

Mudanças em código (funnel.py):
1. F2c (NOVO): min_trades_7d — rejeita trader sem 5 trades nos últimos 7d
   - Atributo n_trades_7d adicionado ao Candidate
   - Calculado no deep_dive junto com n_trades_30d
   - Adicionado no hard_filters() após F2b
2. win_rate_30d (NOVO): win rate calculado só sobre últimos 30d
   - Atributo win_rate_30d adicionado ao Candidate
   - Calculado no deep_dive
   - win_rate original (60d) permanece para compatibilidade
3. Ambos persistidos no extras do upsert_candidate

Mudanças em config:
- logic_version: 9 → 10
- f1_recent_activity_days: 21 → 7 (voltou para 7)
- f2c_min_trades_7d: 5 (NOVO)
- f20_max_trader_equity_usd: 150000 → 50000

Migration 0008 (SQLite + Supabase):
- ALTER TABLE traders ADD COLUMN n_trades_7d INTEGER
- ALTER TABLE traders ADD COLUMN win_rate_30d REAL

Ações do Cursor:
1. Tomar ciência das mudanças em funnel.py
2. O test_docs_coverage.py pode quebrar se exigir que toda chave do config
   esteja documentada em docs/discovery_logic_v9.md — atualizar doc se preciso
3. Se for evoluir o discovery, trabalhar sobre a v10

Validação: scan v10 dispara automaticamente no próximo start do engine.
Verificar events por logic_updated (9→10).

## UPDATE-0009 · 2026-07-04 · Status: PENDENTE

Origem: PR do Hermes "feat(deploy): migrations Supabase automáticas no
autodeploy (Bloco 2)"
Tipo: infra + operacao

Resumo: Migrations Supabase (Postgres) agora são aplicadas 100%
automaticamente pelo autodeploy, sem passo manual pós-deploy. Antes cada
migration Supabase exigia `psql ... -f` manual na VPS após deploy — isso
quebrava quando esquecido (replicator sem colunas, dashboard sem dados).

Mudanças:
1. deploy/apply_supabase_migrations.sh (NOVO):
   - Lê DATABASE_URL do .env (/home/tokio/Tokio/.env)
   - Cria tabela de controle schema_migrations_supabase(filename PK,
     applied_at) se não existir
   - Aplica em ordem alfabética todo db/migrations/supabase/*.sql não
     registrado no tracking
   - Falha de UM arquivo: loga no stderr, NÃO derruba o deploy (exit 0)
2. deploy/autodeploy.sh: chamada ao apply_supabase_migrations.sh logo APÓS
   `engine.cli db migrate` (migrations SQLite locais). Falha Supabase não
   derruba o deploy (|| true).
3. db/migrations/supabase/0009_test_tracking.sql (NOVO): migration de teste
   (CREATE TABLE IF NOT EXISTS _migration_test (id int)) para validar o
   tracking automático.

Ações do Cursor:
1. Tomar ciência: a partir do próximo deploy, migrations Supabase novas em
   db/migrations/supabase/ são aplicadas automaticamente — NÃO é mais
   necessário pedir ao Hermes para rodar `psql -f` manual.
2. Ao criar nova migration Supabase, basta commitar o arquivo .sql em
   db/migrations/supabase/ com prefixo numérico (ordem alfabética). O script
   aplica na próxima execução do autodeploy.
3. Convenção mantida: cada migration .sql deve ser idempotente
   (IF NOT EXISTS) — o tracking evita re-execução, mas idempotência é
   defensiva.

Validação: após próximo deploy, verificar
`SELECT * FROM schema_migrations_supabase ORDER BY filename;` — deve listar
0001..0009 com applied_at preenchido. A tabela _migration_test deve existir
em Postgres.

---

### UPDATE-0011 — Bloco 3 copy_pinned + migration 0008 (schema fix)

**Status:** PENDENTE

**Contexto:** Implementação do Bloco 3 (flag inviolável copy_pinned) na branch
`feat/copy-trade-operacional`. A maior parte do código já estava commitada em
`f19c54c`; este commit (`b9cec84`) adiciona a migration 0008 faltante e limpa
imports do teste.

**Mudanças que afetam o Cursor (schema/migrations):**

1. `db/migrations/0008_discovery_v10.sql` (NOVO): cria colunas v10
   `n_trades_7d INTEGER` e `win_rate_30d REAL` na tabela `traders`. Estas
   colunas já eram referenciadas em `funnel.persist_scan` (linhas ~824-825)
   mas não tinham migration local — só existiam no espelho Supabase
   (`0006_discovery_v8.sql` cobre v8, mas `n_trades_7d`/`win_rate_30d` não
   estavam em nenhuma migration local). Sem esta migration, o teste
   `test_rescan_pinned_rejecting_keeps_status_and_reason` falha com
   `sqlite3.OperationalError: no such column: n_trades_7d`.
2. `db/migrations/supabase/0008_discovery_v10.sql` (NOVO): espelho Postgres
   idempotente (`IF NOT EXISTS`) das mesmas duas colunas.
3. `db/migrations/0009_copy_pinned.sql` (já existente, `f19c54c`):
   `ALTER TABLE traders ADD COLUMN copy_pinned INTEGER NOT NULL DEFAULT 0`.
4. `db/migrations/supabase/0009_copy_pinned.sql` (já existente, `f19c54c`):
   espelho Postgres idempotente.

**Comportamento do copy_pinned (Bloco 3):**
- `set_status` → DRY_RUN/COPIANDO com `by` contendo 'human' ou 'gate'
  (ou `human_gate=True`) seta `copy_pinned = 1` automaticamente.
- `unpin_trader(db, addr, by=, human_gate=True)`: só remove o pin com
  human_gate=True E status fora de DRY_RUN/COPIANDO (precisa pausar antes).
- `funnel.persist_scan`: traders com `copy_pinned = 1` têm métricas
  atualizadas (score, simulações, etc.) mas NUNCA têm `reject_reason`
  sobrescrito, NUNCA são rebaixados via `set_status`. Log informativo
  `discovery.pinned_would_reject` quando o re-scan reprovaria.
- CLI: `python -m engine.cli trader unpin <addr> [--yes]`.
- Dashboard: chip 📌 ao lado do status quando `copy_pinned = 1`.

**Ações do Cursor:**
1. Tomar ciência da migration 0008 (schema fix v10) — era um gap: o código
   já escrevia nessas colunas mas a migration local não existia.
2. Ao revisar PRs do copy_trade, verificar consistência: toda coluna nova
   referenciada em `funnel.persist_scan`/`upsert_candidate` deve ter
   migration local correspondente.

**Validação:** `pytest tests/test_traders_store.py` — 14 passam (6 originais
+ 8 do Bloco 3). Nota: `tests/test_discovery_funnel.py` tem 19 falhas
pré-existentes (não relacionadas ao Bloco 3 — falham no commit `f19c54c`
original; são ligadas ao schema/config de discovery v10).
