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


def run_scan(db: Database, logger: EventLogger, *, reason: str) -> bool:
    """Executa uma varredura e persiste na tabela `traders`. True = sucesso."""
    from engine.strategies.copy_trade.discovery import (
        HyperliquidDiscoverySource,
        LOGIC_VERSION,
        persist_candidates,
        render_markdown,
        run_discovery,
    )

    t0 = time.monotonic()
    logger.info("discovery.scan_started", {"reason": reason, "top": SCAN_TOP})
    try:
        source = HyperliquidDiscoverySource()
        candidates = run_discovery(source, top=SCAN_TOP)
        persist_candidates(db, candidates)

        settings = get_settings()
        out = settings.data_dir / "reports" / "discovery"
        out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d-%H%M")
        (out / f"discovery-{stamp}.md").write_text(render_markdown(candidates))

        approved = len([c for c in candidates if not c.excluded])
        logger.info("discovery.scan_completed", {
            "reason": reason,
            "approved": approved,
            "excluded": len(candidates) - approved,
            "logic_version": LOGIC_VERSION,
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
        """Primeira varredura quando a tabela está vazia (uma vez por start)."""
        if traders_table_empty(self.db):
            self.logger.info("discovery.bootstrap_scan",
                             {"motivo": "tabela traders vazia"})
            return self.scan_fn(reason="bootstrap_tabela_vazia")
        return False

    def run_forever(self, poll_interval_s: float = 30.0) -> None:
        settings = get_settings()
        self.logger.info("health.discovery_scheduler_start",
                         {"scan_hour_sp": SCAN_HOUR, "top": SCAN_TOP})
        self.bootstrap_if_empty()
        target = next_scan_at(self.now_fn())
        while not self._stop:
            if settings.kill_file.exists():
                # kill switch: sem novas varreduras até o incidente ser resolvido
                time.sleep(poll_interval_s)
                target = next_scan_at(self.now_fn())
                continue
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
