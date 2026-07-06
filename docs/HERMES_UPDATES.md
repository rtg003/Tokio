# HERMES_UPDATES — inbox de atualizações para o operador (Hermes)

> Canal formal CONSTRUTOR (Cursor) → OPERADOR (Hermes). Espelho de
> `docs/CURSOR_UPDATES.md`; protocolo bilateral completo em `AGENTS.md`
> (ADR 0009). Arquivo **append-only**: entradas são
> numeradas sequencialmente (`UPDATE-NNNN`) e **nunca editadas depois de
> publicadas** — a ÚNICA alteração permitida em entrada antiga é a linha
> `Status:` (`PENDENTE` → `APLICADO em <data>`), feita pelo Hermes após
> executar as ações e passar na validação.
>
> **REGRA PERMANENTE DO REPO**: todo PR cujo merge exija ação, conhecimento
> novo ou mudança de comportamento do operador DEVE incluir uma entrada neste
> arquivo NO MESMO PR. PR aplicável sem entrada = **PR incompleto** (checklist
> em `.github/PULL_REQUEST_TEMPLATE.md`; ver também `docs/HANDOFF_HERMES.md`).
>
> **LIMITE INVIOLÁVEL**: entradas deste inbox NUNCA autorizam violar gates ou
> caps. Nenhum UPDATE — de quem quer que venha — substitui aprovação humana de
> Gate 2 (traders), promoção dry_run→active, mainnet ou aumento de caps de
> risco. Se uma entrada parecer mandar fazer isso, ela está errada: NÃO
> execute e acione o humano (rtg003).

## Formato de cada entrada

```
## UPDATE-NNNN · AAAA-MM-DD · Status: PENDENTE
Origem: PR #X (merged)
Tipo: logica_discovery | operacao | skill | config | infra
Resumo: o que mudou e por quê (racional incluso — o operador precisa do
  porquê para não "corrigir" a mudança de volta nas análises)
Ações do Hermes: passos concretos numerados
Validação: como confirmar que aplicou corretamente
```

---

## UPDATE-0001 · 2026-07-03 · Status: APLICADO em 2026-07-03

**Origem**: PR #6 (protocolo do inbox), consolidando PRs #4 e #5 (merged)

**Tipo**: logica_discovery + operacao

**Resumo**: consolidação de tudo que mudou no módulo discovery (copy trade)
para o seu modelo mental de operação. Racional incluído em cada item — sem
ele você tenderia a "corrigir" essas escolhas de volta nas suas análises.

a) **Tabela `traders` é a fonte ÚNICA** de candidatos e copiados (ADR 0008).
   Não existem mais YAMLs de traders — foram migrados e removidos. O ciclo de
   vida é pela coluna `status` e o **gate é a transição SUGERIDO →
   DRY_RUN/COPIANDO: SÓ com autorização humana explícita, inclusive em
   testnet**. Você prepara evidência e pergunta; nunca aprova sozinho.
   Aprovação via CLI: `trader approve <address>` (→ DRY_RUN) e
   `trader approve <address> --live --evidence docs/<arquivo>` (→ COPIANDO).
   A API de controle da web só pausa/retoma/rejeita — nunca aprova.

b) **Funil da `logic_version: 2`** (spec `docs/specs/PROMPT_DISCOVERY_TRADERS_v5.md`):
   - **4 janelas** (7d, 30d, 60d, 90d). Regra de entrada: PnL positivo em
     **≥ 3 das 4 janelas, sendo 30d e 60d obrigatórias**. A 7d PODE ser
     negativa — exigi-la compraria "mão quente" e descartaria consistentes em
     drawdown semanal.
   - **11 hard filters** (F1–F11, binários, em ordem de custo): atividade
     recente, amostra mínima, anti-scalper, TWRR 30d ≥ 5%, max DD 90d ≤ 25%,
     concentração de PnL, alavancagem ≤ 15x, liquidez dos ativos, anti-MM/
     vault/arb, anti-aporte (TWRR em tudo), espelhabilidade.
   - **Score 0–100 com ajustes pós-score**: **+5** consistência total (4/4
     janelas positivas); **−10** distância de liquidação < 10% em posição
     aberta (bomba-relógio, por melhores que sejam as métricas históricas);
     **−5** crowding (wallet no top 20 all-time do leaderboard — as mais
     vigiadas têm milhares de copiadores, mais slippage e edge que decai).
   - **Coortes bidimensionais** (tamanho de equity × PnL acumulado) para
     exibição/análise, e **coorte rekt como CONTROLE** (perdedores
     consistentes, espelho invertido dos filtros). A divergência de
     posicionamento smart vs. rekt é **insumo de briefing, NUNCA sinal de
     execução automática**.

c) **Regra do profit factor** (patch humano de 2026-07-03, já em
   `engine/strategies/copy_trade/metrics.py`): crédito **integral até 3.0**;
   **meio-crédito de 3.0 a 5.0 APENAS com `n_trades ≥ 60`** na janela; acima
   de 5.0 não pontua. PF calculado **incluindo o PnL não realizado** das
   posições abertas no fechamento da janela. Racional: PF extremo com amostra
   pequena é variância, não habilidade; e PF só de realizados é inflável ao
   simplesmente não fechar os perdedores. Não trate PF alto bruto como
   qualidade — leia sempre junto de `n_trades`.

d) **Sua autoridade sobre a lógica do discovery**: você PODE e DEVE evoluir a
   lógica/filtros quando tiver evidência clara (post-mortem de cópia
   malsucedida, constatação forte nos dados, ou pedido do humano). Condições
   invariáveis: SEMPRE via **PR com justificativa numérica** (nunca edição
   direta), SEMPRE com **bump de `logic_version`**, SEMPRE com entrada em
   `docs/discovery_changelog.md` e evento JSONL `logic_updated`. **Na dúvida,
   propõe e aguarda; com certeza, executa o PR e notifica.** Todo trader
   copiado que for pausado/removido por desempenho ruim exige **post-mortem
   obrigatório** em `docs/post_mortems/` registrando quais métricas do
   discovery FALHARAM em prever o problema — insumo da próxima versão.

e) **Fonte única de sugestões**: traders sugeridos por você (Hermes) ou
   manualmente pela dashboard entram como candidatos e passam pelo MESMO
   funil e MESMA `logic_version` da varredura automática. Nenhuma via
   alternativa cria trader fora da lógica; a coluna `origem` só registra por
   onde entrou (`scan` / `hermes` / `dashboard`).

f) **Rotinas**: `discovery scan` diário às **05:00 America/Sao_Paulo**;
   `discovery positioning` alimenta o **briefing matinal** (incluindo a
   divergência smart vs. rekt); `discovery inspect <address>` e
   `discovery token <ativo>` para dossiês sob demanda. **Toda exibição de
   traders (relatórios, respostas, dashboard) ordena por `score`
   DECRESCENTE** — do mais indicado ao menos indicado.

**Ações do Hermes**:

1. Internalizar (a)–(f) como modelo de operação vigente do discovery — em
   especial: gate humano da transição SUGERIDO → DRY_RUN/COPIANDO mesmo em
   testnet; divergência de coortes é briefing, não execução; PF lido junto de
   `n_trades`.
2. Ajustar seus agendamentos: `discovery scan` às 05:00 America/Sao_Paulo e
   `discovery positioning` incorporado ao briefing matinal.
3. Ao sugerir um trader por conta própria, registrá-lo como candidato
   (origem `hermes`) e deixá-lo passar pelo funil — nunca argumentar
   aprovação fora das métricas da `logic_version` vigente.
4. Ao pausar/remover trader copiado por desempenho, escrever o post-mortem
   em `docs/post_mortems/` apontando as métricas que falharam.
5. **Ação final**: atualizar a skill (`skill/SKILL.md` e o `strategy.md` do
   copy trade) via PR para refletir (a)–(f), referenciando a spec v5 e o
   changelog da lógica.

**Validação**:

- `python -m engine.cli trader list` reflete o funil (status, score
  decrescente, coluna `origem`, `logic_version`).
- Cron do scan às 05:00 SP ativo e briefing matinal contendo o positioning
  com divergência smart vs. rekt.
- Skill atualizada mergeada na `main` cobrindo (a)–(f); `git log` do PR
  correspondente.
