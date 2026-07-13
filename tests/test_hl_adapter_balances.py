"""HyperliquidAdapter.balances(): sem double-counting da margem no spot USDC.

O `total` do spot USDC inclui o `hold` — a margem que já saiu do spot p/ o perp
e JÁ está no `accountValue`. Somar o `total` contava a mesma margem duas vezes
(UPDATE-0046). O adapter devolve agora o spot LIVRE (total - hold).

Rede-free: um Info falso alimenta user_state/spot_user_state; o adapter é
construído via __new__ p/ pular o __init__ que exige SDK/credenciais.
"""
from __future__ import annotations

from typing import Any

import pytest

from engine.exchanges.hyperliquid.adapter import HyperliquidAdapter


class FakeInfo:
    """Reproduz a evidência de produção (testnet, conta 0x4124…0915)."""

    def __init__(self, spot_balances: list[dict[str, Any]]) -> None:
        self._spot_balances = spot_balances

    def user_state(self, addr: str) -> dict[str, Any]:  # noqa: ARG002
        return {
            "marginSummary": {"accountValue": "442.38", "totalMarginUsed": "442.38"},
            "withdrawable": "0.0",
            "assetPositions": [],
        }

    def spot_user_state(self, addr: str) -> dict[str, Any]:  # noqa: ARG002
        return {"balances": self._spot_balances}


def _adapter(info: FakeInfo) -> HyperliquidAdapter:
    a = HyperliquidAdapter.__new__(HyperliquidAdapter)
    a.info = info
    a.account_address = "0x4124000000000000000000000000000000000915"
    return a


def test_balances_subtracts_hold_from_spot() -> None:
    a = _adapter(FakeInfo([
        {"coin": "USDC", "total": "1041.58", "hold": "442.38", "entryNtl": "0.0"},
    ]))
    b = a.balances()
    # spot LIVRE = total - hold; a margem (hold) fica só no accountValue.
    assert b["spot_usdc"] == pytest.approx(599.20)
    assert b["spot_usdc_total"] == pytest.approx(1041.58)
    assert b["spot_usdc_hold"] == pytest.approx(442.38)
    assert b["accountValue"] == pytest.approx(442.38)
    assert b["margin_used"] == pytest.approx(442.38)
    # equity real = accountValue + spot livre (sem double-count) ≈ 1041.58
    assert b["USDC"] == pytest.approx(1041.58)
    # withdrawable legado = withdrawable_perp (0) + spot livre ≈ 599.20
    assert b["withdrawable"] == pytest.approx(599.20)
    assert b["withdrawable_perp"] == pytest.approx(0.0)


def test_balances_no_hold_key_defaults_zero() -> None:
    # Conta sem margem alocada: hold ausente ⇒ spot livre == total.
    a = _adapter(FakeInfo([{"coin": "USDC", "total": "500.0"}]))
    b = a.balances()
    assert b["spot_usdc"] == pytest.approx(500.0)
    assert b["spot_usdc_hold"] == pytest.approx(0.0)


def test_balances_no_spot_usdc() -> None:
    a = _adapter(FakeInfo([{"coin": "ETH", "total": "1.0", "hold": "0.0"}]))
    b = a.balances()
    assert b["spot_usdc"] == pytest.approx(0.0)
    assert b["accountValue"] == pytest.approx(442.38)
