"""Local backtest harness — the TradingView Strategy Tester has no API
(ADR 0004), so validation happens here with historical Hyperliquid candles
(max 5,000 per call, paginated).

Everything is NET of fees: the report shows net PnL, expectancy per trade,
profit factor, max drawdown, win rate and trade count — the mandatory metrics
for any activation decision.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

MAINNET_INFO_URL = "https://api.hyperliquid.xyz/info"
CANDLES_PER_CALL = 5_000

_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
    "4h": 14_400_000, "1d": 86_400_000,
}


@dataclass(frozen=True)
class Candle:
    ts: int          # open time, ms
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Candle":
        return cls(ts=int(raw["t"]), open=float(raw["o"]), high=float(raw["h"]),
                   low=float(raw["l"]), close=float(raw["c"]), volume=float(raw["v"]))


def fetch_candles(symbol: str, interval: str, start_ms: int, end_ms: int,
                  info_url: str = MAINNET_INFO_URL) -> list[Candle]:
    """Paginated candle download honoring the 5,000/call API limit."""
    import httpx

    step = _INTERVAL_MS[interval] * CANDLES_PER_CALL
    out: list[Candle] = []
    cursor = start_ms
    with httpx.Client(timeout=30.0) as http:
        while cursor < end_ms:
            resp = http.post(info_url, json={
                "type": "candleSnapshot",
                "req": {"coin": symbol, "interval": interval,
                        "startTime": cursor, "endTime": min(cursor + step, end_ms)},
            })
            resp.raise_for_status()
            batch = [Candle.from_api(c) for c in resp.json()]
            if not batch:
                break
            out.extend(c for c in batch if not out or c.ts > out[-1].ts)
            cursor = batch[-1].ts + _INTERVAL_MS[interval]
    return out


class Strategy(Protocol):
    """Bar-close strategy contract: return the TARGET position in [-1, 1]
    (fraction of allocated capital, signed). The harness turns target changes
    into trades with fees applied."""

    def target_position(self, candles: list[Candle], index: int) -> float: ...


@dataclass
class Trade:
    entry_ts: int
    exit_ts: int
    direction: int          # +1 long, -1 short
    entry_price: float
    exit_price: float
    size: float             # base units
    fees: float
    net_pnl: float


@dataclass
class BacktestResult:
    symbol: str
    interval: str
    initial_capital: float
    final_equity: float
    net_pnl: float
    expectancy: float
    profit_factor: float
    max_drawdown_pct: float
    win_rate: float
    n_trades: int
    total_fees: float
    equity_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k not in ("equity_curve", "trades")}
        d["trades"] = [vars(t) for t in self.trades]
        return d


def compute_metrics(trades: list[Trade], equity_curve: list[float],
                    initial_capital: float, symbol: str, interval: str,
                    total_fees: float) -> BacktestResult:
    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    peak, mdd = float("-inf"), 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return BacktestResult(
        symbol=symbol,
        interval=interval,
        initial_capital=initial_capital,
        final_equity=equity_curve[-1] if equity_curve else initial_capital,
        net_pnl=sum(pnls),
        expectancy=(sum(pnls) / len(pnls)) if pnls else 0.0,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0
                      else (float("inf") if gross_win > 0 else 0.0),
        max_drawdown_pct=mdd * 100,
        win_rate=(len(wins) / len(pnls)) if pnls else 0.0,
        n_trades=len(pnls),
        total_fees=total_fees,
        equity_curve=equity_curve,
        trades=trades,
    )


def run_backtest(
    candles: list[Candle],
    strategy: Strategy,
    *,
    symbol: str = "?",
    interval: str = "?",
    initial_capital: float = 1_000.0,
    taker_fee_pct: float = 0.045,
    slippage_pct: float = 0.02,
) -> BacktestResult:
    """Bar-close execution: the target fraction computed at bar i executes at
    the open of bar i+1 (taker fee + slippage on every position change). The
    position only changes when the TARGET changes — no equity-feedback churn.
    """
    equity = initial_capital
    equity_curve: list[float] = []
    trades: list[Trade] = []
    total_fees = 0.0

    current_target = 0.0      # last executed target fraction
    pos_size = 0.0            # signed base units (constant while target holds)
    entry_price = 0.0
    entry_ts = 0
    entry_fees = 0.0

    def open_position(fraction: float, price_ref: float, ts: int) -> None:
        nonlocal pos_size, entry_price, entry_ts, entry_fees, equity, total_fees
        exec_price = price_ref * (1 + slippage_pct / 100 * (1 if fraction > 0 else -1))
        size = (equity * abs(fraction)) / exec_price if exec_price > 0 else 0.0
        fee = size * exec_price * (taker_fee_pct / 100)
        total_fees += fee
        equity -= fee
        pos_size = size * (1 if fraction > 0 else -1)
        entry_price = exec_price
        entry_ts = ts
        entry_fees = fee

    def close_position(price_ref: float, ts: int) -> None:
        nonlocal pos_size, entry_fees, equity, total_fees
        exec_price = price_ref * (1 - slippage_pct / 100 * (1 if pos_size > 0 else -1))
        fee = abs(pos_size) * exec_price * (taker_fee_pct / 100)
        total_fees += fee
        gross = (exec_price - entry_price) * pos_size
        trades.append(Trade(entry_ts=entry_ts, exit_ts=ts,
                            direction=1 if pos_size > 0 else -1,
                            entry_price=entry_price, exit_price=exec_price,
                            size=abs(pos_size), fees=fee + entry_fees,
                            net_pnl=gross - fee - entry_fees))
        equity += gross - fee
        pos_size = 0.0
        entry_fees = 0.0

    for i in range(len(candles) - 1):
        target = max(-1.0, min(1.0, strategy.target_position(candles, i)))
        if target != current_target:
            next_open = candles[i + 1].open
            ts = candles[i + 1].ts
            if pos_size != 0.0:
                close_position(next_open, ts)
            if target != 0.0:
                open_position(target, next_open, ts)
            current_target = target

        mark = candles[i + 1].close
        unrealized = (mark - entry_price) * pos_size if pos_size else 0.0
        equity_curve.append(equity + unrealized)

    # force-close any open position on the last bar (metrics need closed trades)
    if pos_size != 0.0:
        last = candles[-1]
        close_position(last.close, last.ts)
        equity_curve.append(equity)

    return compute_metrics(trades, equity_curve, initial_capital, symbol, interval, total_fees)


# ---------------------------------------------------------------------------
# example strategy — validates the harness end to end
# ---------------------------------------------------------------------------
@dataclass
class MaCross:
    """Long when fast SMA > slow SMA, flat otherwise. Example only."""

    fast: int = 20
    slow: int = 50

    def target_position(self, candles: list[Candle], index: int) -> float:
        if index + 1 < self.slow:
            return 0.0
        closes = [c.close for c in candles[max(0, index + 1 - self.slow):index + 1]]
        fast_ma = sum(closes[-self.fast:]) / self.fast
        slow_ma = sum(closes) / len(closes)
        return 1.0 if fast_ma > slow_ma else 0.0


STRATEGIES: dict[str, Callable[[], Strategy]] = {
    "ma_cross": MaCross,
}


def render_markdown(result: BacktestResult, strategy_name: str) -> str:
    pf = ("inf" if result.profit_factor == float("inf")
          else f"{result.profit_factor:.2f}")
    return "\n".join([
        f"# Backtest — {strategy_name} · {result.symbol} {result.interval}",
        "",
        "| Métrica (líquida de taxas) | Valor |",
        "|---|---|",
        f"| PnL líquido | {result.net_pnl:.2f} USD |",
        f"| Expectância por trade | {result.expectancy:.2f} USD |",
        f"| Profit factor | {pf} |",
        f"| Max drawdown | {result.max_drawdown_pct:.1f}% |",
        f"| Win rate | {result.win_rate * 100:.0f}% |",
        f"| Nº de trades | {result.n_trades} |",
        f"| Taxas totais | {result.total_fees:.2f} USD |",
        f"| Capital {result.initial_capital:.0f} → equity final | {result.final_equity:.2f} USD |",
        "",
        "> Ativação exige expectância positiva líquida + drawdown controlado +",
        "> resultado não concentrado — e é sempre um gate humano.",
    ])


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="tradingview.backtest")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--interval", default="1h", choices=sorted(_INTERVAL_MS))
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--strategy", default="ma_cross", choices=sorted(STRATEGIES))
    parser.add_argument("--capital", type=float, default=1_000.0)
    parser.add_argument("--out", default="docs/reports/backtest")
    args = parser.parse_args(argv)

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 86_400_000
    candles = fetch_candles(args.symbol, args.interval, start_ms, end_ms)
    if not candles:
        print("nenhum candle retornado")
        return 1
    result = run_backtest(candles, STRATEGIES[args.strategy](),
                          symbol=args.symbol, interval=args.interval,
                          initial_capital=args.capital)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    name = f"{args.strategy}-{args.symbol}-{args.interval}-{stamp}"
    (out / f"{name}.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    md = render_markdown(result, args.strategy)
    (out / f"{name}.md").write_text(md)
    print(f"candles: {len(candles)}")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