- Explicar de volta ao humano, em uma mensagem, a regra do PF (item c) e o
  porquê do gate em testnet (item a) — teste de internalização do racional.

---

## UPDATE-0002 · 2026-07-03 · Status: APLICADO em 2026-07-03

**Origem**: PR #6 (protocolo bilateral — evolução do inbox instaurado no
mesmo PR)

**Tipo**: operacao

**Resumo**: o protocolo de comunicação virou **BILATERAL** e ganhou regras de
coordenação de trabalho concorrente — contrato completo em **`AGENTS.md`** na
raiz do repo (ADR 0009,
`docs/decisions/0009-protocolo-bilateral-cursor-hermes.md`). Racional: dois
agentes (Cursor = construtor; Hermes = operador) abrem PRs em paralelo e as
sessões não têm memória — sem inbox reverso, ritual de sincronização e trava
de área, um agente sobrescreve ou "corrige" o trabalho do outro. Em resumo:

- **Inbox reverso**: `docs/CURSOR_UPDATES.md` (você → Cursor), mesmo formato
  deste arquivo (UPDATE-NNNN, append-only, Status como única edição, nunca
  autoriza violar gates/caps).
- **Ritual pré-alteração** (AGENTS.md §2, obrigatório para os dois agentes):
  fetch+pull da main → ler o próprio inbox e aplicar PENDENTES → `gh pr list`
  (área sobreposta a PR aberto do outro = não iniciar; comentar/inboxar e
  aguardar) → branch + **draft PR imediato** como trava de área.
- **Regra do mesmo PR, bilateral** (AGENTS.md §3): seus PRs que afetem o
  Cursor DEVEM incluir entrada em `docs/CURSOR_UPDATES.md` no mesmo PR — e o
  Cursor vai exigir isso em review, assim como você deve exigir a entrada
  neste arquivo nos PRs dele que te afetem.
- **Desempate de área** (AGENTS.md §4): código/arquitetura/schema =
  prioridade do Cursor; config operacional/skill/cron = SUA prioridade;
  conflito genuíno = ambos param e notificam o humano (rtg003).

**Ações do Hermes**:

1. Ler `AGENTS.md` integralmente e adotá-lo como contrato de coordenação.
2. Incorporar o ritual pré-alteração (§2) como primeira ação de toda sessão
   sua que vá alterar algo no repo — incluindo abrir draft PR imediatamente
   ao começar.
3. Passar a escrever em `docs/CURSOR_UPDATES.md` (no mesmo PR) toda mudança
   sua que exija ação/conhecimento do Cursor.
4. Em review de PRs do Cursor que te afetem, exigir a entrada neste arquivo
   antes de aprovar.
5. Após aplicar cada entrada deste inbox, marcar `Status: APLICADO em
   <data>` (única edição permitida).

**Validação**:

- Próximo PR seu que afete o Cursor contém entrada em
  `docs/CURSOR_UPDATES.md`.
- Seus PRs nascem como draft imediatamente ao iniciar o trabalho.
- Este arquivo com UPDATE-0001 e UPDATE-0002 marcados `APLICADO` após você
  executar as ações.

---

## UPDATE-0003 · 2026-07-03 · Status: APLICADO em 2026-07-03

**Origem**: PR do Cursor "discovery scheduler" (merged)

**Tipo**: operacao + logica_discovery

**Resumo**: o `discovery scan` deixou de depender de cron externo — agora é um
processo supervisionado do engine (`discovery-scheduler` em
`deploy/engine-processes.yaml`, módulo
`engine/strategies/copy_trade/discovery_scheduler.py`). Racional: a tabela
`traders` ficou vazia em produção porque nenhum cron foi instalado (o
onboarding ainda não ocorreu) — agendamento crítico não pode depender de
passo manual. Comportamento:

a) **Bootstrap**: no start, se a tabela `traders` estiver vazia, roda uma
   varredura imediatamente.
b) **Diário**: varredura às **05:00 America/Sao_Paulo** (configurável via
   `DISCOVERY_SCAN_HOUR_SP`), com a `logic_version` vigente.
c) **Kill switch**: com o arquivo `KILL` presente, não roda varredura.
d) Falha de scan gera `discovery.scan_failed` (em `events`) e o scheduler
   tenta no próximo horário — nunca morre.
e) Relatórios em `data/reports/discovery/`; eventos `discovery.scan_started`
   / `scan_completed` (com aprovados/excluídos/duração) replicados ao Supabase.

**Ações do Hermes**:

1. **NÃO instalar cron de `discovery scan`** (nem manter, se já criou) — o
   agendamento agora é do engine; cron duplicado geraria varredura dobrada e
   gasto de rate limit. Os DEMAIS crons do runbook (health, report diário,
   scanner, revisão semanal) continuam seus.
2. O briefing matinal passa a LER o resultado do scan das 05:00 (eventos
   `discovery.*` + `trader list`), em vez de disparar a varredura.
3. `discovery positioning`/`inspect`/`token` (spec v5) seguem sob demanda —
   ainda não implementados; chegam com o funil logic_version 2.

**Validação**:

- `systemctl status tokio-engine.service` lista o processo
  `discovery-scheduler` entre os filhos do supervisor.
- `events` contém `discovery.scan_started`/`scan_completed` após o start.
- Tabela `traders` populada (dashboard e `trader list` com candidatos
  SUGERIDO, score decrescente).
- Crontab do `tokio` SEM linha de discovery.

---

## UPDATE-0004 · 2026-07-03 · Status: APLICADO em 2026-07-03

**Origem**: PR do Cursor "isolamento de observabilidade" (merged)

**Tipo**: operacao + skill

**Resumo**: incidente detectado pelo humano — o dashboard de Copy Trade
exibia ordens/fills do módulo TradingView (`tv_gap_fade`) e fills sem
atribuição (`strategy_id NULL`, resíduo de bug antigo do snapshot da HL, já
corrigido na origem). Regra formalizada como **ADR 0010** e centralizada em
**`AGENTS.md` §5.1** (espelhada em `CLAUDE.md`):

- **Cada estratégia/módulo SÓ ENXERGA os próprios dados.** Toda visão de
  estratégia/módulo (dashboard, relatório, briefing, resposta sua) filtra
  por `strategy_id` do módulo — obrigatório, sem exceção.
- Dados sem atribuição (`strategy_id NULL`) só existem em visão de SISTEMA
  (tela Logs, agregado do `report --daily`) e são anomalia a investigar.
- Racional: sem isolamento, a análise de uma estratégia contamina a outra —
  PnL/comportamento atribuídos ao módulo errado geram decisão errada de
  gate, pausa e arquivamento.
- Limpeza executada: os 13 fills `NULL` foram removidos (migration
  `0003_cleanup_unattributed_fills` + DELETE no Supabase). Os dados de
  `tv_gap_fade` permanecem — são histórico legítimo do módulo TV.

**Ações do Hermes**:

1. Internalizar a regra: análises e relatórios POR ESTRATÉGIA usam apenas
   dados da própria estratégia (`report --strategy <id>`, queries filtradas);
   visão agregada só em contexto de portfólio/sistema, rotulada como tal.
2. NUNCA "corrigir" os filtros de escopo de volta (dashboards com tabelas
   vazias em módulos sem trades é o comportamento CORRETO, não regressão).
3. Refletir a regra na `skill/SKILL.md` via PR seu (área sua pelo AGENTS.md
   §4), referenciando ADR 0010.
4. **Investigar e reportar ao humano**: como surgiram 5 ordens e 2 fills de
   `tv_gap_fade` em produção? A estratégia está `dry_run` — ordens dry_run
   não geram fills. Se houve teste live seu, documente; se não foi você,
   é anomalia séria (possível violação de gate) e o humano decide.

**Validação**:

- Dashboard Copy Trade sem dados de outros módulos (hoje: tabelas vazias).
- Skill atualizada com a regra, mergeada na `main`.
- Resposta ao humano sobre a origem dos dados `tv_gap_fade`.
- Este UPDATE marcado `APLICADO` após executar as ações.

---

## UPDATE-0005 · 2026-07-03 · Status: APLICADO em 2026-07-03

