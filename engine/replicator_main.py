"""Replicator service entrypoint (compose service `replicator`).

Drains the local replication_queue to Supabase in batches, forever.
The engine never depends on this process being alive (ADR 0005).
"""
from __future__ import annotations

from engine.core.config import get_settings
from engine.core.db import Database, Replicator, SupabaseSink
from engine.core.logger import EventLogger

# Tabelas pequenas de estado (não-append-only) reenfileiradas no start:
# reconcilia perdas históricas de outbox (ex.: bug de PK do dedup, 2026-07-03).
_RECONCILE_TABLES: dict[str, tuple[str, ...]] = {
    "traders": ("address",),
    "strategies": ("id",),
    "strategy_metrics_daily": ("strategy_id", "day"),
}


def reconcile_state_tables(db: Database, logger: EventLogger) -> int:
    """Reenfileira as linhas atuais das tabelas de estado (upsert idempotente
    no destino). Barato: tabelas pequenas, 1x por start do replicator."""
    total = 0
    for table, pk in _RECONCILE_TABLES.items():
        rows = db.query(f"SELECT * FROM {table}")
        for row in rows:
            db.upsert(table, row, pk)
        total += len(rows)
    if total:
        logger.info("replication.reconciled", {"rows": total,
                                               "tables": sorted(_RECONCILE_TABLES)})
    return total


def main() -> None:
    settings = get_settings()
    db = Database(settings.sqlite_path)
    db.migrate()
    # db no logger: falhas de replicação viram eventos consultáveis (a fila
    # local segura tudo se o Supabase estiver fora — outbox cuida do resto)
    logger = EventLogger("replicator", settings.logs_dir, db=db)
    sink = SupabaseSink()
    logger.info("health.replicator_start", {"configured": sink.configured})
    reconcile_state_tables(db, logger)
    Replicator(
        db, sink,
        batch_size=settings.replication.batch_size,
        interval_seconds=settings.replication.interval_seconds,
        logger=logger,
    ).run_forever()


if __name__ == "__main__":
    main()
