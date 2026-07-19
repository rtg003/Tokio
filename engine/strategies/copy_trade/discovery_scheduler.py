"""Scheduler do discovery — processo supervisionado (tokio-engine.service).

Duas responsabilidades:
1. **Primeira varredura**: se a tabela `traders` está vazia no start, roda um
   scan imediatamente (resolve o bootstrap sem passo manual na VPS).
2. **Varredura diária às 05:00 America/Sao_Paulo** (horário oficial do
   runbook — UPDATE-0001 §f). O agendamento vive NO ENGINE para não depender
   de crontab; o briefing/interpretação do resultado segue sendo do Hermes.

Falha de scan nunca derruba o processo: loga `discovery.scan_failed` e tenta
de novo no próximo horário. Read-only na corretora (o discovery só lê dados
públicos de mainnet).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from engine.core.config import get_settings
from engine.core.db import Database, utcnow
from engine.core.logger import EventLogger

SCAN_TZ = ZoneInfo("America/Sao_Paulo")
SCAN_HOUR = int(os.environ.get("DISCOVERY_SCAN_HOUR_SP", "5"))   # 05:00 SP
SCAN_TOP = int(os.environ.get("DISCOVERY_SCAN_TOP", "10"))
# UPDATE-0081: reclassificação automática de 2 em 2 horas — refresca TODAS as
# colunas dos traders NÃO-rejeitados (SALVO/TESTNET/MAINNET/SUGERIDO). Reusa o
# cache da HLDataClient (cache_ttl_hours ~20h) → barato o suficiente p/ rodar
# 12×/dia sob o teto de requisições do HyperTracker.
RECLASSIFY_INTERVAL_S = float(os.environ.get("DISCOVERY_RECLASSIFY_INTERVAL_S",
                                             str(2 * 3600)))
RECLASSIFY_STATUSES = ("TESTNET", "MAINNET", "SALVO", "SUGERIDO")


def next_scan_at(now: datetime) -> datetime:
    """Próximo disparo: hoje às SCAN_HOUR:00 SP, ou amanhã se já passou."""
    local = now.astimezone(SCAN_TZ)
    candidate = local.replace(hour=SCAN_HOUR, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def traders_table_empty(db: Database) -> bool:
    return db.query("SELECT COUNT(*) AS n FROM traders")[0]["n"] == 0


def logic_outdated(db: Database) -> bool:
    """True quando a tabela foi populada por uma logic_version anterior à do
    config — o bootstrap re-scaneia para re-upsertar na lógica nova."""
    from engine.strategies.copy_trade.funnel import load_config

    current = int(load_config()["logic_version"])
    rows = db.query("SELECT MAX(logic_version) AS v FROM traders")
    populated_with = rows[0]["v"] or 0
    return 0 < populated_with < current


def _score_weights_hash(cfg: dict[str, Any]) -> str:
    """Hash estável da config que decide o score (pesos + ajustes). Muda quando
    a régua muda → dispara reclassify (Parte 2 — AJUSTES 2026-07-11)."""
    payload = {
        "score_weights": cfg.get("score_weights", {}),
        "score_adjustments": cfg.get("score_adjustments", {}),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def reclassify_on_weight_change(db: Database, logger: EventLogger) -> bool:
    """Se o hash dos pesos/ajustes mudou desde o último startup, reclassifica
    TODOS os traders 1x e grava o novo hash em `discovery_meta`. True = rodou."""
    from engine.strategies.copy_trade.funnel import load_config, reclassify_all

    try:
        cfg = load_config()
        current = _score_weights_hash(cfg)
        rows = db.query(
            "SELECT value FROM discovery_meta WHERE key = 'score_weights_hash'")
        stored = rows[0]["value"] if rows else None
        if stored == current:
            return False
        if traders_table_empty(db):
            # nada a reclassificar ainda; só registra o hash p/ o próximo start.
            db.upsert("discovery_meta",
                      {"key": "score_weights_hash", "value": current,
                       "updated_at": utcnow()}, ("key",))
            return False
        summary = reclassify_all(db, cfg, logger=logger)
        db.upsert("discovery_meta",
                  {"key": "score_weights_hash", "value": current,
                   "updated_at": utcnow()}, ("key",))
        logger.info("discovery.reclassified_on_weight_change",
                    {"old_hash": stored, "new_hash": current, **summary})
        return True
    except Exception as exc:  # noqa: BLE001 — nunca derruba o scheduler
        logger.error("discovery.reclassify_on_weight_change_failed",
                     {"error": str(exc)[:300]})
        return False


def run_scan(db: Database, logger: EventLogger, *, reason: str) -> bool:
    """Executa uma varredura v2 (funil completo) e persiste. True = sucesso."""
    from engine.strategies.copy_trade import funnel
    from engine.strategies.copy_trade.discovery import (
        emit_logic_updated_if_needed,
        reports_dir,
    )
    from engine.strategies.copy_trade.hl_data import HLDataClient

    t0 = time.monotonic()
    try:
        cfg = funnel.load_config()
        emit_logic_updated_if_needed(db, logger, cfg)
        logger.info("discovery.scan_started",
                    {"reason": reason, "logic_version": cfg["logic_version"]})
        col = cfg["collection"]
        ht_budget = ((cfg.get("sources") or {}).get("hypertracker") or {}).get("budget") or {}
        client = HLDataClient(db, request_budget=int(col["request_budget"]),
                              min_interval_s=float(col.get("min_request_interval_s", 1.3)),
                              cache_ttl_hours=float(col["cache_ttl_hours"]),
                              ht_daily_cap=int(ht_budget.get("daily_request_cap", 90)),
                              ht_per_scan_cap=int(ht_budget.get("per_scan_cap", 80)))
        result = funnel.run_scan(client, db, cfg, logger=logger)
        funnel.persist_scan(db, result, cfg, client=client, logger=logger)

        js, md = funnel.render_report(result, cfg)
        stamp = time.strftime("%Y-%m-%d-%H%M")
        out = reports_dir()
        (out / f"scan-{stamp}-{result.scan_id}.json").write_text(js)
        (out / f"scan-{stamp}-{result.scan_id}.md").write_text(md)

        logger.info("discovery.scan_completed", {
            "reason": reason,
            "scan_id": result.scan_id,
            "approved": len(result.approved),
            "rejected": len(result.rejected),
            "funnel_stats": result.funnel_stats,
            "requests_used": result.requests_used,
            "logic_version": cfg["logic_version"],
            "duration_s": round(time.monotonic() - t0, 1),
        })
        return True
    except Exception as exc:  # noqa: BLE001 — o scheduler nunca morre por um scan
        logger.error("discovery.scan_failed",
                     {"reason": reason, "error": str(exc)[:300],
                      "duration_s": round(time.monotonic() - t0, 1)})
        return False


def run_reclassify(db: Database, logger: EventLogger, *, reason: str) -> bool:
    """UPDATE-0081: reprocessa TODAS as colunas dos traders NÃO-rejeitados pelo
    pipeline individual completo (funnel.reclassify_wallets), reusando o cache da
    HLDataClient. PRESERVA status/copy_pinned (gate humano) e mantém a guarda
    anti-downgrade. True = executou (mesmo com 0 alvos). Nunca lança."""
    from engine.strategies.copy_trade import funnel
    from engine.strategies.copy_trade.hl_data import HLDataClient

    t0 = time.monotonic()
    try:
        placeholders = ",".join("?" for _ in RECLASSIFY_STATUSES)
        rows = db.query(
            f"SELECT address FROM traders WHERE status IN ({placeholders}) "
            "ORDER BY address", tuple(RECLASSIFY_STATUSES))
        targets = [r["address"] for r in rows]
        if not targets:
            logger.info("discovery.reclassify_timer",
                        {"reason": reason, "n_targets": 0, "reclassified": 0,
                         "duration_s": round(time.monotonic() - t0, 1)})
            return True
        cfg = funnel.load_config()
        col = cfg["collection"]
        ht_budget = ((cfg.get("sources") or {}).get("hypertracker") or {}).get("budget") or {}
        client = HLDataClient(db, request_budget=int(col["request_budget"]),
                              min_interval_s=float(col.get("min_request_interval_s", 1.3)),
                              cache_ttl_hours=float(col["cache_ttl_hours"]),
                              ht_daily_cap=int(ht_budget.get("daily_request_cap", 90)),
                              ht_per_scan_cap=int(ht_budget.get("per_scan_cap", 80)))
        out = funnel.reclassify_wallets(db, targets, client, cfg, logger)
        reclassified = out["reclassified"]
        results = out["results"]
        errors = sum(1 for r in results if r.get("reason") in
                     ("erro_na_analise", "endereco_invalido"))
        skipped = sum(1 for r in results
                      if not r.get("reclassified")) - errors
        logger.info("discovery.reclassify_timer", {
            "reason": reason,
            "n_targets": len(targets),
            "reclassified": reclassified,
            "skipped": skipped,
            "errors": errors,
            "requests_used": getattr(client, "requests_used", None),
            "logic_version": cfg["logic_version"],
            "duration_s": round(time.monotonic() - t0, 1),
        })
        return True
    except Exception as exc:  # noqa: BLE001 — o scheduler nunca morre por um job
        logger.error("discovery.reclassify_timer_failed",
                     {"reason": reason, "error": str(exc)[:300],
                      "duration_s": round(time.monotonic() - t0, 1)})
        return False


class DiscoveryScheduler:
    def __init__(self, db: Database, logger: EventLogger,
                 scan_fn: Callable[..., bool] | None = None,
                 now_fn: Callable[[], datetime] | None = None,
                 reclassify_fn: Callable[..., bool] | None = None) -> None:
        self.db = db
        self.logger = logger
        self.scan_fn = scan_fn or (lambda reason: run_scan(self.db, self.logger,
                                                           reason=reason))
        # UPDATE-0081: injetável p/ teste; por padrão chama o job real de 2h.
        self.reclassify_fn = reclassify_fn or (
            lambda reason: run_reclassify(self.db, self.logger, reason=reason))
        self.now_fn = now_fn or (lambda: datetime.now(SCAN_TZ))
        self._stop = False

    def bootstrap_if_empty(self) -> bool:
        """Varredura imediata quando a tabela está vazia OU quando a
        logic_version do config avançou (re-upsert na lógica nova)."""
        if traders_table_empty(self.db):
            self.logger.info("discovery.bootstrap_scan",
                             {"motivo": "tabela traders vazia"})
            return self.scan_fn(reason="bootstrap_tabela_vazia")
        try:
            outdated = logic_outdated(self.db)
        except Exception:  # noqa: BLE001 — config quebrado não derruba o scheduler
            outdated = False
        if outdated:
            self.logger.info("discovery.bootstrap_scan",
                             {"motivo": "logic_version avançou"})
            return self.scan_fn(reason="bootstrap_logic_version")
        return False

    def run_forever(self, poll_interval_s: float = 30.0,
                    bootstrap_retry_s: float = 900.0) -> None:
        settings = get_settings()
        self.logger.info("health.discovery_scheduler_start",
                         {"scan_hour_sp": SCAN_HOUR, "top": SCAN_TOP})
        # Parte 2: se a régua de score mudou desde o último start, reclassifica
        # todos os traders 1x (barato — sem tocar na corretora).
        reclassify_on_weight_change(self.db, self.logger)
        self.bootstrap_if_empty()
        # bootstrap falhou (ex.: 429)? re-tenta a cada 15 min enquanto vazia,
        # em vez de esperar até as 05:00 do dia seguinte
        next_bootstrap_retry = time.monotonic() + bootstrap_retry_s
        target = next_scan_at(self.now_fn())
        # UPDATE-0081: primeiro disparo 2h após o boot (não compete com o
        # bootstrap/scan inicial); reagenda a cada RECLASSIFY_INTERVAL_S.
        next_reclassify = time.monotonic() + RECLASSIFY_INTERVAL_S
        while not self._stop:
            if settings.kill_file.exists():
                # kill switch: sem novas varreduras nem reclassify até o
                # incidente ser resolvido
                time.sleep(poll_interval_s)
                target = next_scan_at(self.now_fn())
                next_reclassify = time.monotonic() + RECLASSIFY_INTERVAL_S
                continue
            if traders_table_empty(self.db) and time.monotonic() >= next_bootstrap_retry:
                self.bootstrap_if_empty()
                next_bootstrap_retry = time.monotonic() + bootstrap_retry_s
            if self.now_fn() >= target:
                self.scan_fn(reason="agendado_diario")
                target = next_scan_at(self.now_fn())
            if time.monotonic() >= next_reclassify:
                self.reclassify_fn(reason="timer_2h")
                next_reclassify = time.monotonic() + RECLASSIFY_INTERVAL_S
            time.sleep(poll_interval_s)

    def stop(self) -> None:
        self._stop = True


def main() -> None:
    settings = get_settings()
    db = Database(settings.sqlite_path)
    db.migrate()
    logger = EventLogger("discovery-scheduler", settings.logs_dir, db=db)
    DiscoveryScheduler(db, logger).run_forever()


if __name__ == "__main__":
    main()