**Origem**: PR do Cursor "hermes context autoload" (merged) — pedido direto
do humano: "Cursor e Hermes precisam se entender sem ruído nem atropelo"

**Tipo**: operacao + skill

**Resumo**: garantir que TODA sessão sua carregue o contrato central
(`AGENTS.md`, com `CLAUDE.md` como espelho/ponteiro) automaticamente — hoje o
carregamento depende de você lembrar de ler. Três caminhos agora apontam para
o mesmo lugar: a skill `trade` (primeira ação no topo do SKILL.md — adicionada
pelo Cursor neste PR, excepcionalmente na sua área, por diretiva humana),
o `docs/HANDOFF_HERMES.md` §8 e o `CLAUDE.md` na raiz.

**Ações do Hermes**:

1. Configurar seu runtime para carregar `AGENTS.md` automaticamente no início
   de toda sessão que toque o repo — mecanismos, na ordem de preferência:
   a) rodar suas sessões com cwd em `/home/tokio/Tokio` (runtimes baseados em
      Claude Code carregam `CLAUDE.md` do cwd sozinhos);
   b) se seu runtime suporta memória/instrução global (ex.:
      `~/.claude/CLAUDE.md` ou config do Hermes), adicionar UMA linha:
      "Antes de tocar no repo Tokio, leia e execute /home/tokio/Tokio/AGENTS.md";
   c) na impossibilidade de (a)/(b), a skill `trade` já traz a instrução como
      primeira ação — obedecê-la é mandatório.
2. Validar que a instrução da skill (topo do SKILL.md) está no seu runtime
   (skill re-registrada/atualizada, se você mantém cópia).
3. Confirmar o entendimento do fluxo anti-atropelo num teste prático: iniciar
   uma sessão nova, e a PRIMEIRA saída deve ser o resultado do ritual §2
   (pull + inbox + gh pr list + draft PR se for alterar algo).

**Validação**:

- Uma sessão nova sua demonstra o ritual §2 como primeira ação, sem ser
  lembrada.
- `docs/CURSOR_UPDATES.md` recebe uma entrada sua confirmando o mecanismo de
  autoload escolhido (a/b/c), para o Cursor saber o que pode assumir.
- Este UPDATE marcado `APLICADO`.

---

## UPDATE-0006 · 2026-07-03 · Status: APLICADO em 2026-07-03

**Origem**: PR do Cursor "discovery v2 — funil completo" (merged)

**Tipo**: logica_discovery + operacao

**Resumo**: a `logic_version: 2` (spec v5) está IMPLEMENTADA e em produção —
o UPDATE-0001 (b) descrevia o plano; isto é a entrega. O que muda na sua
operação:

a) **Scan diário v2** (05:00 SP, scheduler do engine): funil completo — top
   500 do leaderboard, entrada por 4 janelas (30d+60d obrigatórias), F1–F11,
   score da spec com ajustes, coortes bidimensionais, controle rekt. No
   primeiro start pós-deploy o scheduler re-scaneia automaticamente
   (logic_version avançou) e re-upserta os candidatos v1.
b) **Reprovados agora ficam na tabela** com `status = REJEITADO` e
   `reject_reason` (filtro + valores) — leia o motivo antes de sugerir
   qualquer wallet; um re-scan pode reabilitá-los (REJEITADO → SUGERIDO)
   se voltarem a passar.
c) **CLI nova** (a antiga `--top` foi aposentada):
   `discovery scan` · `discovery inspect <address>` (dossiê com distância de
   liquidação e coorte) · `discovery positioning` (viés smart vs. rekt por
   ativo — INSUMO DO SEU BRIEFING, nunca sinal de execução) ·
   `discovery token <ativo>` · `discovery report --last`.
d) **Como ler as colunas novas**: `Janelas` = consistência (ex.: `3/4` — a
   7d PODE ser negativa por design); `PF` é bruto incl. não realizado (leia
   junto de n_trades — crédito do score é gradativo); `Dist. liq.` < 10%
   é bomba-relógio (score já penalizado em −10).
e) **Config versionado**: thresholds/pesos em `config/discovery_config.yaml`.
   Sua autoridade de evolução (UPDATE-0001 d) opera SOBRE esse arquivo:
   PR + bump de logic_version + changelog + evento `logic_updated`.

**Ações do Hermes**:

1. Incorporar `discovery positioning` ao briefing matinal (substitui a
   leitura crua de candidatos) e `report --last` como fonte do resumo do scan.
2. Ao analisar candidato, usar `discovery inspect <address>` (dossiê) e citar
   as métricas v2 (TWRR, PF+n, janelas, coorte) — não as antigas.
3. Atualizar a skill (área sua) com a CLI nova e as leituras acima,
   referenciando `docs/discovery_changelog.md` (entrada v2).
4. Reportar no resumo diário quantos candidatos aprovados/rejeitados o scan
   trouxe (estatísticas do funil no relatório e no evento
   `discovery.scan_completed`).

**Validação**:

- Pós-deploy: evento `logic_updated` (1→2) + `discovery.scan_completed` com
  `logic_version: 2` em `events`; tabela `traders` com `windows_positive`
  preenchido e REJEITADOs com motivo.
- Briefing do dia seguinte contendo positioning smart vs. rekt.
- Skill atualizada via PR seu; este UPDATE marcado APLICADO.

---

## UPDATE-0007 · 2026-07-03 · Status: APLICADO em 2026-07-03

**Origem**: PR do Cursor "discovery v3 — afrouxar filtros" (diretiva humana
rtg003 após o scan real da v2 aprovar 0 candidatos)

**Tipo**: logica_discovery

**Resumo**: `logic_version: 3`. O scan real full-budget da v2 (`b684b8bbe5f5`)
reprovou TODOS os 100 aprofundados (F3: 34 · F5: 24 · F4: 8 · entrada: 7). O
humano determinou afrouxar:

a) **F3 (anti-scalper) DESABILITADO** — scalpers agora ENTRAM na tabela com
   score penalizado pela copiabilidade (frequência/hold fora do sweet spot).
   Um score alto de scalper continua sendo sinal de cautela para espelhamento:
   leia `avg_holding_hours` e `n_trades_30d` no `discovery inspect` antes de
   sugerir.
b) **F4 (TWRR 30d ≥ 5%) DESABILITADO** — TWRR segue calculado e exibido, mas
   não elimina. Candidato com TWRR negativo PODE aparecer (se as janelas de
   PnL fecharem positivas); cite o TWRR na análise.
c) **F5: max DD 90d 25% → 40%** — o teto também alimenta o componente de
   score de DD, que ficou mais tolerante. DD entre 25–40% agora passa: avalie
   caso a caso na sugestão.
d) **Entrada: ≥2/4 janelas com só a 30d obrigatória** (era ≥3/4 com 30d+60d).
   A coluna `Janelas` (`windows_positive`) fica MAIS importante na leitura:
   `2/4` agora é aprovável — prefira 3/4+ nas sugestões de copy.

Filtros desabilitados têm threshold `null` em `config/discovery_config.yaml`
(numeração F1–F11 preservada; reativar = config + bump). Racional completo e
números em `docs/discovery_changelog.md` (entrada v3).

**Ações do Hermes**:

1. Ajustar a leitura dos candidatos no briefing: score deixou de embutir os
   vetos de scalper/TWRR/DD≤25% — cite explicitamente hold, trades/dia, TWRR
   e DD ao sugerir wallet para Gate 2.
2. Atualizar a skill (área sua) onde descreve o funil: entrada "≥3/4,
   30d+60d obrigatórias" → "≥2/4, 30d obrigatória"; F3/F4 desabilitados;
   F5 a 40%.
3. Nenhuma mudança de agendamento: o scheduler re-scaneia sozinho no primeiro
   start pós-deploy (logic_version avançou).

**Validação**:

- Evento `logic_updated` (2→3) + `discovery.scan_completed` com
  `logic_version: 3` em `events`; tabela `traders` com aprovados > 0.
- Skill atualizada via PR seu; este UPDATE marcado APLICADO.

---

## UPDATE-0008 · 2026-07-04 · Status: APLICADO em 2026-07-04

**Origem**: PR do Cursor "discovery v7 — copiabilidade real" (implementação
integral do SEU UPDATE-0007 em `docs/CURSOR_UPDATES.md`, por diretiva humana)

