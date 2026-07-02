from __future__ import annotations

from typing import Any

from engine.strategies.standalone.sa_dca_btc.runner import DcaBtcRunner
from tests.test_base_runner import FakeGateway


def make_dca(settings, db) -> tuple[DcaBtcRunner, FakeGateway]:
    gw = FakeGateway()
    config: dict[str, Any] = {
        "id": "sa_dca_btc", "name": "dca", "symbol": "BTC",
        "notional_usd": 15.0, "interval_hours": 24,
        "max_exposure_usd": 200.0, "initial_status": "dry_run",
    }
    return DcaBtcRunner(settings=settings, db=db, gateway=gw, config=config), gw


def test_template_registers_dry_run_and_never_active(settings, db) -> None:
    runner, _ = make_dca(settings, db)
    rows = db.query("SELECT module, status FROM strategies WHERE id = 'sa_dca_btc'")
    assert rows[0]["module"] == "standalone"
    assert rows[0]["status"] == "dry_run"


def test_dca_buys_fixed_notional_via_gateway(settings, db) -> None:
    runner, gw = make_dca(settings, db)
    runner.on_cycle()
    assert len(gw.intents) == 1
    intent = gw.intents[0]
    assert intent["symbol"] == "BTC"
    assert intent["notional_usd"] == 15.0
    assert intent["dry_run"] is True                 # template stays dry-run
    assert intent["strategy_cap_usd"] == 200.0       # cap goes to the gateway

    runner.on_cycle()                                # inside the interval: no new buy
    assert len(gw.intents) == 1
