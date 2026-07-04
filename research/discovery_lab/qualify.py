"""Qualificação POINT-IN-TIME de uma wallet a partir do lab.db.

Reconstrói o `Candidate` do funil como se o scan tivesse rodado no instante
T_qual, usando SÓ dados anteriores a T_qual (fills, curva de equity/pnl,
ledger). Reusa as métricas e filtros de produção (funnel/metrics) — a lógica
testada aqui é a MESMA que iria para o engine.

Limitações honestas (documentadas no relatório):
- Filtros de posição aberta (F7b/F12/F13) e alavancagem média (F7) não têm
  estado histórico — ficam neutros na qualificação retroativa.
- PF sem o PnL não realizado do fechamento da janela (não reconstruível).
- Lista de ativos líquidos (F8) = top N por volume DENTRO da janela A do
  próprio dataset (proxy point-in-time; produção usa volume 24h da HL).
"""
from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from engine.strategies.copy_trade import metrics as M
from engine.strategies.copy_trade.funnel import (
    Candidate,
    classify_style,
    entry_rule_ok,
    hard_filters,
    score_candidate,
    utcnow_from_ms,
)

from research.discovery_lab import store

DAY_MS = 86_400_000.0
WINDOWS = {"7d": 7, "30d": 30, "60d": 60, "90d": 90}


def effective_latency_pct(cfg: dict[str, Any],
                          median_hold_h: float | None) -> float:
    """Custo de latência por perna — hipótese do lab `latency_hold_scaling`:
    scalper de hold curto sofre MUITO mais com 200ms-2s do que swing.
    fator = clamp(4h / hold, 0.5, 4.0); sem evidência de hold → 1.0."""
    stage4 = cfg.get("copy_simulation") or {}
    base = float(stage4.get("latency_slippage_pct", 0))
    lab = cfg.get("lab") or {}
    if not lab.get("latency_hold_scaling"):
        return base
    if median_hold_h is None or median_hold_h <= 0:
        return base
    return base * max(0.5, min(4.0, 4.0 / median_hold_h))


def positive_weeks_30d(curve: list[tuple[float, float, float]],
                       t_qual: float) -> int:
    """Nº de semanas com PnL positivo nas 4 semanas anteriores a t_qual."""
    def pnl_at(t: float) -> float | None:
        best = None
        for point in curve:
            if point[0] <= t:
                best = point[2]
            else:
                break
        return best

    positive = 0
    for k in range(4):
        a = pnl_at(t_qual - (k + 1) * 7 * DAY_MS)
        b = pnl_at(t_qual - k * 7 * DAY_MS)
        if a is not None and b is not None and (b - a) > 0:
            positive += 1
    return positive


def liquid_assets_pit(conn: sqlite3.Connection, t_from: float, t_to: float,
                      top_n: int = 25) -> set[str]:
    rows = conn.execute(
        "SELECT coin, SUM(ABS(px*sz)) v FROM fills WHERE t_ms BETWEEN ? AND ?"
        " GROUP BY coin ORDER BY v DESC LIMIT ?", (t_from, t_to, top_n)).fetchall()
    return {r["coin"] for r in rows}


def _value_at(curve: list[tuple[float, float, float]], t: float,
              idx: int) -> float | None:
    """Último valor da curva com t_ms <= t (idx 1=equity, 2=pnl)."""
    best = None
    for point in curve:
        if point[0] <= t:
            best = point[idx]
        else:
            break
    return best


