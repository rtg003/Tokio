from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from engine.strategies.tradingview.backtest.harness import (
    Candle,
    MaCross,
    run_backtest,
)


def make_candles(closes: list[float], start_ts: int = 0) -> list[Candle]:
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(ts=start_ts + i * 3_600_000, open=prev, high=max(prev, c),
                          low=min(prev, c), close=c, volume=1.0))
        prev = c
    return out


@dataclass
class AlwaysLongOnce:
    """Enter long at bar 1, exit at the last decision bar."""

    exit_index: int = 8

    def target_position(self, candles, index):
        return 1.0 if 1 <= index < self.exit_index else 0.0


def test_single_trade_net_pnl_includes_fees_and_slippage() -> None:
    closes = [100.0] * 2 + [110.0] * 8
    candles = make_candles(closes)
    result = run_backtest(candles, AlwaysLongOnce(), initial_capital=1_000.0,
                          taker_fee_pct=0.045, slippage_pct=0.0)
    assert result.n_trades == 1
    trade = result.trades[0]
    # entry at open of bar 2 = close of bar 1 = 100; exit at 110
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(110.0)
    gross = (110 - 100) * trade.size
    assert trade.net_pnl < gross            # fees subtracted
    assert result.net_pnl == pytest.approx(trade.net_pnl)
    assert result.total_fees > 0
    assert result.win_rate == 1.0
    assert result.expectancy == pytest.approx(trade.net_pnl)


def test_flat_market_overtrading_loses_to_fees() -> None:
    """Overtrading in a flat market must show NEGATIVE net PnL (fees)."""

    @dataclass
    class ChurnBot:
        def target_position(self, candles, index):
            return 1.0 if index % 2 == 0 else 0.0

    candles = make_candles([100.0] * 50)
    result = run_backtest(candles, ChurnBot(), initial_capital=1_000.0)
    assert result.n_trades > 10
    assert result.net_pnl < 0
    assert result.profit_factor == 0.0


def test_losing_trade_and_drawdown() -> None:
    closes = [100.0] * 2 + [80.0] * 8
    candles = make_candles(closes)
    result = run_backtest(candles, AlwaysLongOnce(), initial_capital=1_000.0)
    assert result.n_trades == 1
    assert result.net_pnl < 0
    assert result.win_rate == 0.0
    assert result.max_drawdown_pct > 15.0


def test_short_via_negative_target() -> None:
    @dataclass
    class ShortOnce:
        def target_position(self, candles, index):
            return -1.0 if 1 <= index < 8 else 0.0

    closes = [100.0] * 2 + [90.0] * 8
    result = run_backtest(make_candles(closes), ShortOnce(), initial_capital=1_000.0)
    assert result.n_trades == 1
    assert result.trades[0].direction == -1
    assert result.net_pnl > 0                # short profits from the drop


def test_open_position_force_closed_at_end() -> None:
    @dataclass
    class NeverExit:
        def target_position(self, candles, index):
            return 1.0

    closes = [100.0, 100.0, 105.0, 111.0]
    result = run_backtest(make_candles(closes), NeverExit(), initial_capital=1_000.0)
    assert result.n_trades == 1              # force-closed for metrics
    assert result.final_equity != 1_000.0


def test_ma_cross_produces_complete_metrics() -> None:
    # trending series with noise: cross happens and metrics are well-formed
    closes = [100 + i + (5 if i % 7 == 0 else 0) for i in range(120)]
    result = run_backtest(make_candles([float(c) for c in closes]), MaCross(fast=5, slow=15),
                          symbol="BTC", interval="1h", initial_capital=1_000.0)
    assert result.n_trades >= 1
    assert not math.isnan(result.expectancy)
    assert result.max_drawdown_pct >= 0
    assert 0 <= result.win_rate <= 1
    d = result.to_dict()
    assert {"net_pnl", "expectancy", "profit_factor", "max_drawdown_pct",
            "win_rate", "n_trades", "total_fees"} <= set(d)
