"""Funil v2 ponta a ponta com dados sintéticos — incl. o teste OBRIGATÓRIO de
separação de coortes smart vs. rekt (aceite da spec v5)."""
from __future__ import annotations

import json
import statistics
import time
from typing import Any

import pytest

from engine.strategies.copy_trade.funnel import (
    entry_rule_ok,
    load_config,
    parse_leaderboard_row,
    persist_scan,
    render_report,
    run_scan,
    score_candidate,
    Candidate,
)

NOW_MS = time.time() * 1000
DAY_MS = 86_400_000.0
H_MS = 3_600_000.0

CFG = load_config()


# ----------------------------------------------------------------------------
# fixtures sintéticas
# ----------------------------------------------------------------------------
def lb_row(address: str, *, pnl_7d: float, pnl_30d: float, roi_30d: float,
           equity: float) -> dict[str, Any]:
    return {"ethAddress": address, "accountValue": equity, "displayName": None,
            "windowPerformances": [
                ["week", {"pnl": pnl_7d, "roi": 0.01}],
                ["month", {"pnl": pnl_30d, "roi": roi_30d}],
                ["allTime", {"pnl": pnl_30d * 4, "roi": roi_30d * 2}],
            ]}


def swing_fills(n: int = 55, *, hold_h: float = 24.0, pnl_each: float = 120.0,
                start_ms: float | None = None,
                interval_h: float = 24.0) -> list[dict[str, Any]]:
    """n trades fechados, hold configurável, PnL distribuído — trader saudável.

    v10: intervalo padrão de 24h (era 30h) + n=55 garante ≥5 closes nos
    últimos 7d (F2c) mantendo cobertura ≥30d (F16)."""
    start_ms = start_ms or (NOW_MS - 55 * DAY_MS)
    fills = []
    t = start_ms
    for i in range(n):
        fills.append({"coin": "BTC", "time": t, "side": "B", "sz": 0.5,
                      "startPosition": 0.0, "px": 100_000, "closedPnl": 0})
        fills.append({"coin": "BTC", "time": t + hold_h * H_MS, "side": "A",
                      "sz": 0.5, "startPosition": 0.5, "px": 100_500,
                      "closedPnl": pnl_each + (i % 5)})
        t += interval_h * H_MS
    return fills


def growing_curve(days: int = 90, *, start: float = 50_000.0,
                  daily_gain: float = 0.004) -> list[list[float]]:
    out = []
    v = start
    for d in range(days):
        out.append([NOW_MS - (days - d) * DAY_MS, v])
        v *= 1 + daily_gain + (0.001 if d % 7 else -0.002)
    out.append([NOW_MS, v])
    return out


def pnl_hist_from_curve(curve: list[list[float]]) -> list[list[float]]:
    base = curve[0][1]
    return [[t, v - base] for t, v in curve]


class FakeClient:
    """DataClient sintético configurável por endereço."""

    def __init__(self, rows: list[dict[str, Any]],
                 profiles: dict[str, dict[str, Any]]) -> None:
        self.rows = rows
        self.profiles = profiles
        self.requests_used = 0

    def leaderboard(self):
        self.requests_used += 1
        return self.rows

    def _p(self, address: str) -> dict[str, Any]:
        return self.profiles.get(address.lower(), {})

    def fills_by_time(self, address, *, window_days=60, max_pages=4):
        self.requests_used += 1
        return self._p(address).get("fills", []), False

    def portfolio(self, address):
        self.requests_used += 1
        curve = self._p(address).get("curve", growing_curve())
        return {"month": {"accountValueHistory": [c for c in curve if c[0] >= NOW_MS - 30 * DAY_MS],
                          "pnlHistory": [p for p in pnl_hist_from_curve(curve)
                                         if p[0] >= NOW_MS - 30 * DAY_MS]},
                "allTime": {"accountValueHistory": curve,
                            "pnlHistory": pnl_hist_from_curve(curve)}}

    def clearinghouse(self, address):
        self.requests_used += 1
        return self._p(address).get("clearinghouse",
                                    {"marginSummary": {"accountValue": 50_000},
                                     "assetPositions": []})

    def ledger_updates(self, address, *, window_days=35):
        self.requests_used += 1
        return self._p(address).get("ledger", [])

    def liquid_assets(self, top_n=25):
        self.requests_used += 1
        return {"BTC", "ETH", "SOL"}


