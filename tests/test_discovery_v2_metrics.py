"""Métricas do funil v2 (spec v5) — fixtures sintéticas, incl. casos-armadilha."""
from __future__ import annotations

import pytest

from engine.strategies.copy_trade.metrics import (
    ScoreComponents,
    composite_score,
    consistency_score,
    copyability_score,
    deposit_growth_share,
    drawdown_quality,
    looks_like_mm,
    median_hold_hours,
    net_expectancy_score,
    pnl_cohort,
    position_episodes,
    roi_log_score,
    simulate_copy,
    size_cohort,
    top_n_concentration,
    twrr,
    weekly_stability,
)

H = 3_600_000.0  # 1h em ms


def fill(coin: str, t_ms: float, side: str, sz: float, start_pos: float) -> dict:
    return {"coin": coin, "time": t_ms, "side": side, "sz": sz,
            "startPosition": start_pos}


# --- TWRR: neutro a aportes (armadilha: inflado por depósito) ---------------------
def test_twrr_ignores_deposit_growth() -> None:
    # equity 100 -> 200, mas 100 veio de depósito no meio: TWRR ~ 0%
    curve = [(0.0, 100.0), (10.0, 100.0), (20.0, 200.0)]
    flows = [(15.0, 100.0)]
    assert twrr(curve, flows) == pytest.approx(0.0, abs=1e-9)


def test_twrr_pure_trading_gain() -> None:
    curve = [(0.0, 100.0), (10.0, 110.0), (20.0, 121.0)]
    assert twrr(curve) == pytest.approx(0.21, abs=1e-9)


def test_twrr_withdrawal_does_not_penalize() -> None:
    # 100 -> ganha 10% -> saca 55 -> equity 55; retorno de trading = +10%
    curve = [(0.0, 100.0), (10.0, 110.0), (20.0, 55.0)]
    flows = [(15.0, -55.0)]
    assert twrr(curve, flows) == pytest.approx(0.10, abs=1e-9)


def test_deposit_growth_share_f10() -> None:
    assert deposit_growth_share(100.0, 200.0, 100.0) == 1.0     # tudo aporte
    assert deposit_growth_share(100.0, 200.0, 20.0) == 0.2
    assert deposit_growth_share(100.0, 90.0, 50.0) == 0.0       # sem crescimento


# --- Episódios de posição: o BUG exato da v1 -------------------------------------
def test_swing_trader_adding_to_position_is_not_scalper() -> None:
    """Bug v1: trader que só AUMENTA posição existente tinha hold 0 e virava
    scalper. v2: início desconhecido -> excluído da mediana (None), nunca 0."""
    fills = [
        fill("BTC", 0 * H, "B", 1.0, start_pos=5.0),    # já tinha 5 BTC
        fill("BTC", 2 * H, "B", 1.0, start_pos=6.0),    # aumenta de novo
    ]
    eps = position_episodes(fills)
    assert len(eps) == 1
    assert eps[0].known_start is False
    assert median_hold_hours(eps) is None               # sem evidência ≠ scalper


def test_full_episode_hold_measured() -> None:
    fills = [
        fill("ETH", 0 * H, "B", 2.0, start_pos=0.0),    # abre do zero
        fill("ETH", 6 * H, "B", 1.0, start_pos=2.0),    # aumenta
        fill("ETH", 30 * H, "A", 3.0, start_pos=3.0),   # zera
    ]
    eps = position_episodes(fills)
    assert len(eps) == 1 and eps[0].hold_hours == pytest.approx(30.0)
    assert median_hold_hours(eps) == pytest.approx(30.0)


def test_flip_closes_and_opens_episode() -> None:
    fills = [
        fill("SOL", 0 * H, "B", 1.0, start_pos=0.0),
        fill("SOL", 10 * H, "A", 3.0, start_pos=1.0),   # flip p/ -2
        fill("SOL", 15 * H, "B", 2.0, start_pos=-2.0),  # zera
    ]
    eps = position_episodes(fills)
    holds = sorted(e.hold_hours for e in eps)
    assert holds == [pytest.approx(5.0), pytest.approx(10.0)]


