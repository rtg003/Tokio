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
        p = self._p(address)
        # UPDATE-0056: perfis podem expor um histórico longitudinal próprio e um
        # flag de truncamento (páginas estouradas). Default preserva o legado.
        return (p.get("fills_longitudinal", p.get("fills", [])),
                p.get("fills_truncated", False))

    def fills_recent(self, address):
        self.requests_used += 1
        p = self._p(address)
        return p.get("fills_recent", p.get("fills", []))

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

    def hypertracker_wallet(self, address):
        # UPDATE-0057 (Fase 2): agregado por wallet. Default {} (sem
        # enriquecimento → idade via portfolio.allTime, comportamento da Fase 1).
        self.requests_used += 1
        return self._p(address).get("hypertracker", {})


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
        # pnl_each=800: com sim_net agora no ranking (peso 0.30, AJUSTES
        # 2026-07-11) o "trader saudável" precisa RENDER na cópia simulada — não
        # basta PF/consistência. $800/trade → sim_net ~$457 (ratio $1k/$50k),
        # levando o score ao terço superior da escala (test_..._full_scale).
        GOOD: {"fills": swing_fills(pnl_each=800),
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
    # logic v14: f16_min_coverage_days caiu p/ 10 dias, e o scalper tem ~19d de
    # histórico → PASSA a cobertura (F16) e morre na SIMULAÇÃO: copiar o edge
    # magro não paga taxa+slippage (F15, net simulado de 30d negativo). Na v9 o
    # F16 era 30d e o matava antes; hoje a simulação é o filtro decisivo.
    assert SCALP in rejected and rejected[SCALP].startswith("F15")
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


def test_v14_cheap_cut_equity_filter_separates_f20(db) -> None:
    """v14: F20 fora do corte barato por padrão — só corta no hard filter.

    Candidato fora da banda F20 (equity aproximada do leaderboard) passa o
    corte barato quando `cheap_cut_equity_filter=false`; é cortado quando true."""
    import copy

    rows = [
        lb_row(GOOD, pnl_7d=800, pnl_30d=9_000, roi_30d=0.18, equity=50_000),
        lb_row(SCALP, pnl_7d=2_000, pnl_30d=25_000, roi_30d=0.40, equity=200_000),
    ]
    profile = {GOOD: {"fills": swing_fills(), "clearinghouse": healthy_clearinghouse()}}
    cfg = copy.deepcopy(CFG)
    cfg["hard_filters"]["f20_min_trader_equity_usd"] = 1_000
    cfg["hard_filters"]["f20_max_trader_equity_usd"] = 60_000  # 200k fica fora

    cfg["collection"]["cheap_cut_equity_filter"] = False
    off = run_scan(FakeClient(rows, dict(profile)), db, cfg, now_ms=NOW_MS)
    assert off.funnel_stats["corte_barato_f20"] == 0

    cfg["collection"]["cheap_cut_equity_filter"] = True
    on = run_scan(FakeClient(rows, dict(profile)), db, cfg, now_ms=NOW_MS)
    assert on.funnel_stats["corte_barato_f20"] == 1


def test_v14_cheap_cut_last_activity_days_cuts_inactive(db) -> None:
    """v14: corta candidatos sem fill recente antes do deep dive (opt-in)."""
    import copy

    rows = [
        lb_row(GOOD, pnl_7d=800, pnl_30d=9_000, roi_30d=0.18, equity=50_000),
        lb_row(SCALP, pnl_7d=700, pnl_30d=8_000, roi_30d=0.15, equity=40_000),
    ]
    profiles = {
        GOOD: {"fills": swing_fills(), "clearinghouse": healthy_clearinghouse()},
        SCALP: {"fills": []},   # inativo: sem fills na janela
    }
    cfg = copy.deepcopy(CFG)

    # desligado (default null): não corta ninguém no corte de atividade
    cfg["collection"]["cheap_cut_last_activity_days"] = None
    disabled = run_scan(FakeClient(rows, dict(profiles)), db, cfg, now_ms=NOW_MS)
    assert disabled.funnel_stats["corte_barato_inativos"] == 0

    # ligado: SCALP (sem fills) é cortado antes do deep dive
    cfg["collection"]["cheap_cut_last_activity_days"] = 3
    result = run_scan(FakeClient(rows, dict(profiles)), db, cfg, now_ms=NOW_MS)
    assert result.funnel_stats["corte_barato_inativos"] == 1
    assert SCALP not in {c.address for c in result.approved}


# ============================================================================
# UPDATE-0054 — reprocessamento diário dos traders JÁ SALVOS (fora do leaderboard)
# ============================================================================
import copy as _copy  # noqa: E402

from engine.strategies.copy_trade.traders_store import (  # noqa: E402
    set_status,
    upsert_candidate,
)

# Endereços de traders salvos que NÃO aparecem no leaderboard sintético — só
# entram no funil via injeção de reprocessamento.
SAVED_OK = "0x" + "71" * 20        # saudável → aprova
SAVED_PIN_FAIL = "0x" + "72" * 20  # reprova F10, mas TESTNET (pinned)
SAVED_MANUAL = "0x" + "73" * 20    # reprova F10, SUGERIDO origin="usuário"
SAVED_DISC = "0x" + "74" * 20      # reprova F10, SUGERIDO origin="discovery"
SAVED_NOFILLS = "0x" + "75" * 20   # sem fills → deep dive vazio (anti-wipe)
SAVED_REJ = "0x" + "76" * 20       # REJEITADO → fora do escopo


def _good_profile() -> dict[str, Any]:
    return {"fills": swing_fills(pnl_each=800),
            "clearinghouse": healthy_clearinghouse()}


def _deposit_fail_profile() -> dict[str, Any]:
    """Mesmo perfil do DEPOSIT: recente (passa F1) mas inflado por aporte (F10)."""
    flat_curve = [[NOW_MS - (90 - d) * DAY_MS, 49_000.0 + d * 135] for d in range(91)]
    return {"fills": swing_fills(n=45, pnl_each=20.0, start_ms=NOW_MS - 45 * DAY_MS),
            "curve": flat_curve,
            "ledger": [{"time": NOW_MS - 15 * DAY_MS,
                        "delta": {"type": "deposit", "usdc": 40_000}}]}


def _client_with(extra: dict[str, dict[str, Any]]) -> FakeClient:
    """make_client() + perfis extra em endereços FORA do leaderboard."""
    c = make_client()
    for addr, prof in extra.items():
        c.profiles[addr.lower()] = prof
    return c


def _seed(db, address: str, *, status: str, origin: str = "discovery",
          score: float | None = None) -> None:
    """Insere um trader salvo direto na tabela `traders` (fora do leaderboard)."""
    upsert_candidate(db, address=address, origin=origin, score=score)
    if status != "SUGERIDO":
        set_status(db, address, status, by="dashboard-humano", human_gate=True)


def _row(db, address: str) -> dict[str, Any]:
    return db.query("SELECT * FROM traders WHERE address = ?", (address.lower(),))[0]


def test_reprocess_injects_saved_trader_outside_leaderboard(db) -> None:
    """SALVO fora do leaderboard é reprocessado: métricas recalculadas."""
    _seed(db, SAVED_OK, status="SALVO", score=None)
    client = _client_with({SAVED_OK: _good_profile()})

    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    assert result.funnel_stats["reprocessados"] >= 1
    assert SAVED_OK in {c.address for c in result.approved}

    persist_scan(db, result, CFG, client=client)
    row = _row(db, SAVED_OK)
    assert row["status"] == "SALVO"          # nunca rebaixado
    assert row["score"] is not None          # métricas atualizadas


def test_reprocess_pinned_never_demoted(db) -> None:
    """TESTNET (copy_pinned=1) que reprova F10 segue TESTNET, sem reject_reason."""
    _seed(db, SAVED_PIN_FAIL, status="TESTNET")
    client = _client_with({SAVED_PIN_FAIL: _deposit_fail_profile()})

    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result, CFG, client=client)

    row = _row(db, SAVED_PIN_FAIL)
    assert row["status"] == "TESTNET"
    assert row["reject_reason"] is None
    assert row["n_trades_30d"] is not None   # deep dive rodou (métricas frescas)


