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


def test_simulate_copy_caps_notional_by_max_copy_leverage() -> None:
    # trader equity $10k, fill $1M, copy capital $1k:
    # proportional seria $100k; v9 cap 3x limita a $3k e escala o PnL no mesmo fator.
    fills = [sim_fill(NOW - 2 * 86_400_000.0, 10, 100_000, closed_pnl=10_000)]
    uncapped = simulate_copy(fills, 10_000, 1_000, now_ms=NOW)
    capped = simulate_copy(fills, 10_000, 1_000, max_copy_leverage=3.0, now_ms=NOW)
    assert uncapped is not None and capped is not None
    assert uncapped.median_copy_notional_usd == pytest.approx(100_000.0)
    assert capped.median_copy_notional_usd == pytest.approx(3_000.0)
    # PnL proporcional seria $1000; cap de 3k/100k = 3% => $30 bruto.
    assert capped.gross_pnl_usd == pytest.approx(30.0)
    assert capped.cost_usd == pytest.approx(1.95)
    assert capped.net_pnl_usd == pytest.approx(28.05)


# --- Estágio 4 (v8): latência, expectância, DD da cópia e fator de ranking --------
def test_simulate_copy_latency_cost_reduces_net() -> None:
    fills = [sim_fill(NOW - 2 * 86_400_000.0, 1, 10_000, closed_pnl=100)]
    base = simulate_copy(fills, 100_000, 1_000, now_ms=NOW)
    with_lat = simulate_copy(fills, 100_000, 1_000,
                             latency_slippage_pct=0.03, now_ms=NOW)
    assert base is not None and with_lat is not None
    # custo extra = 100 (copy notional) × 0.0003 = 0.03
    assert with_lat.latency_cost_usd == pytest.approx(0.03)
    assert with_lat.net_pnl_usd == pytest.approx(base.net_pnl_usd - 0.03)


def test_simulate_copy_expectancy_and_dd_of_copy() -> None:
    # 2 trades fechados: -500 depois +800 (ratio 0.01 → -5 depois +8)
    fills = [sim_fill(NOW - 5 * 86_400_000.0, 1, 10_000, closed_pnl=-500),
             sim_fill(NOW - 2 * 86_400_000.0, 1, 10_000, closed_pnl=800)]
    sim = simulate_copy(fills, 100_000, 1_000, now_ms=NOW)
    assert sim is not None
    assert sim.n_closed == 2
    assert sim.expectancy_usd == pytest.approx(sim.net_pnl_usd / 2)
    # equity cai para ~995 antes de recuperar: DD ≈ 0.5% do capital de 1000
    assert 0.4 < sim.max_dd_pct < 0.7


def test_copy_sim_factor_clamped() -> None:
    from engine.strategies.copy_trade.metrics import copy_sim_factor

    assert copy_sim_factor(100.0, 1_000) == pytest.approx(1.10)   # ROI 10%
    assert copy_sim_factor(0.0, 1_000) == pytest.approx(1.0)      # neutro
    assert copy_sim_factor(900.0, 1_000) == pytest.approx(1.2)    # cap
    assert copy_sim_factor(-800.0, 1_000) == pytest.approx(0.5)   # floor
    assert copy_sim_factor(50.0, 0.0) == 1.0                      # sem capital


# --- UPDATE-0066: cap do ratio quando trader_equity < mirror_capital ----------
def test_simulate_copy_caps_ratio_when_equity_below_capital() -> None:
    # Caso real 0xd487e26c: equity $394 copiado com $1.000. Sem o cap, ratio 2.54x
    # inflaria PnL/DD (SIM DD 206% em produção). Com o cap (ratio 1.0), o net é o
    # PnL real do trader − custos e o DD é mensurável (≤100%).
    fills = [sim_fill(NOW - 5 * 86_400_000.0, 1, 2_000, closed_pnl=-500),
             sim_fill(NOW - 2 * 86_400_000.0, 1, 2_000, closed_pnl=800)]
    sim = simulate_copy(fills, 394.0, 1_000.0, now_ms=NOW)
    assert sim is not None
    # ratio capado em 1.0: gross = -500 + 800 = 300; custo = 2×2000×0.00065 = 2.6
    assert sim.gross_pnl_usd == pytest.approx(300.0)
    assert sim.cost_usd == pytest.approx(2.6)
    assert sim.net_pnl_usd == pytest.approx(297.4)      # NÃO 2.54x (=~760)
    # equity 1000 → 1000-500-1.3=498.7 (peak 1000) → DD 50.13%; depois recupera
    assert sim.max_dd_pct == pytest.approx(50.13, abs=0.02)
    assert sim.max_dd_pct <= 100.0


