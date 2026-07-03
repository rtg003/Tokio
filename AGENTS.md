# AGENTS.md — protocolo bilateral de coordenação Cursor ⇄ Hermes (ADR 0009)

> **LEIA E EXECUTE NO INÍCIO DE TODA SESSÃO.** Este repositório é trabalhado
> por DOIS agentes em paralelo: o **Cursor** (CONSTRUTOR — código,
> arquitetura, schema) e o **Hermes** (OPERADOR — produção, skill, crons).
> Sessões de agente NÃO têm memória entre si; este arquivo é o contrato
> persistente de coordenação. O ritual da seção 2 é a **primeira ação de
> qualquer sessão** que vá alterar algo.
>
> **LIMITE INVIOLÁVEL**: nada neste protocolo — inbox, PR, review ou
> desempate — autoriza violar gates ou caps (Gate 2 de traders, promoção
> dry_run→active, mainnet, caps de risco; ver `docs/HANDOFF_HERMES.md` §7).

## 1. Inboxes (um por agente; cada um ESCREVE no do OUTRO)

| Arquivo | Direção | Quem escreve | Quem aplica e marca APLICADO |
|---|---|---|---|
| `docs/HERMES_UPDATES.md` | Cursor → Hermes | Cursor | Hermes |
| `docs/CURSOR_UPDATES.md` | Hermes → Cursor | Hermes | Cursor |

Formato comum aos dois: entradas `UPDATE-NNNN` sequenciais, **append-only**
(entradas publicadas nunca são editadas nem renumeradas), com
`Status: PENDENTE → APLICADO em <data>` como única edição permitida, feita
pelo destinatário após executar as ações e passar na validação da entrada.
Entradas NUNCA autorizam violar gates/caps.

## 2. Ritual pré-alteração (OBRIGATÓRIO, sem exceção)

Antes de INICIAR qualquer mudança em código/config/lógica:

1. **Sincronizar**: `git fetch origin main` + pull da `main`.
2. **Ler o próprio inbox** e aplicar/ackar as entradas `PENDENTE` ANTES de
   qualquer trabalho novo (Cursor lê `docs/CURSOR_UPDATES.md`; Hermes lê
   `docs/HERMES_UPDATES.md`).
3. **Listar PRs abertos**: `gh pr list --state open`. Se a área que você vai
   tocar sobrepõe um PR aberto do OUTRO agente: **NÃO inicie** — comente no
   PR dele ou escreva entrada no inbox dele, e aguarde.
4. **Trabalhar em branch e abrir DRAFT PR imediatamente ao começar** — o
   draft PR é a trava de área visível para o outro agente.

## 3. Regra do mesmo PR (bilateral)

- PR do **Cursor** cujo merge exija ação, conhecimento novo ou mudança de
  comportamento do Hermes → entrada em `docs/HERMES_UPDATES.md` **no mesmo
  PR**.
- PR do **Hermes** que afete o Cursor (mudança de código/config/convenção
  que sessões futuras do construtor precisam conhecer) → entrada em
  `docs/CURSOR_UPDATES.md` **no mesmo PR**.
- Em review, cada agente **EXIGE** do outro a entrada faltante: PR aplicável
  sem entrada = PR incompleto — não aprovar até a entrada existir.

## 4. Desempate de área

| Área | Prioridade |
|---|---|
| Código do engine/web, arquitetura, schema/migrations | **Cursor** |
| Config operacional, `skill/`, crons, rotina de produção | **Hermes** |
| Conflito genuíno (a mesma mudança disputada pelos dois) | **Ambos PARAM e notificam o humano (rtg003)** |

Prioridade define quem conduz e quem revisa — não dispensa o ritual da
seção 2 nem a regra do mesmo PR da seção 3.

## 5. Persistência deste protocolo

- Registrado como decisão em
  `docs/decisions/0009-protocolo-bilateral-cursor-hermes.md`.
- Referenciado no `README.md` e embutido no checklist de PR
  (`.github/PULL_REQUEST_TEMPLATE.md`).
- Sessões do Cursor carregam este `AGENTS.md` automaticamente: execute o
  ritual da seção 2 como primeira ação. O Hermes tem o mesmo dever via
  `docs/HANDOFF_HERMES.md` §8 e via a entrada correspondente em
  `docs/HERMES_UPDATES.md`.
