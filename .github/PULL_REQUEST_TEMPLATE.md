# Descrição

<!-- O que muda e por quê (racional incluso). -->

## Checklist

- [ ] Conventional commits; nenhum secret commitado.
- [ ] Testes relevantes verdes (`pytest`).
- [ ] Migrations versionadas, se houver mudança de schema.
- [ ] Mudança de lógica do discovery: bump de `logic_version` + entrada em
      `docs/discovery_changelog.md` + evento `logic_updated`.
- [ ] **Inbox do operador (REGRA PERMANENTE)**: se o merge deste PR exigir
      ação, conhecimento novo ou mudança de comportamento do operador
      (Hermes), há uma entrada `UPDATE-NNNN` em `docs/HERMES_UPDATES.md`
      NESTE MESMO PR. PR aplicável sem entrada = PR incompleto.
      Entradas do inbox nunca autorizam violar gates/caps.
