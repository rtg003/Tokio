-- 0019_tv_executor — módulo TV-Executor (Trading View). Migração ADITIVA.
-- PROMPT-TV-EXECUTOR-v1.4.2 §8.5. Reusa `strategies` (module='tradingview'),
-- nunca duplica cadastro: os campos específicos do TV vivem na satélite
-- `tv_strategy_meta` (§12.2.2, decisão travada no EXECUTION_PLAN). A config
-- completa da estratégia (§6.1) continua em `strategies.config_snapshot`.
--
-- Isolamento (AGENTS.md §5.1): a view `tv_events` só expõe dados do módulo TV.
-- Kill switch (desvio deliberado, §12): reusa a fonte única EXISTENTE
-- (settings.kill_file / /control/kill / /health.kill_switch) — este schema NÃO
-- cria flag DB de kill switch.

-- ---------------------------------------------------------------------------
-- Cadastro satélite: campos TV que não cabem em strategies.
-- environment é a FONTE DE VERDADE do ambiente de execução (§5.3): NUNCA vem do
-- payload nem do seletor global de UI.
CREATE TABLE IF NOT EXISTS tv_strategy_meta (
    strategy_id     TEXT PRIMARY KEY REFERENCES strategies(id),
    environment     TEXT NOT NULL DEFAULT 'testnet'
                    CHECK (environment IN ('testnet', 'mainnet')),
    secret_hash     TEXT,                 -- sha256 do secret do PAYLOAD (por estratégia)
    url_secret_hash TEXT,                 -- sha256 do secret no PATH da URL do webhook
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_tv_meta_env ON tv_strategy_meta(environment);

-- Auditoria versionada de cada alteração de estratégia (humana ou Hermes).
-- Alimenta a expansão HERMES/USER da view tv_events (§8.5, §9).
CREATE TABLE IF NOT EXISTS tv_strategy_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id    TEXT NOT NULL REFERENCES strategies(id),
    version        INTEGER NOT NULL,
    config         TEXT,                  -- json: snapshot completo do rules.json na versão
    changed_by     TEXT NOT NULL,         -- 'hermes' | endereço/ator humano | 'system'
    change_summary TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (strategy_id, version)
);
CREATE INDEX IF NOT EXISTS idx_tv_versions_strategy
    ON tv_strategy_versions(strategy_id, version);

-- Sinais recebidos (TradingView, Hermes, manual, teste). raw_payload é
-- persistido ANTES do parsing (§8.1). signal_key = idempotência (§5.3, TTL 24h
-- imposto na aplicação; a UNIQUE garante que o replay não insira duplicata).
CREATE TABLE IF NOT EXISTS tv_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_key  TEXT UNIQUE,              -- sha256(strategy_id+alert_id+bar_time+action+market_position)
    source      TEXT NOT NULL
                CHECK (source IN ('tradingview', 'hermes', 'manual', 'test')),
    strategy_id TEXT,                     -- NULL até resolver; pode ser desconhecido (rejeitado)
    environment TEXT CHECK (environment IN ('testnet', 'mainnet')),
    raw_payload TEXT NOT NULL,            -- json cru (persistido antes do parse)
    parsed      TEXT,                     -- json normalizado
    state       TEXT NOT NULL DEFAULT 'RECEIVED'
                CHECK (state IN ('RECEIVED', 'DUPLICATE', 'REJECTED', 'VALIDATING',
                                 'BLOCKED', 'APPROVED', 'QUEUED', 'SUBMITTED',
                                 'FILLED', 'PARTIAL', 'PROTECTED', 'FAILED',
                                 'CLOSED', 'RECONCILED')),
    source_ip   TEXT,                     -- IP de origem logado por request (§8.1)
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_tv_signals_strategy ON tv_signals(strategy_id, received_at);
CREATE INDEX IF NOT EXISTS idx_tv_signals_state ON tv_signals(state, received_at);

-- Decisão do validator: checklist completo required-vs-actual (§8.2) sempre
-- persistido, mesmo quando passa. netting_plan preenchido só quando APPROVED.
CREATE TABLE IF NOT EXISTS tv_signal_decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id        INTEGER NOT NULL REFERENCES tv_signals(id),
    outcome          TEXT NOT NULL CHECK (outcome IN ('APPROVED', 'BLOCKED', 'DUPLICATE')),
    block_code       TEXT,                -- ex.: STRATEGY_DISABLED, SIGNAL_STALE, SPREAD_TOO_WIDE
    checks           TEXT,                -- json: array de {n, check, required, actual, result}
    netting_plan     TEXT,                -- json: plano de intenções (§8.3)
    computed_size_usd REAL,               -- sizing calculado no servidor (§6.3)
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_tv_decisions_signal ON tv_signal_decisions(signal_id);

-- Ticker do TradingView → coin da Hyperliquid (§4 fato 9). Não mapeado = rejeição.
CREATE TABLE IF NOT EXISTS tv_symbol_map (
    tv_ticker TEXT PRIMARY KEY,
    hl_coin   TEXT NOT NULL,
    enabled   INTEGER NOT NULL DEFAULT 1  -- 0/1
);

