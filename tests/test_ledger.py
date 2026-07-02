from __future__ import annotations

import pytest

from engine.gateway.ledger import Ledger, cloid_strategy_prefix, make_cloid


def test_cloid_format_and_prefix() -> None:
    cloid = make_cloid("ct_whale01")
    assert cloid.startswith("0x") and len(cloid) == 34  # 128-bit hex
    assert cloid[2:10] == cloid_strategy_prefix("ct_whale01")
    assert make_cloid("ct_whale01") != cloid  # random suffix


def test_fill_attribution_and_realized_pnl_net_of_fees() -> None:
    ledger = Ledger()
    cloid_open = make_cloid("sa_test")
    ledger.register_order(cloid_open, "sa_test")
    r1 = ledger.apply_fill(cloid=cloid_open, symbol="BTC", side="buy",
                           price=100.0, size=1.0, fee=0.05)
    assert r1 is None  # opening: nothing realized

    cloid_close = make_cloid("sa_test")
    ledger.register_order(cloid_close, "sa_test")
    r2 = ledger.apply_fill(cloid=cloid_close, symbol="BTC", side="sell",
                           price=110.0, size=1.0, fee=0.06)
    assert r2 == pytest.approx(10.0 - 0.06)  # gross 10, net of closing fee

    book = ledger.book("sa_test")
    assert book.realized_pnl == pytest.approx(9.94)
    assert book.fees_paid == pytest.approx(0.11)
    assert book.positions["BTC"].size == 0.0


def test_two_strategies_do_not_contaminate() -> None:
    ledger = Ledger()
    a, b = make_cloid("st_a"), make_cloid("st_b")
    ledger.register_order(a, "st_a")
    ledger.register_order(b, "st_b")
    ledger.apply_fill(cloid=a, symbol="ETH", side="buy", price=100, size=2, fee=0)
    ledger.apply_fill(cloid=b, symbol="ETH", side="buy", price=200, size=1, fee=0)
    assert ledger.book("st_a").positions["ETH"].avg_entry == 100
    assert ledger.book("st_b").positions["ETH"].avg_entry == 200


def test_opposite_directions_emit_warning() -> None:
    warnings: list[tuple[str, dict]] = []

    class Spy:
        def warning(self, event: str, payload: dict, **kw: object) -> None:
            warnings.append((event, payload))

        def info(self, *a: object, **kw: object) -> None: ...

    ledger = Ledger(Spy())
    a, b = make_cloid("st_long"), make_cloid("st_short")
    ledger.register_order(a, "st_long")
    ledger.register_order(b, "st_short")
    ledger.apply_fill(cloid=a, symbol="BTC", side="buy", price=100, size=1, fee=0)
    ledger.apply_fill(cloid=b, symbol="BTC", side="sell", price=100, size=1, fee=0)
    assert any(e == "risk.opposite_directions" for e, _ in warnings)


def test_flip_through_zero_resets_entry() -> None:
    ledger = Ledger()
    c1 = make_cloid("st_f")
    ledger.register_order(c1, "st_f")
    ledger.apply_fill(cloid=c1, symbol="SOL", side="buy", price=100, size=1, fee=0)
    c2 = make_cloid("st_f")
    ledger.register_order(c2, "st_f")
    realized = ledger.apply_fill(cloid=c2, symbol="SOL", side="sell", price=120, size=3, fee=0)
    pos = ledger.book("st_f").positions["SOL"]
    assert realized == pytest.approx(20.0)
    assert pos.size == -2
    assert pos.avg_entry == 120