def test_simulate_copy_ratio_capped_matches_ratio_one() -> None:
    # equity < capital (ratio cru 2.0 → cap 1.0) deve bater exatamente com o caso
    # equity == capital (ratio 1.0): mesmo net/gross/DD.
    fills = [sim_fill(NOW - 2 * 86_400_000.0, 1, 5_000, closed_pnl=120)]
    below = simulate_copy(fills, 500.0, 1_000.0, now_ms=NOW)    # ratio cru 2.0
    at = simulate_copy(fills, 1_000.0, 1_000.0, now_ms=NOW)     # ratio 1.0
    assert below is not None and at is not None
    assert below.net_pnl_usd == pytest.approx(at.net_pnl_usd)
    assert below.gross_pnl_usd == pytest.approx(at.gross_pnl_usd)
    assert below.max_dd_pct == pytest.approx(at.max_dd_pct)


def test_simulate_copy_high_equity_unchanged_by_cap() -> None:
    # equity $10k, capital $1k → ratio 0.1 (< 1.0): o cap NÃO dispara, resultado
    # idêntico ao comportamento pré-UPDATE-0066.
    fills = [sim_fill(NOW - 2 * 86_400_000.0, 1, 10_000, closed_pnl=300)]
    sim = simulate_copy(fills, 10_000.0, 1_000.0, now_ms=NOW)
    assert sim is not None
    assert sim.gross_pnl_usd == pytest.approx(30.0)             # 300 × 0.1
    assert sim.cost_usd == pytest.approx(0.65)                  # 1000 × 0.00065
    assert sim.net_pnl_usd == pytest.approx(29.35)


def test_simulate_copy_equity_equals_capital_ratio_one() -> None:
    # equity == capital → ratio exatamente 1.0, sem normalização.
    fills = [sim_fill(NOW - 2 * 86_400_000.0, 1, 4_000, closed_pnl=200)]
    sim = simulate_copy(fills, 1_000.0, 1_000.0, now_ms=NOW)
    assert sim is not None
    assert sim.gross_pnl_usd == pytest.approx(200.0)
    assert sim.cost_usd == pytest.approx(2.6)                   # 4000 × 0.00065
    assert sim.net_pnl_usd == pytest.approx(197.4)


def test_simulate_copy_zero_equity_returns_none() -> None:
    # guard existente preservado: sem equity não há como dimensionar a cópia.
    fills = [sim_fill(NOW - 1 * 86_400_000.0, 1, 2_000, closed_pnl=100)]
    assert simulate_copy(fills, 0.0, 1_000.0, now_ms=NOW) is None


def test_simulate_copy_dd_never_exceeds_100pct() -> None:
    # dados sintéticos extremos: 10 perdas grandes num trader de equity baixo.
    # Sem o cap (ratio 3.33x) a curva iria a negativo (DD > 100%); com o cap fica
    # ≤ 100% (a asserção de sanidade que o spec queria — aqui no teste, não no
    # código de produção, onde `python -O` removeria o assert).
    fills = [sim_fill(NOW - (10 - i) * 86_400_000.0, 1, 3_000, closed_pnl=-90)
             for i in range(10)]
    sim = simulate_copy(fills, 300.0, 1_000.0, now_ms=NOW)
    assert sim is not None
    assert 0.0 < sim.max_dd_pct <= 100.0


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
