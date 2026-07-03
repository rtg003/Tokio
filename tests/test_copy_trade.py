"""Phase 2 acceptance: target fills mirrored through the gateway with fixed and
percent sizing; drift check and latency logged; ledger attributes via cloid.
Fonte de traders: tabela `traders` (ADR 0008)."""
from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from engine.core.config import Settings
from engine.core.db import Database
from engine.strategies.copy_trade.executor import CopyTradeExecutor, TraderConfig

TARGET = "0x00000000000000000000000000000000000000aa"


class FakeWatcher:
    def __init__(self) -> None:
        self.subs: dict[str, list[Callable[[dict[str, Any]], None]]] = {}

    def subscribe(self, address: str, callback: Callable[[dict[str, Any]], None]) -> None:
        self.subs.setdefault(address, []).append(callback)

    def emit(self, address: str, fill: dict[str, Any]) -> None:
        for cb in self.subs.get(address, []):
            cb(fill)


class RecordingGateway:
    """Records intents; simulates ledger endpoint for the drift check."""

    def __init__(self) -> None:
        self.intents: list[dict[str, Any]] = []
        self.ledger_response: dict[str, Any] = {}

        outer = self

        class _C:
            def get(self, path: str):
                class R:
                    def json(_self) -> dict[str, Any]:
                        return outer.ledger_response
                return R()

        self._client = _C()

    def send_intent(self, **payload: Any) -> dict[str, Any]:
        self.intents.append(payload)
        return {"ok": True, "cloid": "0xtest", "status": "dry_run"}

    def cancel(self, **payload: Any) -> dict[str, Any]:
        return {"ok": True}


def seed_trader(db: Database, **overrides: Any) -> None:
    row = {
        "address": TARGET, "name": "whale01", "status": "DRY_RUN",
        "mode": "fixed_usdc", "value": 100.0, "max_leverage": 3.0,
        "blocked_assets": "[]", "dry_run": 1, "thresholds": "{}",
        **{k: (json.dumps(v) if k in ("blocked_assets", "thresholds")
               and not isinstance(v, str) else v) for k, v in overrides.items()},
    }
    db.upsert("traders", row, ("address",))


def make_executor(settings: Settings, db: Database,
                  **overrides: Any) -> tuple[CopyTradeExecutor, FakeWatcher, RecordingGateway]:
    watcher = FakeWatcher()
    gw = RecordingGateway()
    seed_trader(db, **overrides)
    ex = CopyTradeExecutor(settings=settings, db=db, gateway=gw, watcher=watcher,
                           my_equity_fn=lambda: 1_000.0,
                           target_equity_fn=lambda _a: 100_000.0)
    return ex, watcher, gw


def fill(coin: str, side: str, sz: float, px: float, start_pos: float,
         time_ms: float = 0.0) -> dict[str, Any]:
    return {"coin": coin, "side": side, "sz": str(sz), "px": str(px),
            "startPosition": str(start_pos), "time": time_ms}


def test_registers_trader_as_dry_run_strategy(settings, db) -> None:
    make_executor(settings, db)
    rows = db.query("SELECT module, status FROM strategies WHERE id = 'ct_whale01'")
    assert rows and rows[0]["module"] == "copy_trade" and rows[0]["status"] == "dry_run"


def test_reload_picks_up_new_table_trader(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db)
    other = "0x00000000000000000000000000000000000000bb"
    db.upsert("traders", {"address": other, "name": "novo", "status": "DRY_RUN",
                          "mode": "fixed_usdc", "value": 50.0, "max_leverage": 2.0,
                          "blocked_assets": "[]", "dry_run": 1, "thresholds": "{}"},
              ("address",))
    ex.reload_traders()   # mudanças via API de controle entram sem restart
    assert other in watcher.subs
    assert "ct_novo" in ex.traders


def test_paused_trader_is_not_mirrored(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db)
    db.execute("UPDATE traders SET status = 'PAUSADO' WHERE address = ?", (TARGET,))
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0))
    assert gw.intents == []