def build_candidate_pit(conn: sqlite3.Connection, address: str, t_qual: float,
                        cfg: dict[str, Any], liquid: set[str],
                        *, fills_window_days: int = 60) -> Candidate | None:
    """Candidate como o deep_dive teria visto em t_qual (sem clearinghouse)."""
    curve = store.wallet_curve(conn, address, t_to=t_qual)
    if len(curve) < 2:
        return None
    # cobertura honesta: fills truncados (hiperativos, cap de 10k) que terminam
    # ANTES de t_qual não permitem qualificar nem medir — excluir, não reprovar
    w = conn.execute("SELECT fills_truncated, fills_to_ms FROM wallets"
                     " WHERE address = ?", (address,)).fetchone()
    if w and w["fills_truncated"] and w["fills_to_ms"] and \
            w["fills_to_ms"] < t_qual - 3 * DAY_MS:
        return None
    c = Candidate(address=address)

    equity = _value_at(curve, t_qual, 1)
    if not equity or equity <= 0:
        return None
    c.equity = float(equity)
    # piso de equity (mesmo espírito do corte barato da coleta em produção):
    # equity ínfimo explode o ratio $1k/equity e gera simulações absurdas
    min_eq = float((cfg.get("collection") or {}).get("min_equity_usd", 2000))
    if c.equity < min_eq:
        return None

    # janelas de PnL a partir da curva acumulada (mesma regra da produção)
    pnl_now = _value_at(curve, t_qual, 2) or 0.0
    for key, days in WINDOWS.items():
        base = _value_at(curve, t_qual - days * DAY_MS, 2)
        c.windows_pnl[key] = pnl_now - (base if base is not None else curve[0][2])
    eq_30d_ago = _value_at(curve, t_qual - 30 * DAY_MS, 1) or c.equity
    c.roi_30d_pct = (c.windows_pnl["30d"] / eq_30d_ago * 100) if eq_30d_ago > 0 else 0.0

    fills = store.wallet_fills(conn, address,
                               t_from=t_qual - fills_window_days * DAY_MS,
                               t_to=t_qual)
    hf = cfg["hard_filters"]
    if fills:
        times = [f["time"] for f in fills]
        covered_days = max((t_qual - min(times)) / DAY_MS, 1e-9)
        c.fills_per_day = len(fills) / covered_days
        c.last_activity = utcnow_from_ms(max(times))
        episodes = M.position_episodes(fills)
        c.median_hold_hours = M.median_hold_hours(episodes)

        closing = [f for f in fills if float(f.get("closedPnl", 0) or 0) != 0.0]
        c.n_trades = len(closing)
        c.n_trades_30d = len([f for f in closing
                              if f["time"] >= t_qual - 30 * DAY_MS])
        c.trades_per_day = len(closing) / covered_days
        closed_pnls = [float(f["closedPnl"]) for f in closing]
        wins = [p for p in closed_pnls if p > 0]
        c.win_rate = len(wins) / len(closed_pnls) if closed_pnls else None
        c.top3_concentration = M.top_n_concentration(closed_pnls, 3)

        volume = sum(abs(f["sz"] * f["px"]) for f in fills)
        pnl_total = sum(closed_pnls)
        c.pnl_over_volume = (pnl_total / volume) if volume > 0 else 0.0
        liquid_vol = sum(abs(f["sz"] * f["px"]) for f in fills
                         if f["coin"] in liquid)
        c.liquid_volume_share = (liquid_vol / volume) if volume > 0 else 0.0
        by_asset: dict[str, float] = {}
        for f in fills:
            by_asset[f["coin"]] = by_asset.get(f["coin"], 0.0) + abs(f["sz"] * f["px"])
        c.top_assets = [a for a, _ in sorted(by_asset.items(), key=lambda x: -x[1])[:3]]
        gains = sum(wins)
        losses = abs(sum(p for p in closed_pnls if p < 0))
        c.pf = M.profit_factor(gains, losses, 0.0) if (gains or losses) else None
        if closed_pnls and volume > 0:
            c.avg_trade_pnl_pct = (pnl_total / len(closed_pnls)) / \
                (volume / len(fills)) * 100
        c.median_fill_notional = statistics.median(
            abs(f["sz"] * f["px"]) for f in fills)

    # TWRR + anti-aporte na janela [t_qual-30d, t_qual]
    curve_30 = [(t, eq) for t, eq, _ in curve if t >= t_qual - 30 * DAY_MS]
    flows = store.wallet_ledger(conn, address, t_qual - 35 * DAY_MS, t_qual)
    if len(curve_30) >= 2:
        c.twrr_30d_pct = M.twrr(curve_30, flows) * 100
        net_dep = sum(a for _, a in flows if a > 0)
        c.deposit_share = M.deposit_growth_share(
            curve_30[0][1], curve_30[-1][1], net_dep)

    # drawdown 90d point-in-time
    curve_90 = [(t, eq) for t, eq, _ in curve if t >= t_qual - 90 * DAY_MS]
    if len(curve_90) >= 2:
        c.max_dd_90d_pct, c.dd_quality = M.drawdown_quality(
            curve_90, max_dd_cap_pct=float(hf["f5_max_drawdown_90d_pct"]),
            bands=hf.get("f5_dd_quality_bands"))

    # consistência semanal (curva de pnl dos últimos 30d)
    pnl_hist = [(t, pnl) for t, _, pnl in curve if t >= t_qual - 30 * DAY_MS]
    if len(pnl_hist) >= 8:
        step = max(1, len(pnl_hist) // 4)
        weekly = [pnl_hist[min(i + step, len(pnl_hist) - 1)][1] - pnl_hist[i][1]
                  for i in range(0, len(pnl_hist) - 1, step)]
        c.weekly_stability = M.weekly_stability(weekly)

    # F15 (sem latência) + Estágio 4 (com latência) DENTRO da janela A
    cost = cfg["cost_of_copy"]
    if fills and hf.get("f15_sim_window_days") is not None:
        sim = M.simulate_copy(fills, c.equity, float(hf["f11_mirror_capital_usd"]),
                              taker_fee_pct=float(cost["taker_fee_pct"]),
                              slippage_pct=float(cost["slippage_pct"]),
                              window_days=float(hf["f15_sim_window_days"]),
                              now_ms=t_qual)
        if sim is not None:
            c.sim_net_pnl_usd = sim.net_pnl_usd
            c.sim_copy_notional_usd = sim.median_copy_notional_usd
    stage4 = cfg.get("copy_simulation")
    if fills and stage4:
        lat = effective_latency_pct(cfg, c.median_hold_hours)
        sim4 = M.simulate_copy(
            fills, c.equity, float(hf["f11_mirror_capital_usd"]),
            taker_fee_pct=float(cost["taker_fee_pct"]),
            slippage_pct=float(cost["slippage_pct"]),
            latency_slippage_pct=lat,
            window_days=float(stage4.get("window_days", 60)), now_ms=t_qual)
        if sim4 is not None:
            c.sim_stage4_net_usd = sim4.net_pnl_usd
            c.sim_expectancy_usd = sim4.expectancy_usd
            c.sim_max_dd_pct = sim4.max_dd_pct
        # consistência da CÓPIA: net das duas metades de A (30d antigas + 30d
        # recentes) — o edge copiável precisa aparecer nas DUAS.
        # NB: simulate_copy só corta o limite INFERIOR da janela; o superior
        # é garantido aqui filtrando os fills (bug pego na validação do lab).
        for attr, now in (("sim_half_old_net", t_qual - 30 * DAY_MS),
                          ("sim_half_new_net", t_qual)):
            half_fills = [f for f in fills if f["time"] <= now]
            h = M.simulate_copy(
                half_fills, c.equity, float(hf["f11_mirror_capital_usd"]),
                taker_fee_pct=float(cost["taker_fee_pct"]),
                slippage_pct=float(cost["slippage_pct"]),
                latency_slippage_pct=lat, window_days=30.0, now_ms=now)
            setattr(c, attr, h.net_pnl_usd if h is not None else None)

    c.style = classify_style(c.median_hold_hours)
    c.positive_weeks = positive_weeks_30d(curve, t_qual)  # type: ignore[attr-defined]
    return c


def qualify(conn: sqlite3.Connection, address: str, t_qual: float,
            cfg: dict[str, Any], liquid: set[str]) -> tuple[Candidate | None, str | None]:
    """(candidate, motivo_reprovacao|None) — mesma sequência do run_scan."""
    c = build_candidate_pit(conn, address, t_qual, cfg, liquid)
    if c is None:
        return None, "sem dados point-in-time"
    if not entry_rule_ok(c, cfg):
        return c, f"entrada: janelas {c.windows_positive}"
    reason = hard_filters(c, cfg, now_ms=t_qual)
    if reason:
        return c, reason
    score_candidate(c, cfg)
    lab = cfg.get("lab") or {}
    # hipótese (análise 2026-07-04): equity menor prediz cópia melhor (ratio
    # $1k/equity maior transfere mais do edge) — teto opcional de equity
    max_eq = lab.get("max_equity_usd")
    if max_eq is not None and c.equity > float(max_eq):
        return c, f"lab_equity: {c.equity:.0f} > {max_eq}"
    # hipótese: consistência temporal — semanas positivas nas 4 semanas de A
    min_weeks = lab.get("min_positive_weeks")
    if min_weeks is not None and getattr(c, "positive_weeks", 0) < int(min_weeks):
        return c, f"lab_semanas: {getattr(c, 'positive_weeks', 0)}/4 < {min_weeks}"
    # hipótese: copiabilidade como gate binário
    min_cop = lab.get("min_copyability")
    if min_cop is not None and c.components is not None and \
            c.components.copyability < float(min_cop):
        return c, f"lab_copiabilidade: {c.components.copyability:.2f} < {min_cop}"
    min_score = float(cfg.get("score_adjustments", {}).get("min_score_for_suggestion", 0))
    if min_score > 0 and c.score < min_score:
        return c, f"score {c.score:.1f} < mínimo {min_score:.0f}"
    stage4 = cfg.get("copy_simulation")
    # hipótese: estágio 4 como qualificador com piso > 0
    min_net = float(lab.get("min_stage4_net", 0.0))
    if stage4 and c.sim_stage4_net_usd is not None and c.sim_stage4_net_usd <= min_net:
        return c, f"copy_sim_negativa: US$ {c.sim_stage4_net_usd:.2f} <= {min_net}"
    # hipótese: DD máximo da CÓPIA simulada em A (risco da cópia, não do trader)
    max_sim_dd = lab.get("max_sim_dd_pct")
    if max_sim_dd is not None and c.sim_max_dd_pct is not None and \
            c.sim_max_dd_pct > float(max_sim_dd):
        return c, f"lab_sim_dd: {c.sim_max_dd_pct:.1f}% > {max_sim_dd}%"
    # hipótese: cópia consistente — metade recente positiva SEMPRE; metade
    # antiga positiva QUANDO há cobertura de dados (None = sem evidência,
    # não reprova — honestidade com o limite de 90d de fills do dataset)
    if lab.get("sim_positive_halves"):
        old = getattr(c, "sim_half_old_net", None)
        new = getattr(c, "sim_half_new_net", None)
        if new is None or new <= 0 or (old is not None and old <= 0):
            return c, (f"lab_sim_metades: antiga={old if old is not None else 'n/d'}"
                       f" recente={new if new is not None else 'n/d'}")
    if stage4:
        c.sim_factor = M.copy_sim_factor(
            c.sim_stage4_net_usd if c.sim_stage4_net_usd is not None else 0.0,
            float(cfg["hard_filters"]["f11_mirror_capital_usd"]),
            floor=float(stage4.get("factor_floor", 0.5)),
            cap=float(stage4.get("factor_cap", 1.2)))
    return c, None