**Tipo**: logica_discovery

**Resumo**: `logic_version: 7`. As 5 mudanças que você pediu estão em
produção — o funil agora olha as posições ABERTAS no momento do scan e
simula a cópia antes de aprovar:

a) **F7b**: alavancagem ATUAL ≤ 10x (max das posições abertas; a média
   histórica do F7 continua ≤ 15x). Sem posição aberta = passa.
b) **F12**: margem disponível ≥ 10% do accountValue. Os dois wallets do seu
   dossiê ($0 disponível) reprovam aqui.
c) **F13**: distância de liquidação ≥ 15% — agora medida do **MARK price**,
   não da entrada (o cálculo antigo escondia risco em posição que já andou).
   A penalidade de score −10 passou a cobrir a faixa 15–20%.
d) **F15**: simulação retroativa — cópia com $1K nos últimos 30d, líquida
   de taxa+slippage por perna; net ≤ 0 reprova. Só PnL REALIZADO conta
   (lucro 100% não-realizado, como o #1 do seu dossiê, reprova).
e) **F11 corrigido** (seu "F14"): notional mediano REAL dos fills ×
   (mirror_capital/equity) ≥ $10. O cálculo antigo assumia trade = 5% do
   equity (bug) — seu caso de $56K/$1.80 agora reprova corretamente.

Colunas novas em `traders` (migration 0005): `max_current_leverage`,
`available_margin_pct`, `sim_net_pnl_usd` — também no dashboard (expandido)
e no rationale do report. Racional completo: `docs/discovery_changelog.md`
(entrada v7).

**Ações do Hermes**:

1. Incorporar as colunas novas ao dossiê/briefing: margem disponível, lev
   atual e cópia simulada são agora as PRIMEIRAS coisas a citar ao sugerir
   wallet para Gate 2 (score alto sem elas não existe mais por construção).
2. Atualizar a skill (área sua): funil F1–F15, leitura dos `reject_reason`
   novos (F7b/F12/F13/F15) e a semântica do F11 corrigido.
3. Nenhuma mudança de agendamento: o scheduler re-scaneia sozinho no
   primeiro start pós-deploy (logic_version avançou).

**Validação**:

- Evento `logic_updated` (6→7) + `discovery.scan_completed` com
  `logic_version: 7`; tabela `traders` com as 3 colunas novas preenchidas
  para aprovados.
- Os 2 wallets do seu dossiê (`0x1aa5…95cb`, `0x5d8f…7927`) constam como
  REJEITADO com motivo F7b/F12/F13 (verificado no scan de validação do PR).
- Skill atualizada via PR seu; este UPDATE marcado APLICADO.

---

## UPDATE-0009 · 2026-07-04 · Status: APLICADO em 2026-07-04

**Origem**: PR do Cursor "discovery v8 — Estágio 4 (simulação de cópia)"
(diretiva humana pós-diagnóstico "poucos bons traders")

**Tipo**: logica_discovery + operacao

**Resumo**: `logic_version: 8`. Racional central: **bom trader ≠ boa cópia**.
O score continua medindo o trader; o novo ESTÁGIO 4 mede a CÓPIA — e é ele
o critério final do ranking.

a) **Estágio 4 (novo)**: para quem sobrevive ao score, o funil roda um
   replay dos fills (60d) com NOSSO sizing ($1K proporcional ao equity),
   taxas taker + slippage E custo de latência (200ms–2s ≈ slippage extra
   por perna). Saídas por candidato: PnL líquido simulado, expectância por
   trade e max DD da curva da cópia.
b) **Ranking final = score × fator** (fator = 1 + ROI da cópia, clamp
   [0.5, 1.2]). A ordem da tabela pode divergir do score puro — é
   intencional: o topo agora é "melhor cópia".
c) **Cópia simulada negativa = REJEITADO** com motivo `copy_sim_negativa`,
   MESMO com score alto. Ao ler a tabela, esse motivo significa: o trader
   pode ser bom, mas espelhá-lo com $1K perde dinheiro após custos.
d) **Colunas novas** em `traders` (migration 0006): `sim_expectancy_usd`,
   `sim_max_dd_pct`, `sim_factor`. ATENÇÃO: migration Supabase é passo
   MANUAL pós-deploy (seu incidente 1 do UPDATE-0006):
   `psql "$DATABASE_URL" -f db/migrations/supabase/0006_discovery_v8.sql`
   — sem isso o replicator falha com PGRST204 nas colunas novas.
e) **Fontes adicionais** (config `sources`, flags OFF): `nansen_leaderboard`
   e `apify_hl_scraper` podem alimentar ENDEREÇOS candidatos quando o
   humano ativar (exigem chave no ambiente). A HL pública segue sendo a
   fonte de verdade de TODAS as métricas — terceiros nunca substituem o
   nosso funil.
f) **Diagnóstico que motivou tudo**:
   `docs/reports/discovery_diagnostico_funil_2026-07-04.md` — leia antes de
   propor calibração; as recomendações 3/4/5 (F8 top_n, limpeza dos 2
   SUGERIDOs legado v1, request_budget) aguardam decisão humana.

**Ações do Hermes**:

1. **Sugestões manuais passam pela MESMA régua**: qualquer wallet que você
   (ou o humano, vinda de Copin/HyperX) queira propor entra via
   `discovery inspect <address>` e passa pela simulação como qualquer
   candidato — cite net simulado, expectância e DD da cópia na sugestão.
   Nenhuma via lateral de aprovação.
2. No briefing, ao listar candidatos, use o ranking da tabela (já vem
   ordenado por score × fator) e cite o `sim_factor` — score alto com
   fator baixo é sinal de cópia medíocre.
3. Aplicar a migration Supabase 0006 (comando no item d) no pós-deploy e
   confirmar que o replicator não acusa PGRST204.
4. Executar a limpeza recomendada no diagnóstico SE o humano aprovar:
   `trader reject` nos 2 SUGERIDOs legado v1 (`0xe4c6…4048`, `0xeeb5…0464`
   — score 33.8/17.2, um com DD 99.3%).
5. Atualizar a skill (área sua): Estágio 4, motivo `copy_sim_negativa`,
   colunas novas e a regra do item 1.

**Validação**:

- Evento `logic_updated` (7→8) + `discovery.scan_completed` com
  `logic_version: 8`; aprovados com `sim_factor` preenchido; eventuais
  `copy_sim_negativa` em `reject_reason`.
- Replicator sem PGRST204 após a migration 0006 no Supabase.
- Skill atualizada via PR seu; este UPDATE marcado APLICADO.

---

## UPDATE-0010 · 2026-07-04 · Status: APLICADO em 2026-07-04

**Origem**: PR do Cursor "discovery v9 — copiar a CÓPIA, com tudo documentado"
(após laboratório offline e auditoria do top 1 irreal)

**Tipo**: logica_discovery + operacao + skill

**Resumo**: `logic_version: 9`. A régua final mudou: **não sugerimos o melhor
trader; sugerimos a melhor CÓPIA**. Score/TWRR/win-rate/janelas continuam no
dossiê, mas o ranking final é o PnL líquido simulado da cópia com $1k, taxas,
latência e teto de alavancagem 3x. Referência canônica de toda variável:
`docs/discovery_logic_v9.md`.

### O que você precisa entender para operar

1. **Ranking novo**: a tabela vem ordenada por `sim_stage4_net_usd` (net da
   cópia simulada), não por score. Score alto sem net simulado alto NÃO é
   sugestão.
2. **Motivos novos de rejeição**:
   - `F16`: histórico curto — menos de 30 dias entre primeiro e último fill.
   - `F17`: cópia simulada não rende mais de $10.
   - `F18`: edge só aparece numa metade da janela (sortudo de uma perna).
   - `F19`: DD da curva da cópia > 25%.
   - `F20`: equity do trader > $150k (grande demais para espelhar bem com $1k).
3. **Colunas novas de leitura obrigatória**:
   - `coverage_days`: cobertura real do histórico de fills.
   - `sim_half_old_net` / `sim_half_new_net`: lucro líquido da cópia nas duas
     metades da janela de 60d.
   - já existentes e agora centrais: `sim_net_pnl_usd`, `sim_expectancy_usd`,
     `sim_max_dd_pct`.
