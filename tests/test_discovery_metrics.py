"""Métricas do discovery v2 — patch do profit factor (crédito gradativo)."""
from __future__ import annotations

import math
import time

import pytest

from engine.strategies.copy_trade.metrics import (
    DAY_MS,
    HOUR_MS,
    pf_effective,
    pf_score_credit,
    position_metrics_from_ht,
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


# ============================================================================
# UPDATE-0062 (v15) — métricas de POSIÇÃO a partir do HyperTracker (função pura)
# ============================================================================
def _closed(coin: str, pnl: float, *, open_h: float, close_h: float,
            now_ms: float, **extra: object) -> dict:
    """Posição FECHADA sintética (abriu open_h atrás, fechou close_h atrás)."""
    return {"coin": coin, "status": "closed", "realizedPnl": pnl,
            "openedAt": now_ms - open_h * HOUR_MS,
            "closedAt": now_ms - close_h * HOUR_MS, **extra}


def test_position_metrics_from_ht_computes_win_pf_hold_concentration() -> None:
    """v15: WR/PF/hold/concentração derivam das posições CONSOLIDADAS do HT
    (sem o teto de fills). Função PURA — posições sintéticas, sem I/O."""
    now_ms = time.time() * 1000
    positions = [
        _closed("BTC", 100.0, open_h=10, close_h=8, now_ms=now_ms),   # win, hold 2h
        _closed("ETH", 300.0, open_h=30, close_h=24, now_ms=now_ms),  # win, hold 6h
        _closed("SOL", -50.0, open_h=50, close_h=40, now_ms=now_ms),  # loss, hold 10h
        _closed("ARB", -50.0, open_h=100, close_h=90, now_ms=now_ms), # loss, hold 10h
        # posição ABERTA: alimenta alavancagem, não conta como trade fechado.
        {"coin": "BTC", "status": "open", "leverage": 5,
         "openedAt": now_ms - 5 * HOUR_MS, "unrealizedPnl": 0.0},
    ]
    m = position_metrics_from_ht(positions, now_ms)

    assert m["n_trades"] == 4
    assert m["n_trades_30d"] == 4 and m["n_trades_7d"] == 4
    assert m["win_rate"] == pytest.approx(0.5)          # 2 wins de 4 fechadas
    assert m["profit_factor"] == pytest.approx(4.0)     # ganhos 400 / perdas 100
    assert m["median_hold_hours"] == pytest.approx(8.0)  # mediana de [2,6,10,10]
    assert m["top3_concentration"] == pytest.approx(1.0)  # top3 = 100% dos ganhos
    assert m["avg_leverage"] == pytest.approx(5.0)      # única posição aberta


def test_position_metrics_from_ht_liquid_share_and_coverage() -> None:
    """v15: liquid_volume_share respeita o set de líquidos; covered_days sai da
    posição mais antiga; janela de 7d exclui fechamentos mais velhos."""
    now_ms = time.time() * 1000
    positions = [
        _closed("BTC", 40.0, open_h=20 * 24, close_h=18 * 24, now_ms=now_ms,
                volume=1000.0),                            # fechou há 18d (fora de 7d)
        _closed("DOGE", -10.0, open_h=48, close_h=24, now_ms=now_ms,
                volume=1000.0),                            # fechou há 1d (dentro de 7d)
    ]
    m = position_metrics_from_ht(positions, now_ms, liquid={"BTC"})

    assert m["n_trades"] == 2
    assert m["n_trades_7d"] == 1                           # só o DOGE fechou nos 7d
    assert m["liquid_volume_share"] == pytest.approx(0.5)  # BTC 1000 / 2000 total
    assert m["covered_days"] == pytest.approx(20.0, abs=0.1)  # abertura mais antiga


def test_position_metrics_from_ht_empty_is_neutral() -> None:
    """Sem posições → métricas neutras/None (nunca estoura)."""
    m = position_metrics_from_ht([], time.time() * 1000)
    assert m["n_trades"] == 0
    assert m["win_rate"] is None
    assert m["profit_factor"] is None
    assert m["median_hold_hours"] is None
    assert m["covered_days"] is None
