-- 0031_hl_agents_standby — status 'standby' p/ troca REVERSÍVEL de executor.
-- UPDATE-0083: a master wallet do topo do dashboard passa a TROCAR o executor
-- do ambiente (ativa o agente provisionado da wallet + reload do adapter). Ao
-- trocar, o agente `active` anterior vira `standby` (NÃO `revoked`): a aprovação
-- on-chain do agente persiste, então dá p/ voltar a ele depois SEM nova
-- assinatura. `standby` fica FORA do índice único (env,active|expiring), então
-- não ocupa o slot de executor e é ignorado por resolve_active_key/boot.
--
-- SQLite não faz ALTER de CHECK: rebuild da tabela preservando os dados. Sem
-- foreign keys apontando p/ hl_agents (rebuild seguro).

CREATE TABLE hl_agents_new (
  id             TEXT PRIMARY KEY,
  env            TEXT NOT NULL CHECK (env IN ('testnet','mainnet')),
  master_address TEXT NOT NULL,
  agent_address  TEXT NOT NULL UNIQUE,
  agent_name     TEXT NOT NULL DEFAULT 'engine_gateway',
  privkey_enc    TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','active','standby','expiring','revoked','expired')),
  approved_at    TEXT,
  valid_until    TEXT,
  revoked_at     TEXT,
  created_at     TEXT NOT NULL
);

INSERT INTO hl_agents_new (id, env, master_address, agent_address, agent_name,
  privkey_enc, status, approved_at, valid_until, revoked_at, created_at)
SELECT id, env, master_address, agent_address, agent_name,
  privkey_enc, status, approved_at, valid_until, revoked_at, created_at
FROM hl_agents;

DROP TABLE hl_agents;
ALTER TABLE hl_agents_new RENAME TO hl_agents;

-- No máximo 1 agente vivo (active|expiring) por ambiente — standby NÃO conta.
CREATE UNIQUE INDEX idx_hl_agents_active
  ON hl_agents (env) WHERE status IN ('active','expiring');

CREATE INDEX idx_hl_agents_env_status ON hl_agents (env, status);