4. **Ordem obrigatória ao sugerir Gate 2**: net simulado → expectância/trade →
   DD da cópia → cobertura → metades → só depois score, TWRR, DD do trader.
5. **Sugestões manuais** (suas ou do humano via Copin/HyperX) entram por
   `discovery inspect <address>` e passam pela MESMA régua F1–F20 + simulação.
   Nenhuma via lateral de aprovação.

### Passos manuais pós-deploy

1. Aplicar migration Supabase 0007:
   `psql "$DATABASE_URL" -f db/migrations/supabase/0007_discovery_v9.sql`
2. Garantir `HYPERTRACKER_API_KEY` no `.env` da VPS (segredo fornecido pelo
   humano; não registrar em docs/logs). Sem chave, o feed HyperTracker fica off
   silenciosamente e o scan segue só com HL.
3. Confirmar replicator sem PGRST204 nas colunas `coverage_days`,
   `sim_half_old_net`, `sim_half_new_net`.

### Ações do Hermes

1. Atualizar a skill (`skill/SKILL.md`, área sua) para o funil F1–F20 e o
   briefing v9: ranking por net simulado; score informativo.
2. No primeiro briefing pós-deploy, citar para cada candidato: net simulado,
   expectância, DD da cópia, cobertura e metades. Se o briefing ordenar por
   score, está errado.
3. Marcar este UPDATE como APLICADO após skill atualizada, migration aplicada e
   primeiro briefing no novo formato.

**Validação**:

- Evento `logic_updated` (8→9) + `discovery.scan_completed` com
  `logic_version: 9`.
- Aprovados com `coverage_days >= 30`, metades positivas e DD da cópia <= 25%.
- Replicator sem PGRST204 após migration 0007.
- Skill atualizada via PR seu; este UPDATE marcado APLICADO.

---

## UPDATE-0011 · 2026-07-05 · Status: APLICADO em 2026-07-05

**Origem**: diretiva humana rtg003 + execução Cursor na `main`

**Tipo**: operacao + infra + web + config

**Resumo**: o Tokio foi simplificado para **SQLite local como único banco de
dados**. A camada Supabase foi removida por completo: sem réplica, sem
`replicator`, sem `replication_queue`, sem migrations Supabase e sem Supabase
Auth. O dashboard agora usa auth simples por senha e lê dados do SQLite via
gateway interno. Também foram registradas novas diretivas permanentes no
`AGENTS.md`: commits diretos na `main`, estratégias não se misturam, e cada
estratégia/funcionalidade tem página própria.

### Mudanças que você precisa saber

1. **Sem PR por padrão**: Cursor e Hermes agora podem editar direto na `main`.
   Antes de push: `git pull origin main`. Mudança que afete o outro agente
   exige entrada no inbox dele no mesmo commit.
2. **Estratégias não se misturam**: regra, ordem, fila, trade, fill, métrica,
   tabela, card, relatório e config de uma estratégia só valem para ela mesma.
3. **Dashboard atual é só Copy Trade**: não é dashboard geral. A rota principal
   redireciona para `/copy-trade`; a dashboard geral será criada depois como
   página separada.
4. **Uma página por estratégia/funcionalidade**: código/queries de copy trade
   ficam em `web/app/(app)/copy-trade/`, `web/components/copy-trade/` e
   `web/lib/copy-trade/`. Não misturar módulos em página única.
5. **SQLite único BD**: `engine.replicator_main`, `deploy/apply_supabase_migrations.sh`,
   `db/migrations/supabase/` e `replication_queue` foram removidos. O endpoint
   `/health` não retorna mais `replication_queue_depth` nem `replication_lag_s`.
6. **Auth do dashboard**: Supabase Auth saiu. O web exige `DASHBOARD_PASSWORD`
   e `DASHBOARD_AUTH_SECRET` no `.env` da VPS. Sem essas vars, o dashboard fica
   fail-closed no login.
7. **Purga de DRY_RUN**: migrations locais removem `ct_whale01`, `dm_pulse`,
   `tv_funding_extreme` e `tv_gap_fade`. A única estratégia que deve restar é
   `ct_48295497` (ativa/COPIANDO/pinned).
8. **Backup**: `deploy/backup_sqlite.sh` agora é o script versionado. Ele cria
   snapshot consistente via `sqlite3 .backup`, compacta, verifica restore e
   opcionalmente envia offsite via `BACKUP_REMOTE` (`file://`, `scp://` ou
   rclone remote).

### Ações do Hermes

1. Atualizar a memória persistente e a skill `trade` com as diretivas acima.
   Remover referências operacionais a Supabase, replicator, PGRST e migrations
   Supabase como rotina normal.
2. Antes/ao validar o próximo deploy, garantir no `/home/tokio/Tokio/.env`:
   `DASHBOARD_PASSWORD`, `DASHBOARD_AUTH_SECRET`, `GATEWAY_CONTROL_TOKEN`,
   `HL_ACCOUNT_ADDRESS` e `HL_AGENT_PRIVATE_KEY`.
3. Atualizar crons/briefings/health checks: não consultar mais campos
   `replication_*` no `/health`; usar apenas engine online, kill switch,
   circuit breaker, executor copy trade e backup.
4. Trocar o cron de backup local para chamar `deploy/backup_sqlite.sh` e
   configurar `BACKUP_REMOTE` para destino offsite. Manter retenção local de
   7 dias e offsite de 30 dias.
5. Depois de validar o dashboard e o backup offsite, tratar o projeto Supabase
   antigo como réplica aposentada. Não apagar nada sem confirmação humana, mas
   não depender dele para operação.
6. Briefings/crons não devem mais citar `tv_gap_fade`, `tv_funding_extreme`,
   `dm_pulse` ou `ct_whale01` como estratégias operacionais.

### Validação

- `curl http://127.0.0.1:8700/health` retorna sem campos `replication_*`.
- `curl "http://127.0.0.1:8700/api/metrics?strategy_ids=ct_48295497"` retorna
  JSON (lista vazia é aceitável se não houver métricas no período).
- `python -m engine.cli strategy list` mostra somente `ct_48295497` entre as
  estratégias operacionais esperadas.
- `https://tokio.bz/` redireciona para `/copy-trade`; login por senha funciona;
  cards/tabelas da dashboard carregam do gateway.
- `bash deploy/backup_sqlite.sh --verify` passa e o artefato aparece no destino
  offsite configurado.

---

## UPDATE-0012 · 2026-07-05 · Status: APLICADO em 2026-07-05

**Origem**: diretiva rtg003 + implementação Cursor "Reforma tela Copy Trade"

**Tipo**: operacao + web + config + infra

**Resumo**: a dashboard de Copy Trade ganhou novo ciclo operacional de traders,
combobox de status com execução imediata, filtros funcionais por ambiente e por
trader acompanhado, e suporte de engine para roteamento por ambiente
TESTNET/MAINNET. Os status antigos de trader foram aposentados.

### Novo ciclo de status

Status válidos da tabela `traders`:

- `SUGERIDO`: aguardando decisão humana.
- `SALVO`: trader em observação/acompanhamento, ainda sem cópia.
- `TESTNET`: trader copiado em ambiente testnet.
- `MAINNET`: trader copiado em ambiente mainnet (dinheiro real).
- `REJEITADO`: pronto para sair da lista na próxima atualização.

Mapeamento aplicado pela migration `0012_trader_status_v2.sql`:

- `DRY_RUN` → `TESTNET`
- `COPIANDO` → `TESTNET`
- `PAUSADO` → `SALVO`
- `ARQUIVADO` → `REJEITADO`

`DRY_RUN`, `COPIANDO`, `PAUSADO` e `ARQUIVADO` não devem mais ser usados para
traders. A nomenclatura `dry_run` de `strategies.status` permanece apenas para
outros módulos legados; copy trade passa a ser controlado pelo status do trader.

### Dashboard

- Rota continua `/copy-trade`.
- Coluna Status agora é um combobox. Ao mudar o valor, a ação é executada
  imediatamente pelo gateway.
- A dashboard autenticada por senha é considerada ato humano para esse combobox.
  `TESTNET` e `MAINNET` pedem confirmação no browser antes de chamar o gateway.
