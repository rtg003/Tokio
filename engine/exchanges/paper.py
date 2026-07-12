"""PaperAdapter — in-memory venue for dry-run and tests.

Fills market orders instantly at the configured mark price and emits fill
events to subscribers, applying taker fees so dry-run PnL is NET of fees.
"""
from __future__ import annotations

import itertools
import threading
from typing import Any

from engine.exchanges.base import (
    ExchangeAdapter,
    FillCallback,
    OrderRequest,
    OrderResult,
    Position,
)

_DEFAULT_PRICES = {"BTC": 100_000.0, "ETH": 4_000.0, "SOL": 200.0}


class PaperAdapter(ExchangeAdapter):
    name = "paper"
    network = "paper"

    def __init__(self, taker_fee_pct: float = 0.045, prices: dict[str, float] | None = None) -> None:
        self.taker_fee_pct = taker_fee_pct
        self.prices: dict[str, float] = dict(prices or _DEFAULT_PRICES)
        self._positions: dict[str, Position] = {}
        self._own_fill_subs: list[FillCallback] = []
        self._user_fill_subs: dict[str, list[FillCallback]] = {}
        self._oid = itertools.count(1)
        self._lock = threading.Lock()
        self.placed_orders: list[OrderRequest] = []

    def set_price(self, symbol: str, price: float) -> None:
        self.prices[symbol] = price

    def place_order(self, request: OrderRequest) -> OrderResult:
        with self._lock:
            self.placed_orders.append(request)
            price = request.price if request.order_type == "limit" and request.price else \
                self.prices.get(request.symbol, 0.0)
            if price <= 0:
                return OrderResult(ok=False, cloid=request.cloid, status="rejected",
                                   error=f"no price for {request.symbol}")
            oid = str(next(self._oid))
            signed = request.size if request.side == "buy" else -request.size
            pos = self._positions.get(request.symbol)
            if pos is None:
                self._positions[request.symbol] = Position(
                    symbol=request.symbol, size=signed, entry_price=price)
            else:
                new_size = pos.size + signed
                if new_size == 0:
                    del self._positions[request.symbol]
                else:
                    if (pos.size > 0) == (signed > 0):
                        total = abs(pos.size) + abs(signed)
                        pos.entry_price = (
                            pos.entry_price * abs(pos.size) + price * abs(signed)) / total
                    pos.size = new_size

            fee = abs(request.size) * price * (self.taker_fee_pct / 100.0)
            fill = {
                "coin": request.symbol,
                "px": price,
                "sz": request.size,
                "side": request.side,
                "fee": fee,
                "feeToken": "USDC",
                "cloid": request.cloid,
                "oid": oid,
            }
        for cb in list(self._own_fill_subs):
            cb(fill)
        return OrderResult(ok=True, exchange_order_id=oid, cloid=request.cloid,
                           status="filled", filled_size=request.size, avg_price=price,
                           raw=fill)

    def place_trigger(self, symbol: str, side: str, size: float, trigger_px: float,
                      tpsl: str, *, reduce_only: bool = True,
                      cloid: str | None = None) -> OrderResult:
        """Trigger reduce_only: fica RESTING (não preenche na hora). Determinístico."""
        with self._lock:
            self.placed_orders.append(OrderRequest(
                symbol=symbol, side=side, size=size, order_type="trigger",
                price=trigger_px, reduce_only=reduce_only, cloid=cloid))
            oid = str(next(self._oid))
        return OrderResult(ok=True, exchange_order_id=oid, cloid=cloid, status="acked")

    def cancel(self, symbol: str, exchange_order_id: str | None = None,
               cloid: str | None = None) -> bool:
        return True  # market orders fill instantly; nothing resting

    def positions(self, address: str | None = None) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def balances(self, address: str | None = None) -> dict[str, float]:
        return {"USDC": 10_000.0}

    def subscribe_own_fills(self, callback: FillCallback) -> None:
        self._own_fill_subs.append(callback)

    def subscribe_user_fills(self, address: str, callback: FillCallback) -> None:
        self._user_fill_subs.setdefault(address.lower(), []).append(callback)

    def emit_user_fill(self, address: str, fill: dict[str, Any]) -> None:
        """Test hook: simulate a fill on a watched third-party address."""
        for cb in self._user_fill_subs.get(address.lower(), []):
            cb(fill)

    def market_meta(self, symbol: str) -> dict[str, Any]:
        return {"name": symbol, "maxLeverage": 50, "szDecimals": 4, "minNotional": 10.0}

    def mid_price(self, symbol: str) -> float:
        return self.prices.get(symbol, 0.0)

    def bbo(self, symbol: str) -> dict[str, float]:
        mid = self.prices.get(symbol, 0.0)
        return {"bid": mid, "ask": mid, "mid": mid, "spread": 0.0}
