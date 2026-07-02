from __future__ import annotations

from typing import Any

from engine.core.config import Settings
from engine.core.db import Database
from engine.strategies.base_runner import BaseRunner


class FakeGateway:
    def __init__(self) -> None:
        self.intents: list[dict[str, Any]] = []

    def send_intent(self, **payload: Any) -> dict[str, Any]:
        self.intents.append(payload)
        return {"ok": True, "cloid": "0xabc", "status": "dry_run"}

    def cancel(self, **payload: Any) -> dict[str, Any]:
        return {"ok": True}


def make_runner(settings: Settings, db: Database, **config: Any) -> tuple[BaseRunner, FakeGateway]:
    gw = FakeGateway()
    runner = BaseRunner("sa_unit", settings=settings, db=db, gateway=gw,
                        config={"name": "unit", **config})
    return runner, gw


def test_registers_as_dry_run_by_default(settings: Settings, db: Database) -> None:
    runner, _ = make_runner(settings, db)
    assert runner.status() == "dry_run"
    assert runner.is_dry_run()


def test_intent_carries_dry_run_flag_and_cap(settings: Settings, db: Database) -> None:
    runner, gw = make_runner(settings, db, max_exposure_usd=250.0)
    runner.send_intent(symbol="BTC", side="buy", notional_usd=20.0)
    intent = gw.intents[0]
    assert intent["dry_run"] is True
    assert intent["strategy_cap_usd"] == 250.0
    assert intent["strategy_id"] == "sa_unit"


def test_threshold_breach_auto_pauses(settings: Settings, db: Database) -> None:
    runner, _ = make_runner(settings, db, thresholds={
        "min_net_pnl": 0.0, "eval_window_days": 7, "min_trades": 3,
    })
    db.upsert("strategy_metrics_daily", {
        "strategy_id": "sa_unit", "day": "2026-07-01",
        "net_pnl": -25.0, "n_trades": 5, "fees": 1.0,
    }, ("strategy_id", "day"))
    db.execute("UPDATE strategy_metrics_daily SET day = date('now') WHERE strategy_id = 'sa_unit'")
    assert runner.check_thresholds() is True
    assert runner.status() == "auto_paused"


def test_no_auto_pause_without_sample(settings: Settings, db: Database) -> None:
    runner, _ = make_runner(settings, db, thresholds={
        "min_net_pnl": 0.0, "min_trades": 10,
    })
    assert runner.check_thresholds() is False
    assert runner.status() == "dry_run"


def test_runner_exits_on_archive(settings: Settings, db: Database) -> None:
    runner, _ = make_runner(settings, db, cycle_interval_s=0.01)
    runner.heartbeat_interval = 0.01
    db.execute("UPDATE strategies SET status = 'archived' WHERE id = 'sa_unit'")
    runner.run_forever()  # returns immediately instead of looping forever
    assert runner.status() == "archived"


def test_runner_halts_on_kill_switch(settings: Settings, db: Database) -> None:
    runner, _ = make_runner(settings, db, cycle_interval_s=0.01)
    settings.kill_file.write_text("test")
    runner.run_forever()
    assert runner.kill_switch_engaged()