def test_reprocess_manual_suggestion_protected(db) -> None:
    """Q2: SUGERIDO origin='usuário' que reprova NUNCA vira REJEITADO."""
    _seed(db, SAVED_MANUAL, status="SUGERIDO", origin="usuário")
    client = _client_with({SAVED_MANUAL: _deposit_fail_profile()})

    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result, CFG, client=client)

    row = _row(db, SAVED_MANUAL)
    assert row["status"] == "SUGERIDO"       # curadoria humana prevalece
    assert row["reject_reason"] is None
    assert row["n_trades_30d"] is not None


def test_reprocess_discovery_suggestion_still_demotes(db) -> None:
    """Comportamento preservado: SUGERIDO origin='discovery' que reprova → REJEITADO."""
    _seed(db, SAVED_DISC, status="SUGERIDO", origin="discovery")
    client = _client_with({SAVED_DISC: _deposit_fail_profile()})

    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result, CFG, client=client)

    row = _row(db, SAVED_DISC)
    assert row["status"] == "REJEITADO"
    assert row["reject_reason"] and row["reject_reason"].startswith("F10")


def test_reprocess_rejected_out_of_scope(db) -> None:
    """REJEITADO não é reincluído no scan (sem recuperação automática)."""
    _seed(db, SAVED_REJ, status="SUGERIDO", origin="discovery")
    set_status(db, SAVED_REJ, "REJEITADO", by="discovery_v14")
    client = _client_with({SAVED_REJ: _good_profile()})

    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    assert result.funnel_stats.get("reprocessados", 0) == 0
    assert SAVED_REJ not in {c.address for c in result.approved}


