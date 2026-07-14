"""Regressão do Bug C — ledger reidratado no restart do gateway.

`Ledger` é 100% em memória. Sem `hydrate_from_db`, após um `systemctl restart` o
reconcile de startup compararia o alvo do trader contra um book VAZIO e reabriria
tudo (posições dobradas: AAVE 15.41→30.80, HYPE 0→2.32 em produção 2026-07-14).
Reproduzir os fills persistidos (ordem `id ASC`) reconstrói o SIZE líquido.
"""
from __future__ import annotations

import pytest

from engine.gateway.ledger import Ledger


def _row(strategy_id: str, symbol: str, side: str, price: float, size: float,
         fee: float = 0.0, cloid: str | None = None) -> dict:
    return {"cloid": cloid, "strategy_id": strategy_id, "symbol": symbol,
            "side": side, "price": price, "size": size, "fee": fee}


def test_hydrate_reconstructs_net_position() -> None:
    ledger = Ledger()
    ledger.hydrate_from_db([
        _row("ct_a", "AAVE", "buy", 100.0, 10.0),
        _row("ct_a", "AAVE", "buy", 110.0, 5.41),
        _row("ct_a", "HYPE", "buy", 30.0, 2.32),
    ])
    snap = ledger.snapshot()
    assert snap["ct_a"]["positions"]["AAVE"]["size"] == pytest.approx(15.41)
    assert snap["ct_a"]["positions"]["HYPE"]["size"] == pytest.approx(2.32)


def test_hydrate_nets_opens_and_closes_to_flat() -> None:
    ledger = Ledger()
    ledger.hydrate_from_db([
        _row("ct_a", "BTC", "buy", 100.0, 1.0),
        _row("ct_a", "BTC", "sell", 110.0, 1.0),   # fecha
    ])
    # posição líquida zero não aparece no snapshot (filtro size != 0).
    assert "BTC" not in ledger.snapshot().get("ct_a", {}).get("positions", {})


def test_hydrate_clears_previous_books() -> None:
    ledger = Ledger()
    # posição obsoleta pré-existente (como estaria antes do restart lógico).
    ledger.register_order("0xstale", "ct_a")
    ledger.apply_fill(cloid="0xstale", symbol="ETH", side="buy",
                      price=1.0, size=99.0, fee=0.0)
    ledger.hydrate_from_db([_row("ct_a", "BTC", "buy", 100.0, 1.0)])
    snap = ledger.snapshot()
    assert "ETH" not in snap["ct_a"]["positions"]           # obsoleto some
    assert snap["ct_a"]["positions"]["BTC"]["size"] == pytest.approx(1.0)


def test_hydrate_isolates_per_strategy() -> None:
    ledger = Ledger()
    ledger.hydrate_from_db([
        _row("ct_a", "BTC", "buy", 100.0, 1.0),
        _row("ct_b", "BTC", "buy", 200.0, 2.0),
    ])
    snap = ledger.snapshot()
    assert snap["ct_a"]["positions"]["BTC"]["avg_entry"] == pytest.approx(100.0)
    assert snap["ct_b"]["positions"]["BTC"]["avg_entry"] == pytest.approx(200.0)
