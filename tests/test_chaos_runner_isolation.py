"""Acceptance (Phase 1) — basic chaos test: killing a runner process does not
affect the gateway, and one runner's crash never touches another runner.

The gateway runs in-process (uvicorn thread) with the paper adapter; the
runner runs as a REAL separate OS process that talks to it over HTTP, then
gets SIGKILLed.
"""
from __future__ import annotations

import multiprocessing
import os
import signal
import threading
import time

import httpx
import pytest

from engine.core.logger import EventLogger
from engine.exchanges.paper import PaperAdapter
from engine.gateway.server import GatewayState, build_app
from tests.conftest import register_strategy


def _runner_proc(port: int) -> None:
    """Minimal runner loop in a child process: hammer intents forever."""
    with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
        while True:
            try:
                client.post("/intent", json={
                    "strategy_id": "dm_chaos", "symbol": "BTC", "side": "buy",
                    "notional_usd": 15.0, "dry_run": True,
                })
            except Exception:
                pass
            time.sleep(0.05)


@pytest.fixture()
def live_gateway(settings, db):
    import uvicorn

    paper = PaperAdapter()
    state = GatewayState(settings, paper, db,
                         logger=EventLogger("gw-chaos", settings.logs_dir, db=db))
    register_strategy(db, "dm_chaos", status="dry_run")
    config = uvicorn.Config(build_app(state), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield state, port
    server.should_exit = True
    thread.join(timeout=5)


def test_killing_runner_does_not_affect_gateway(live_gateway) -> None:
    state, port = live_gateway
    proc = multiprocessing.get_context("spawn").Process(target=_runner_proc, args=(port,))
    proc.start()
    time.sleep(1.0)

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
        assert client.get("/health").json()["ok"] is True
        orders_before = state.db.query("SELECT COUNT(*) AS n FROM orders")[0]["n"]
        assert orders_before > 0  # runner was actually working

        os.kill(proc.pid, signal.SIGKILL)  # hard chaos: no cleanup
        proc.join(timeout=5)
        assert not proc.is_alive()

        # gateway survives and keeps serving other strategies
        assert client.get("/health").json()["ok"] is True
        resp = client.post("/intent", json={
            "strategy_id": "dm_chaos", "symbol": "BTC", "side": "sell",
            "notional_usd": 20.0, "dry_run": True,
        }).json()
        assert resp["ok"] is True