def test_reprocess_anti_wipe_preserves_metrics(db) -> None:
    """Trader salvo reprocessado SEM fills (deep dive vazio) mantém métricas."""
    _seed(db, SAVED_NOFILLS, status="SALVO", score=42.0)
    # grava métricas prévias que NÃO podem ser zeradas
    upsert_candidate(db, address=SAVED_NOFILLS, origin="discovery", score=42.0,
                     extras={"n_trades_30d": 33, "sim_net_pnl_usd": 111.0})
    client = _client_with({})  # sem perfil → fills vazios

    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result, CFG, client=client)

    row = _row(db, SAVED_NOFILLS)
    assert row["score"] == 42.0              # não foi apagado
    assert row["n_trades_30d"] == 33
    assert row["sim_net_pnl_usd"] == 111.0


def test_reprocess_flag_off_disables_injection(db) -> None:
    """reprocess_saved_traders=false → nenhum salvo é injetado."""
    _seed(db, SAVED_OK, status="SALVO", score=None)
    client = _client_with({SAVED_OK: _good_profile()})
    cfg = _copy.deepcopy(CFG)
    cfg["collection"]["reprocess_saved_traders"] = False

    result = run_scan(client, db, cfg, now_ms=NOW_MS)
    assert result.funnel_stats.get("reprocessados", 0) == 0
    assert SAVED_OK not in {c.address for c in result.approved}


# ============================================================================
# UPDATE-0062 (v15) — HyperTracker como fonte primária de POSIÇÕES + cohorts
# ============================================================================
from engine.strategies.copy_trade.funnel import deep_dive  # noqa: E402

COHORT_A = "0x" + "1a" * 20
COHORT_B = "0x" + "2b" * 20


def _ht_closed(coin: str, pnl: float, *, open_days: float,
               close_days: float) -> dict[str, Any]:
    """Posição CONSOLIDADA fechada do HyperTracker (abriu/fechou N dias atrás)."""
    return {"coin": coin, "status": "closed", "realizedPnl": pnl,
            "openedAt": NOW_MS - open_days * DAY_MS,
            "closedAt": NOW_MS - close_days * DAY_MS}


def _ht_positions_40d() -> list[dict[str, Any]]:
    """10 posições fechadas cobrindo ~40 dias (7 wins / 3 losses)."""
    pos = [_ht_closed("BTC", 50.0, open_days=40, close_days=39)]
    for i in range(6):
        pos.append(_ht_closed("ETH", 30.0 + i, open_days=30 - i, close_days=29 - i))
    for i in range(3):
        pos.append(_ht_closed("SOL", -20.0, open_days=20 - i, close_days=19 - i))
    return pos


class HTFakeClient(FakeClient):
    """FakeClient + métodos HyperTracker (v15). Lê `ht_positions` do perfil e
    injeta `hypertracker_cohorts` via `external_candidates_by_source`."""

    def __init__(self, rows: list[dict[str, Any]],
                 profiles: dict[str, dict[str, Any]], *,
                 cohort_addrs: list[str] | None = None,
                 heatmap: dict[str, Any] | None = None) -> None:
        super().__init__(rows, profiles)
        self._cohort_addrs = cohort_addrs or []
        self._heatmap = heatmap or {}
        self.ht_positions_calls: list[str] = []

    def ht_positions(self, address: str) -> list[dict[str, Any]]:
        self.ht_positions_calls.append(address.lower())
        return self._p(address).get("ht_positions", [])

    def ht_segments(self) -> list[dict[str, Any]]:
        return [{"id": 8}, {"id": 9}]

    def ht_heatmap(self, *, opened_within: str = "7d") -> dict[str, Any]:
        return self._heatmap

    def external_candidates_by_source(self, sources_cfg):
        return {"hypertracker_cohorts": list(self._cohort_addrs)}


