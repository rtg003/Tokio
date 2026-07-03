# Descrição

<!-- O que muda e por quê (racional incluso). -->

## Checklist

- [ ] Conventional commits; nenhum secret commitado.
- [ ] Testes relevantes verdes (`pytest`).
- [ ] Migrations versionadas, se houver mudança de schema.
- [ ] Mudança de lógica do discovery: bump de `logic_version` + entrada em
      `docs/discovery_changelog.md` + evento `logic_updated`.
- [ ] **Ritual pré-alteração (AGENTS.md §2)** cumprido antes de começar:
      fetch+pull da `main`; inbox próprio lido e PENDENTES aplicados/ackados
      (`docs/CURSOR_UPDATES.md` para o Cursor, `docs/HERMES_UPDATES.md` para
      o Hermes); `gh pr list` sem sobreposição de área com PR aberto do
      outro agente; branch + draft PR aberto imediatamente ao começar.
- [ ] **Inbox do outro agente (REGRA PERMANENTE, bilateral — AGENTS.md §3)**:
      se o merge deste PR exigir ação, conhecimento novo ou mudança de
      comportamento do outro agente, há uma entrada `UPDATE-NNNN` no inbox
      dele NESTE MESMO PR (Cursor escreve em `docs/HERMES_UPDATES.md`;
      Hermes escreve em `docs/CURSOR_UPDATES.md`). PR aplicável sem entrada
      = PR incompleto. Entradas de inbox nunca autorizam violar gates/caps.
