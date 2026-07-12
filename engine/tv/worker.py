"""Worker da fila (SQLite WAL) — consome `tv_queue`, valida e persiste (§8.1/§8.2).

F0: SEM execução. O worker monta o `ValidatorContext` (DB + gateway read-only),
roda o validator determinístico e persiste a decisão com o checklist completo.
Logs JSON correlacionados por `signal_id` (§8.6). A execução real (enviar ao
gateway) entra na F1.

Fonte única do kill switch (decisão travada): lê `/health.kill_switch`; se o
gateway estiver mudo, cai para o arquivo sentinela (mesma fonte, file-based) —
nunca falha ABERTO.
"""
from __future__ import annotations

import json
import time
from typing import Any

from engine.core.config import Settings, get_settings
from engine.core.db import Database
from engine.core.logger import EventLogger
from engine.strategies.base_runner import GatewayClient
from engine.tv import store
from engine.tv.models import ParsedSignal, SchemaError, parse_signal
from engine.tv.validator import Decision, ValidatorContext, validate

POLL_INTERVAL_S = 0.5


def _mid_price(gw: GatewayClient, coin: str, environment: str,
               logger: EventLogger | None) -> float | None:
    """mid do ambiente da estratégia. Falha/0.0 ⇒ None (MARKET_DATA_UNAVAILABLE)."""
    try:
        meta = gw.market_meta(coin, environment=environment)
    except Exception as exc:  # noqa: BLE001 — hiccup do venue não pode matar o worker
        if logger:
            logger.warning("tv.market_data_error", {"coin": coin, "error": str(exc)[:200]})
        return None
    if not meta.get("ok"):
        return None
    mid = meta.get("mid")
    try:
        mid = float(mid)
    except (TypeError, ValueError):
        return None
    return mid if mid > 0 else None


def _kill_switch(gw: GatewayClient, settings: Settings) -> bool:
    """Fonte única. Preferir /health; fallback = arquivo sentinela (fail-closed)."""
    try:
        health = gw.health()
        return bool(health.get("kill_switch", False))
    except Exception:  # noqa: BLE001
        return settings.kill_file.exists()


def build_context(db: Database, gw: GatewayClient, settings: Settings,
                  sig: ParsedSignal, cfg: Any, *, coin: str | None,
                  symbol_enabled: bool | None, duplicate: bool,
                  logger: EventLogger | None = None) -> ValidatorContext:
    """Reúne TODO o estado que o validator (puro) consome."""
    ctx = ValidatorContext(
        now_epoch=time.time(),
        kill_switch=_kill_switch(gw, settings),
        duplicate=duplicate,
        coin=coin,
        symbol_enabled=symbol_enabled,
    )
    # Sem estratégia/coin/ambiente resolvidos não há o que buscar de mercado/ledger.
    if cfg is None or coin is None:
        return ctx
    env = cfg.environment
    ctx.mid = _mid_price(gw, coin, env, logger)
    ctx.bbo = None  # F1: novo método bbo(symbol) no adapter (§8.4.1). F0 ⇒ skipped.
    ctx.trades_today = store.trades_today(db, cfg.strategy_id)
    ctx.daily_loss_usd = store.module_daily_loss(db, env)
    cooldown = float(cfg.risk_rules.get("cooldown_minutes_after_loss", 0) or 0)
    ctx.in_cooldown = store.in_cooldown(db, cfg.strategy_id, cooldown)
    ctx.symbol_locked_by = store.symbol_lock_holder(
        db, coin=coin, environment=env, exclude_strategy=cfg.strategy_id)
    ctx.position_size = store.position_proxy(
        db, strategy_id=cfg.strategy_id, coin=coin, environment=env)
    return ctx


