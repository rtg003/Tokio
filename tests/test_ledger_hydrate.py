"""Regressão do Bug C — ledger reidratado no restart do gateway.

`Ledger` é 100% em memória. Sem `hydrate_from_db`, após um `systemctl restart` o
reconcile de startup compararia o alvo do trader contra um book VAZIO e reabriria
tudo (posições dobradas: AAVE 15.41→30.80, HYPE 0→2.32 em produção 2026-07-14).
Reproduzir os fills persistidos (ordem `id ASC`) reconstrói o SIZE líquido.
"""
from __future__ import annotations

import pytest

from engine.gateway.ledger import Ledger, make_cloid


def _row(strategy_id: str, symbol: str, side: str, price: float, size: float,
         fee: float = 0.0, cloid: str | None = None,
         synthetic: int = 0) -> dict:
    return {"cloid": cloid, "strategy_id": strategy_id, "symbol": symbol,
            "side": side, "price": price, "size": size, "fee": fee,
            "synthetic": synthetic}


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


def test_resync_persists_synthetic_and_hydrate_reproduces_size() -> None:
    # Fix 1b: a venue foi a flat mas o book ainda tem size (fantasma). resync_position
    # zera o book EM MEMÓRIA e devolve a linha de fill sintético a persistir; após
    # "restart" o hydrate reproduz o size corrigido (0) a partir dos fills.
    ledger = Ledger()
    cloid = make_cloid("ct_a")
    ledger.register_order(cloid, "ct_a")
    open_row = _row("ct_a", "AAVE", "buy", 100.0, 15.41, cloid=cloid)
    ledger.apply_fill(cloid=cloid, symbol="AAVE", side="buy", price=100.0,
                      size=15.41, fee=0.0)
    assert ledger.book("ct_a").positions["AAVE"].size == pytest.approx(15.41)

    row = ledger.resync_position(strategy_id="ct_a", symbol="AAVE",
                                 venue_size=0.0, reason="drift.venue_resync",
                                 network="testnet", master_address="0x4124")
    assert row is not None
    assert row["synthetic"] == 1 and row["realized_pnl"] == 0.0 and row["fee"] == 0.0
    assert row["side"] == "sell" and row["size"] == pytest.approx(15.41)
    # Book em memória já zerado.
    assert ledger.book("ct_a").positions["AAVE"].size == pytest.approx(0.0)

    # "Restart": novo ledger reidrata do histórico (abertura + ajuste sintético).
    fresh = Ledger()
    fresh.hydrate_from_db([open_row, row])
    assert fresh.book("ct_a").positions.get(
        "AAVE", type("P", (), {"size": 0.0})()).size == pytest.approx(0.0)


def test_synthetic_fill_is_pnl_neutral_but_reconstructs_size() -> None:
    # Fix 1b: synthetic=1 ajusta SÓ o size — nunca acumula realized/fees (senão o
    # book em memória divergiria das queries que filtram synthetic=0). Mas ENTRA
    # na reconstrução de size no hydrate.
    ledger = Ledger()
    ledger.apply_fill(cloid=None, strategy_id="ct_a", symbol="AAVE", side="buy",
                      price=100.0, size=10.0, fee=0.5)
    before = ledger.book("ct_a").realized_pnl
    fees_before = ledger.book("ct_a").fees_paid
    # Ajuste sintético (delta de -4 no size): não move PnL/fees.
    ledger.apply_fill(cloid=None, strategy_id="ct_a", symbol="AAVE", side="sell",
                      price=0.0, size=4.0, fee=0.0, synthetic=True)
    book = ledger.book("ct_a")
    assert book.realized_pnl == pytest.approx(before)      # PnL inalterado
    assert book.fees_paid == pytest.approx(fees_before)    # fees inalteradas
    assert book.positions["AAVE"].size == pytest.approx(6.0)  # size ajustado
