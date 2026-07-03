# ADR 0009 — Protocolo bilateral de coordenação Cursor ⇄ Hermes

- Status: aceito
- Data: 2026-07-03
- Estende: protocolo unilateral do inbox `docs/HERMES_UPDATES.md` (PR #6)

## Contexto

O repositório passou a ser trabalhado por dois agentes em paralelo: o
**Cursor** (construtor — código, arquitetura, schema) e o **Hermes**
(operador — produção, skill, crons), ambos abrindo PRs. Sessões de agente
não têm memória entre si. Sem um contrato persistente, os riscos são:
trabalho sobreposto na mesma área (PRs conflitantes), mudanças de um agente
"corrigidas" de volta pelo outro por desconhecer o racional, e conhecimento
operacional perdido entre sessões. O PR #6 criou o canal Cursor → Hermes;
faltava o canal reverso e as regras de concorrência.

## Decisão

1. **Inboxes bilaterais** (um por agente; cada um escreve no do OUTRO):
   `docs/HERMES_UPDATES.md` (Cursor → Hermes, já existente) e
   `docs/CURSOR_UPDATES.md` (Hermes → Cursor, novo). Mesmo formato:
   `UPDATE-NNNN` sequencial, append-only, `Status: PENDENTE → APLICADO`
   como única edição permitida (pelo destinatário), e o mesmo limite:
   entradas nunca autorizam violar gates/caps.
2. **Ritual pré-alteração obrigatório** antes de iniciar qualquer mudança:
   (a) fetch + pull da `main`; (b) ler o próprio inbox e aplicar/ackar
   PENDENTES antes de trabalho novo; (c) `gh pr list` — área sobreposta a PR
   aberto do outro agente bloqueia o início (comentar/inboxar e aguardar);
   (d) branch + **draft PR imediato** como trava de área visível.
3. **Regra do mesmo PR, agora bilateral**: PR que exija ação/conhecimento do
   outro agente inclui entrada no inbox dele no mesmo PR; em review, o
   destinatário exige a entrada faltante (PR aplicável sem entrada =
   incompleto).
4. **Desempate de área**: código/arquitetura/schema = prioridade do Cursor;
   config operacional/skill/cron = prioridade do Hermes; conflito genuíno =
   ambos param e notificam o humano (rtg003).
5. **Persistência**: o protocolo integral vive em `AGENTS.md` na raiz
   (carregado automaticamente por toda sessão do Cursor), é referenciado no
   `README.md`, no checklist de PR e no `docs/HANDOFF_HERMES.md` §8.

## Consequências

- Coordenação assíncrona sem canal em tempo real: a `main`, os inboxes e os
  draft PRs são o estado compartilhado; o ritual garante que todo agente
  parte dele.
- Custo pequeno por sessão (fetch + leitura de inbox + `pr list`) em troca
  de eliminar retrabalho e regressões de racional.
- Os gates humanos permanecem intocados: o protocolo coordena os agentes
  entre si, nunca substitui aprovação do humano.
