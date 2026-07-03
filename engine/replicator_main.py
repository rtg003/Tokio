"""Replicator service entrypoint (compose service `replicator`).

Drains the local replication_queue to Supabase in batches, forever.
The engine never depends on this process being alive (ADR 0005).
"""
from __future__ import annotations

from engine.core.config import get_settings
from engine.core.db import Database, Replicator, SupabaseSink
from engine.core.logger import EventLogger


def main() -> None:
    settings = get_settings()
    db = Database(settings.sqlite_path)
    db.migrate()
    # db no logger: falhas de replicação viram eventos consultáveis (a fila
    # local segura tudo se o Supabase estiver fora — outbox cuida do resto)
    logger = EventLogger("replicator", settings.logs_dir, db=db)
    sink = SupabaseSink()
    logger.info("health.replicator_start", {"configured": sink.configured})
    Replicator(
        db, sink,
        batch_size=settings.replication.batch_size,
        interval_seconds=settings.replication.interval_seconds,
        logger=logger,
    ).run_forever()


if __name__ == "__main__":
    main()
