"""Persistência do módulo TV + montagem do `ValidatorContext` (§8.5).

Toda query filtra por módulo TV + ambiente (isolamento §5.1). O ledger do módulo
(perda diária, trades/dia, cooldown) vem dos NOSSOS fills atribuídos a
estratégias TV no ambiente — NUNCA de stub de adapter (§6.4.4, T7). A posição e o
symbol-lock em F0 derivam do estado dos `tv_signals` (sem execução); a F1
cruza com as posições reais do gateway.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.core.db import Database, utcnow
from engine.tv.models import StrategyConfig
from engine.tv.validator import Decision, ValidatorContext

# Estados em que um sinal "segura" o símbolo (posição aberta ou intenção em voo).
HOLDING_STATES = ("QUEUED", "SUBMITTED", "FILLED", "PARTIAL", "PROTECTED")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# -- ingestão -----------------------------------------------------------------
def persist_raw(db: Database, *, source: str, raw_payload: str,
                source_ip: str | None) -> int:
    """Persiste o sinal cru ANTES do parse (§8.1). Estado inicial RECEIVED.

    O secret do path (URL) é validado SÍNCRONO no receiver (401 em sinal
    forjado, T1); só sinais autenticados chegam à fila, então não é preciso
    guardá-lo por sinal — o worker confia na fronteira do receiver."""
    return db.insert("tv_signals", {
        "source": source,
        "raw_payload": raw_payload,
        "source_ip": source_ip,
        "state": "RECEIVED",
        "received_at": utcnow(),
    })


def enqueue(db: Database, signal_id: int) -> int:
    return db.insert("tv_queue", {"signal_id": signal_id, "status": "pending",
                                  "created_at": utcnow(), "updated_at": utcnow()})


def dequeue_next(db: Database) -> dict[str, Any] | None:
    """Reserva o próximo pendente (pending→processing) de forma atômica."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM tv_queue WHERE status = 'pending' "
            "ORDER BY created_at, id LIMIT 1").fetchall()
        if not rows:
            return None
        row = dict(rows[0])
        db._conn.execute(
            "UPDATE tv_queue SET status = 'processing', attempts = attempts + 1, "
            "updated_at = ? WHERE id = ?", (utcnow(), row["id"]))
        db._conn.commit()
        return row


def finish_queue(db: Database, queue_id: int, status: str,
                 last_error: str | None = None) -> None:
    db.execute("UPDATE tv_queue SET status = ?, last_error = ?, updated_at = ? "
               "WHERE id = ?", (status, last_error, utcnow(), queue_id))


# -- sinais / decisões --------------------------------------------------------
def update_signal(db: Database, signal_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE tv_signals SET {sets} WHERE id = ?",
               [*fields.values(), signal_id])


