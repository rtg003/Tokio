# HERMES_UPDATES — inbox de atualizações para o operador (Hermes)

> Canal formal CONSTRUTOR → OPERADOR. Arquivo **append-only**: entradas são
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

## UPDATE-0001 · 2026-07-03 · Status: PENDENTE

**Origem**: PRs #4 e #5 (merged) + este PR (que instaura o protocolo do inbox)

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