def test_open_episode_excluded_from_median() -> None:
    fills = [fill("BTC", 0 * H, "B", 1.0, start_pos=0.0)]   # nunca fecha
    eps = position_episodes(fills)
    assert len(eps) == 1 and eps[0].end_ms is None
    assert median_hold_hours(eps) is None


# --- Drawdown quality ----------------------------------------------------------
def test_drawdown_quality_rewards_recovery() -> None:
    recovered = [(float(i), v) for i, v in enumerate([100, 90, 100, 110, 120])]
    stuck = [(float(i), v) for i, v in enumerate([100, 120, 95, 94, 93])]
    dd_r, q_r = drawdown_quality(recovered)
    dd_s, q_s = drawdown_quality(stuck)
    assert dd_r == pytest.approx(10.0)
    assert q_r > q_s


# --- Consistência / concentração (armadilha: sortudo de 1 trade) -----------------
def test_lucky_one_trade_flagged_by_concentration() -> None:
    pnls = [500.0] + [1.0] * 9
    assert top_n_concentration(pnls, 3) > 0.95            # reprova F6 (> 0.5)
    even = [50.0] * 10
    assert top_n_concentration(even, 3) == pytest.approx(0.3)


def test_weekly_stability_and_consistency() -> None:
    stable = weekly_stability([100, 110, 95, 105])
    erratic = weekly_stability([500, -400, 600, -500])
    assert stable > 0.7 and erratic < 0.2
    assert consistency_score(4, 4, stable) > consistency_score(2, 4, erratic)


# --- ROI log (não premia alavancagem) ----------------------------------------
def test_roi_log_saturates() -> None:
    assert roi_log_score(-5.0) == 0.0
    assert roi_log_score(10.0) < roi_log_score(30.0) < roi_log_score(50.0)
    assert roi_log_score(50.0) == pytest.approx(1.0)
    assert roi_log_score(500.0) == 1.0                    # saturado


# --- Copiabilidade (armadilha: scalper lucrativo) ---------------------------------
def test_profitable_scalper_scores_low_on_copyability() -> None:
    scalper = copyability_score(hold_hours=0.2, trades_per_day=80.0,
                                liquid_volume_share=1.0)
    swinger = copyability_score(hold_hours=24.0, trades_per_day=3.0,
                                liquid_volume_share=1.0)
    assert swinger > scalper
    assert swinger == pytest.approx(1.0)


def test_net_expectancy_zero_when_costs_eat_edge() -> None:
    # custo de cópia ida+volta: 2*(0.045+0.02) = 0.13%
    assert net_expectancy_score(0.10, 0.13) == 0.0
    assert net_expectancy_score(0.50, 0.13) > 0.3


# --- Anti-MM (armadilha: delta-neutro) --------------------------------------------
def test_delta_neutral_mm_detected() -> None:
    assert looks_like_mm(300.0, 0.001, 0.5) is True                # HFT
    assert looks_like_mm(60.0, 0.00001, 0.5) is True               # pnl/vol ~ 0
    assert looks_like_mm(30.0, 0.002, 0.005) is True               # delta-neutro
    assert looks_like_mm(3.0, 0.01, 0.8) is False                  # trader normal


# --- Coortes -------------------------------------------------------------------
def test_bidimensional_cohorts() -> None:
    size_bands = {"Shrimp": 250, "Fish": 10_000, "Dolphin": 100_000,
                  "Whale": 5_000_000, "Leviathan": float("inf")}
    pnl_bands = {"Rekt": 0, "Flat": 1_000, "Printer": float("inf")}
    assert size_cohort(100, size_bands) == "Shrimp"
    assert size_cohort(50_000, size_bands) == "Dolphin"
    assert size_cohort(9_000_000, size_bands) == "Leviathan"
    assert pnl_cohort(-5_000, pnl_bands) == "Rekt"
    assert pnl_cohort(500, pnl_bands) == "Flat"
    assert pnl_cohort(2_000_000, pnl_bands) == "Printer"


# --- Simulação retroativa de cópia (v7 — F15) --------------------------------------
def sim_fill(t_ms: float, sz: float, px: float, closed_pnl: float = 0.0) -> dict:
    return {"coin": "BTC", "time": t_ms, "sz": sz, "px": px,
            "closedPnl": closed_pnl}


NOW = 100 * 86_400_000.0  # dia 100 em ms