-- Incidentes do módulo (posição sem stop, divergência de reconciliação, etc.).
CREATE TABLE IF NOT EXISTS tv_incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   INTEGER REFERENCES tv_signals(id),  -- NULL para incidentes de sistema
    type        TEXT NOT NULL,           -- ex.: INCIDENT_UNPROTECTED_POSITION, RECON_DIVERGENCE
    details     TEXT,                    -- json
    resolved    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tv_incidents_resolved ON tv_incidents(resolved, created_at);

-- Fila persistente (SQLite WAL + worker; sem Redis — §12.1). O worker consome
-- pendentes em ordem e atualiza status/attempts.
CREATE TABLE IF NOT EXISTS tv_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id  INTEGER NOT NULL REFERENCES tv_signals(id),
    status     TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_tv_queue_status ON tv_queue(status, created_at);

-- ---------------------------------------------------------------------------
-- VIEW tv_strategies: cadastro TV = strategies (module='tradingview') ⋈ meta.
-- É a fonte de verdade da lista operacional (§4 fato 10).
DROP VIEW IF EXISTS tv_strategies;
CREATE VIEW tv_strategies AS
SELECT
    s.id              AS strategy_id,
    s.name            AS name,
    s.status          AS status,
    s.config_snapshot AS config_snapshot,
    s.thresholds      AS thresholds,
    s.created_at      AS created_at,
    s.archived_at     AS archived_at,
    m.environment     AS environment,
    m.secret_hash     AS secret_hash,
    m.url_secret_hash AS url_secret_hash,
    m.version         AS version,
    m.updated_at      AS meta_updated_at
FROM strategies s
JOIN tv_strategy_meta m ON m.strategy_id = s.id
WHERE s.module = 'tradingview';

-- VIEW tv_events (§3.7 do design, §8.5 do prompt): fusão minimalista de
-- sinais/decisões ∪ incidentes ∪ auditoria (versões) ∪ eventos de sistema do
-- módulo. Colunas: ts, kind, severity, summary, ref_id, detail. ISOLAMENTO: só
-- dados do módulo TV — SYSTEM restrito a event_type 'tv.%' ou strategy_id TV.
DROP VIEW IF EXISTS tv_events;
CREATE VIEW tv_events AS
    -- SIGNAL: cada sinal com o desfecho da sua decisão.
    SELECT
        sig.received_at AS ts,
        'SIGNAL'        AS kind,
        CASE
            WHEN d.outcome = 'APPROVED'  THEN 'pos'
            WHEN d.outcome = 'DUPLICATE' THEN 'faint'
            WHEN d.outcome = 'BLOCKED'   THEN 'neg'
            ELSE 'info'
        END AS severity,
        sig.source || ' · ' || COALESCE(sig.strategy_id, '?') || ' · '
                   || COALESCE(d.block_code, d.outcome, sig.state) AS summary,
        CAST(sig.id AS TEXT) AS ref_id,
        json_object('state', sig.state, 'environment', sig.environment,
                    'outcome', d.outcome, 'block_code', d.block_code,
                    'checks', d.checks, 'netting_plan', d.netting_plan,
                    'computed_size_usd', d.computed_size_usd) AS detail
    FROM tv_signals sig
    LEFT JOIN tv_signal_decisions d ON d.signal_id = sig.id

    UNION ALL

    -- INCIDENT
    SELECT
        inc.created_at AS ts,
        'INCIDENT'     AS kind,
        'neg'          AS severity,
        inc.type       AS summary,
        CAST(inc.id AS TEXT) AS ref_id,
        json_object('details', inc.details, 'resolved', inc.resolved,
                    'signal_id', inc.signal_id, 'resolved_at', inc.resolved_at) AS detail
    FROM tv_incidents inc

    UNION ALL

    -- HERMES / USER: auditoria de alterações de estratégia (diff + bump de versão).
    SELECT
        v.created_at AS ts,
        CASE WHEN v.changed_by = 'hermes' THEN 'HERMES' ELSE 'USER' END AS kind,
        CASE WHEN v.changed_by = 'hermes' THEN 'amber' ELSE 'info' END AS severity,
        v.strategy_id || ' v' || v.version
                      || COALESCE(' — ' || v.change_summary, '') AS summary,
        v.strategy_id AS ref_id,
        json_object('version', v.version, 'changed_by', v.changed_by,
                    'change_summary', v.change_summary, 'config', v.config) AS detail
    FROM tv_strategy_versions v

    UNION ALL

    -- SYSTEM: eventos do módulo (escopo TV apenas — isolamento §5.1).
    SELECT
        e.ts          AS ts,
        'SYSTEM'      AS kind,
        e.level       AS severity,
        e.event_type  AS summary,
        CAST(e.id AS TEXT) AS ref_id,
        e.payload     AS detail
    FROM events e
    WHERE e.event_type LIKE 'tv.%'
       OR e.strategy_id IN (SELECT strategy_id FROM tv_strategy_meta);
