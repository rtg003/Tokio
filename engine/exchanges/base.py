"""ExchangeAdapter ABC — the ONLY boundary between the gateway and any venue.

ADR 0003: Hyperliquid v1 uses the official SDK (hyperliquid-python-sdk), NOT
CCXT — copy trade depends on WS subscriptions to arbitrary addresses, outside
CCXT's unified API. Future venues may sit behind this same interface (e.g.
exchanges/ccxt_generic/).

ADR 0002: `subaccount_address` is optional from day 1 so Phase B (subaccounts
per risk bucket, signed by the gateway via vaultAddress) needs no refactor.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str                     # buy | sell
    size: float                   # base asset units
    order_type: str = "market"    # market | limit
    price: float | None = None    # required for limit
    reduce_only: bool = False
    cloid: str | None = None      # client order id — strategy attribution
    subaccount_address: str | None = None  # Phase B (vaultAddress)


@dataclass
class OrderResult:
    ok: bool
    exchange_order_id: str | None = None
    cloid: str | None = None
    status: str = "unknown"       # acked | filled | rejected | error
    filled_size: float = 0.0
    avg_price: float | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    size: float                   # signed: + long, - short
    entry_price: float
    unrealized_pnl: float = 0.0
    leverage: float | None = None


FillCallback = Callable[[dict[str, Any]], None]


class ExchangeAdapter(ABC):
    """Uniform venue interface. The gateway is the only caller."""

    name: str = "abstract"
    network: str = "testnet"

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel(self, symbol: str, exchange_order_id: str | None = None,
               cloid: str | None = None) -> bool: ...

    @abstractmethod
    def positions(self, address: str | None = None) -> list[Position]: ...

    @abstractmethod
    def balances(self, address: str | None = None) -> dict[str, float]: ...

    @abstractmethod
    def subscribe_own_fills(self, callback: FillCallback) -> None:
        """Stream fills for the engine's own account."""

    @abstractmethod
    def subscribe_user_fills(self, address: str, callback: FillCallback) -> None:
        """Stream fills for an ARBITRARY address (copy-trade requirement)."""

    @abstractmethod
    def market_meta(self, symbol: str) -> dict[str, Any]:
        """Asset metadata: max leverage, size decimals, min notional, etc."""

    @abstractmethod
    def mid_price(self, symbol: str) -> float: ...

    def close(self) -> None:  # optional cleanup (WS connections)
        return None
