"""F0 do TV-Executor: contrato + recepção + validação determinística (§11).

Cobre os casos obrigatórios da F0: T1–T9, T14, T16. Sem execução — todos os
testes exercem o receiver, o worker e o validator puro contra um gateway fake.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import pytest

from engine.core.config import Settings
from engine.core.db import Database
from engine.tv import store, worker
from engine.tv.models import StrategyConfig, parse_signal
from engine.tv.validator import ValidatorContext, validate


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


class FakeGateway:
    """GatewayClient de teste: só health + market_meta (read-only, F0)."""

    def __init__(self, *, mid: float | None = 100_000.0, kill: bool = False) -> None:
        self._mid = mid
        self._kill = kill

    def health(self) -> dict[str, Any]:
        return {"ok": True, "kill_switch": self._kill}

    def market_meta(self, symbol: str, environment: str | None = None) -> dict[str, Any]:
        if self._mid is None:
            return {"ok": True, "mid": 0.0}
        return {"ok": True, "mid": self._mid, "szDecimals": 3, "maxLeverage": 50}


def register_tv_strategy(db: Database, sid: str, *, environment: str = "testnet",
                         status: str = "active", secret: str = "psecret",
                         url_secret: str = "urlsecret",
                         symbols: list[str] | None = None,
                         timeframes: list[str] | None = None,
                         overrides: dict[str, Any] | None = None) -> None:
    config = {
        "strategy_id": sid,
        "symbols_allowed": symbols if symbols is not None else ["BTC"],
        "timeframes_allowed": timeframes if timeframes is not None else ["4h"],
        "position_policy": {"on_opposite_signal": "reject",
                            "on_same_direction_signal": "ignore", "max_adds": 0},
        "sizing": {"method": "fixed_fractional", "allocation_usd": 1000,
                   "risk_per_trade_pct": 0.75, "min_trade_usd": 12,
                   "max_position_usd": 200},
        "risk_rules": {"max_trades_per_day": 5, "max_daily_loss_usd": 100,
                       "cooldown_minutes_after_loss": 30, "max_leverage": 3},
        "exit_rules": {"stop_loss_pct": 1.2, "take_profit_pct": 2.4},
        "execution_guards": {"max_signal_age_seconds": 90,
                             "max_price_deviation_pct": 0.5, "max_spread_bps": 10},
    }
    if overrides:
        config.update(overrides)
    db.upsert("strategies", {
        "id": sid, "module": "tradingview", "name": sid, "status": status,
        "config_snapshot": json.dumps(config, ensure_ascii=False),
        "thresholds": "{}",
    }, ("id",))
    db.upsert("tv_strategy_meta", {
        "strategy_id": sid, "environment": environment,
        "secret_hash": _sha(secret), "url_secret_hash": _sha(url_secret),
        "version": 1,
    }, ("strategy_id",))


def make_payload(sid: str, *, action: str = "buy", market_position: str = "long",
                 ticker: str = "BTCUSD", timeframe: str = "4h",
                 bar_time: str | None = None, price: str = "100000",
                 alert_id: str = "a1", secret: str = "psecret",
                 source: str = "tradingview") -> dict[str, Any]:
    return {
        "secret": secret, "strategy_id": sid, "alert_id": alert_id, "source": source,
        "ticker": ticker, "action": action, "market_position": market_position,
        "position_size": "0.01", "price": price, "timeframe": timeframe,
        "bar_time": bar_time or str(int(time.time() * 1000)), "comment": "x",
    }


def add_symbol_map(db: Database, ticker: str = "BTCUSD", coin: str = "BTC") -> None:
    db.upsert("tv_symbol_map", {"tv_ticker": ticker, "hl_coin": coin, "enabled": 1},
              ("tv_ticker",))


def run_signal(db: Database, gw: FakeGateway, payload: dict[str, Any], *,
               source: str = "tradingview") -> Any:
    """Simula receiver (persist raw + enqueue) + worker (process)."""
    settings = Settings()
    raw = json.dumps(payload)
    signal_id = store.persist_raw(db, source=source, raw_payload=raw,
                                  source_ip="1.2.3.4")
    store.enqueue(db, signal_id)
    row = db.query("SELECT * FROM tv_signals WHERE id = ?", (signal_id,))[0]
    decision = worker.process_signal(db, gw, settings, row)
    return signal_id, decision


# ---------------------------------------------------------------------------
# Validator puro (rápido, determinístico)
# ---------------------------------------------------------------------------
def _cfg(**over: Any) -> StrategyConfig:
    base = {
        "strategy_id": "tv_bt", "name": "BT", "status": "active",
        "environment": "testnet", "secret_hash": "x", "url_secret_hash": "y",
        "version": 1,
        "config_snapshot": json.dumps({
            "symbols_allowed": ["BTC"], "timeframes_allowed": ["4h"],
            "position_policy": {"on_opposite_signal": "reject",
                                "on_same_direction_signal": "ignore", "max_adds": 0},
            "sizing": {"method": "fixed_fractional", "allocation_usd": 1000,
                       "risk_per_trade_pct": 0.75, "min_trade_usd": 12,
                       "max_position_usd": 200},
            "risk_rules": {"max_trades_per_day": 5, "max_daily_loss_usd": 100,
                           "cooldown_minutes_after_loss": 30},
            "exit_rules": {"stop_loss_pct": 1.2},
            "execution_guards": {"max_signal_age_seconds": 90,
                                 "max_price_deviation_pct": 0.5, "max_spread_bps": 10},
        }),
    }
    base.update(over)
    return StrategyConfig.from_row(base)


def test_validator_happy_path_approved() -> None:
    sig = parse_signal(make_payload("tv_bt"))
    ctx = ValidatorContext(now_epoch=time.time(), coin="BTC", symbol_enabled=True,
                           mid=100_000.0)
    d = validate(sig, _cfg(), ctx, url_secret_ok=True, payload_secret_ok=True)
    assert d.outcome == "APPROVED", d.checks
    assert d.computed_size_usd == 200.0  # capado por max_position_usd
    # check 9 (spread) fica skipped em F0 (bbo=None)
    spread = [c for c in d.checks if c["check"] == "spread"][0]
    assert spread["result"] == "skipped"


def test_t5_signal_stale() -> None:
    old = str(int((time.time() - 600) * 1000))
    sig = parse_signal(make_payload("tv_bt", bar_time=old))
    ctx = ValidatorContext(now_epoch=time.time(), coin="BTC", symbol_enabled=True,
                           mid=100_000.0)
    d = validate(sig, _cfg(), ctx, url_secret_ok=True, payload_secret_ok=True)
    assert d.outcome == "BLOCKED" and d.block_code == "SIGNAL_STALE"


def test_t6_spread_too_wide_and_market_data() -> None:
    sig = parse_signal(make_payload("tv_bt"))
    # spread 50 bps > 10 bps: (ask-bid)/mid = 500/100000 = 0,5% = 50 bps
    ctx = ValidatorContext(now_epoch=time.time(), coin="BTC", symbol_enabled=True,
                           mid=100_000.0, bbo=(99_750.0, 100_250.0))
    d = validate(sig, _cfg(), ctx, url_secret_ok=True, payload_secret_ok=True)
    assert d.block_code == "SPREAD_TOO_WIDE", d.checks
    # mid indisponível → MARKET_DATA_UNAVAILABLE (check 8)
    ctx2 = ValidatorContext(now_epoch=time.time(), coin="BTC", symbol_enabled=True,
                            mid=None)
    d2 = validate(sig, _cfg(), ctx2, url_secret_ok=True, payload_secret_ok=True)
    assert d2.block_code == "MARKET_DATA_UNAVAILABLE"


def test_t8_opposite_position_reject() -> None:
    sig = parse_signal(make_payload("tv_bt", action="sell", market_position="short"))
    ctx = ValidatorContext(now_epoch=time.time(), coin="BTC", symbol_enabled=True,
                           mid=100_000.0, position_size=1.0)  # long aberto
    d = validate(sig, _cfg(), ctx, url_secret_ok=True, payload_secret_ok=True)
    assert d.block_code == "BLOCKED_OPPOSITE_POSITION"


def test_invalid_combination() -> None:
    sig = parse_signal(make_payload("tv_bt", action="buy", market_position="short"))
    ctx = ValidatorContext(now_epoch=time.time(), coin="BTC", symbol_enabled=True,
                           mid=100_000.0)
    d = validate(sig, _cfg(), ctx, url_secret_ok=True, payload_secret_ok=True)
    assert d.block_code == "INVALID_COMBINATION"


# ---------------------------------------------------------------------------
# Worker + store (integração com DB)
# ---------------------------------------------------------------------------
def test_t2_replay_duplicate(db: Database) -> None:
    register_tv_strategy(db, "tv_bt")
    add_symbol_map(db)
    gw = FakeGateway()
    p = make_payload("tv_bt")
    _, d1 = run_signal(db, gw, p)
    assert d1.outcome == "APPROVED", d1.block_code
    _, d2 = run_signal(db, gw, p)  # replay exato
    assert d2.outcome == "DUPLICATE"


def test_t3_unknown_strategy(db: Database) -> None:
    add_symbol_map(db)
    gw = FakeGateway()
    _, d = run_signal(db, gw, make_payload("tv_ghost"))
    assert d.block_code == "STRATEGY_UNKNOWN"


def test_t4_symbol_unmapped(db: Database) -> None:
    register_tv_strategy(db, "tv_bt")
    gw = FakeGateway()
    _, d = run_signal(db, gw, make_payload("tv_bt", ticker="DOGEUSD"))
    assert d.block_code == "SYMBOL_UNMAPPED"


def test_t7_daily_loss_from_real_ledger(db: Database) -> None:
    register_tv_strategy(db, "tv_bt")
    add_symbol_map(db)
    # fill perdedor de hoje, atribuído à estratégia TV do ambiente
    db.insert("fills", {"strategy_id": "tv_bt", "symbol": "BTC", "side": "sell",
                        "price": 100_000.0, "size": 0.01, "realized_pnl": -150.0})
    gw = FakeGateway()
    _, d = run_signal(db, gw, make_payload("tv_bt"))
    assert d.block_code == "LIMIT_DAILY_LOSS", d.checks


def test_t9_kill_switch_blocks_all_sources(db: Database) -> None:
    register_tv_strategy(db, "tv_bt")
    add_symbol_map(db)
    gw = FakeGateway(kill=True)
    _, d_tv = run_signal(db, gw, make_payload("tv_bt"))
    assert d_tv.block_code == "KILL_SWITCH_ACTIVE"
    # também bloqueia hermes (source interno)
    _, d_h = run_signal(db, gw, make_payload("tv_bt", source="hermes", alert_id="h1"),
                        source="hermes")
    assert d_h.block_code == "KILL_SWITCH_ACTIVE"


def test_t14_symbol_lock_cross_strategy(db: Database) -> None:
    register_tv_strategy(db, "tv_a")
    register_tv_strategy(db, "tv_b")
    add_symbol_map(db)
    # A segura BTC (posição/intenção em voo) no mesmo ambiente
    sid_a = store.persist_raw(db, source="tradingview", raw_payload="{}",
                              source_ip=None)
    store.update_signal(db, sid_a, strategy_id="tv_a", environment="testnet",
                        state="FILLED",
                        parsed=json.dumps({"coin": "BTC", "market_position": "long"}))
    gw = FakeGateway()
    _, d = run_signal(db, gw, make_payload("tv_b"))
    assert d.block_code == "SYMBOL_LOCKED_BY_STRATEGY"
    # após A fechar e reconciliar, B é aprovado
    store.update_signal(db, sid_a, state="CLOSED")
    _, d2 = run_signal(db, gw, make_payload("tv_b", alert_id="a2"))
    assert d2.outcome == "APPROVED", d2.block_code


def test_t16_environment_from_strategy_not_payload(db: Database) -> None:
    """O ambiente de execução vem de tv_strategy_meta, nunca do payload (§5.3)."""
    register_tv_strategy(db, "tv_bt", environment="mainnet")
    add_symbol_map(db)
    gw = FakeGateway()
    sid, _ = run_signal(db, gw, make_payload("tv_bt"))
    row = db.query("SELECT environment FROM tv_signals WHERE id = ?", (sid,))[0]
    assert row["environment"] == "mainnet"


# ---------------------------------------------------------------------------
# Receiver (T1) — autenticação síncrona de secret
# ---------------------------------------------------------------------------
def test_t1_bad_secret_returns_401(db: Database, settings: Settings) -> None:
    from fastapi.testclient import TestClient
    from engine.tv.receiver import build_app

    register_tv_strategy(db, "tv_bt", secret="right", url_secret="righturl")
    add_symbol_map(db)
    app = build_app(settings=settings, db=db, internal_token="itok")
    client = TestClient(app)

    # secret de payload errado
    bad = make_payload("tv_bt", secret="wrong")
    r = client.post("/tv/righturl", json=bad)
    assert r.status_code == 401
    # url secret errado
    good_body = make_payload("tv_bt", secret="right")
    r2 = client.post("/tv/wrongurl", json=good_body)
    assert r2.status_code == 401
    # nada persistido como sinal válido
    approved = db.query("SELECT COUNT(*) AS n FROM tv_signals WHERE state = 'APPROVED'")
    assert approved[0]["n"] == 0


def test_receiver_valid_signal_202(db: Database, settings: Settings) -> None:
    from fastapi.testclient import TestClient
    from engine.tv.receiver import build_app

    register_tv_strategy(db, "tv_bt", secret="right", url_secret="righturl")
    add_symbol_map(db)
    app = build_app(settings=settings, db=db, internal_token="itok")
    client = TestClient(app)
    r = client.post("/tv/righturl", json=make_payload("tv_bt", secret="right"))
    assert r.status_code == 202
    body = r.json()
    assert body["ok"] and body["state"] == "QUEUED"
    # enfileirado
    pending = db.query("SELECT COUNT(*) AS n FROM tv_queue WHERE status = 'pending'")
    assert pending[0]["n"] == 1


def test_healthz(db: Database, settings: Settings) -> None:
    from fastapi.testclient import TestClient
    from engine.tv.receiver import build_app

    app = build_app(settings=settings, db=db, internal_token="itok")
    client = TestClient(app)
    r = client.get("/tv/healthz")
    assert r.status_code == 200
    assert r.json()["receiver"] == "online"