def process_signal(db: Database, gw: GatewayClient, settings: Settings,
                   signal_row: dict[str, Any],
                   logger: EventLogger | None = None) -> Decision:
    """Valida um sinal já persistido (RECEIVED). Persiste decisão + move estado."""
    signal_id = signal_row["id"]
    started = time.time()

    # Parse estrito (§5.3). Falha ⇒ REJECTED · SCHEMA_INVALID (fora do checklist).
    try:
        raw = json.loads(signal_row["raw_payload"])
    except (json.JSONDecodeError, TypeError):
        return _reject(db, signal_id, "SCHEMA_INVALID", "raw_payload não é JSON", logger)
    try:
        sig = parse_signal(raw, source=signal_row.get("source") or "tradingview")
    except SchemaError as exc:
        return _reject(db, signal_id, "SCHEMA_INVALID", str(exc), logger,
                       field=exc.field_name)

    cfg = store.get_strategy(db, sig.strategy_id)

    # Secrets: o secret do path (URL) já foi validado SÍNCRONO no receiver — só
    # sinais autenticados chegam à fila (T1). O worker confia nessa fronteira
    # (url_secret_ok=True) e re-valida o secret do PAYLOAD como defesa em
    # profundidade. Sinais internos (hermes/manual/test) usam token interno.
    url_secret_ok = True
    internal = sig.source in ("hermes", "manual", "test")
    if internal or cfg is None:
        payload_secret_ok = True
    else:
        payload_secret_ok = bool(cfg.secret_hash) and sig.secret is not None \
            and store.sha256_hex(sig.secret) == cfg.secret_hash

    # Símbolo (resolvido cedo p/ gravar coin em parsed → symbol-lock/posição).
    mapped = store.lookup_symbol(db, sig.ticker)
    coin = mapped[0] if mapped else None
    symbol_enabled = mapped[1] if mapped else None

    duplicate = store.is_duplicate(db, sig.signal_key, exclude_id=signal_id)

    ctx = build_context(db, gw, settings, sig, cfg, coin=coin,
                        symbol_enabled=symbol_enabled, duplicate=duplicate,
                        logger=logger)
    decision = validate(sig, cfg, ctx, url_secret_ok=url_secret_ok,
                        payload_secret_ok=payload_secret_ok)

    # Persistir: parsed (com coin resolvida), strategy_id, ambiente, signal_key.
    parsed_json = {**sig.redacted(), "coin": coin,
                   "signal_key": sig.signal_key}
    fields: dict[str, Any] = {
        "strategy_id": sig.strategy_id,
        "environment": cfg.environment if cfg else None,
        "parsed": json.dumps(parsed_json, ensure_ascii=False),
    }
    # signal_key só é fixado no PRIMEIRO sinal (UNIQUE); duplicata fica sem key.
    if not duplicate:
        fields["signal_key"] = sig.signal_key
    store.update_signal(db, signal_id, **fields)
    store.record_decision(db, signal_id, decision)

    if logger:
        logger.info("tv.signal_decided", {
            "signal_id": signal_id, "strategy_id": sig.strategy_id,
            "source": sig.source, "outcome": decision.outcome,
            "block_code": decision.block_code, "coin": coin,
            "environment": cfg.environment if cfg else None,
        }, strategy_id=sig.strategy_id,
            latency_ms=(time.time() - started) * 1000.0)
    return decision


def _reject(db: Database, signal_id: int, code: str, detail: str,
            logger: EventLogger | None, field: str | None = None) -> Decision:
    checks = [{"n": 1, "check": "schema_and_secrets",
               "required": {"schema_valid": True},
               "actual": {"schema_valid": False, "field": field, "detail": detail},
               "result": "fail"}]
    decision = Decision(outcome="BLOCKED", block_code=code, checks=checks)
    store.record_decision(db, signal_id, decision)
    store.update_signal(db, signal_id, state="REJECTED")
    if logger:
        logger.warning("tv.signal_rejected",
                       {"signal_id": signal_id, "code": code, "field": field})
    return decision


def run_once(db: Database, gw: GatewayClient, settings: Settings,
             logger: EventLogger | None = None) -> bool:
    """Processa UM item da fila. Retorna True se havia trabalho."""
    item = store.dequeue_next(db)
    if item is None:
        return False
    try:
        rows = db.query("SELECT * FROM tv_signals WHERE id = ?", (item["signal_id"],))
        if not rows:
            store.finish_queue(db, item["id"], "failed", "signal não encontrado")
            return True
        process_signal(db, gw, settings, rows[0], logger)
        store.finish_queue(db, item["id"], "done")
    except Exception as exc:  # noqa: BLE001 — um sinal ruim não derruba a fila
        store.finish_queue(db, item["id"], "failed", str(exc)[:500])
        if logger:
            logger.error("tv.worker_error",
                         {"queue_id": item["id"], "error": str(exc)[:500]})
    return True


def main() -> None:  # pragma: no cover — entrypoint do supervisor
    settings = get_settings()
    db = Database(settings.sqlite_path)
    db.migrate()
    logger = EventLogger("tv-worker", settings.logs_dir, db=db)
    gw = GatewayClient()
    logger.info("tv.worker_started", {})
    while True:
        try:
            worked = run_once(db, gw, settings, logger)
        except Exception as exc:  # noqa: BLE001
            logger.error("tv.worker_loop_error", {"error": str(exc)[:500]})
            worked = False
        if not worked:
            time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":  # pragma: no cover
    main()
