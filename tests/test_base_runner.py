from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.core.config import Settings
from engine.core.db import Database
from engine.strategies.base_runner import BaseRunner


def _add_fill(db: Database, strategy_id: str, realized_pnl: float,
              *, forced_close: int = 0, ts: str | None = None) -> None:
    """Insert a closed fill so the breach calc (which reads `fills`) has data."""
    row: dict[str, Any] = {
        "strategy_id": strategy_id, "symbol": "BTC", "side": "sell",
        "price": 100.0, "size": 1.0, "fee": 0.0,
        "realized_pnl": realized_pnl, "forced_close": forced_close,
    }
    if ts is not None:
        row["ts"] = ts
    db.insert("fills", row)


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
    _add_fill(db, "sa_unit", -25.0)  # breach PnL now comes from fills (B3)
    assert runner.check_thresholds() is True
    assert runner.status() == "auto_paused"


def test_auto_pause_event_has_rich_payload(settings: Settings, db: Database) -> None:
    """B1: the persisted strategy.auto_paused event carries the evaluated
    pnl / n_trades / win_rate / thresholds, not just the breach string."""
    runner, _ = make_runner(settings, db, thresholds={
        "min_net_pnl": 0.0, "eval_window_days": 7, "min_trades": 3,
    })
    db.upsert("strategy_metrics_daily", {
        "strategy_id": "sa_unit", "day": "2026-07-01",
        "net_pnl": 0.0, "n_trades": 5, "win_rate": 0.4, "fees": 1.0,
    }, ("strategy_id", "day"))
    db.execute("UPDATE strategy_metrics_daily SET day = date('now') WHERE strategy_id = 'sa_unit'")
    _add_fill(db, "sa_unit", -30.0)
    assert runner.check_thresholds() is True
    rows = db.query(
        "SELECT payload FROM events WHERE event_type = 'strategy.auto_paused' "
        "AND strategy_id = 'sa_unit' ORDER BY id DESC LIMIT 1"
    )
    assert rows, "expected a persisted strategy.auto_paused event"
    payload = json.loads(rows[0]["payload"])
    assert "breach" in payload
    assert payload["n_trades"] == 5
    assert payload["pnl"] == -30.0
    assert payload["window_days"] == 7
    assert payload["thresholds"]["min_net_pnl"] == 0.0


def test_forced_close_excluded_from_breach(settings: Settings, db: Database) -> None:
    """B3: an ADL/liquidation fill (forced_close=1) must NOT downgrade the
    strategy — the breach PnL ignores it."""
    runner, _ = make_runner(settings, db, thresholds={
        "min_net_pnl": 0.0, "eval_window_days": 7, "min_trades": 3,
    })
    db.upsert("strategy_metrics_daily", {
        "strategy_id": "sa_unit", "day": "2026-07-01",
        "net_pnl": -50.0, "n_trades": 5, "fees": 1.0,
    }, ("strategy_id", "day"))
    db.execute("UPDATE strategy_metrics_daily SET day = date('now') WHERE strategy_id = 'sa_unit'")
    _add_fill(db, "sa_unit", -50.0, forced_close=1)  # involuntary — excluded
    assert runner.check_thresholds() is False
    assert runner.status() == "dry_run"


def test_auto_resume_after_hours(settings: Settings, db: Database) -> None:
    """B2: an auto_paused strategy resumes once auto_resume_after_hours has
    elapsed and there's no current breach; emits strategy.auto_resumed."""
    runner, _ = make_runner(settings, db, auto_resume_after_hours=1.0)
    db.execute("UPDATE strategies SET status = 'auto_paused' WHERE id = 'sa_unit'")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db.insert_event(ts=old_ts, strategy_id="sa_unit",
                    event_type="strategy.auto_paused", level="warning",
                    payload={"breach": "net_pnl -30.00 < 0.0"})
    assert runner.maybe_auto_resume() is True
    assert runner.status() == "active"
    rows = db.query(
        "SELECT 1 FROM events WHERE event_type = 'strategy.auto_resumed' "
        "AND strategy_id = 'sa_unit'"
    )
    assert rows, "expected a persisted strategy.auto_resumed event"


def test_auto_resume_restores_prev_status_not_promote(settings: Settings, db: Database) -> None:
    """Gate humano: um dry_run auto-pausado volta como dry_run, NUNCA como active."""
    runner, _ = make_runner(settings, db, auto_resume_after_hours=1.0)
    db.execute("UPDATE strategies SET status = 'auto_paused' WHERE id = 'sa_unit'")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db.insert_event(ts=old_ts, strategy_id="sa_unit",
                    event_type="strategy.auto_paused", level="warning",
                    payload={"breach": "x", "prev_status": "dry_run"})
    assert runner.maybe_auto_resume() is True
    assert runner.status() == "dry_run"  # restaurado, não promovido


def test_auto_resume_waits_for_window(settings: Settings, db: Database) -> None:
    """Resume does NOT fire before the configured window elapses."""
    runner, _ = make_runner(settings, db, auto_resume_after_hours=6.0)
    db.execute("UPDATE strategies SET status = 'auto_paused' WHERE id = 'sa_unit'")
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    db.insert_event(ts=recent_ts, strategy_id="sa_unit",
                    event_type="strategy.auto_paused", level="warning",
                    payload={"breach": "x"})
    assert runner.maybe_auto_resume() is False
    assert runner.status() == "auto_paused"


def test_auto_resume_disabled_by_default(settings: Settings, db: Database) -> None:
    """auto_resume_after_hours=None (default) keeps the manual behavior."""
    runner, _ = make_runner(settings, db)
    db.execute("UPDATE strategies SET status = 'auto_paused' WHERE id = 'sa_unit'")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    db.insert_event(ts=old_ts, strategy_id="sa_unit",
                    event_type="strategy.auto_paused", level="warning",
                    payload={"breach": "x"})
    assert runner.maybe_auto_resume() is False
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