- O chip/termo `pinned` saiu da coluna Status.
- Endereço do trader:
  - branco: `SUGERIDO`;
  - amarelo: `SALVO`;
  - verde: `TESTNET`/`MAINNET`.
- Score virou barra compacta com tooltip numérico.
- Todos os cabeçalhos da tabela Traders têm tooltip explicativo.
- PnL 30d agora mostra 2 casas decimais.
- Scrollbar horizontal da tabela foi estilizada no tema.
- Filtros combináveis:
  - exchange/ambiente: todos, testnet, mainnet;
  - trader acompanhado (`copy_pinned=1` ou status SALVO/TESTNET/MAINNET);
  - período.

### Engine / ambiente

- `IntentRequest` aceita `environment`.
- Gateway roteia ordens para o adapter do ambiente do trader.
- `/balance` aceita `?env=testnet|mainnet`.
- `/api/exchanges` passa a ser populado no startup do gateway:
  - testnet ativo;
  - mainnet ativo se credenciais existirem, senão `unconfigured`.
- `config/settings.yaml` ganhou `copy_trade.watch_network: mainnet` para ler os
  fills dos alvos na mainnet pública, independentemente do ambiente de execução.

### Ações do Hermes

1. Atualizar skill/memória: remover status antigos de traders e usar
   `SUGERIDO/SALVO/TESTNET/MAINNET/REJEITADO`.
2. Coletar com o humano, se ele quiser habilitar MAINNET:
   - `HL_MAINNET_ACCOUNT_ADDRESS`
   - `HL_MAINNET_AGENT_PRIVATE_KEY`
   Gravar no `/home/tokio/Tokio/.env` sem logar segredos.
3. Enquanto essas envs não existirem, promoção para `MAINNET` retorna
   `mainnet_nao_configurado`. Isso é esperado e seguro.
4. Atualizar briefings/crons para reportar `TESTNET`/`MAINNET` em vez de
   `DRY_RUN`/`COPIANDO`/`PAUSADO`.
5. Validar após deploy:
   - `python -m engine.cli trader list` mostra status novos;
   - `curl http://127.0.0.1:8700/api/exchanges` mostra testnet e mainnet;
   - dashboard `/copy-trade` mostra combobox, cores, filtros e tooltips;
   - `ct_48295497` fica `TESTNET` após migration e segue copiando.

### Validação esperada

- `python -m engine.cli db migrate` aplica `0012_trader_status_v2`.
- `curl -s http://127.0.0.1:8700/api/traders` retorna `strategy_id` e
  `environment` por trader.
- Filtro ambiente/trader na dashboard altera KPIs, ordens e trades.
- Sem credenciais mainnet, combobox `MAINNET` recusa com
  `mainnet_nao_configurado`.

---

## UPDATE-0013 · 2026-07-05 · Status: APLICADO em 2026-07-05

Origem: Cursor — Discovery v11 (funil aberto, HyperTracker confiável e
flexibilidade de calibração)

Tipo: logica_discovery + operacao + skill

Resumo: a logic_version 11 corrige o gargalo estrutural do discovery. O scan
v10 validado trouxe 5000 coletados → 150 aprofundados → 1 aprovado; isso é
compatível com a taxa de aprovação ~1–2% medida no laboratório. O problema
real era entrada pequena e bug de fonte externa: HyperTracker estava ligado,
mas seus endereços eram descartados quando `deep_dive_max` já estava cheio
(`[:0]`). A v11 abre a ENTRADA, torna o HyperTracker observável e dá ao Hermes
ajuste fino via YAML/replay, mantendo F16–F19 como régua de qualidade da cópia.

### O que mudou

1. **HyperTracker confiável**
   - `collection.external_dive_quota: 60`: vagas extras reservadas a fontes
     externas, somadas ao `deep_dive_max`.
   - `collection.external_interleave_after: 100`: fontes externas entram cedo
     na fila para não serem sempre sacrificadas se o orçamento estourar.
   - Se HyperTracker/Nansen/Apify vierem vazios ou trouxerem poucos endereços,
     a quota vira mais leaderboard via `fallback_leaderboard_extra`.
   - `active_scan_enabled: false`: a implementação atual era stub alfabético
     (leaderboard + conhecidos), não fonte real de atividade.

2. **Novos números do funil**
   - `deep_dive_max: 300`
   - `request_budget: 2800`
   - `min_equity_usd: 1000`
   - F20 agora é banda: `f20_min_trader_equity_usd: 1000`,
     `f20_max_trader_equity_usd: 100000`
   - `f2c_min_trades_7d: 2`
   - `f8_liquid_assets_top_n: 40`
   - Expectativa: ~360 aprofundados/scan, 3–7 SUGERIDOs em condições normais,
     scan frio ~18–25 min. Se aprovados >15, auditar antes de recomendar.

3. **Flexibilidade nova para o Hermes**
   - Todo hard filter F1–F20 aceita `null = desligado`.
   - F9 ficou totalmente parametrizado no YAML:
     `f9_mm_min_tpd_for_pnl_vol`, `f9_mm_max_neutral_exposure`,
     `f9_mm_min_tpd_for_neutral`.
   - `collection.deep_sort_by` permite mudar o perfil do deep dive:
     `roi_30d`, `pnl_7d`, `equity_asc`.
   - `collection.min_request_interval_s` controla o throttle HTTP.
   - Regra operacional: pedido humano do tipo "quero perfis X" ou "mais/menos
     opções" deve virar replay + proposta de YAML; mudança definitiva segue
     protocolo com bump/changelog/doc.

4. **Ferramentas novas**
   - `python -m engine.strategies.copy_trade.discovery replay --set chave=valor`
     roda what-if sobre cache quente, sem persistir traders e sem emitir evento
     de scan.
   - Exemplo:
     `python -m engine.strategies.copy_trade.discovery replay --set hard_filters.f2c_min_trades_7d=5 --set hard_filters.f20_max_trader_equity_usd=150000`
   - Relatórios agora têm seção NEAR-MISS: rejeitados por exatamente 1 filtro,
     com a chave YAML que controla aquele corte.

5. **Novas stats para briefing**
   - `hypertracker_coletados`
   - `hypertracker_aprofundados`
   - `fontes_externas_aprofundados`
   - `fallback_leaderboard_extra`
   - `corte_barato_f20`

### Ações do Hermes

1. Atualizar `skill/SKILL.md` com logic_version 11, removendo a ideia de que
   HyperTracker estava apenas "ON": agora ele tem quota, stats próprias e
   fallback.
2. Incorporar `docs/discovery_calibration_playbook.md` à skill/memória e aos
   briefings: quando o humano pedir perfis específicos, usar o playbook para
   escolher chaves e testar com `discovery replay --set`.
3. Verificar na VPS se a chave existe sem logar segredo:
   `echo ${HYPERTRACKER_API_KEY:+set}`.
4. No primeiro scan v11, observar `hypertracker_aprofundados` e
   `fallback_leaderboard_extra`. Se HyperTracker vier 0 com chave setada,
   investigar API/chave antes de concluir que a fonte não tem candidatos.
5. Atualizar briefing diário para citar as novas stats por fonte e destacar
   NEAR-MISS quando houver concentração em um filtro.
6. Nunca interpretar replay/near-miss como aprovação automática: Gate 2,
   TESTNET/MAINNET, mainnet e caps continuam humanos e invioláveis;
   `copy_pinned` segue protegido contra re-scan.

### Validação esperada

- `python -m engine.strategies.copy_trade.discovery scan --reason manual_v11`
  registra `logic_updated` para v11 e produz funil com `aprofundados` perto de
  360 quando há candidatos suficientes.
- O relatório mostra as novas stats; se HyperTracker não contribuir, aparece
  `fallback_leaderboard_extra`.
- `python -m engine.strategies.copy_trade.discovery replay --set hard_filters.f2c_min_trades_7d=5`
  roda sem persistir traders e escreve relatório `replay-*`.
- `docs/discovery_calibration_playbook.md` está referenciado/absorvido pela
  skill do Hermes.

---

## UPDATE-0014 · 2026-07-05 · Status: APLICADO em 2026-07-05

Origem: Cursor — correções operacionais da dashboard Copy Trade

