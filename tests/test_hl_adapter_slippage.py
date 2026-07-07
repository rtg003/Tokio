"""HyperliquidAdapter: market order = IOC agressivo com slippage + retry.

O SDK oficial manda market_open/market_close com slippage FIXO de 1%; em ativos
voláteis/ilíquidos (ex.: HYPE, asset=135) o preço não cruza o book e o HL rejeita
com "could not immediately match against any resting orders". O adapter agora
alarga o slippage e re-tenta antes de desistir, e loga o NOME do coin.

Rede-free: um Exchange falso dirige o retry sem SDK nem socket (construímos o
adapter via __new__ para pular o __init__ que exige o SDK/credenciais).
"""
from __future__ import annotations

import threading
from typing import Any

from engine.exchanges.base import OrderRequest
from engine.exchanges.hyperliquid.adapter import HyperliquidAdapter

_IOC_REJECT = {
    "status": "ok",
    "response": {"data": {"statuses": [
        {"error": "Order could not immediately match against any resting orders. asset=135"}
    ]}},
}
_MARGIN_REJECT = {
    "status": "ok",
    "response": {"data": {"statuses": [{"error": "Insufficient margin to place order"}]}},
}


def _fill(size: float) -> dict[str, Any]:
    return {"status": "ok", "response": {"data": {"statuses": [
        {"filled": {"oid": 1, "totalSz": size, "avgPx": 100.0}}
    ]}}}


class FakeExchange:
    """Preenche a partir da tentativa `ok_after` (0-based); antes disso, rejeita
    com o motivo `reject` (default: IOC no-match)."""

    def __init__(self, ok_after: int, reject: dict[str, Any] | None = None) -> None:
        self.ok_after = ok_after
        self.reject = reject or _IOC_REJECT
        self.open_slippages: list[float] = []
        self.close_slippages: list[float] = []

    def market_open(self, symbol, is_buy, size, px, slippage, cloid=None):  # noqa: ANN001
        self.open_slippages.append(slippage)
        if len(self.open_slippages) - 1 >= self.ok_after:
            return _fill(size)
        return self.reject

    def market_close(self, symbol, size, px, slippage, cloid=None):  # noqa: ANN001
        self.close_slippages.append(slippage)
        if len(self.close_slippages) - 1 >= self.ok_after:
            return _fill(size)
        return self.reject


def _bare_adapter(steps: list[float]) -> HyperliquidAdapter:
    sup = HyperliquidAdapter.__new__(HyperliquidAdapter)
    sup._lock = threading.Lock()
    sup.slippage_steps = list(steps)
    return sup


def _req(reduce_only: bool = False) -> OrderRequest:
    return OrderRequest(symbol="HYPE", side="buy", size=1.0,
                        order_type="market", reduce_only=reduce_only)


def test_retry_widens_slippage_until_fill() -> None:
    ex = FakeExchange(ok_after=2)  # preenche na 3ª tentativa
    sup = _bare_adapter([0.05, 0.10, 0.15])
    result = sup._place_market_with_retry(ex, _req(), True, None)
    assert result.ok is True
    assert ex.open_slippages == [0.05, 0.10, 0.15]


def test_non_ioc_error_stops_immediately() -> None:
    ex = FakeExchange(ok_after=99, reject=_MARGIN_REJECT)
    sup = _bare_adapter([0.05, 0.10, 0.15])
    result = sup._place_market_with_retry(ex, _req(), True, None)
    assert result.ok is False
    assert ex.open_slippages == [0.05]          # sem retry — slippage não resolve margem
    assert "HYPE" in (result.error or "")


def test_all_steps_exhausted_returns_named_error() -> None:
    ex = FakeExchange(ok_after=99)
    sup = _bare_adapter([0.05, 0.10, 0.15])
    result = sup._place_market_with_retry(ex, _req(), True, None)
    assert result.ok is False
    assert ex.open_slippages == [0.05, 0.10, 0.15]
    assert "HYPE" in (result.error or "")        # nome do coin, não só 'asset=135'


def test_reduce_only_uses_market_close_with_retry() -> None:
    ex = FakeExchange(ok_after=1)
    sup = _bare_adapter([0.05, 0.10, 0.15])
    result = sup._place_market_with_retry(ex, _req(reduce_only=True), True, None)
    assert result.ok is True
    assert ex.close_slippages == [0.05, 0.10]
    assert ex.open_slippages == []