def test_open_from_flat_fixed_usdc(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db)
    watcher.emit(TARGET, fill("BTC", "B", 2.0, 50_000.0, start_pos=0.0))
    assert len(gw.intents) == 1
    intent = gw.intents[0]
    # fixed 100 USDC at 50k => 0.002 BTC, regardless of the whale's 2 BTC
    assert intent["size"] == pytest.approx(0.002)
    assert intent["side"] == "buy"
    assert intent["dry_run"] is True
    assert intent["strategy_id"] == "ct_whale01"


def test_percent_mode_proportional_to_equity(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db,
                                    mode="percent", value=1.0)
    # whale (100k equity) buys 2 BTC @50k (100k notional) -> us (1k equity):
    # notional = 100k * 1.0 * (1000/100000) = 1000 USD -> 0.02 BTC
    watcher.emit(TARGET, fill("BTC", "B", 2.0, 50_000.0, start_pos=0.0))
    assert gw.intents[0]["size"] == pytest.approx(0.02)


def test_partial_reduction_mirrors_proportionally(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    watcher.emit(TARGET, fill("ETH", "B", 10.0, 2_000.0, start_pos=0.0))   # open
    watcher.emit(TARGET, fill("ETH", "A", 5.0, 2_100.0, start_pos=10.0))   # -50%
    assert len(gw.intents) == 2
    open_size = gw.intents[0]["size"]
    reduce = gw.intents[1]
    assert reduce["side"] == "sell"
    assert reduce["reduce_only"] is True
    assert reduce["size"] == pytest.approx(open_size * 0.5)


def test_full_close_mirrors_flat(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    watcher.emit(TARGET, fill("ETH", "B", 10.0, 2_000.0, start_pos=0.0))
    watcher.emit(TARGET, fill("ETH", "A", 10.0, 2_050.0, start_pos=10.0))
    close = gw.intents[1]
    assert close["reduce_only"] is True
    assert close["size"] == pytest.approx(gw.intents[0]["size"])
    assert ex._my_pos[("ct_whale01", "ETH")] == 0.0


def test_below_min_notional_skipped_and_logged(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=20.0)
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0))   # 20 USD open ok
    # whale trims 2% -> our delta ~0.4 USD < 10 USD minimum -> skip
    watcher.emit(TARGET, fill("BTC", "A", 0.02, 50_000.0, start_pos=1.0))
    assert len(gw.intents) == 1
    logs = db.query("SELECT event_type FROM events WHERE event_type = 'decision.skipped_min_notional'")
    assert logs


def test_blocked_asset_skipped(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, blocked_assets=["DOGE"])
    watcher.emit(TARGET, fill("DOGE", "B", 1000.0, 0.5, start_pos=0.0))
    assert gw.intents == []


def test_latency_logged_on_every_mirror(settings, db) -> None:
    import json as _json

    ex, watcher, gw = make_executor(settings, db)
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0, time_ms=1.0))
    rows = db.query("SELECT payload FROM events WHERE event_type = 'decision.mirrored'")
    assert rows
    # latency lives in the JSONL record; the mirrored decision is in the DB
    log_files = list(settings.logs_dir.glob("runner-copytrade-*.jsonl"))
    assert log_files
    lines = [_json.loads(line) for line in log_files[0].read_text().splitlines()]
    mirrored = [l for l in lines if l["event_type"] == "decision.mirrored"]
    assert mirrored and mirrored[0]["latency_ms"] >= 0


def test_drift_check_alerts_above_tolerance(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    watcher.emit(TARGET, fill("ETH", "B", 10.0, 2_000.0, start_pos=0.0))
    expected = ex._my_pos[("ct_whale01", "ETH")]
    gw.ledger_response = {"ct_whale01": {"positions": {"ETH": {"size": expected * 0.5}}}}
    drifts = ex.drift_check()
    assert len(drifts) == 1 and drifts[0]["symbol"] == "ETH"
    gw.ledger_response = {"ct_whale01": {"positions": {"ETH": {"size": expected}}}}
    assert ex.drift_check() == []


def test_mirror_config_validation() -> None:
    with pytest.raises(Exception):
        TraderConfig(name="x", address="0xabc", mode="yolo")