def _ht_client(profiles: dict[str, dict[str, Any]], *,
               cohort_addrs: list[str] | None = None,
               heatmap: dict[str, Any] | None = None) -> HTFakeClient:
    base = make_client()
    client = HTFakeClient(base.rows, base.profiles, cohort_addrs=cohort_addrs,
                          heatmap=heatmap)
    client.profiles.update(profiles)
    return client


def test_v15_ht_positions_source_marks_complete(monkeypatch) -> None:
    """v15: com o HT cobrindo a janela, `position_metrics_source==hypertracker` e
    `metrics_confidence==complete` MESMO com pouquíssimos fills — enquanto a
    confiança DOS FILLS (que gate a copy sim) segue insuficiente."""
    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    client = _ht_client({GOOD: {
        "fills": swing_fills(n=3),                 # amostra pobre (3 closes < 30)
        "clearinghouse": healthy_clearinghouse(),
        "ht_positions": _ht_positions_40d(),       # cobre ~40d, 10 posições
    }})
    c = Candidate(address=GOOD)
    deep_dive(c, client, CFG, set(), NOW_MS)

    assert c.position_metrics_source == "hypertracker"
    assert c.n_trades == 10                         # veio das posições consolidadas
    assert c.metrics_confidence == "complete"       # HT cobre a janela
    # separação: a copy sim segue em fills HL, cuja amostra é insuficiente.
    assert c.fills_metrics_confidence == "insufficient"
    assert c.fills_closed_count == 3


def test_v15_ht_unavailable_falls_back_to_fills(monkeypatch) -> None:
    """Fallback: HT ligado mas SEM posições p/ a wallet → `hl_fills` e métricas
    reconstruídas dos fills (comportamento pré-v15 preservado)."""
    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    client = _ht_client({GOOD: {
        "fills": swing_fills(n=3),
        "clearinghouse": healthy_clearinghouse(),
        # sem "ht_positions" → ht_positions() devolve [] → degrada p/ fills
    }})
    c = Candidate(address=GOOD)
    deep_dive(c, client, CFG, set(), NOW_MS)

    assert c.position_metrics_source == "hl_fills"
    assert c.n_trades == 3                           # veio dos fills, não do HT
    assert c.metrics_confidence == c.fills_metrics_confidence


def test_v15_copy_sim_stays_on_fills_regardless_of_ht(monkeypatch) -> None:
    """INVARIANTE: a simulação de cópia (sim_*) SEMPRE consome fills HL — o
    override de posição pelo HT não altera nenhuma métrica de sim."""
    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    profile = {
        "fills": swing_fills(pnl_each=800),
        "clearinghouse": healthy_clearinghouse(),
        "ht_positions": _ht_positions_40d(),
    }
    # com HT (override de posição)
    c_ht = Candidate(address=GOOD)
    deep_dive(c_ht, _ht_client({GOOD: dict(profile)}), CFG, set(), NOW_MS,
              use_ht_positions=True)
    # sem HT (posição também vem dos fills)
    c_fills = Candidate(address=GOOD)
    deep_dive(c_fills, _ht_client({GOOD: dict(profile)}), CFG, set(), NOW_MS,
              use_ht_positions=False)

    assert c_ht.position_metrics_source == "hypertracker"
    assert c_fills.position_metrics_source == "hl_fills"
    # o override MEXEU nas métricas de posição…
    assert c_ht.n_trades != c_fills.n_trades
    # …mas a copy sim (fills HL) é IDÊNTICA nos dois caminhos.
    assert c_ht.sim_net_pnl_usd is not None
    assert c_ht.sim_net_pnl_usd == pytest.approx(c_fills.sim_net_pnl_usd)
    assert c_ht.sim_stage4_net_usd == pytest.approx(c_fills.sim_stage4_net_usd)


def test_v15_cohort_sourcing_injects_and_counts(db, monkeypatch) -> None:
    """v15: sourcing por cohort injeta endereços NOVOS e conta
    `ht_cohort_novos`/`ht_cohort_aprofundados`."""
    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    client = _ht_client({}, cohort_addrs=[COHORT_A, COHORT_B])
    result = run_scan(client, db, CFG, now_ms=NOW_MS)

    assert result.funnel_stats["ht_cohort_novos"] == 2
    assert result.funnel_stats["ht_cohort_aprofundados"] == 2
