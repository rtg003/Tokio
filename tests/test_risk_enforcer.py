from __future__ import annotations

from engine.core.config import Settings
from engine.gateway.ledger import Ledger, make_cloid
from engine.gateway.risk_enforcer import RiskEnforcer


def make_enforcer(settings: Settings) -> tuple[RiskEnforcer, Ledger]:
    ledger = Ledger()
    return RiskEnforcer(settings, ledger, kill_file=settings.kill_file), ledger


def test_rejects_below_min_notional(settings: Settings) -> None:
    enf, _ = make_enforcer(settings)
    v = enf.check_intent(strategy_id="s", symbol="BTC", notional_usd=5,
                         leverage=None, prices={"BTC": 100_000})
    assert not v.allowed and "below_min_notional" in v.reason


def test_rejects_above_max_order_notional(settings: Settings) -> None:
    enf, _ = make_enforcer(settings)
    v = enf.check_intent(strategy_id="s", symbol="BTC", notional_usd=10_000,
                         leverage=None, prices={})
    assert not v.allowed and "max_order_notional" in v.reason


def test_rejects_leverage_above_global(settings: Settings) -> None:
    enf, _ = make_enforcer(settings)
    v = enf.check_intent(strategy_id="s", symbol="BTC", notional_usd=100,
                         leverage=50, prices={})
    assert not v.allowed and "max_leverage" in v.reason


def test_strategy_exposure_cap(settings: Settings) -> None:
    enf, ledger = make_enforcer(settings)
    cloid = make_cloid("hungry")
    ledger.register_order(cloid, "hungry")
    ledger.apply_fill(cloid=cloid, symbol="BTC", side="buy",
                      price=100_000, size=0.0049, fee=0)  # ~490 USD exposure
    v = enf.check_intent(strategy_id="hungry", symbol="BTC", notional_usd=100,
                         leverage=None, prices={"BTC": 100_000})
    assert not v.allowed and "strategy_exposure_cap" in v.reason


def test_total_exposure_cap_across_strategies(settings: Settings) -> None:
    settings.risk.max_strategy_exposure_usd = 5_000
    enf, ledger = make_enforcer(settings)
    for sid in ("a", "b", "c", "d"):
        cloid = make_cloid(sid)
        ledger.register_order(cloid, sid)
        ledger.apply_fill(cloid=cloid, symbol="ETH", side="buy",
                          price=1_000, size=0.49, fee=0)  # 490 each => 1960 total
    v = enf.check_intent(strategy_id="e", symbol="ETH", notional_usd=100,
                         leverage=None, prices={"ETH": 1_000})
    assert not v.allowed and "total_exposure_cap" in v.reason


def test_kill_switch_blocks_everything(settings: Settings) -> None:
    enf, _ = make_enforcer(settings)
    enf.engage_kill_switch("test")
    v = enf.check_intent(strategy_id="s", symbol="BTC", notional_usd=100,
                         leverage=None, prices={})
    assert not v.allowed and v.reason == "kill_switch_engaged"


def test_circuit_breaker_opens_on_daily_loss(settings: Settings) -> None:
    enf, _ = make_enforcer(settings)
    enf.record_daily_pnl("2026-07-02", -settings.risk.max_daily_loss_usd - 1)
    assert enf.circuit_open
    v = enf.check_intent(strategy_id="s", symbol="BTC", notional_usd=100,
                         leverage=None, prices={})
    assert not v.allowed and v.reason == "circuit_breaker_open"
    # new day resets
    enf.record_daily_pnl("2026-07-03", 0.0)
    assert not enf.circuit_open


def test_rate_budget_per_strategy_and_cancel_reserve(settings: Settings) -> None:
    settings.rate_limit.default_strategy_budget_per_min = 10
    settings.rate_limit.reserve_for_cancels = 0.2
    enf, _ = make_enforcer(settings)
    allowed = 0
    for _ in range(20):
        v = enf.check_intent(strategy_id="greedy", symbol="BTC", notional_usd=100,
                             leverage=None, prices={"BTC": 100_000})
        if v.allowed:
            allowed += 1
    assert allowed == 8  # 10 * (1 - 0.2)
    # another strategy is unaffected (isolation)
    v = enf.check_intent(strategy_id="other", symbol="BTC", notional_usd=100,
                         leverage=None, prices={"BTC": 100_000})
    assert v.allowed
    # cancels still allowed for the greedy one (reserve)
    v = enf.check_intent(strategy_id="greedy", symbol="BTC", notional_usd=0,
                         leverage=None, prices={}, is_cancel=True)
    assert v.allowed
