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


def test_engine_writes_never_block_on_unconfigured_sink(db: Database) -> None:
    # No Supabase config at all: inserts still succeed instantly.
    for i in range(50):
        db.insert_event(ts="2026-07-02T00:00:00Z", strategy_id=None,
                        event_type="health.tick", level="info", payload={"i": i})
    assert db.queue_depth() >= 50