GOOD = "0x" + "aa" * 20     # swing saudável — deve APROVAR
SCALP = "0x" + "bb" * 20    # scalper: v3 aprovava com score baixo; v7 REPROVA
                            # no F15 (custo de cópia come o PnL magro)
DEPOSIT = "0x" + "cc" * 20  # inflado por aporte — deve REPROVAR (F10)
REKT1 = "0x" + "dd" * 20
REKT2 = "0x" + "ee" * 20


def healthy_clearinghouse(equity: float = 50_000) -> dict[str, Any]:
    """v7: posição aberta SAUDÁVEL — lev 3x, margem livre ~83%, liq a 30%."""
    return {
        "marginSummary": {"accountValue": equity, "totalMarginUsed": equity * 0.17},
        "assetPositions": [{"position": {
            "coin": "BTC", "szi": 0.25, "positionValue": 25_000,
            "entryPx": 100_000, "liquidationPx": 70_000,
            "leverage": {"value": 3}, "unrealizedPnl": 0,
        }}],
    }


def make_client() -> FakeClient:
    rows = [
        lb_row(GOOD, pnl_7d=800, pnl_30d=9_000, roi_30d=0.18, equity=50_000),
        lb_row(SCALP, pnl_7d=2_000, pnl_30d=25_000, roi_30d=0.40, equity=80_000),
        lb_row(DEPOSIT, pnl_7d=100, pnl_30d=5_000, roi_30d=0.08, equity=49_000),
        lb_row(REKT1, pnl_7d=-500, pnl_30d=-8_000, roi_30d=-0.20, equity=30_000),
        lb_row(REKT2, pnl_7d=-100, pnl_30d=-2_000, roi_30d=-0.05, equity=5_000),
    ]
    flat_curve = [[NOW_MS - (90 - d) * DAY_MS, 49_000.0 + d * 135] for d in range(91)]
    scalper_fills = []
    t = NOW_MS - 20 * DAY_MS
    for i in range(900):
        scalper_fills.append({"coin": "BTC", "time": t, "side": "B", "sz": 1,
                              "startPosition": 0.0, "px": 100_000, "closedPnl": 0})
        scalper_fills.append({"coin": "BTC", "time": t + 0.2 * H_MS, "side": "A",
                              "sz": 1, "startPosition": 1.0, "px": 100_030,
                              "closedPnl": 30.0})
        t += 0.5 * H_MS
    profiles = {
        GOOD: {"fills": swing_fills(),
               "clearinghouse": healthy_clearinghouse()},
        SCALP: {"fills": scalper_fills},
        # início há 45d → último trade recente (passa F1/F2c) e cai no F10
        DEPOSIT: {"fills": swing_fills(n=45, pnl_each=20.0,
                                       start_ms=NOW_MS - 45 * DAY_MS),
                  "curve": flat_curve,
                  "ledger": [{"time": NOW_MS - 15 * DAY_MS,
                              "delta": {"type": "deposit", "usdc": 40_000}}]},
    }
    return FakeClient(rows, profiles)


