"""HyperliquidAdapter.ensure_perp_margin(): auto-transfer spot→perp INTRA-CONTA.

Incidente 2026-07-16: a conta tinha USDC só no spot ($922.49) e $0 no perp, então
a ordem perp falhou por falta de margem. Na HL spot e perp são pools separados —
`usd_class_transfer(amount, True)` move spot→perp. Estes testes cobrem a lógica de
decisão (quanto transferir, quando não transferir) sem tocar a rede.

Rede-free: um Info falso alimenta balances(); um Exchange falso registra as
transferências. O adapter é construído via __new__ p/ pular o __init__ (SDK/creds).
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from engine.exchanges.hyperliquid.adapter import HyperliquidAdapter


class FakeInfo:
    def __init__(self, *, perp_withdrawable: float, spot_free: float) -> None:
        self._perp = perp_withdrawable
        self._spot = spot_free

    def user_state(self, addr: str) -> dict[str, Any]:  # noqa: ARG002
        return {
            "marginSummary": {"accountValue": str(self._perp),
                              "totalMarginUsed": "0.0"},
            "withdrawable": str(self._perp),
            "assetPositions": [],
        }

    def spot_user_state(self, addr: str) -> dict[str, Any]:  # noqa: ARG002
        return {"balances": [{"coin": "USDC", "total": str(self._spot), "hold": "0.0"}]}


class FakeExchange:
    def __init__(self, resp: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[float, bool]] = []
        self._resp = resp if resp is not None else {"status": "ok"}

    def usd_class_transfer(self, amount: float, to_perp: bool) -> dict[str, Any]:
        self.calls.append((amount, to_perp))
        return self._resp


def _adapter(info: FakeInfo, exchange: FakeExchange,
             *, address: str = "0x4124") -> HyperliquidAdapter:
    a = HyperliquidAdapter.__new__(HyperliquidAdapter)
    a.info = info
    a.exchange = exchange
    a.account_address = address
    a._lock = threading.Lock()
    return a


def test_transfers_spot_to_perp_when_perp_empty() -> None:
    ex = FakeExchange()
    a = _adapter(FakeInfo(perp_withdrawable=0.0, spot_free=900.0), ex)
    res = a.ensure_perp_margin(180.0, buffer_pct=5.0, min_transfer_usd=1.0)
    # needed=180; amount=min(180*1.05, 900)=189
    assert res["transferred"] == pytest.approx(189.0)
    assert res["ok"] is True
    assert ex.calls == [(189.0, True)]  # spot→perp


def test_no_transfer_when_perp_sufficient() -> None:
    ex = FakeExchange()
    a = _adapter(FakeInfo(perp_withdrawable=500.0, spot_free=900.0), ex)
    res = a.ensure_perp_margin(180.0)
    assert res["transferred"] == 0.0
    assert res["reason"] == "margem_suficiente"
    assert ex.calls == []  # venue untouched


def test_no_transfer_when_spot_below_min() -> None:
    ex = FakeExchange()
    a = _adapter(FakeInfo(perp_withdrawable=0.0, spot_free=0.5), ex)
    res = a.ensure_perp_margin(180.0, min_transfer_usd=1.0)
    assert res["transferred"] == 0.0
    assert res["reason"] == "spot_insuficiente"
    assert ex.calls == []


def test_no_transfer_when_no_margin_required() -> None:
    ex = FakeExchange()
    a = _adapter(FakeInfo(perp_withdrawable=0.0, spot_free=900.0), ex)
    res = a.ensure_perp_margin(0.0)
    assert res["transferred"] == 0.0
    assert res["reason"] == "sem_margem_requerida"
    assert ex.calls == []


def test_amount_capped_by_spot_free() -> None:
    # needed 180, buffer would want 189, but only 120 spot free ⇒ transfere 120.
    ex = FakeExchange()
    a = _adapter(FakeInfo(perp_withdrawable=0.0, spot_free=120.0), ex)
    res = a.ensure_perp_margin(180.0, buffer_pct=5.0)
    assert res["transferred"] == pytest.approx(120.0)
    assert ex.calls == [(120.0, True)]


def test_transfer_isolated_per_account() -> None:
    """Cada adapter transfere só na PRÓPRIA conta — nunca cruza wallets."""
    ex_a = FakeExchange()
    ex_b = FakeExchange()
    a = _adapter(FakeInfo(perp_withdrawable=0.0, spot_free=900.0), ex_a, address="0x4124")
    b = _adapter(FakeInfo(perp_withdrawable=0.0, spot_free=900.0), ex_b, address="0xd2c7")
    a.ensure_perp_margin(100.0)
    assert len(ex_a.calls) == 1
    assert ex_b.calls == []  # a conta B não foi tocada


def test_transfer_failure_reported() -> None:
    ex = FakeExchange(resp={"status": "err", "response": "nope"})
    a = _adapter(FakeInfo(perp_withdrawable=0.0, spot_free=900.0), ex)
    res = a.ensure_perp_margin(180.0)
    assert res["transferred"] == 0.0
    assert res["ok"] is False
    assert res["reason"] == "transfer_falhou"
