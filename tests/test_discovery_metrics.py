"""Métricas do discovery v2 — patch do profit factor (crédito gradativo)."""
from __future__ import annotations

import math

import pytest

from engine.strategies.copy_trade.metrics import (
    pf_effective,
    pf_score_credit,
    profit_factor,
)


# --- casos exigidos pelo patch ------------------------------------------------
def test_pf_48_with_32_trades_gets_no_extended_credit() -> None:
    # PF 4.8 com amostra pequena: trecho estendido NÃO conta -> trava em 3.0
    assert pf_effective(4.8, n_trades=32) == pytest.approx(3.0)
    assert pf_score_credit(4.8, 32) == pytest.approx(3.0 / 4.0)


def test_pf_45_with_80_trades_gets_extended_credit() -> None:
    # PF 4.5 com n >= 60: 3.0 integral + meio-crédito de (4.5 - 3.0)
    assert pf_effective(4.5, n_trades=80) == pytest.approx(3.0 + 0.5 * 1.5)
    assert pf_score_credit(4.5, 80) == pytest.approx(3.75 / 4.0)
    # e é estritamente melhor que o mesmo PF sem amostra
    assert pf_score_credit(4.5, 80) > pf_score_credit(4.5, 32)


# --- fronteiras do crédito ------------------------------------------------------
def test_full_credit_up_to_3() -> None:
    assert pf_effective(1.0, 10) == pytest.approx(1.0)
    assert pf_effective(3.0, 10) == pytest.approx(3.0)   # integral independe de n
    assert pf_score_credit(3.0, 5) == pytest.approx(0.75)


def test_above_5_does_not_score_further() -> None:
    # acima de 5.0 não pontua: 8.0 vale o mesmo que 5.0 (com amostra)
    assert pf_effective(8.0, 100) == pytest.approx(pf_effective(5.0, 100))
    assert pf_score_credit(5.0, 100) == pytest.approx(1.0)
    # e sem amostra, PF gigante continua travado nos 3.0
    assert pf_effective(50.0, 59) == pytest.approx(3.0)


def test_degenerate_pf() -> None:
    assert pf_effective(0.0, 100) == 0.0
    assert pf_effective(-1.0, 100) == 0.0


# --- PF incluindo não realizado --------------------------------------------------
def test_profit_factor_includes_unrealized_loss() -> None:
    # Só realizados: 300/100 = 3.0. Mas há -200 aberto no fechamento da janela:
    # o perdedor não fechado NÃO pode inflar o PF -> (300)/(100+200) = 1.0
    assert profit_factor(300.0, 100.0) == pytest.approx(3.0)
    assert profit_factor(300.0, 100.0, unrealized_pnl=-200.0) == pytest.approx(1.0)


def test_profit_factor_includes_unrealized_gain() -> None:
    assert profit_factor(300.0, 100.0, unrealized_pnl=100.0) == pytest.approx(4.0)


def test_profit_factor_edge_cases() -> None:
    assert profit_factor(100.0, 0.0) == math.inf     # sem perdas
    assert profit_factor(0.0, 0.0) == 0.0
    assert profit_factor(0.0, 50.0) == 0.0
    with pytest.raises(ValueError):
        profit_factor(-1.0, 10.0)