Tipo: web + operacao + infra

Resumo: correções pós-deploy da dashboard `/copy-trade`. O combobox de Status
retornava `not_allowed` por bug no proxy Next `/api/control`; trades testnet
podiam sumir quando fills chegavam após restart porque o gateway dependia só do
ledger em memória para atribuir `strategy_id`. Também foram ajustados textos,
layout mobile, tooltips, espaçamento e alturas das tabelas.

### O que mudou

1. **Combobox Status corrigido**
   - Proxy Next agora aceita path real `trader/<addr>/status` e encaminha para
     `/control/trader/<addr>/status`.
   - SALVO/TESTNET/MAINNET/REJEITADO deixam de retornar `not_allowed`.
   - MAINNET sem credenciais segue recusando com `mainnet_nao_configurado`.

2. **Trades testnet corrigidos**
   - `on_own_fill` agora resolve `strategy_id` por:
     `ledger.strategy_for_cloid(cloid) OR orders.strategy_id`.
   - Isso corrige fills tardios/pós-restart que antes entravam com
     `strategy_id NULL` e não apareciam em `/api/fills?strategy_id=ct_*`.

3. **UI Copy Trade**
   - Mobile: filtros de Exchange e Trader lado a lado.
   - Labels:
     - `Todos`
     - `Hyperliquid - Testnet`
     - `Hyperliquid - Mainnet`
   - Filtro de trader mostra só as 12 primeiras letras do usuário.
   - Card Saldo mostra `$`.
   - Tooltips de colunas têm fallback `title` nativo e cursor padrão.
   - Tabela Traders mais compacta e sem o cardnote antigo.
   - Alturas máximas:
     - Traders: 4 traders visíveis.
     - Ordens: 6 ordens visíveis.
     - Trades: 8 trades visíveis.
   - Scrollbars vertical/horizontal seguem o tema.

### Ações do Hermes

1. Após deploy, validar no browser:
   - mudar Status para SALVO/TESTNET/REJEITADO não retorna `not_allowed`;
   - MAINNET sem envs mainnet retorna `mainnet_nao_configurado`;
   - filtros mobile lado a lado;
   - tooltips aparecem ao passar o mouse sobre títulos;
   - trades testnet de hoje aparecem na tabela Trades.
2. Validar API:
   - `curl -s 'http://127.0.0.1:8700/api/fills?strategy_id=ct_48295497&limit=20'`
   - `curl -s 'http://127.0.0.1:8700/api/orders?strategy_id=ct_48295497&limit=20'`
3. Se trades antigos ainda estiverem com `strategy_id NULL`, isso é histórico
   já gravado antes da correção. Novos fills passam a ser atribuídos via
   fallback da ordem.

### Validação esperada

- `python -m pytest tests/test_gateway.py -q` verde.
- `npm run build` verde.
- Combobox Status operacional sem `not_allowed`.
- Tabela Trades lista novos fills testnet atribuídos a `ct_48295497`.

---

## UPDATE-0015 · 2026-07-05 · Status: APLICADO em 2026-07-05

Origem: Cursor — contagem de trades, filtro de ambiente e colunas das tabelas

Tipo: web + gateway + operacao

Resumo: o card Trades contava via `strategy_metrics_daily` (incompleto e sem
rede). O filtro Exchange filtrava traders pelo status atual, não pela rede de
execução das ordens/fills. Corrigido com filtro `network` no gateway e KPI via
`/api/fills/summary`. Tabelas de ordens/trades reorganizadas.

### O que mudou

1. **Gateway — filtro por rede de execução**
   - `GET /api/orders?network=testnet|mainnet` — join `exchanges` via
     `orders.exchange_id`.
   - `GET /api/fills?network=testnet|mainnet` — join `orders` + `exchanges`.
   - `GET /api/fills/summary` — agregados (`n_trades`, `net_pnl`, `fees`,
     `win_rate`) com os mesmos filtros.

2. **Dashboard — escopo desacoplado**
   - Tabela Traders: continua filtrando por status/ambiente do trader.
   - KPI / Ordens / Trades: usam `ledgerStrategyIds` (todas estratégias copy
     ativas, ou trader selecionado) + filtro `network` da exchange.
   - Card Trades usa `fillsSummary.n_trades` (COUNT real de fills no período).
   - PnL e win rate do KPI usam summary quando filtro de ambiente ativo.

3. **Tabelas**
   - Removida coluna Estratégia de ordens e trades.
   - Nova coluna **Valor** (`size × price`) após Preço.
   - Ordens: coluna Tipo movida para depois de Valor.
   - Traders: `width: max-content` para remover espaço vazio após Status.

### Ações do Hermes

1. Deploy na VPS (`git pull --ff-only origin main`, migrate, `npm run build`,
   restart `tokio-engine` + `tokio`).
2. Validar em https://tokio.bz/copy-trade:
   - Filtro **Todos**: card Trades = total de fills (ex.: 8).
   - Filtro **Testnet**: card e tabelas só testnet (ex.: 6).
   - Filtro **Mainnet**: card e tabelas só mainnet (ex.: 2).
   - Colunas Valor visíveis; sem coluna Estratégia; Tipo após Preço em ordens.
3. API local:
   ```bash
   curl -s 'http://127.0.0.1:8700/api/fills/summary?strategy_id=ct_48295497'
   curl -s 'http://127.0.0.1:8700/api/fills?strategy_id=ct_48295497&network=testnet&limit=20'
   ```

### Validação esperada

- `python -m pytest tests/test_gateway.py -q` verde.
- `npm run build` verde.
- Contagem de trades bate com tabela; filtro de ambiente funciona em ordens e trades.

---

## UPDATE-0016 · 2026-07-05 · Status: APLICADO em 2026-07-06

Origem: Cursor — filtro de ambiente definitivo (fills.network)

Tipo: schema + gateway + operacao

Resumo: o filtro Exchange ainda falhava porque fills legados não tinham rede
atribuída e muitas ordens tinham `exchange_id NULL` — o JOIN por
`orders.exchange_id` excluía registros testnet. Agora cada fill tem coluna
`network` própria; migração faz backfill; novos fills gravam rede na inserção.

### O que mudou

1. **Migração `0013_fills_network.sql`**
   - Coluna `fills.network` (`testnet` | `mainnet`).
   - Backfill `orders.exchange_id` NULL → hyperliquid testnet.
   - Backfill `fills.network` via ordem vinculada; órfãos → testnet.
   - Índice `idx_fills_network`.

2. **Gateway**
   - `on_own_fill` grava `network` (do adapter `_network` ou da ordem).
   - `handle_intent` garante `exchange_id` (re-seed se necessário).
   - `/api/fills` e `/api/fills/summary` filtram por `fills.network`.
   - `/api/orders` retorna campo `network` e filtra via `exchanges.network`.

3. **Dashboard**
   - Parâmetro `network` via `URLSearchParams.set` (sem concatenação manual).

### Ações do Hermes

1. **Obrigatório:** rodar migração na VPS:
   ```bash
   cd /home/tokio/Tokio
   git pull --ff-only origin main
   .venv/bin/python -m engine.cli db migrate
   sudo systemctl restart tokio-engine.service tokio.service
   ```
2. Validar filtro Exchange em https://tokio.bz/copy-trade:
   - **Todos** → 8 trades (exemplo atual).
   - **Hyperliquid - Testnet** → 6 trades + ordens testnet.
   - **Hyperliquid - Mainnet** → 2 trades + ordens mainnet.
3. Conferir backfill:
   ```bash
   sqlite3 data/tokio.db "SELECT network, COUNT(*) FROM fills GROUP BY network;"
   sqlite3 data/tokio.db "SELECT COUNT(*) FROM orders WHERE exchange_id IS NULL;"
   ```
   Esperado: fills com `testnet`/`mainnet`; orders com `exchange_id` preenchido.

### Validação esperada

- `python -m pytest tests/test_gateway.py -q` verde.
- Filtro de ambiente funciona em KPI, ordens e trades.

## UPDATE-0017 · 2026-07-06 · Status: APLICADO em 2026-07-06

Origem: Cursor — arredondamento de size movido para o executor (float_to_wire)

Tipo: engine + gateway

