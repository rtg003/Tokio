-- 0027_circuit_breaker_state — estado do circuit breaker por (wallet, ambiente).
-- Migração ADITIVA (tabela nova; nenhuma linha existente é tocada).
--
-- O breaker deixou de ser um booleano global (que pausava TODAS as estratégias e
-- violava o isolamento de wallet, AGENTS.md §5.1/§5.2) e passou a ser escopado
-- por (wallet, ambiente): uma perda em 0x4124/testnet NUNCA pausa 0xd2c7/mainnet.
--
-- A tabela é NECESSÁRIA (não dá pra recomputar de `fills`) para:
--   (i)   sobreviver a restart antes do 1º fill do dia (open persistido);
--   (ii)  `opened_at` p/ a UI e auditoria;
--   (iii) IDEMPOTÊNCIA DO RESET — `acknowledged_day` guarda que o operador já
--         limpou este escopo hoje, impedindo o breaker de reabrir no MESMO dia
--         UTC com o próximo fill perdedor (decisão: "reconhecer até o rollover").
--
-- Chave por (wallet, environment, day): um registro por escopo por dia UTC.
CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    wallet           TEXT NOT NULL,
    environment      TEXT NOT NULL CHECK (environment IN ('testnet', 'mainnet')),
    day              TEXT NOT NULL,                 -- YYYY-MM-DD (UTC)
    open             INTEGER NOT NULL DEFAULT 0,    -- 1 = breaker aberto (escopo pausado)
    opened_at        TEXT,                          -- ISO-8601 UTC do momento da abertura
    net_pnl          REAL,                          -- perda agregada que disparou (só reais)
    cap              REAL,                          -- cap aplicado (max_daily_loss_usd)
    acknowledged_day TEXT,                          -- =day quando o operador limpou hoje
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (wallet, environment, day)
);

CREATE INDEX IF NOT EXISTS idx_cb_state_day ON circuit_breaker_state (day);
