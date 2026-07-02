"""Pure risk math: SL/TP/leverage sizing. No I/O — fully unit-tested.

Formula (from the build spec):

    leverage = (capital * risk_pct) / (stop_pct * allocated_margin)

    capital           = account equity (bankroll)
    risk_pct          = fraction of capital accepted as loss on the trade
    stop_pct          = (ATR * multiplier) / entry_price
    allocated_margin  = margin allocated to the trade

Leverage is ALWAYS truncated by the global ceiling and by the exchange's
maximum for the asset. Degenerate inputs (ATR ~ 0, tiny stop_pct) must never
produce absurd leverage — they clamp to the ceiling.
"""
from __future__ import annotations

from dataclasses import dataclass

_EPS = 1e-12


@dataclass(frozen=True)
class RiskInputs:
    capital: float            # account equity, USD
    risk_pct: float           # e.g. 0.01 = risk 1% of capital
    atr: float                # average true range, price units
    atr_multiplier: float     # stop distance in ATRs
    entry_price: float        # entry price
    allocated_margin: float   # margin allocated to the trade, USD


@dataclass(frozen=True)
class RiskPlan:
    stop_pct: float
    leverage: float           # after truncation
    raw_leverage: float       # before truncation (for audit logs)
    stop_price_long: float
    stop_price_short: float
    take_profit_long: float
    take_profit_short: float
    notional: float           # allocated_margin * leverage
    max_loss_usd: float       # expected loss if the stop is hit


def compute_stop_pct(atr: float, atr_multiplier: float, entry_price: float) -> float:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if atr < 0 or atr_multiplier <= 0:
        raise ValueError("atr must be >= 0 and atr_multiplier > 0")
    return (atr * atr_multiplier) / entry_price


def compute_leverage(
    capital: float,
    risk_pct: float,
    stop_pct: float,
    allocated_margin: float,
    *,
    max_leverage_global: float,
    max_leverage_asset: float,
) -> tuple[float, float]:
    """Return (truncated, raw) leverage. Clamps degenerate stop_pct to ceiling."""
    if capital <= 0 or allocated_margin <= 0:
        raise ValueError("capital and allocated_margin must be positive")
    if not 0 < risk_pct <= 1:
        raise ValueError("risk_pct must be in (0, 1]")
    if max_leverage_global <= 0 or max_leverage_asset <= 0:
        raise ValueError("leverage ceilings must be positive")

    ceiling = min(max_leverage_global, max_leverage_asset)
    if stop_pct <= _EPS:
        # ATR ~ 0 or absurdly tight stop: the formula diverges; clamp to ceiling.
        return (ceiling, float("inf"))
    raw = (capital * risk_pct) / (stop_pct * allocated_margin)
    return (min(raw, ceiling), raw)


def build_risk_plan(
    inputs: RiskInputs,
    *,
    max_leverage_global: float,
    max_leverage_asset: float,
    take_profit_rr: float = 2.0,
) -> RiskPlan:
    """Full plan: stop %, truncated leverage, SL/TP prices and worst-case loss.

    take_profit_rr: reward:risk ratio (TP distance = rr * stop distance).
    """
    stop_pct = compute_stop_pct(inputs.atr, inputs.atr_multiplier, inputs.entry_price)
    leverage, raw = compute_leverage(
        inputs.capital,
        inputs.risk_pct,
        stop_pct,
        inputs.allocated_margin,
        max_leverage_global=max_leverage_global,
        max_leverage_asset=max_leverage_asset,
    )
    stop_dist = stop_pct * inputs.entry_price
    tp_dist = take_profit_rr * stop_dist
    notional = inputs.allocated_margin * leverage
    max_loss = notional * stop_pct
    return RiskPlan(
        stop_pct=stop_pct,
        leverage=leverage,
        raw_leverage=raw,
        stop_price_long=inputs.entry_price - stop_dist,
        stop_price_short=inputs.entry_price + stop_dist,
        take_profit_long=inputs.entry_price + tp_dist,
        take_profit_short=inputs.entry_price - tp_dist,
        notional=notional,
        max_loss_usd=max_loss,
    )