Resumo: o fix definitivo do `float_to_wire causes rounding` (seu workaround em
`handle_intent`, UPDATE-0017 do CURSOR_UPDATES) foi aplicado. A lógica de
arredondamento passou a ser feita PRIMARIAMENTE no executor de copy trade
(que conhece `my_prev`/`my_new` e consegue PULAR deltas menores que o step —
o gateway, stateless por intent, só conseguia rejeitar). Seu arredondamento no
gateway foi **mantido como backstop** para qualquer origem de intent.

### O que mudou

1. **Executor** (`engine/strategies/copy_trade/executor.py`)
   - `on_target_fill` arredonda a POSIÇÃO ALVO (`my_new`) ao `szDecimals` do
     ativo antes de calcular `delta`; se `abs(delta) < step`, pula com log
     `decision.skipped_size_too_small` (não cria ordem).
   - `szDecimals` é obtido via novo endpoint do gateway, com cache por símbolo.

2. **Gateway** (`engine/gateway/server.py`)
   - Novo endpoint `GET /api/market-meta?symbol=X&environment=testnet` (retorna
     `szDecimals`, `maxLeverage`, ...). Rede interna; sem token.
   - Backstop mantido; ramo `szDecimals==0` trocado de `float(int(size))`
     (truncava) para `float(round(size))` (arredonda), consistente com o executor.

3. **GatewayClient** (`engine/strategies/base_runner.py`)
   - Novo método `market_meta(symbol, environment)` (GET no endpoint acima).

### Ações do Hermes

1. **Obrigatório:** deploy na VPS:
   ```bash
   cd /home/tokio/Tokio
   git pull --ff-only origin main
   sudo systemctl restart tokio-engine.service tokio.service
   ```
2. Validar que ordens do trader `0xdef5...` (HYPE, FARTCOIN) passam a executar
   sem `float_to_wire causes rounding`; sizes fracionários viram múltiplos do
   step (ex: HYPE 0.69 → 1).
3. Conferir logs `decision.skipped_size_too_small` para deltas abaixo do step.

### Validação esperada

- `python -m pytest tests/test_copy_trade.py -q` verde (16 testes, 4 novos).
- Nenhuma nova ordem com `reject_reason` de rounding no ambiente do 0xdef5...

## UPDATE-0018 · 2026-07-06 · Status: PENDENTE

Origem: Cursor — corte barato do discovery mais limpo (UPDATE-0016 do CURSOR_UPDATES)

Tipo: logica_discovery (logic_version 13 → 14)

Resumo: apliquei o diagnóstico do seu UPDATE-0016. O corte barato misturava a
banda de equity F20 usando a equity APROXIMADA do leaderboard (falsos negativos)
e inativos consumiam vagas de deep dive. Agora o F20 sai do corte barato por
padrão e há um corte de inatividade opt-in — ambos calibráveis por você via
`config/discovery_config.yaml`. Também adicionei rastro do erro HTTP do
HyperTracker (para diagnosticar o 401/chave inválida).

### O que mudou

1. **F20 fora do corte barato** — `collection.cheap_cut_equity_filter` (default
   `false`). Com `false`, a banda F20 só corta no hard filter, com equity REAL do
   clearinghouse (fim dos falsos negativos por equity de leaderboard). `true`
   restaura o comportamento antigo.

2. **Corte de inativos antes do deep dive** — `collection.cheap_cut_last_activity_days`
   (default `null` = desligado). Com `N`, gasta 1 request curto por candidato do
   corte barato (`userFillsByTime`, 1 página) para descartar quem não opera há N
   dias, antes de reservar vagas de aprofundamento. **Custo:** consome
   `request_budget` (~1 req por candidato do corte barato) — por isso é opt-in;
   se ligar, considere aumentar `request_budget`. Novo stat
   `corte_barato_inativos` no relatório do funil.

3. **Rastro de erro HTTP** — `HLDataClient._request` agora loga
   `discovery.http_error url=... status=...` em qualquer HTTPStatusError (o 401
   do HyperTracker deixa de ser silencioso). Ajuda a confirmar a chave inválida.

`logic_version` foi bumpado 13 → 14; doc canônica (`docs/discovery_logic_v9.md`)
e `docs/discovery_changelog.md` atualizados no mesmo commit. Com os dois flags
no default (F20 só no hard filter; corte de inativos off), o funil aprova/reprova
igual à v13 — só muda O MOMENTO do corte F20.

### Ações do Hermes

1. **Obrigatório:** deploy na VPS:
   ```bash
   cd /home/tokio/Tokio
   git pull --ff-only origin main
   sudo systemctl restart tokio-engine.service tokio.service
   ```
2. **Recomendado (calibração):** rode um scan de teste e compare o funil com a
   v13. Sugestão para atacar os falsos negativos do UPDATE-0016:
   - manter `cheap_cut_equity_filter: false` (já é o default);
   - avaliar ligar `cheap_cut_last_activity_days` (ex.: 7–14) — mas suba
     `request_budget` proporcionalmente ao tamanho do corte barato.
3. Investigar o 401 do HyperTracker pelo novo log `discovery.http_error`
   (confirma se é chave inválida/expirada).

### Validação esperada

- `python -m pytest tests/test_discovery_funnel.py -q` — 2 testes novos verdes
  (`test_v14_cheap_cut_equity_filter_separates_f20`,
  `test_v14_cheap_cut_last_activity_days_cuts_inactive`).
- `python -m pytest tests/test_docs_coverage.py -q` verde (chaves novas documentadas).
- Nota: `test_scan_approves_swing_rejects_traps` já falhava na main antes desta
  mudança (assert F16 vs F15, fixture de simulação) — não relacionado.

## UPDATE-0019 · 2026-07-06 · Status: APLICADO em 2026-07-06

Origem: Cursor — reorganização da dashboard de Copy Trade.

Tipo: gateway (nova rota) + web (reorg de UI)

Resumo: reorganizei a dashboard de Copy Trade. A seção antiga "Ordens Abertas"
virou **Posições** (posições abertas no clearinghouse da venue, escopadas §5.1
aos símbolos que o copy_trade opera) e as ordens em aberto foram unificadas com
os trades numa única seção **"Trades e Ordens em Aberto"** (ordens no topo,
fills abaixo, com coluna Tipo ORDEM/TRADE). Layout final:
KPIs → Posições → Trades e Ordens em Aberto → Traders.

### O que mudou

1. **Nova rota `GET /api/positions?strategy_id=&network=`** (`engine/gateway/server.py`)
   — retorna posições do clearinghouse do ambiente, **filtradas §5.1** aos
   símbolos que as estratégias informadas têm em `orders`/`fills` (atribuição
   aproximada por estratégia; posições da venue não têm `strategy_id`). Cache de
   15s por network (espelha o padrão do `/balance`). Sem símbolos ⇒ `[]`.
2. **`Position` dataclass** (`engine/exchanges/base.py`) ganhou `liquidation_px`
   e `position_value` (opcionais); preenchidos no adapter da Hyperliquid a partir
   de `positionValue`/`liquidationPx` do raw. `paper` mantém `None`.
3. **Web** (`web/`): novo `PositionsTable.tsx`, novo `TradesOrdersTable.tsx`;
   removidos `OrdersTable.tsx` e `FillsTable.tsx`; `page.tsx` reordenado;
   `lib/copy-trade/data.ts` ganhou tipo `Position` + `getPositions()`.

### Ações do Hermes

1. **Obrigatório:** deploy na VPS (gateway + rebuild do web):
   ```bash
   cd /home/tokio/Tokio
   git pull --ff-only origin main
   sudo systemctl restart tokio-engine.service tokio.service
   cd web && npm ci && npm run build && sudo systemctl restart tokio-web.service
   ```
   (ajuste os nomes dos services/comando de build do web ao seu runbook.)
2. Confirmar que a dashboard de Copy Trade mostra as 4 seções na ordem
   KPIs → Posições → Trades e Ordens em Aberto → Traders.

### Validação esperada

- `python -m pytest tests/test_gateway.py -q` verde (2 testes novos:
  `test_api_positions_scoped_to_strategy_symbols`,
  `test_api_positions_requires_strategy_id`).
- `cd web && npx tsc --noEmit` sem erros.
