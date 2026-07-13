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
        # UPDATE-0045: registra chamadas de update_leverage e a ORDEM relativa
        # às aberturas/fechamentos (leverage tem de vir ANTES de abrir).
        self.leverage_calls: list[tuple[int, str, bool]] = []
        self.events: list[str] = []

    def update_leverage(self, leverage, name, is_cross=True):  # noqa: ANN001
        self.leverage_calls.append((leverage, name, is_cross))
        self.events.append("leverage")

    def market_open(self, symbol, is_buy, size, px, slippage, cloid=None):  # noqa: ANN001
        self.open_slippages.append(slippage)
        self.events.append("open")
        if len(self.open_slippages) - 1 >= self.ok_after:
            return _fill(size)
        return self.reject

    def market_close(self, symbol, size, px, slippage, cloid=None):  # noqa: ANN001
        self.close_slippages.append(slippage)
        self.events.append("close")
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


# -- UPDATE-0045: leverage aplicada no ativo antes de abrir ---------------
def _req_lev(leverage: float | None, reduce_only: bool = False) -> OrderRequest:
    return OrderRequest(symbol="HYPE", side="buy", size=1.0, order_type="market",
                        reduce_only=reduce_only, leverage=leverage)


def _adapter_with(ex: "FakeExchange") -> HyperliquidAdapter:
    sup = _bare_adapter([0.05])
    sup.exchange = ex
    return sup


def test_place_order_sets_leverage_before_open() -> None:
    ex = FakeExchange(ok_after=0)
    result = _adapter_with(ex).place_order(_req_lev(leverage=5.0))
    assert result.ok is True
    assert ex.leverage_calls == [(5, "HYPE", True)]   # int, cross, símbolo certo
    assert ex.events == ["leverage", "open"]          # leverage ANTES de abrir


def test_place_order_no_leverage_skips_update() -> None:
    ex = FakeExchange(ok_after=0)
    result = _adapter_with(ex).place_order(_req_lev(leverage=None))
    assert result.ok is True
    assert ex.leverage_calls == []                    # default da venue mantido


def test_reduce_only_does_not_touch_leverage() -> None:
    ex = FakeExchange(ok_after=0)
    result = _adapter_with(ex).place_order(_req_lev(leverage=5.0, reduce_only=True))
    assert result.ok is True
    assert ex.leverage_calls == []                    # fechamento não mexe em leverage


def test_leverage_failure_does_not_abort_order() -> None:
    ex = FakeExchange(ok_after=0)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("asset does not support cross")

    ex.update_leverage = _boom  # type: ignore[method-assign]
    result = _adapter_with(ex).place_order(_req_lev(leverage=5.0))
    assert result.ok is True                          # ordem segue apesar da falha
