-- 0014_hl_agents — keyring cifrado de agent wallets HL + trilha de auditoria.
-- SPEC hl-auth v2.0 §7. As agent keys deixam de viver em texto puro no .env
-- (HL_AGENT_PRIVATE_KEY / HL_MAINNET_AGENT_PRIVATE_KEY) e passam a viver
-- cifradas (AES-256-GCM) em hl_agents.privkey_enc. O gateway resolve a chave
-- de cada ambiente na ordem: hl_agents (status=active) → fallback .env (D3).
--
-- master_address é o endereço da MetaMask que assinou o approveAgent — vira o
-- account_address do adapter daquele ambiente (REQUISITO rtg003: logar com uma
-- wallet nova E operar naquela conta). privkey_enc NUNCA aparece em resposta de
-- endpoint nem em log (regra do repo: segredos nunca logados — config.py).

CREATE TABLE hl_agents (
  id             TEXT PRIMARY KEY,                 -- uuid4 gerado na aplicação
  env            TEXT NOT NULL CHECK (env IN ('testnet','mainnet')),
  master_address TEXT NOT NULL,
  agent_address  TEXT NOT NULL UNIQUE,             -- nunca reutilizado (ADR 0001)
  agent_name     TEXT NOT NULL DEFAULT 'engine_gateway',
  privkey_enc    TEXT NOT NULL,                    -- base64(iv || ciphertext+tag)
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','active','expiring','revoked','expired')),
  approved_at    TEXT,                             -- ISO-8601 UTC (utcnow() do repo)
  valid_until    TEXT,
  revoked_at     TEXT,
  created_at     TEXT NOT NULL
);

-- No máximo 1 agente vivo (active|expiring) por ambiente.
CREATE UNIQUE INDEX idx_hl_agents_active
  ON hl_agents (env) WHERE status IN ('active','expiring');

CREATE INDEX idx_hl_agents_env_status ON hl_agents (env, status);

CREATE TABLE hl_auth_audit (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  at     TEXT NOT NULL,
  actor  TEXT NOT NULL,          -- address SIWE ou 'password_session'/'control_api'
  action TEXT NOT NULL,          -- siwe_login|agent_prepare|agent_activate|agent_revoke|agent_expiring|adapter_reload
  env    TEXT CHECK (env IN ('testnet','mainnet')),
  detail TEXT                    -- JSON (sem chaves, sem assinaturas)
);

CREATE INDEX idx_hl_auth_audit_at ON hl_auth_audit (at);
