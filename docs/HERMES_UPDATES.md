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

## UPDATE-0006 · 2026-07-03 · Status: PENDENTE

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

## UPDATE-0007 · 2026-07-03 · Status: PENDENTE

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
