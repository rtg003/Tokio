"""Phase 8 acceptance — `strategy archive` end to end: cancels open orders via
the live gateway, marks archived in the DB (runner exits on next cycle), moves
the folder out of the runtime tree, and NEVER deletes history."""
from __future__ import annotations

import threading
import time

import pytest

import engine.cli as cli
from engine.core.db import utcnow
from engine.core.logger import EventLogger
from engine.exchanges.paper import PaperAdapter
from engine.gateway.server import GatewayState, build_app
from engine.strategies.base_runner import BaseRunner
from tests.test_base_runner import FakeGateway


@pytest.fixture()
def live_gateway(settings, db, monkeypatch):
    import uvicorn

    paper = PaperAdapter()
    state = GatewayState(settings, paper, db,
                         logger=EventLogger("gw-arch", settings.logs_dir, db=db))
    config = uvicorn.Config(build_app(state), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    monkeypatch.setenv("GATEWAY_HOST", "127.0.0.1")
    monkeypatch.setenv("GATEWAY_PORT", str(port))
    yield state
    server.should_exit = True
    thread.join(timeout=5)


def test_archive_end_to_end(settings, db, tmp_path, monkeypatch, live_gateway) -> None:
    # isolate the CLI onto the test settings/db and a fake repo tree
    monkeypatch.setattr(cli, "_db", lambda: db)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    fake_repo = tmp_path / "repo"
    strategy_dir = fake_repo / "engine" / "strategies" / "standalone" / "sa_doomed"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.md").write_text("# sa_doomed")
    monkeypatch.setattr(cli, "REPO_ROOT", fake_repo)

    # a real runner registers the strategy (dry_run) and produces history
    runner = BaseRunner("sa_doomed", settings=settings, db=db, gateway=FakeGateway(),
                        config={"name": "doomed"})
    assert runner.status() == "dry_run"
    db.insert("orders", {
        "cloid": "0xdeadbeef", "strategy_id": "sa_doomed", "symbol": "BTC",
        "side": "buy", "type": "limit", "size": 0.001, "price": 90_000.0,
        "status": "acked", "created_at": utcnow(),
    })
    db.insert_event(ts=utcnow(), strategy_id="sa_doomed",
                    event_type="order.created", level="info", payload={})

    rc = cli.main(["strategy", "archive", "sa_doomed", "--yes"])
    assert rc == 0

    # archived in the DB with timestamp; open order cancelled via gateway
    row = db.query("SELECT status, archived_at FROM strategies WHERE id = 'sa_doomed'")[0]
    assert row["status"] == "archived" and row["archived_at"]
    order = db.query("SELECT status FROM orders WHERE cloid = '0xdeadbeef'")[0]
    assert order["status"] == "cancelled"

    # folder moved out of the runtime tree
    assert not strategy_dir.exists()
    assert (fake_repo / "engine" / "strategies" / "archive" / "sa_doomed").exists()

    # HISTORY IS NEVER DELETED: orders/fills/events remain queryable
    assert db.query("SELECT 1 FROM orders WHERE strategy_id = 'sa_doomed'")
    assert db.query("SELECT 1 FROM events WHERE strategy_id = 'sa_doomed'")

    # the runner's loop exits on the next cycle (terminal status)
    runner.heartbeat_interval = 0.01
    runner.config["cycle_interval_s"] = 0.01
    runner.run_forever()          # returns immediately instead of spinning
    assert runner.status() == "archived"

    # archive is idempotent
    assert cli.main(["strategy", "archive", "sa_doomed", "--yes"]) == 0
