"""core/risk.py — mandatory unit tests: normal + extreme cases."""
from __future__ import annotations

import math

import pytest

from engine.core.risk import RiskInputs, build_risk_plan, compute_leverage, compute_stop_pct


def test_leverage_formula_normal_case() -> None:
    # capital=10_000, risk 1% => 100 USD; stop 2%; margin 1_000
    # leverage = (10000*0.01)/(0.02*1000) = 5
    lev, raw = compute_leverage(10_000, 0.01, 0.02, 1_000,
                                max_leverage_global=10, max_leverage_asset=50)
    assert raw == pytest.approx(5.0)
    assert lev == pytest.approx(5.0)


def test_leverage_truncated_by_global_ceiling() -> None:
    lev, raw = compute_leverage(10_000, 0.05, 0.01, 500,
                                max_leverage_global=5, max_leverage_asset=50)
    assert raw == pytest.approx(100.0)
    assert lev == 5.0


def test_leverage_truncated_by_asset_max() -> None:
    lev, _ = compute_leverage(10_000, 0.05, 0.01, 500,
                              max_leverage_global=100, max_leverage_asset=3)
    assert lev == 3.0


def test_atr_near_zero_clamps_to_ceiling_not_infinity() -> None:
    stop_pct = compute_stop_pct(atr=0.0, atr_multiplier=2.0, entry_price=50_000)
    assert stop_pct == 0.0
    lev, raw = compute_leverage(10_000, 0.01, stop_pct, 1_000,
                                max_leverage_global=5, max_leverage_asset=50)
    assert math.isinf(raw)
    assert lev == 5.0  # clamped, never absurd


def test_tiny_stop_pct_never_produces_absurd_leverage() -> None:
    lev, raw = compute_leverage(100_000, 0.02, 1e-9, 100,
                                max_leverage_global=4, max_leverage_asset=50)
    assert raw > 1e6
    assert lev == 4.0


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        compute_stop_pct(atr=1.0, atr_multiplier=2.0, entry_price=0)
    with pytest.raises(ValueError):
        compute_leverage(0, 0.01, 0.02, 100, max_leverage_global=5, max_leverage_asset=5)
    with pytest.raises(ValueError):
        compute_leverage(1_000, 0, 0.02, 100, max_leverage_global=5, max_leverage_asset=5)
    with pytest.raises(ValueError):
        compute_leverage(1_000, 1.5, 0.02, 100, max_leverage_global=5, max_leverage_asset=5)


def test_full_plan_prices_and_max_loss() -> None:
    plan = build_risk_plan(
        RiskInputs(capital=10_000, risk_pct=0.01, atr=500, atr_multiplier=2,
                   entry_price=50_000, allocated_margin=1_000),
        max_leverage_global=10, max_leverage_asset=50, take_profit_rr=2.0,
    )
    assert plan.stop_pct == pytest.approx(0.02)
    assert plan.leverage == pytest.approx(5.0)
    assert plan.stop_price_long == pytest.approx(49_000)
    assert plan.stop_price_short == pytest.approx(51_000)
    assert plan.take_profit_long == pytest.approx(52_000)
    assert plan.take_profit_short == pytest.approx(48_000)
    assert plan.notional == pytest.approx(5_000)
    # loss if stop hit ~= capital * risk_pct
    assert plan.max_loss_usd == pytest.approx(100.0)