def is_duplicate(db: Database, signal_key: str, *, ttl_hours: int = 24,
                 exclude_id: int | None = None) -> bool:
    """Idempotência §5.3: mesmo signal_key dentro do TTL. A UNIQUE do schema é a
    rede final; esta checagem impõe a janela de 24h na aplicação."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat()
    rows = db.query(
        "SELECT id FROM tv_signals WHERE signal_key = ? AND received_at >= ? "
        "AND (? IS NULL OR id != ?)",
        (signal_key, cutoff, exclude_id, exclude_id))
    return len(rows) > 0


def record_decision(db: Database, signal_id: int, decision: Decision) -> int:
    """Persiste a decisão (checklist completo) e move o estado do sinal."""
    row = decision.as_row()
    row["signal_id"] = signal_id
    row["created_at"] = utcnow()
    decision_id = db.insert("tv_signal_decisions", row)
    state = {"APPROVED": "APPROVED", "BLOCKED": "BLOCKED",
             "DUPLICATE": "DUPLICATE"}.get(decision.outcome, "REJECTED")
    update_signal(db, signal_id, state=state)
    return decision_id


def record_incident(db: Database, *, incident_type: str,
                    details: dict[str, Any] | None = None,
                    signal_id: int | None = None) -> int:
    return db.insert("tv_incidents", {
        "signal_id": signal_id,
        "type": incident_type,
        "details": json.dumps(details or {}, ensure_ascii=False),
        "resolved": 0,
        "created_at": utcnow(),
    })


# -- cadastro / symbol map ----------------------------------------------------
def get_strategy(db: Database, strategy_id: str) -> StrategyConfig | None:
    rows = db.query("SELECT * FROM tv_strategies WHERE strategy_id = ?", (strategy_id,))
    return StrategyConfig.from_row(rows[0]) if rows else None


def create_strategy(db: Database, *, strategy_id: str, name: str, environment: str,
                    config: dict[str, Any], secret_hash: str, url_secret_hash: str,
                    changed_by: str, change_summary: str = "criação") -> None:
    """Cria a estratégia TV NASCENDO 'draft' (§4 passo 4: disabled-first ⇒ o
    sinal de teste bate STRATEGY_DISABLED, provando o pipeline com risco zero).
    Escreve `strategies` (módulo tradingview), a satélite `tv_strategy_meta` e a
    versão 1 na auditoria `tv_strategy_versions`. NÃO grava segredos em claro."""
    db.upsert("strategies", {
        "id": strategy_id, "module": "tradingview", "name": name, "status": "draft",
        "config_snapshot": json.dumps(config, ensure_ascii=False), "thresholds": "{}",
    }, ("id",))
    db.upsert("tv_strategy_meta", {
        "strategy_id": strategy_id, "environment": environment,
        "secret_hash": secret_hash, "url_secret_hash": url_secret_hash, "version": 1,
    }, ("strategy_id",))
    db.insert("tv_strategy_versions", {
        "strategy_id": strategy_id, "version": 1,
        "config": json.dumps(config, ensure_ascii=False),
        "changed_by": changed_by, "change_summary": change_summary,
        "created_at": utcnow(),
    })


def set_strategy_status(db: Database, strategy_id: str, status: str) -> None:
    db.execute("UPDATE strategies SET status = ? WHERE id = ? AND module = 'tradingview'",
               (status, strategy_id))


def latest_signal(db: Database, strategy_id: str) -> dict[str, Any] | None:
    """Último sinal (com desfecho da decisão) de uma estratégia — base do
    polling de handshake do wizard (§4 passo 4)."""
    rows = db.query(
        "SELECT s.id, s.source, s.state, s.received_at, d.outcome, d.block_code "
        "FROM tv_signals s LEFT JOIN tv_signal_decisions d ON d.signal_id = s.id "
        "WHERE s.strategy_id = ? ORDER BY s.received_at DESC, s.id DESC LIMIT 1",
        (strategy_id,))
    return dict(rows[0]) if rows else None


def lookup_symbol(db: Database, tv_ticker: str) -> tuple[str, bool] | None:
    rows = db.query("SELECT hl_coin, enabled FROM tv_symbol_map WHERE tv_ticker = ?",
                    (tv_ticker,))
    if not rows:
        return None
    return rows[0]["hl_coin"], bool(rows[0]["enabled"])


# -- ledger do módulo (isolado por ambiente) ----------------------------------
def _tv_strategy_ids(db: Database, environment: str,
                     exclude: str | None = None) -> list[str]:
    rows = db.query("SELECT strategy_id FROM tv_strategy_meta WHERE environment = ?",
                    (environment,))
    ids = [r["strategy_id"] for r in rows]
    return [i for i in ids if i != exclude] if exclude else ids


def _today_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def trades_today(db: Database, strategy_id: str) -> int:
    rows = db.query(
        "SELECT COUNT(*) AS n FROM fills WHERE strategy_id = ? AND ts >= ?",
        (strategy_id, _today_start_iso()))
    return int(rows[0]["n"]) if rows else 0


def module_daily_loss(db: Database, environment: str) -> float:
    """Perda líquida realizada HOJE do módulo no ambiente (magnitude ≥ 0).
    Fonte: NOSSOS fills atribuídos a estratégias TV do ambiente (§6.4.4)."""
    ids = _tv_strategy_ids(db, environment)
    if not ids:
        return 0.0
    placeholders = ",".join("?" * len(ids))
    rows = db.query(
        f"SELECT COALESCE(SUM(realized_pnl), 0) AS net FROM fills "
        f"WHERE strategy_id IN ({placeholders}) AND ts >= ? "
        f"AND realized_pnl IS NOT NULL",
        (*ids, _today_start_iso()))
    net = float(rows[0]["net"]) if rows else 0.0
    return -net if net < 0 else 0.0


def in_cooldown(db: Database, strategy_id: str, cooldown_minutes: float) -> bool:
    if cooldown_minutes <= 0:
        return False
    rows = db.query(
        "SELECT ts FROM fills WHERE strategy_id = ? AND realized_pnl < 0 "
        "ORDER BY ts DESC LIMIT 1", (strategy_id,))
    if not rows:
        return False
    try:
        last = datetime.fromisoformat(str(rows[0]["ts"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(minutes=cooldown_minutes)


def symbol_lock_holder(db: Database, *, coin: str, environment: str,
                       exclude_strategy: str) -> str | None:
    """Outra estratégia do MESMO ambiente segurando a coin (posição/intenção).
    F0: deriva do estado dos tv_signals (coin resolvida gravada em parsed)."""
    ids = _tv_strategy_ids(db, environment, exclude=exclude_strategy)
    if not ids:
        return None
    placeholders = ",".join("?" * len(ids))
    states = ",".join("?" * len(HOLDING_STATES))
    rows = db.query(
        f"SELECT strategy_id FROM tv_signals "
        f"WHERE strategy_id IN ({placeholders}) AND environment = ? "
        f"AND json_extract(parsed, '$.coin') = ? AND state IN ({states}) "
        f"ORDER BY received_at DESC LIMIT 1",
        (*ids, environment, coin, *HOLDING_STATES))
    return rows[0]["strategy_id"] if rows else None


def position_proxy(db: Database, *, strategy_id: str, coin: str,
                   environment: str) -> float | None:
    """Sinal (+long/-short/None) da posição atual da estratégia na coin. F0: do
    último sinal em estado de holding. Netting só usa o SINAL, não a magnitude."""
    states = ",".join("?" * len(HOLDING_STATES))
    rows = db.query(
        f"SELECT json_extract(parsed, '$.market_position') AS mp FROM tv_signals "
        f"WHERE strategy_id = ? AND environment = ? "
        f"AND json_extract(parsed, '$.coin') = ? AND state IN ({states}) "
        f"ORDER BY received_at DESC LIMIT 1",
        (strategy_id, environment, coin, *HOLDING_STATES))
    if not rows:
        return None
    mp = rows[0]["mp"]
    if mp == "long":
        return 1.0
    if mp == "short":
        return -1.0
    return None
