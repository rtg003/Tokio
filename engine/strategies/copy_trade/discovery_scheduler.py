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

import os
import time
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from engine.core.config import get_settings
from engine.core.db import Database
from engine.core.logger import EventLogger

SCAN_TZ = ZoneInfo("America/Sao_Paulo")
SCAN_HOUR = int(os.environ.get("DISCOVERY_SCAN_HOUR_SP", "5"))   # 05:00 SP
SCAN_TOP = int(os.environ.get("DISCOVERY_SCAN_TOP", "10"))


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
        client = HLDataClient(db, request_budget=int(col["request_budget"]),
                              cache_ttl_hours=float(col["cache_ttl_hours"]))
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


class DiscoveryScheduler:
    def __init__(self, db: Database, logger: EventLogger,
                 scan_fn: Callable[..., bool] | None = None,
                 now_fn: Callable[[], datetime] | None = None) -> None:
        self.db = db
        self.logger = logger
        self.scan_fn = scan_fn or (lambda reason: run_scan(self.db, self.logger,
                                                           reason=reason))
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
        self.bootstrap_if_empty()
        # bootstrap falhou (ex.: 429)? re-tenta a cada 15 min enquanto vazia,
        # em vez de esperar até as 05:00 do dia seguinte
        next_bootstrap_retry = time.monotonic() + bootstrap_retry_s
        target = next_scan_at(self.now_fn())
        while not self._stop:
            if settings.kill_file.exists():
                # kill switch: sem novas varreduras até o incidente ser resolvido
                time.sleep(poll_interval_s)
                target = next_scan_at(self.now_fn())
                continue
            if traders_table_empty(self.db) and time.monotonic() >= next_bootstrap_retry:
                self.bootstrap_if_empty()
                next_bootstrap_retry = time.monotonic() + bootstrap_retry_s
            if self.now_fn() >= target:
                self.scan_fn(reason="agendado_diario")
                target = next_scan_at(self.now_fn())
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
