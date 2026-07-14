"""Bug D — ADL/liquidação NUNCA vira posição oposta no ledger virtual.

A Hyperliquid deslevera por ativo e manda o fill cru com `dir` =
"Auto-Deleveraging"/"Liquidation" e `cloid=null`. Antes do fix, `apply_fill`
tratava esse fill como ordem normal e fazia flip-through-zero: uma posição long
2.76 HYPE recebendo um ADL sell 17.4 virava um SHORT fantasma de -14.64 no book
virtual, enquanto a venue foi a FLAT. O `forced_close=True` clampa em zero quando
o fill "fecharia mais" do que temos. O realizado (gross - fee) é ortogonal ao
clamp e não pode regredir.
"""
from __future__ import annotations

import pytest

from engine.gateway.ledger import Ledger


def test_forced_close_clamps_to_zero_not_phantom_short() -> None:
    ledger = Ledger()
    ledger.register_order("0xopen", "ct_x")
    ledger.apply_fill(cloid="0xopen", symbol="HYPE", side="buy",
                      price=76.0, size=2.76, fee=0.0)
    assert ledger.book("ct_x").positions["HYPE"].size == pytest.approx(2.76)

    # ADL fecha 17.4 (muito mais que 2.76) — a venue está flat, não short.
    ledger.apply_fill(cloid=None, strategy_id="ct_x", symbol="HYPE", side="sell",
                      price=76.815, size=17.4, fee=0.0, forced_close=True)
    assert ledger.book("ct_x").positions["HYPE"].size == 0.0  # NÃO -14.64


def test_forced_close_realized_pnl_is_gross_minus_fee() -> None:
    ledger = Ledger()
    ledger.register_order("0xopen", "ct_x")
    ledger.apply_fill(cloid="0xopen", symbol="HYPE", side="buy",
                      price=76.0, size=2.76, fee=0.0)
    realized = ledger.apply_fill(
        cloid=None, strategy_id="ct_x", symbol="HYPE", side="sell",
        price=76.815, size=17.4, fee=0.05, forced_close=True)
    # closing = min(17.4, 2.76) = 2.76; gross = (76.815 - 76.0) * 2.76 * 1
    expected = (76.815 - 76.0) * 2.76 - 0.05
    assert realized == pytest.approx(expected)
    assert ledger.book("ct_x").realized_pnl == pytest.approx(expected)


def test_normal_close_still_reduces_partially() -> None:
    # forced_close=False: fill menor que a posição reduz normalmente (regressão).
    ledger = Ledger()
    ledger.register_order("0xopen", "ct_y")
    ledger.apply_fill(cloid="0xopen", symbol="AAVE", side="buy",
                      price=200.0, size=5.0, fee=0.0)
    ledger.apply_fill(cloid=None, strategy_id="ct_y", symbol="AAVE", side="sell",
                      price=210.0, size=3.0, fee=0.0, forced_close=False)
    assert ledger.book("ct_y").positions["AAVE"].size == pytest.approx(2.0)


def test_without_forced_close_a_big_close_flips_to_short() -> None:
    # Contraste: sem o flag, o mesmo ADL faria o flip-through-zero (o bug).
    ledger = Ledger()
    ledger.register_order("0xopen", "ct_z")
    ledger.apply_fill(cloid="0xopen", symbol="HYPE", side="buy",
                      price=76.0, size=2.76, fee=0.0)
    ledger.apply_fill(cloid=None, strategy_id="ct_z", symbol="HYPE", side="sell",
                      price=76.815, size=17.4, fee=0.0, forced_close=False)
    assert ledger.book("ct_z").positions["HYPE"].size == pytest.approx(-14.64)


def test_hydrate_replays_forced_close_flag() -> None:
    # O replay de startup precisa reconstruir a posição clampada em zero — senão
    # o reconcile compararia contra um short fantasma e reabriria tudo.
    ledger = Ledger()
    rows = [
        {"cloid": None, "strategy_id": "ct_x", "symbol": "HYPE", "side": "buy",
         "price": 76.0, "size": 2.76, "fee": 0.0, "forced_close": 0},
        {"cloid": None, "strategy_id": "ct_x", "symbol": "HYPE", "side": "sell",
         "price": 76.815, "size": 17.4, "fee": 0.0, "forced_close": 1},
    ]
    ledger.hydrate_from_db(rows)
    pos = ledger.book("ct_x").positions.get("HYPE")
    assert pos is not None and pos.size == 0.0  # NÃO short fantasma
