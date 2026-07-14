"""Regressão do Bug B — fills órfãos (ADL/liquidação, cloid=null).

Fills de auto-deleveraging da Hyperliquid chegam sem `cloid` e sem ordem casada.
Antes, `strategy_id` ficava NULL e `apply_fill` devolvia None ⇒ `realized_pnl`
NULL gravado, ignorando o `closedPnl` que a venue manda — o PnL sumia da dash.

Agora: (1) atribui-se o fill à estratégia ÚNICA que segura o símbolo
(`strategy_holding_symbol`, nunca cruza estratégias — §5.1); (2) usa-se o
`closedPnl` da HL quando não há dono único; (3) `tid` dá idempotência contra
re-entrega do websocket (não dobra o ledger).
"""
from __future__ import annotations

import pytest

from ..conftest import register_strategy


def _open_position(state, *, strategy_id: str, cloid: str, symbol: str,
                   size: float, price: float) -> None:
    """Abre uma posição atribuída (order + cloid mapeado no ledger)."""
    register_strategy(state.db, strategy_id, module="copy_trade")
    state.db.insert("orders", {
        "cloid": cloid, "strategy_id": strategy_id, "symbol": symbol,
        "side": "buy", "type": "market", "size": size, "status": "created",
    })
    state.ledger.register_order(cloid, strategy_id)
    state.on_own_fill({"cloid": cloid, "coin": symbol, "side": "B",
                       "px": price, "sz": size, "fee": 0.0})


def test_orphan_fill_attributed_to_unique_holder(gateway_state) -> None:
    st = gateway_state
    _open_position(st, strategy_id="ct_h", cloid="0xopen",
                   symbol="AAVE", size=10.0, price=100.0)
    # ADL fecha a posição: cloid=null, mas carrega closedPnl/tid/hash.
    st.on_own_fill({"cloid": None, "coin": "AAVE", "side": "A", "px": 120.0,
                    "sz": 10.0, "fee": 0.5, "closedPnl": 199.5,
                    "tid": "t1", "hash": "0xhash1"})
    rows = st.db.query(
        "SELECT strategy_id, realized_pnl, tid, fill_hash FROM fills WHERE tid = 't1'")
    assert len(rows) == 1
    assert rows[0]["strategy_id"] == "ct_h"                # atribuído ao dono único
    # ledger realiza (120-100)*10 - 0.5 = 199.5 (não some da dashboard).
    assert rows[0]["realized_pnl"] == pytest.approx(199.5)
    assert rows[0]["fill_hash"] == "0xhash1"
    # posição fechada no ledger.
    assert "AAVE" not in st.ledger.snapshot().get("ct_h", {}).get("positions", {})


def test_orphan_fill_without_unique_holder_uses_closed_pnl(gateway_state) -> None:
    st = gateway_state
    # nenhuma estratégia segura DOGE ⇒ sem atribuição possível (§5.1).
    st.on_own_fill({"cloid": None, "coin": "DOGE", "side": "A", "px": 0.5,
                    "sz": 100.0, "fee": 0.1, "closedPnl": 12.34,
                    "tid": "t2", "hash": "0xh2"})
    rows = st.db.query(
        "SELECT strategy_id, realized_pnl FROM fills WHERE tid = 't2'")
    assert len(rows) == 1
    assert rows[0]["strategy_id"] is None                  # fica em visão de sistema
    assert rows[0]["realized_pnl"] == pytest.approx(12.34)  # PnL vem do closedPnl


def test_two_holders_stay_unattributed(gateway_state) -> None:
    st = gateway_state
    _open_position(st, strategy_id="ct_x", cloid="0xx", symbol="ETH",
                   size=1.0, price=2_000.0)
    _open_position(st, strategy_id="ct_y", cloid="0xy", symbol="ETH",
                   size=2.0, price=2_000.0)
    # dois donos do mesmo símbolo ⇒ ambíguo ⇒ NÃO atribui (nunca cruza, §5.1).
    st.on_own_fill({"cloid": None, "coin": "ETH", "side": "A", "px": 2_100.0,
                    "sz": 0.5, "fee": 0.0, "closedPnl": 50.0,
                    "tid": "t3", "hash": "0xh3"})
    row = st.db.query("SELECT strategy_id FROM fills WHERE tid = 't3'")[0]
    assert row["strategy_id"] is None


def test_duplicate_tid_skipped_and_ledger_not_doubled(gateway_state) -> None:
    st = gateway_state
    register_strategy(st.db, "ct_d", module="copy_trade")
    st.db.insert("orders", {
        "cloid": "0xo2", "strategy_id": "ct_d", "symbol": "BTC",
        "side": "buy", "type": "market", "size": 1.0, "status": "created",
    })
    st.ledger.register_order("0xo2", "ct_d")
    fill = {"cloid": "0xo2", "coin": "BTC", "side": "B", "px": 100.0,
            "sz": 1.0, "fee": 0.0, "tid": "open1", "hash": "0xopenhash"}
    st.on_own_fill(fill)
    st.on_own_fill(dict(fill))   # re-entrega do MESMO tid pelo WS
    rows = st.db.query("SELECT id FROM fills WHERE tid = 'open1'")
    assert len(rows) == 1        # não duplica a linha
    # ledger não dobra a posição (ficaria 2.0 sem o guard).
    assert st.ledger.snapshot()["ct_d"]["positions"]["BTC"]["size"] == pytest.approx(1.0)
