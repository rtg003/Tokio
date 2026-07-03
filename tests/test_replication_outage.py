"""Acceptance (Phase 1): a simulated Supabase outage never stops the engine —
rows queue locally and sync once the sink recovers."""
from __future__ import annotations

from engine.core.db import Database, Replicator, replication_lag_seconds
from tests.conftest import register_strategy


class FlakySink:
    """Sink that fails until `recover()` is called."""

    def __init__(self) -> None:
        self.down = True
        self.received: list[tuple[str, list[dict]]] = []

    @property
    def configured(self) -> bool:
        return True

    def upsert_rows(self, table: str, rows: list[dict]) -> None:
        if self.down:
            raise ConnectionError("supabase unreachable (simulated outage)")
        self.received.append((table, rows))

    def recover(self) -> None:
        self.down = False


def test_outage_queues_locally_then_syncs(db: Database) -> None:
    register_strategy(db, "sa_outage")
    for i in range(5):
        db.insert_event(ts=f"2026-07-02T00:00:0{i}Z", strategy_id="sa_outage",
                        event_type="order.test", level="info", payload={"i": i})
    depth_before = db.queue_depth()
    assert depth_before >= 6  # strategy row + 5 events

    sink = FlakySink()
    rep = Replicator(db, sink, batch_size=100, interval_seconds=0.01)

    synced, failed = rep.replicate_once()          # outage: nothing lost
    assert synced == 0 and failed == depth_before
    assert db.queue_depth() == depth_before        # rows stay queued
    assert replication_lag_seconds(db) >= 0

    # the engine keeps writing during the outage
    db.insert_event(ts="2026-07-02T00:01:00Z", strategy_id="sa_outage",
                    event_type="order.during_outage", level="info", payload={})
    assert db.queue_depth() == depth_before + 1

    sink.recover()
    synced, failed = rep.replicate_once()
    assert failed == 0 and synced == depth_before + 1
    assert db.queue_depth() == 0
    assert any(t == "events" for t, _ in sink.received)


def test_every_replicated_table_has_explicit_pk() -> None:
    """Invariante: tabela replicada sem PK mapeada quebra a dedup/on_conflict
    (bug real da tabela traders, 2026-07-03)."""
    from engine.core.db import _PK_COLUMNS, _REPLICATED_TABLES

    assert set(_PK_COLUMNS) == _REPLICATED_TABLES
    assert _PK_COLUMNS["traders"] == ("address",)


def test_traders_rows_replicate_with_address_pk(db: Database) -> None:
    """Duas versões do mesmo address colapsam em 1; addresses distintos não."""
    from engine.strategies.copy_trade.traders_store import upsert_candidate

    a1, a2 = "0x" + "11" * 20, "0x" + "22" * 20
    upsert_candidate(db, address=a1, score=50.0)
    upsert_candidate(db, address=a1, score=60.0)   # versão nova do mesmo trader
    upsert_candidate(db, address=a2, score=40.0)

    sink = FlakySink()
    sink.recover()
    rep = Replicator(db, sink, batch_size=100, interval_seconds=0.01)
    synced, failed = rep.replicate_once()
    assert failed == 0

    traders_batches = [rows for t, rows in sink.received if t == "traders"]
    assert traders_batches, "batch de traders não chegou ao sink"
    rows = traders_batches[0]
    by_addr = {r["address"]: r for r in rows}
    assert set(by_addr) == {a1, a2}                 # não colapsou addresses distintos
    assert by_addr[a1]["score"] == 60.0             # última versão vence


def test_engine_writes_never_block_on_unconfigured_sink(db: Database) -> None:
    # No Supabase config at all: inserts still succeed instantly.
    for i in range(50):
        db.insert_event(ts="2026-07-02T00:00:00Z", strategy_id=None,
                        event_type="health.tick", level="info", payload={"i": i})
    assert db.queue_depth() >= 50