# ----------------------------------------------------------------------------
def test_scan_approves_swing_rejects_traps(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)

    approved = {c.address: c for c in result.approved}
    rejected = {c.address: c.reject_reason for c in result.rejected}

    assert GOOD in approved
    # v9: o scalper morre ANTES da simulação — 19 dias de histórico < F16 (30d)
    # (na v7 ele morria no F15; o edge magro continua barrado, mais cedo)
    assert SCALP in rejected and rejected[SCALP].startswith("F16")
    assert DEPOSIT in rejected and rejected[DEPOSIT].startswith("F10")  # anti-aporte
    assert result.funnel_stats["aprovados"] == len(result.approved)
    assert result.funnel_stats["coletados"] == 5
    # rekt não entra no funil de aprovação, vira coorte de controle
    assert {c.address for c in result.rekt_sample} == {REKT1, REKT2}


def test_approved_scores_use_full_scale(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    good = next(c for c in result.approved if c.address == GOOD)
    assert good.score >= 55, f"score punitivo demais: {good.score}"
    assert good.windows_positive == "4/4"
    assert good.cohort.startswith("Dolphin")


def test_smart_vs_rekt_separation(db) -> None:
    """Aceite da spec: o score separa CLARAMENTE smart de rekt."""
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    smart_scores = [c.score for c in result.approved]
    # pontua os rekt pela MESMA régua (sem filtros; pior cenário p/ o teste)
    rekt_scores = []
    for c in result.rekt_sample:
        score_candidate(c, CFG)
        rekt_scores.append(c.score)
    assert smart_scores and rekt_scores
    sep = statistics.mean(smart_scores) - statistics.mean(rekt_scores)
    assert sep >= 20, f"separação insuficiente: {sep:.1f} pontos"


def test_persist_scan_populates_traders_and_snapshots(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result, CFG, client=client)

    rows = {r["address"]: r for r in db.query("SELECT * FROM traders")}
    good = rows[GOOD]
    assert good["status"] == "SUGERIDO"
    assert good["logic_version"] == CFG["logic_version"]
    assert good["windows_positive"] == "4/4"
    assert good["reject_reason"] is None
    assert good["n_trades_30d"] > 0
    assert json.loads(good["top_assets"]) == ["BTC"]

    deposit = rows[DEPOSIT]
    assert deposit["status"] == "REJEITADO"
    assert deposit["reject_reason"].startswith("F10")

    snaps = db.query("SELECT DISTINCT cohort FROM cohort_snapshots")
    assert {s["cohort"] for s in snaps} <= {"smart", "rekt"}


def test_rescan_reinstates_rejected_that_now_passes(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result, CFG)
    # reprovado por aporte (F10) para de aportar no scan seguinte: sem ledger e
    # com curva de crescimento orgânico → REJEITADO volta a SUGERIDO
    # (pnl_each alto p/ superar o min_score 60 da v4 — fixture estava quebrado
    # no main desde a v4, que introduziu o piso sem atualizar este teste)
    client2 = make_client()
    client2.profiles[DEPOSIT] = {"fills": swing_fills(pnl_each=500.0),
                                 "clearinghouse": healthy_clearinghouse()}
    result2 = run_scan(client2, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result2, CFG)
    row = db.query("SELECT status, reject_reason FROM traders WHERE address = ?",
                   (DEPOSIT,))[0]
    assert row["status"] == "SUGERIDO"
    assert row["reject_reason"] is None


def test_report_contains_funnel_stats_and_ranking(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    js, md = render_report(result, CFG)
    payload = json.loads(js)
    assert payload["logic_version"] == CFG["logic_version"]
    assert payload["funnel_stats"]["coletados"] == 5
    assert "F10" in json.dumps(payload["rejected_reasons"])
    assert "near_miss" in payload
    assert "| 1 |" in md and "Funil:" in md


def test_entry_rule_windows() -> None:
    # v9: entrada por janelas de PnL DESATIVADA (poder preditivo ~0 no lab) —
    # até all-negative passa; quem decide é a simulação (F16-F19)
    c3 = Candidate(address="0x3", windows_pnl={"7d": -10, "30d": -5, "60d": -50, "90d": -80})
    assert entry_rule_ok(c3, CFG) is True and c3.windows_positive == "0/4"
    # o MECANISMO continua funcional para reativação via config (regras v3):
    import copy

    cfg_v3 = copy.deepcopy(CFG)
    cfg_v3["entry_rule"] = {"min_positive_windows": 2, "required_windows": ["30d"]}
    ok = Candidate(address="0x1", windows_pnl={"7d": -10, "30d": 100, "60d": 50, "90d": 80})
    assert entry_rule_ok(ok, cfg_v3) is True and ok.windows_positive == "3/4"
    bad30 = Candidate(address="0x2", windows_pnl={"7d": 10, "30d": -5, "60d": 50, "90d": 80})
    assert entry_rule_ok(bad30, cfg_v3) is False
    only30 = Candidate(address="0x4", windows_pnl={"7d": -1, "30d": 100, "60d": -5, "90d": -8})
    assert entry_rule_ok(only30, cfg_v3) is False and only30.windows_positive == "1/4"


def test_null_thresholds_disable_f3_f4() -> None:
    """v3: threshold null desabilita o filtro — hiperfrequência e TWRR negativo
    não são mais eliminatórios (viram penalidade de score)."""
    from engine.strategies.copy_trade.funnel import hard_filters
    from engine.core.db import utcnow

    c = Candidate(
        address="0x5", windows_pnl={"7d": 1, "30d": 1, "60d": 1, "90d": 1},
        last_activity=utcnow(), n_trades=50, n_trades_30d=20, n_trades_7d=5,  # F2b/F2c (v5/v10) exigem
        trades_per_day=300.0,
        median_hold_hours=0.1, twrr_30d_pct=-50.0, max_dd_90d_pct=10.0,
        top3_concentration=0.1, avg_leverage=5.0, liquid_volume_share=1.0,
        fills_per_day=10.0, pnl_over_volume=0.01, net_exposure_share=1.0,
        deposit_share=0.0, equity=50_000.0,
    )
    assert hard_filters(c, CFG, now_ms=NOW_MS) is None


# ----------------------------------------------------------------------------
# v7 (UPDATE-0007): copiabilidade real — posição aberta + simulação
# ----------------------------------------------------------------------------
def v7_base_candidate(**overrides: Any) -> Candidate:
    """Candidato que passa TODOS os filtros — cada teste quebra um por vez."""
    from engine.core.db import utcnow

    base = dict(
        address="0x7", windows_pnl={"7d": 1, "30d": 1, "60d": 1, "90d": 1},
        last_activity=utcnow(), n_trades=50, n_trades_30d=20, n_trades_7d=5,
        trades_per_day=2.0, median_hold_hours=24.0, twrr_30d_pct=15.0,
        max_dd_90d_pct=10.0, top3_concentration=0.1, avg_leverage=5.0,
        liquid_volume_share=1.0, fills_per_day=4.0, pnl_over_volume=0.01,
        net_exposure_share=1.0, deposit_share=0.0, equity=50_000.0,
        max_current_leverage=3.0, available_margin_pct=80.0,
        liq_distance_pct=35.0, median_fill_notional=5_000.0,
        sim_net_pnl_usd=12.5,
        # v9: campos de cópia exigidos por F16-F19
        coverage_days=45.0, sim_stage4_net_usd=42.0, sim_max_dd_pct=8.0,
        sim_half_old_net=15.0, sim_half_new_net=27.0,
    )
    base.update(overrides)
    return Candidate(**base)


def test_v7_baseline_passes_all_filters() -> None:
    from engine.strategies.copy_trade.funnel import hard_filters

    assert hard_filters(v7_base_candidate(), CFG, now_ms=NOW_MS) is None


@pytest.mark.parametrize("overrides, expected_prefix", [
    # dossiê #1: 20x AGORA mesmo com média ok
    ({"max_current_leverage": 20.0}, "F7b"),
    # dossiê #6: SOL a 7.5% da liquidação (F12 desabilitado no config de produção)
    ({"liq_distance_pct": 7.5}, "F13"),
    # dossiê #6: equity $50k, fills de ~$100 → cópia de $2.00 com $1k
    ({"equity": 50_000.0, "median_fill_notional": 100.0}, "F11"),
    # cópia simulada não paga o custo
    ({"sim_net_pnl_usd": -3.2}, "F15"),
])
def test_v7_copyability_filters_reject(overrides: dict, expected_prefix: str) -> None:
    from engine.strategies.copy_trade.funnel import hard_filters

    reason = hard_filters(v7_base_candidate(**overrides), CFG, now_ms=NOW_MS)
    assert reason is not None and reason.startswith(expected_prefix), reason


def test_v7_f12_rejects_when_enabled() -> None:
    """F12 continua implementado — só desabilitado via null no config de produção."""
    import copy

    from engine.strategies.copy_trade.funnel import hard_filters

    cfg = copy.deepcopy(CFG)
    cfg["hard_filters"]["f12_min_available_margin_pct"] = 10.0
    reason = hard_filters(v7_base_candidate(available_margin_pct=0.0), cfg,
                          now_ms=NOW_MS)
    assert reason is not None and reason.startswith("F12")


def test_v7_null_thresholds_disable_new_filters() -> None:
    """Threshold null desabilita todos os hard filters F1–F20."""
    import copy

    from engine.strategies.copy_trade.funnel import hard_filters

    cfg = copy.deepcopy(CFG)
    for key in ("f1_recent_activity_days", "f2_min_closed_trades", "f2b_min_trades_30d",
                "f2c_min_trades_7d", "f5_max_drawdown_90d_pct",
                "f6_max_top3_pnl_concentration", "f7_max_avg_leverage",
                "f7b_max_current_leverage", "f12_min_available_margin_pct",
                "f13_min_liq_distance_pct", "f15_sim_window_days",
                "f16_min_coverage_days", "f17_min_sim_net_usd",
                "f19_max_sim_dd_pct", "f20_min_trader_equity_usd",
                "f20_max_trader_equity_usd", "f8_min_liquid_volume_share",
                "f10_max_deposit_growth_share", "f11_min_mirror_notional_usd"):
        cfg["hard_filters"][key] = None
    for key in ("f9_mm_max_trades_per_day", "f9_mm_max_pnl_over_volume",
                "f9_mm_min_tpd_for_pnl_vol", "f9_mm_max_neutral_exposure",
                "f9_mm_min_tpd_for_neutral"):
        cfg["hard_filters"][key] = None
    cfg["hard_filters"]["f18_sim_positive_halves"] = False
    c = v7_base_candidate(last_activity=None, n_trades=0, n_trades_30d=0,
                          n_trades_7d=0, max_dd_90d_pct=99.0,
                          top3_concentration=1.0, avg_leverage=99.0,
                          max_current_leverage=25.0, available_margin_pct=0.0,
                          liq_distance_pct=3.0, liquid_volume_share=0.0,
                          fills_per_day=500.0, pnl_over_volume=0.0,
                          net_exposure_share=0.0, deposit_share=1.0,
                          sim_net_pnl_usd=-50.0, coverage_days=2.0,
                          sim_stage4_net_usd=-99.0, sim_max_dd_pct=90.0,
                          sim_half_new_net=-1.0, equity=999_999.0,
                          median_fill_notional=1.0)
    assert hard_filters(c, cfg, now_ms=NOW_MS) is None


def test_v7_no_open_positions_is_not_rejected() -> None:
    """Sem posição aberta = sem evidência: F7b/F12/F13 não reprovam por None."""
    from engine.strategies.copy_trade.funnel import hard_filters

    c = v7_base_candidate(max_current_leverage=None, available_margin_pct=None,
                          liq_distance_pct=None)
    assert hard_filters(c, CFG, now_ms=NOW_MS) is None


# ----------------------------------------------------------------------------
# v8: Estágio 4 — simulação de cópia como critério final de ranking
# ----------------------------------------------------------------------------
def test_v8_stage4_demotes_negative_copy_sim(db) -> None:
    """Guarda final: cópia negativa → REJEITADO mesmo com F15/F17 desligados."""
    import copy

    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    approved_before = {c.address for c in result.approved}
    assert GOOD in approved_before                       # baseline: GOOD aprova

    # mesma carteira, replay muito mais caro (latência absurda) e SEM os gates
    # F15/F17/F18 — o rebaixamento copy_sim_negativa é a última linha de defesa
    cfg2 = copy.deepcopy(CFG)
    cfg2["copy_simulation"]["latency_slippage_pct"] = 50.0
    cfg2["hard_filters"]["f15_sim_window_days"] = None
    cfg2["hard_filters"]["f17_min_sim_net_usd"] = None
    cfg2["hard_filters"]["f18_sim_positive_halves"] = False
    cfg2["hard_filters"]["f19_max_sim_dd_pct"] = None
    client2 = make_client()
    result2 = run_scan(client2, db, cfg2, now_ms=NOW_MS)
    rejected = {c.address: c.reject_reason for c in result2.rejected}
    assert GOOD in rejected
    assert rejected[GOOD].startswith("copy_sim_negativa")
    assert result2.funnel_stats.get("rebaixados_copy_sim", 0) >= 1


def test_v9_f17_rejects_before_score(db) -> None:
    """v9: cópia que não rende > $10 morre no F17 (hard filter, pré-score)."""
    import copy

    cfg2 = copy.deepcopy(CFG)
    cfg2["copy_simulation"]["latency_slippage_pct"] = 50.0   # torna o GOOD negativo
    cfg2["hard_filters"]["f15_sim_window_days"] = None       # deixa o F17 decidir
    client = make_client()
    result = run_scan(client, db, cfg2, now_ms=NOW_MS)
    rejected = {c.address: c.reject_reason for c in result.rejected}
    assert GOOD in rejected and rejected[GOOD].startswith("F17")


def test_v8_final_ranking_uses_sim_factor(db) -> None:
    """Ranking final = score × fator da simulação (não só score)."""
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    assert result.approved, "esperava ao menos 1 aprovado no fixture"
    for c in result.approved:
        assert c.sim_factor is not None and c.sim_factor > 0
        assert any(r.startswith("cópia simulada:") for r in c.rationale)
    # v9: ranking final = net da cópia simulada (score é informativo)
    ranks = [c.sim_stage4_net_usd for c in result.approved]
    assert ranks == sorted(ranks, reverse=True)


def test_v11_external_source_hook_is_optional(db) -> None:
    """Cliente sem hook externo segue funcional; com hook vazio registra zero."""
    client = make_client()   # FakeClient NÃO tem external_candidates: getattr cobre
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    assert result.funnel_stats.get("fontes_externas_novos") == 0

    client2 = make_client()
    calls: list[dict] = []
    client2.external_candidates = lambda cfg_sources: calls.append(cfg_sources) or []  # type: ignore[attr-defined]
    result2 = run_scan(client2, db, CFG, now_ms=NOW_MS)
    # o hook é chamado, mas sem endereços novos → 0 novos
    assert result2.funnel_stats.get("fontes_externas_novos") == 0
    assert calls and calls[0] is CFG["sources"]


def test_v11_external_quota_enters_when_leaderboard_full(db) -> None:
    import copy

    cfg = copy.deepcopy(CFG)
    cfg["collection"]["deep_dive_max"] = 1
    cfg["collection"]["external_dive_quota"] = 1
    cfg["collection"]["external_interleave_after"] = 1
    client = make_client()
    hyper = "0x" + "12" * 20
    client.external_candidates_by_source = lambda sources: {"hypertracker": [hyper]}  # type: ignore[attr-defined]
    result = run_scan(client, db, cfg, now_ms=NOW_MS)

    assert result.funnel_stats["hypertracker_coletados"] == 1
    assert result.funnel_stats["hypertracker_aprofundados"] == 1
    assert result.funnel_stats["fontes_externas_aprofundados"] == 1
    assert hyper in {c.address for c in result.rejected}


def test_v11_external_quota_falls_back_to_leaderboard(db) -> None:
    import copy

    cfg = copy.deepcopy(CFG)
    cfg["collection"]["deep_dive_max"] = 1
    cfg["collection"]["external_dive_quota"] = 2
    client = make_client()
    client.external_candidates_by_source = lambda sources: {"hypertracker": []}  # type: ignore[attr-defined]
    result = run_scan(client, db, cfg, now_ms=NOW_MS)

    assert result.funnel_stats["hypertracker_coletados"] == 0
    assert result.funnel_stats["fontes_externas_aprofundados"] == 0
    assert result.funnel_stats["fallback_leaderboard_extra"] == 2
    assert result.funnel_stats["aprofundados"] == 3


def test_v11_replay_does_not_persist(db, monkeypatch, tmp_path, capsys) -> None:
    import argparse
    import copy

    from engine.strategies.copy_trade import discovery

    cfg = copy.deepcopy(CFG)
    cfg["collection"]["deep_dive_max"] = 1
    cfg["collection"]["external_dive_quota"] = 0
    monkeypatch.setattr(discovery.funnel, "load_config", lambda: cfg)
    monkeypatch.setattr(discovery, "_db", lambda: db)
    monkeypatch.setattr(discovery, "_replay_client", lambda _db, _cfg: make_client())
    monkeypatch.setattr(discovery, "reports_dir", lambda: tmp_path)

    args = argparse.Namespace(sets=["hard_filters.f2c_min_trades_7d=5"])
    assert discovery.cmd_replay(args) == 0
    assert db.query("SELECT * FROM traders") == []
    assert list(tmp_path.glob("replay-*.json"))
    assert "Replay — diff" in capsys.readouterr().out


def test_v9_hl_client_external_candidates_no_key_is_silent() -> None:
    """v9: hypertracker ON no config, mas SEM chave no ambiente → OFF silencioso
    (zero requests); demais fontes seguem desligadas por flag."""
    import os

    from engine.strategies.copy_trade.hl_data import HLDataClient

    client = HLDataClient(None, request_budget=0)   # budget 0: request explodiria
    cfg_sources = CFG["sources"]
    assert cfg_sources["hypertracker"]["enabled"] is True
    assert cfg_sources["nansen_leaderboard"]["enabled"] is False
    old = os.environ.pop("HYPERTRACKER_API_KEY", None)
    try:
        assert client.external_candidates(cfg_sources) == []
    finally:
        if old is not None:
            os.environ["HYPERTRACKER_API_KEY"] = old


def test_v7_liq_distance_uses_mark_price(db) -> None:
    """F13 mede do MARK price: posição lucrativa longe da entrada mas perto
    da liquidação pelo preço atual deve reprovar."""
    client = make_client()
    # GOOD com posição que ENTROU a 100k (liq 70k = 30% da entrada), mas o
    # mark caiu para 75k: distância real = |75k-70k|/75k = 6.7% → F13
    client.profiles[GOOD]["clearinghouse"] = {
        "marginSummary": {"accountValue": 50_000, "totalMarginUsed": 8_500},
        "assetPositions": [{"position": {
            "coin": "BTC", "szi": 0.25, "positionValue": 18_750,  # mark 75k
            "entryPx": 100_000, "liquidationPx": 70_000,
            "leverage": {"value": 3}, "unrealizedPnl": -6_250,
        }}],
    }
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    rejected = {c.address: c.reject_reason for c in result.rejected}
    assert GOOD in rejected and rejected[GOOD].startswith("F13"), rejected.get(GOOD)