def test_simulate_copy_profitable_trader_nets_positive() -> None:
    # trader $100k, 2 trades de $10k com $500 de lucro cada — custo não come o edge
    fills = [sim_fill(NOW - 5 * 86_400_000.0, 1, 10_000),
             sim_fill(NOW - 4 * 86_400_000.0, 1, 10_000, closed_pnl=500),
             sim_fill(NOW - 3 * 86_400_000.0, 1, 10_000),
             sim_fill(NOW - 2 * 86_400_000.0, 1, 10_000, closed_pnl=500)]
    sim = simulate_copy(fills, 100_000, 1_000, now_ms=NOW)
    assert sim is not None
    # ratio 0.01: gross = 1000×0.01 = 10; custo = 4×100×0.00065 = 0.26
    assert sim.gross_pnl_usd == pytest.approx(10.0)
    assert sim.cost_usd == pytest.approx(0.26)
    assert sim.net_pnl_usd == pytest.approx(9.74)
    assert sim.median_copy_notional_usd == pytest.approx(100.0)
    assert sim.n_fills == 4


def test_simulate_copy_thin_edge_eaten_by_costs() -> None:
    # PnL magro sobre volume alto: custo de cópia vira o sinal (net < 0)
    fills = [sim_fill(NOW - i * 3_600_000.0, 1, 50_000,
                      closed_pnl=(10 if i % 2 else 0)) for i in range(40)]
    sim = simulate_copy(fills, 100_000, 1_000, now_ms=NOW)
    assert sim is not None
    assert sim.gross_pnl_usd > 0
    assert sim.net_pnl_usd < 0    # 200×0.01=2 de gross vs 40×500×0.00065=13 de custo


def test_simulate_copy_respects_window_and_guards() -> None:
    old = sim_fill(NOW - 40 * 86_400_000.0, 1, 10_000, closed_pnl=999_999)
    recent = sim_fill(NOW - 1 * 86_400_000.0, 1, 10_000, closed_pnl=100)
    sim = simulate_copy([old, recent], 100_000, 1_000,
                        window_days=30, now_ms=NOW)
    assert sim is not None and sim.n_fills == 1          # fill velho fora da janela
    assert sim.gross_pnl_usd == pytest.approx(1.0)
    assert simulate_copy([recent], 0.0, 1_000, now_ms=NOW) is None    # sem equity
    assert simulate_copy([old], 100_000, 1_000, window_days=30,
                         now_ms=NOW) is None             # janela vazia


def test_simulate_copy_sign_is_capital_invariant() -> None:
    # o net escala linearmente com o capital — sinal nunca muda com $1k vs $100k
    fills = [sim_fill(NOW - 2 * 86_400_000.0, 1, 20_000, closed_pnl=50)]
    small = simulate_copy(fills, 100_000, 1_000, now_ms=NOW)
    big = simulate_copy(fills, 100_000, 100_000, now_ms=NOW)
    assert small is not None and big is not None
    assert small.net_pnl_usd * 100 == pytest.approx(big.net_pnl_usd)


# --- Score composto: régua inteira -------------------------------------------------
WEIGHTS = {"consistency": 0.25, "profit_factor": 0.20, "roi_log": 0.15,
           "drawdown_quality": 0.15, "copyability": 0.15, "net_expectancy": 0.10}


def test_good_candidate_scores_high() -> None:
    good = ScoreComponents(consistency=0.9, profit_factor=0.75, roi_log=0.8,
                           drawdown_quality=0.85, copyability=1.0,
                           net_expectancy=0.6, adjustments=[("4/4", 5.0)])
    score = composite_score(good, WEIGHTS)
    assert 75 <= score <= 95                              # régua inteira em uso


def test_adjustments_applied_and_clamped() -> None:
    risky = ScoreComponents(consistency=0.5, profit_factor=0.5, roi_log=0.5,
                            drawdown_quality=0.5, copyability=0.5,
                            net_expectancy=0.5,
                            adjustments=[("liq", -10.0), ("crowding", -5.0)])
    assert composite_score(risky, WEIGHTS) == pytest.approx(35.0)
    floor = ScoreComponents(adjustments=[("liq", -10.0)])
    assert composite_score(floor, WEIGHTS) == 0.0
