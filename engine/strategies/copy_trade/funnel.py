"""Funil v2 do discovery (logic_version 2 — spec PROMPT_DISCOVERY_TRADERS_v5).

3 estágios: coleta ampla (4 janelas) → 11 hard filters (baratos primeiro) →
score ponderado + ajustes + coortes (bidimensional e controle rekt).
Read-only: este módulo nunca importa signer nem envia ordem.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from engine.core.db import Database, utcnow
from engine.strategies.copy_trade import metrics as M
from engine.strategies.copy_trade.traders_store import (
    list_traders,
    set_status,
    upsert_candidate,
)

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "discovery_config.yaml"
DAY_MS = 86_400_000.0
WINDOW_KEYS = ("7d", "30d", "60d", "90d")


def load_config(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or CONFIG_PATH).read_text())


class DataClient(Protocol):
    requests_used: int

    def leaderboard(self) -> list[dict[str, Any]]: ...
    def fills_by_time(self, address: str, *, window_days: int = 60,
                      max_pages: int = 4) -> tuple[list[dict[str, Any]], bool]: ...
    def portfolio(self, address: str) -> dict[str, Any]: ...
    def clearinghouse(self, address: str) -> dict[str, Any]: ...
    def ledger_updates(self, address: str, *, window_days: int = 35) -> list[dict[str, Any]]: ...
    def liquid_assets(self, top_n: int = 25) -> set[str]: ...
    def active_addresses(self, *, window_hours: int = 48,
                         max_addresses: int = 200,
                         min_notional_usd: float = 1000) -> list[str]: ...


# ----------------------------------------------------------------------------
@dataclass
class Candidate:
    address: str
    name: str | None = None
    windows_pnl: dict[str, float] = field(default_factory=dict)   # 7d/30d/60d/90d
    roi_30d_pct: float = 0.0
    equity: float = 0.0
    # métricas do aprofundamento
    twrr_30d_pct: float | None = None
    max_dd_90d_pct: float | None = None
    dd_quality: float = 0.0
    pf: float | None = None
    n_trades: int = 0                 # episódios fechados na janela
    n_trades_30d: int = 0
    n_trades_7d: int = 0            # v10: trades fechados nos últimos 7d (filtro de inatividade)
    fills_per_day: float = 0.0
    trades_per_day: float = 0.0
    median_hold_hours: float | None = None
    win_rate: float | None = None
    win_rate_30d: float | None = None   # v10: win rate só dos últimos 30d (não 60d)
    top3_concentration: float = 0.0
    avg_leverage: float | None = None
    liquid_volume_share: float = 1.0
    pnl_over_volume: float = 0.0
    net_exposure_share: float = 1.0
    deposit_share: float = 0.0
    liq_distance_pct: float | None = None
    avg_trade_pnl_pct: float = 0.0
    # v7 (UPDATE-0007): copiabilidade real — posições abertas + simulação
    max_current_leverage: float | None = None    # max lev das posições ABERTAS
    available_margin_pct: float | None = None    # margem livre / accountValue
    median_fill_notional: float | None = None    # mediana |sz×px| dos fills
    sim_net_pnl_usd: float | None = None         # F15: net da cópia simulada
    sim_copy_notional_usd: float | None = None   # mediana do notional espelhado
    # v8 (Estágio 4): replay com latência — critério FINAL de ranking
    sim_stage4_net_usd: float | None = None      # net do replay c/ latência
    sim_expectancy_usd: float | None = None      # net / trade fechado
    sim_max_dd_pct: float | None = None          # max DD da curva da cópia
    sim_factor: float | None = None              # multiplicador do ranking
    # v9: cobertura + consistência da cópia (F16/F18)
    coverage_days: float | None = None           # dias entre 1º e último fill
    sim_half_old_net: float | None = None        # net da cópia na metade antiga
    sim_half_new_net: float | None = None        # net da cópia na metade recente
    top_assets: list[str] = field(default_factory=list)
    last_activity: str | None = None
    history_truncated: bool = False
    weekly_stability: float = 0.5
    is_top20_alltime: bool = False
    # resultado
    windows_positive: str = "0/4"
    style: str = "misto"
    cohort: str = ""
    score: float = 0.0
    components: M.ScoreComponents | None = None
    reject_reason: str | None = None
    reject_reasons: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)


@dataclass
class ScanResult:
    scan_id: str
    approved: list[Candidate]
    rejected: list[Candidate]
    funnel_stats: dict[str, int]
    rekt_sample: list[Candidate]
    requests_used: int = 0
    duration_s: float = 0.0


# ----------------------------------------------------------------------------
def parse_leaderboard_row(row: dict[str, Any]) -> Candidate:
    perfs = dict(row.get("windowPerformances", []))
    week = perfs.get("week", {})
    month = perfs.get("month", {})
    return Candidate(
        address=str(row["ethAddress"]).lower(),
        name=row.get("displayName"),
        equity=float(row.get("accountValue", 0) or 0),
        roi_30d_pct=float(month.get("roi", 0) or 0) * 100,
        windows_pnl={"7d": float(week.get("pnl", 0) or 0),
                     "30d": float(month.get("pnl", 0) or 0)},
    )


def _series(portfolio: dict[str, Any], window: str, key: str) -> list[tuple[float, float]]:
    data = dict(portfolio).get(window) or {}
    return [(float(t), float(v)) for t, v in (data.get(key) or []) if v is not None]


def fill_windows_from_portfolio(c: Candidate, portfolio: dict[str, Any],
                                now_ms: float | None = None) -> None:
    """Deriva PnL 60d/90d da série allTime (leaderboard só tem 7d/30d)."""
    now_ms = now_ms or time.time() * 1000
    pnl_hist = _series(portfolio, "allTime", "pnlHistory")
    for days, key in ((60, "60d"), (90, "90d")):
        cutoff = now_ms - days * DAY_MS
        base = None
        for t, v in pnl_hist:
            if t <= cutoff:
                base = v
            else:
                break
        if pnl_hist:
            last = pnl_hist[-1][1]
            c.windows_pnl[key] = last - (base if base is not None else pnl_hist[0][1])


def entry_rule_ok(c: Candidate, cfg: dict[str, Any]) -> bool:
    rule = cfg["entry_rule"]
    positive = [w for w in WINDOW_KEYS if c.windows_pnl.get(w, 0.0) > 0]
    c.windows_positive = f"{len(positive)}/4"
    if len(positive) < int(rule["min_positive_windows"]):
        return False
    return all(w in positive for w in rule["required_windows"])


def _is_set(value: Any) -> bool:
    return value is not None


def _float_or_none(value: Any) -> float | None:
    return float(value) if _is_set(value) else None


def _int_or_none(value: Any) -> int | None:
    return int(value) if _is_set(value) else None


def _filter_key(reason: str | None) -> str | None:
    if not reason:
        return None
    if reason.startswith("score "):
        return "score_adjustments.min_score_for_suggestion"
    if reason.startswith("copy_sim_negativa"):
        return "copy_simulation.*"
    prefix = reason.split(":", 1)[0]
    return {
        "F1": "hard_filters.f1_recent_activity_days",
        "F2": "hard_filters.f2_min_closed_trades",
        "F2b": "hard_filters.f2b_min_trades_30d",
        "F2c": "hard_filters.f2c_min_trades_7d",
        "F3": "hard_filters.f3_*",
        "F4": "hard_filters.f4_min_twrr_30d_pct",
        "F5": "hard_filters.f5_max_drawdown_90d_pct",
        "F6": "hard_filters.f6_max_top3_pnl_concentration",
        "F7": "hard_filters.f7_max_avg_leverage",
        "F7b": "hard_filters.f7b_max_current_leverage",
        "F8": "hard_filters.f8_min_liquid_volume_share",
        "F9": "hard_filters.f9_*",
        "F10": "hard_filters.f10_max_deposit_growth_share",
        "F11": "hard_filters.f11_min_mirror_notional_usd",
        "F12": "hard_filters.f12_min_available_margin_pct",
        "F13": "hard_filters.f13_min_liq_distance_pct",
        "F15": "hard_filters.f15_*",
        "F16": "hard_filters.f16_min_coverage_days",
        "F17": "hard_filters.f17_min_sim_net_usd",
        "F18": "hard_filters.f18_sim_positive_halves",
        "F19": "hard_filters.f19_max_sim_dd_pct",
        "F20": "hard_filters.f20_*_trader_equity_usd",
        "copy_sim_negativa": "copy_simulation.*",
        "entrada": "entry_rule.*",
        "score": "score_adjustments.min_score_for_suggestion",
    }.get(prefix)


def _equity_in_band(equity: float, cfg: dict[str, Any]) -> bool:
    f = cfg["hard_filters"]
    min_eq = _float_or_none(f.get("f20_min_trader_equity_usd"))
    max_eq = _float_or_none(f.get("f20_max_trader_equity_usd"))
    if min_eq is not None and equity < min_eq:
        return False
    if max_eq is not None and equity > max_eq:
        return False
    return True


def classify_style(median_hold_h: float | None) -> str:
    if median_hold_h is None:
        return "misto"
    if median_hold_h >= 72:
        return "posição"
    if median_hold_h >= 4:
        return "swing"
    return "misto"


# ----------------------------------------------------------------------------
def precheck_activity(c: Candidate, client: DataClient, cfg: dict[str, Any],
                      now_ms: float | None = None) -> str | None:
    """F1 barato ANTES do deep dive (1 request na janela de 7d).

    Também corrige o viés da paginação: `userFillsByTime` pagina do mais
    antigo p/ o mais novo — em traders hiperativos as páginas da janela longa
    nunca alcançam os fills recentes, e o F1 reprovaria exatamente quem mais
    opera. A janela curta dedicada dá o last_activity correto."""
    f1_days = _int_or_none(cfg["hard_filters"].get("f1_recent_activity_days"))
    if f1_days is None:
        return None
    recent, _ = client.fills_by_time(c.address, window_days=f1_days, max_pages=1)
    if not recent:
        return f"F1: sem trade nos últimos {f1_days}d"
    c.last_activity = utcnow_from_ms(max(float(f["time"]) for f in recent))
    return None


def _cut_inactive_cheap(cheap: list[Candidate], client: DataClient,
                        cfg: dict[str, Any], stats: dict[str, int],
                        logger: Any | None = None) -> list[Candidate]:
    """v14: corta candidatos sem fill recente ANTES do deep dive (opt-in).

    Gasta 1 request curto por candidato do corte barato (`fills_by_time` com
    `max_pages=1`) para não reservar vaga de aprofundamento a quem parou de
    operar. Desligado quando `cheap_cut_last_activity_days` é null. Se o
    orçamento estourar no meio, para de checar e MANTÉM o restante (conservador:
    não corta quem não deu para verificar)."""
    days = _int_or_none(cfg["collection"].get("cheap_cut_last_activity_days"))
    if not days:
        stats["corte_barato_inativos"] = 0
        return cheap
    from engine.strategies.copy_trade.hl_data import RequestBudgetExceeded

    kept: list[Candidate] = []
    for i, c in enumerate(cheap):
        try:
            recent, _ = client.fills_by_time(c.address, window_days=days, max_pages=1)
        except RequestBudgetExceeded:
            # orçamento acabou: mantém este e os que faltam sem checar
            kept.extend(cheap[i:])
            if logger:
                logger.warning("discovery.cheap_cut_budget_exceeded",
                               {"checked": i, "kept_unchecked": len(cheap) - i})
            break
        if recent:
            c.last_activity = utcnow_from_ms(max(float(f["time"]) for f in recent))
            kept.append(c)
    stats["corte_barato_inativos"] = len(cheap) - len(kept)
    return kept


def deep_dive(c: Candidate, client: DataClient, cfg: dict[str, Any],
              liquid: set[str], now_ms: float | None = None) -> None:
    """Coleta cara por candidato + cálculo de todas as métricas do Estágio 2/3."""
    col = cfg["collection"]
    now_ms = now_ms or time.time() * 1000

    fills, truncated = client.fills_by_time(
        c.address, window_days=int(col["fills_window_days"]),
        max_pages=int(col["fills_max_pages"]))
    c.history_truncated = truncated

    portfolio = client.portfolio(c.address)
    fill_windows_from_portfolio(c, portfolio, now_ms)

    # -- fills-derived ------------------------------------------------------
    if fills:
        times = [float(f["time"]) for f in fills]
        covered_days = max((now_ms - min(times)) / DAY_MS, 1e-9)
        c.fills_per_day = len(fills) / covered_days
        latest = utcnow_from_ms(max(times))
        # não regredir o last_activity do precheck (janela curta é mais fresca)
        c.last_activity = max(c.last_activity, latest) if c.last_activity else latest
        episodes = M.position_episodes(fills)
        c.median_hold_hours = M.median_hold_hours(episodes)

        # "trade fechado" = fill de fechamento (closedPnl != 0): position
        # traders reduzem parcialmente sem nunca zerar — contar episódios
        # zerados os excluiria injustamente (validação real de 2026-07-03)
        closing_fills = [f for f in fills
                         if float(f.get("closedPnl", 0) or 0) != 0.0]
        c.n_trades = len(closing_fills)
        c.n_trades_30d = len([f for f in closing_fills
                              if float(f["time"]) >= now_ms - 30 * DAY_MS])
        c.n_trades_7d = len([f for f in closing_fills
                             if float(f["time"]) >= now_ms - 7 * DAY_MS])
        c.trades_per_day = len(closing_fills) / covered_days

        closed_pnls = [float(f.get("closedPnl", 0) or 0) for f in closing_fills]
        wins = [p for p in closed_pnls if p > 0]
        c.win_rate = len(wins) / len(closed_pnls) if closed_pnls else None
        # v10: win_rate_30d — calculado só sobre closing fills dos últimos 30d
        # (o win_rate da janela de 60d é enviesado por fills antigos)
        closing_30d = [f for f in closing_fills
                       if float(f["time"]) >= now_ms - 30 * DAY_MS]
        closed_pnls_30d = [float(f.get("closedPnl", 0) or 0) for f in closing_30d]
        wins_30d = [p for p in closed_pnls_30d if p > 0]
        c.win_rate_30d = len(wins_30d) / len(closed_pnls_30d) if closed_pnls_30d else None
        c.top3_concentration = M.top_n_concentration(closed_pnls, 3)

        volume = sum(abs(float(f.get("sz", 0)) * float(f.get("px", 0))) for f in fills)
        pnl_total = sum(closed_pnls)
        c.pnl_over_volume = (pnl_total / volume) if volume > 0 else 0.0
        if liquid:
            liquid_vol = sum(abs(float(f.get("sz", 0)) * float(f.get("px", 0)))
                             for f in fills if str(f.get("coin")) in liquid)
            c.liquid_volume_share = (liquid_vol / volume) if volume > 0 else 0.0
        else:
            c.liquid_volume_share = 1.0
        by_asset: dict[str, float] = {}
        for f in fills:
            by_asset[str(f.get("coin"))] = by_asset.get(str(f.get("coin")), 0.0) + \
                abs(float(f.get("sz", 0)) * float(f.get("px", 0)))
        c.top_assets = [a for a, _ in sorted(by_asset.items(), key=lambda x: -x[1])[:3]]
        gains = sum(wins)
        losses = abs(sum(p for p in closed_pnls if p < 0))
        if closed_pnls and volume > 0:
            c.avg_trade_pnl_pct = (pnl_total / len(closed_pnls)) / \
                (volume / len(fills)) * 100
        # v7: notional REAL por fill (o F11 antigo assumia 5% do equity — bug)
        c.median_fill_notional = statistics.median(
            abs(float(f.get("sz", 0)) * float(f.get("px", 0))) for f in fills)
    else:
        gains = losses = 0.0

    # -- clearinghouse (posições abertas) ---------------------------------------
    ch = client.clearinghouse(c.address)
    positions = [p["position"] for p in ch.get("assetPositions", [])]
    unrealized = sum(float(p.get("unrealizedPnl", 0) or 0) for p in positions)
    c.pf = M.profit_factor(gains, losses, unrealized) if (gains or losses or unrealized) else None
    equity_now = float(ch.get("marginSummary", {}).get("accountValue", 0) or 0)
    if equity_now > 0:
        c.equity = equity_now
    levs = [float(p.get("leverage", {}).get("value", 0) or 0) for p in positions]
    c.avg_leverage = statistics.mean([l for l in levs if l > 0]) if any(levs) else None
    # v7 — F7b: alavancagem ATUAL (o F7 mede a média; o trader pode estar 20x agora)
    c.max_current_leverage = max((l for l in levs if l > 0), default=None)
    # v7 — F12: margem disponível (available = 0 → 100% comprometido)
    margin_used = float(ch.get("marginSummary", {}).get("totalMarginUsed", 0) or 0)
    if equity_now > 0:
        c.available_margin_pct = max(0.0, (equity_now - margin_used) / equity_now * 100)
    # distância de liquidação da posição mais próxima — referência = MARK price
    # (v7: usar a entrada escondia risco em posição que já andou muito)
    dists = []
    for p in positions:
        liq_px = p.get("liquidationPx")
        if liq_px is None:
            continue
        szi = abs(float(p.get("szi", 0) or 0))
        notional = float(p.get("positionValue", 0) or 0)
        ref = (notional / szi) if szi > 0 and notional > 0 else \
            float(p.get("entryPx", 0) or 0)
        if ref <= 0:
            continue
        dists.append(abs(ref - float(liq_px)) / ref * 100)
    c.liq_distance_pct = min(dists) if dists else None
    # exposição líquida (delta-neutro p/ F9)
    notionals = [float(p.get("positionValue", 0) or 0) *
                 (1 if float(p.get("szi", 0) or 0) >= 0 else -1) for p in positions]
    gross = sum(abs(n) for n in notionals)
    c.net_exposure_share = (abs(sum(notionals)) / gross) if gross > 0 else 1.0

    # -- TWRR e anti-aporte -------------------------------------------------------
    curve_30d = _series(portfolio, "month", "accountValueHistory")
    flows = []
    for u in client.ledger_updates(c.address, window_days=35):
        delta = u.get("delta", {})
        kind = str(delta.get("type", ""))
        amount = float(delta.get("usdc", 0) or 0)
        if kind in ("deposit",):
            flows.append((float(u.get("time", 0)), amount))
        elif kind in ("withdraw", "withdrawal"):
            flows.append((float(u.get("time", 0)), -abs(amount)))
    if len(curve_30d) >= 2:
        c.twrr_30d_pct = M.twrr(curve_30d, flows) * 100
        net_dep = sum(a for _, a in flows if a > 0)
        c.deposit_share = M.deposit_growth_share(curve_30d[0][1], curve_30d[-1][1], net_dep)

    # -- drawdown 90d ----------------------------------------------------------
    curve_all = _series(portfolio, "allTime", "accountValueHistory")
    curve_90d = [(t, v) for t, v in curve_all if t >= now_ms - 90 * DAY_MS] or curve_30d
    dd_bands = cfg["hard_filters"].get("f5_dd_quality_bands")
    f5_cap = _float_or_none(cfg["hard_filters"].get("f5_max_drawdown_90d_pct"))
    c.max_dd_90d_pct, c.dd_quality = M.drawdown_quality(
        curve_90d,
        max_dd_cap_pct=f5_cap if f5_cap is not None else 100.0,
        bands=dd_bands)

    # -- consistência semanal ------------------------------------------------------
    pnl_30d_hist = _series(portfolio, "month", "pnlHistory")
    if len(pnl_30d_hist) >= 8:
        step = max(1, len(pnl_30d_hist) // 4)
        weekly = [pnl_30d_hist[min(i + step, len(pnl_30d_hist) - 1)][1] - pnl_30d_hist[i][1]
                  for i in range(0, len(pnl_30d_hist) - 1, step)]
        c.weekly_stability = M.weekly_stability(weekly)

    # simulações de cópia (F15/F17/F18) — implementação ÚNICA, usada também
    # pelo laboratório (research/discovery_lab/qualify.py)
    compute_copy_sims(c, fills, cfg, now_ms)

    c.style = classify_style(c.median_hold_hours)


def compute_copy_sims(c: Candidate, fills: list[dict[str, Any]],
                      cfg: dict[str, Any], now_ms: float) -> None:
    """Preenche as métricas de simulação de cópia do candidato.

    - F15 (v7): net sem latência na janela curta.
    - Estágio 4 (v8): net/expectância/DD com latência e TETO DE ALAVANCAGEM (v9).
    - F16 (v9): cobertura de fills (dias entre 1º e último).
    - F18 (v9): net das duas metades da janela (consistência da CÓPIA).
    """
    hf = cfg["hard_filters"]
    if not fills or c.equity <= 0:
        return
    cost = cfg["cost_of_copy"]
    stage4 = cfg.get("copy_simulation") or {}
    max_lev = stage4.get("max_copy_leverage")
    capital = float(hf["f11_mirror_capital_usd"])

    times = [float(f.get("time", 0)) for f in fills]
    c.coverage_days = (max(times) - min(times)) / DAY_MS if len(times) >= 2 else 0.0

    if hf.get("f15_sim_window_days") is not None:
        sim = M.simulate_copy(
            fills, c.equity, capital,
            taker_fee_pct=float(cost["taker_fee_pct"]),
            slippage_pct=float(cost["slippage_pct"]),
            max_copy_leverage=max_lev,
            window_days=float(hf["f15_sim_window_days"]), now_ms=now_ms)
        if sim is not None:
            c.sim_net_pnl_usd = sim.net_pnl_usd
            c.sim_copy_notional_usd = sim.median_copy_notional_usd

    if stage4:
        lat = float(stage4.get("latency_slippage_pct", 0))
        window = float(stage4.get("window_days", 60))
        sim4 = M.simulate_copy(
            fills, c.equity, capital,
            taker_fee_pct=float(cost["taker_fee_pct"]),
            slippage_pct=float(cost["slippage_pct"]),
            latency_slippage_pct=lat, max_copy_leverage=max_lev,
            window_days=window, now_ms=now_ms)
        if sim4 is not None:
            c.sim_stage4_net_usd = sim4.net_pnl_usd
            c.sim_expectancy_usd = sim4.expectancy_usd
            c.sim_max_dd_pct = sim4.max_dd_pct
        # F18 — metades: o limite SUPERIOR é garantido filtrando os fills
        # (simulate_copy só corta o inferior)
        half = window / 2
        for attr, half_end in (("sim_half_old_net", now_ms - half * DAY_MS),
                               ("sim_half_new_net", now_ms)):
            half_fills = [f for f in fills if float(f.get("time", 0)) <= half_end]
            h = M.simulate_copy(
                half_fills, c.equity, capital,
                taker_fee_pct=float(cost["taker_fee_pct"]),
                slippage_pct=float(cost["slippage_pct"]),
                latency_slippage_pct=lat, max_copy_leverage=max_lev,
                window_days=half, now_ms=half_end)
            setattr(c, attr, h.net_pnl_usd if h is not None else None)


def utcnow_from_ms(ms: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ----------------------------------------------------------------------------
def hard_filters_all(c: Candidate, cfg: dict[str, Any],
                     now_ms: float | None = None) -> list[str]:
    """F1–F20 completos. Todo threshold null = filtro desligado."""
    f = cfg["hard_filters"]
    now_ms = now_ms or time.time() * 1000
    reasons: list[str] = []

    f1_days = _float_or_none(f.get("f1_recent_activity_days"))
    if f1_days is not None:
        if not c.last_activity:
            reasons.append("F1: sem atividade recente")
        else:
            from datetime import datetime

            last_ms = datetime.fromisoformat(c.last_activity).timestamp() * 1000
            if now_ms - last_ms > f1_days * DAY_MS:
                reasons.append(f"F1: último trade há mais de {f['f1_recent_activity_days']}d")

    f2 = _int_or_none(f.get("f2_min_closed_trades"))
    if f2 is not None and c.n_trades < f2:
        reasons.append(f"F2: amostra {c.n_trades} < {f['f2_min_closed_trades']} trades fechados")

    # v5: F2b — trader sem atividade recente não tem o que copiar
    f2b = _int_or_none(f.get("f2b_min_trades_30d"))
    if f2b is not None and c.n_trades_30d < f2b:
        reasons.append(f"F2b: {c.n_trades_30d} trades fechados nos últimos 30d < {f2b}")

    # v10: F2c — trader sem atividade nas últimas 48h/7d não tem o que copiar AGORA
    f2c = _int_or_none(f.get("f2c_min_trades_7d"))
    if f2c is not None and c.n_trades_7d < f2c:
        reasons.append(f"F2c: {c.n_trades_7d} trades fechados nos últimos 7d < {f2c} (inativo)")

    # v9 — F16: cobertura mínima de histórico (dias entre 1º e último fill).
    # Auditoria do "top 1" do lab: 5 dias de atividade geravam +250% irreal.
    f16 = _float_or_none(f.get("f16_min_coverage_days"))
    if f16 is not None and c.coverage_days is not None and c.coverage_days < f16:
        reasons.append(f"F16: histórico de {c.coverage_days:.0f}d < "
                       f"{f['f16_min_coverage_days']}d (wallet nova demais p/ julgar)")

    # v11 — F20: banda de equity do trader; ambos os lados são ajustáveis.
    f20_min = _float_or_none(f.get("f20_min_trader_equity_usd"))
    f20_max = _float_or_none(f.get("f20_max_trader_equity_usd"))
    if f20_min is not None and c.equity < f20_min:
        reasons.append(f"F20: equity US$ {c.equity:,.0f} < "
                       f"{f20_min:,.0f} (pequeno demais p/ filtrar nesta banda)")
    if f20_max is not None and c.equity > f20_max:
        reasons.append(f"F20: equity US$ {c.equity:,.0f} > "
                       f"{f20_max:,.0f} (grande demais p/ espelhar)")

    # F3 anti-scalper (threshold null = desabilitado): exige EVIDÊNCIA positiva
    # (hold None nunca reprova sozinho)
    f3_max = _float_or_none(f.get("f3_max_trades_per_day"))
    if f3_max is not None and c.trades_per_day > f3_max:
        reasons.append(f"F3: {c.trades_per_day:.1f} trades/dia > {f['f3_max_trades_per_day']}")
    f3_min = _float_or_none(f.get("f3_min_avg_holding_hours"))
    if f3_min is not None and c.median_hold_hours is not None and c.median_hold_hours < f3_min:
        reasons.append(f"F3: hold mediano {c.median_hold_hours:.2f}h < {f['f3_min_avg_holding_hours']}h")

    f4 = _float_or_none(f.get("f4_min_twrr_30d_pct"))
    if f4 is not None and c.twrr_30d_pct is not None and c.twrr_30d_pct < f4:
        reasons.append(f"F4: TWRR 30d {c.twrr_30d_pct:.1f}% < {f['f4_min_twrr_30d_pct']}%")

    f5 = _float_or_none(f.get("f5_max_drawdown_90d_pct"))
    if f5 is not None and c.max_dd_90d_pct is not None and c.max_dd_90d_pct > f5:
        reasons.append(f"F5: max DD 90d {c.max_dd_90d_pct:.1f}% > {f['f5_max_drawdown_90d_pct']}%")

    # v7 — F13: posição aberta perto demais da liquidação (medida do mark price)
    f13 = _float_or_none(f.get("f13_min_liq_distance_pct"))
    if f13 is not None and c.liq_distance_pct is not None and c.liq_distance_pct < f13:
        reasons.append(f"F13: dist. liquidação {c.liq_distance_pct:.1f}% < "
                       f"{f['f13_min_liq_distance_pct']}%")

    f6 = _float_or_none(f.get("f6_max_top3_pnl_concentration"))
    if f6 is not None and c.top3_concentration > f6:
        reasons.append(f"F6: top-3 trades = {c.top3_concentration * 100:.0f}% do PnL")

    f7 = _float_or_none(f.get("f7_max_avg_leverage"))
    if f7 is not None and c.avg_leverage is not None and c.avg_leverage > f7:
        reasons.append(f"F7: alavancagem média {c.avg_leverage:.1f}x > {f['f7_max_avg_leverage']}x")

    # v7 — F7b: alavancagem ATUAL das posições abertas (a média esconde o agora)
    f7b = _float_or_none(f.get("f7b_max_current_leverage"))
    if f7b is not None and c.max_current_leverage is not None and c.max_current_leverage > f7b:
        reasons.append(f"F7b: alavancagem atual {c.max_current_leverage:.1f}x > "
                       f"{f['f7b_max_current_leverage']}x")

    # v7 — F12: margem 100% comprometida = qualquer movimento contra liquida
    f12 = _float_or_none(f.get("f12_min_available_margin_pct"))
    if f12 is not None and c.available_margin_pct is not None and c.available_margin_pct < f12:
        reasons.append(f"F12: margem disponível {c.available_margin_pct:.1f}% < "
                       f"{f['f12_min_available_margin_pct']}%")

    f8 = _float_or_none(f.get("f8_min_liquid_volume_share"))
    if f8 is not None and c.liquid_volume_share < f8:
        reasons.append(f"F8: só {c.liquid_volume_share * 100:.0f}% do volume em ativos líquidos")

    f9_values = (
        _float_or_none(f.get("f9_mm_max_trades_per_day")),
        _float_or_none(f.get("f9_mm_max_pnl_over_volume")),
        _float_or_none(f.get("f9_mm_min_tpd_for_pnl_vol")),
        _float_or_none(f.get("f9_mm_max_neutral_exposure")),
        _float_or_none(f.get("f9_mm_min_tpd_for_neutral")),
    )
    if any(v is not None for v in f9_values) and \
            M.looks_like_mm(
                c.fills_per_day, c.pnl_over_volume, c.net_exposure_share,
                max_tpd=f9_values[0],
                max_pnl_vol=f9_values[1],
                min_tpd_for_pnl_vol=f9_values[2],
                max_neutral_exposure=f9_values[3],
                min_tpd_for_neutral=f9_values[4]):
        reasons.append("F9: padrão de MM/arb/delta-neutro")

    f10 = _float_or_none(f.get("f10_max_deposit_growth_share"))
    if f10 is not None and c.deposit_share > f10:
        reasons.append(f"F10: {c.deposit_share * 100:.0f}% do crescimento veio de aporte")

    # v7 — F11 corrigido: notional REAL dos fills (o placeholder de 5% do equity
    # estimava US$ 50 de cópia onde o real era US$ 1.80 — dossiê #6 do Hermes)
    f11_min = _float_or_none(f.get("f11_min_mirror_notional_usd"))
    if f11_min is not None and c.equity > 0 and c.median_fill_notional is not None:
        copy_notional = c.median_fill_notional * \
            float(f["f11_mirror_capital_usd"]) / c.equity
        if copy_notional < f11_min:
            reasons.append(f"F11: cópia estimada US$ {copy_notional:.2f} < "
                           f"{f['f11_min_mirror_notional_usd']} com capital configurado")

    # v7 — F15: simulação retroativa — cópia que não paga taxa+slippage não serve
    f15_window = _float_or_none(f.get("f15_sim_window_days"))
    if f15_window is not None and c.sim_net_pnl_usd is not None and \
            c.sim_net_pnl_usd <= float(f.get("f15_min_net_pnl_usd", 0.0)):
        reasons.append(f"F15: cópia simulada {f['f15_sim_window_days']}d com "
                       f"US$ {f['f11_mirror_capital_usd']:.0f} → PnL líquido "
                       f"US$ {c.sim_net_pnl_usd:.2f}")

    # v9 — F17: a cópia simulada (com latência e teto de alavancagem) precisa
    # RENDER, não só não perder. Quintis do lab: top +$71 em B vs +$0.3 no 2º.
    f17 = _float_or_none(f.get("f17_min_sim_net_usd"))
    if f17 is not None and c.sim_stage4_net_usd is not None and c.sim_stage4_net_usd <= f17:
        reasons.append(f"F17: cópia simulada rende US$ {c.sim_stage4_net_usd:.2f} <= "
                       f"{f['f17_min_sim_net_usd']} (não paga o risco)")

    # v9 — F18: edge nas DUAS metades da janela (mata o sortudo de uma perna).
    # Só opera quando a simulação foi computada (sim_stage4 não-None); metade
    # antiga sem dados = sem evidência, não reprova — mas a RECENTE é
    # obrigatória. Lab: corte 2 foi de −$94 p/ +$770 com este gate.
    if f.get("f18_sim_positive_halves") and c.sim_stage4_net_usd is not None:
        if c.sim_half_new_net is None or c.sim_half_new_net <= 0 or \
                (c.sim_half_old_net is not None and c.sim_half_old_net <= 0):
            old_s = f"{c.sim_half_old_net:.2f}" if c.sim_half_old_net is not None else "n/d"
            new_s = f"{c.sim_half_new_net:.2f}" if c.sim_half_new_net is not None else "n/d"
            reasons.append(f"F18: metades da cópia (antiga US$ {old_s} / recente US$ {new_s})")

    # v9 — F19: DD máximo da curva da CÓPIA (risco da cópia, não do trader).
    # Lab: perdedores fora da amostra tinham DD de cópia 56–75% já visível aqui.
    f19 = _float_or_none(f.get("f19_max_sim_dd_pct"))
    if f19 is not None and c.sim_max_dd_pct is not None and c.sim_max_dd_pct > f19:
        reasons.append(f"F19: DD da cópia simulada {c.sim_max_dd_pct:.1f}% > "
                       f"{f['f19_max_sim_dd_pct']}%")
    return reasons


def hard_filters(c: Candidate, cfg: dict[str, Any],
                 now_ms: float | None = None) -> str | None:
    """Retorna o primeiro motivo de reprovação, preservando a API histórica."""
    reasons = hard_filters_all(c, cfg, now_ms)
    c.reject_reasons = reasons
    return reasons[0] if reasons else None


# ----------------------------------------------------------------------------
_COMPONENT_KEYS = (
    "consistency", "profit_factor", "roi_log", "drawdown_quality",
    "copyability", "net_expectancy", "sim_net",
)


def serialize_components(comps: M.ScoreComponents) -> str:
    """Serializa os 7 componentes normalizados [0,1] + adjustments para JSON
    (Parte 2 — reclassify sem refazer o deep dive)."""
    payload = {k: getattr(comps, k) for k in _COMPONENT_KEYS}
    payload["adjustments"] = [[name, val] for name, val in comps.adjustments]
    return json.dumps(payload, ensure_ascii=False)


def deserialize_components(raw: str | None) -> M.ScoreComponents | None:
    """Reconstrói ScoreComponents do JSON persistido; None se ausente/inválido."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    comps = M.ScoreComponents(
        **{k: float(data.get(k, 0.0) or 0.0) for k in _COMPONENT_KEYS}
    )
    comps.adjustments = [(str(n), float(v)) for n, v in data.get("adjustments", [])]
    return comps


def score_candidate(c: Candidate, cfg: dict[str, Any]) -> float:
    """Estágio 3 — score composto + ajustes pós-score (spec v5)."""
    w = cfg["score_weights"]
    adj = cfg["score_adjustments"]
    cop = cfg["copyability"]
    cost = cfg["cost_of_copy"]

    positive = sum(1 for k in WINDOW_KEYS if c.windows_pnl.get(k, 0.0) > 0)
    cost_pct = 2 * (float(cost["taker_fee_pct"]) + float(cost["slippage_pct"]))

    comps = M.ScoreComponents(
        consistency=M.consistency_score(positive, 4, c.weekly_stability),
        profit_factor=M.pf_score_credit(c.pf or 0.0, c.n_trades),
        roi_log=M.roi_log_score(c.roi_30d_pct),
        drawdown_quality=c.dd_quality,
        copyability=M.copyability_score(
            c.median_hold_hours, c.trades_per_day, c.liquid_volume_share,
            sweet_spot=tuple(cop["hold_sweet_spot_hours"]),
            freq_spot=tuple(cop["freq_sweet_spot_trades_day"])),
        net_expectancy=M.net_expectancy_score(c.avg_trade_pnl_pct, cost_pct),
        # sim_net: cópia simulada líquida 30d normalizada — US$ 5k = score máximo.
        sim_net=max(0.0, min(1.0, (c.sim_net_pnl_usd or 0.0) / 5000.0)),
    )
    if positive == 4:
        comps.adjustments.append(("consistencia_4/4", float(adj["full_consistency_bonus"])))
    if c.liq_distance_pct is not None and \
            c.liq_distance_pct < float(adj["liq_distance_threshold_pct"]):
        comps.adjustments.append(("risco_liquidacao", float(adj["liq_distance_penalty"])))
    if c.is_top20_alltime:
        comps.adjustments.append(("crowding_top20", float(adj["crowding_penalty"])))

    # (removido em 2026-07-11) penalidade de "PF absurdo" (> 10): rebaixava
    # traders excelentes. PF é capado em 10.0 na exibição e sim_net (peso 0.30)
    # é a régua decisiva.

    c.components = comps
    c.score = M.composite_score(comps, w)
    # v5: capar PF exibido em 10.0 (PF > 10 é enganoso)
    pf_display = min(c.pf, 10.0) if c.pf is not None else None
    c.rationale = [
        f"janelas positivas: {c.windows_positive}",
        f"TWRR 30d: {c.twrr_30d_pct:.1f}%" if c.twrr_30d_pct is not None else "TWRR: n/d",
        f"PF: {pf_display:.2f} (n={c.n_trades})" if pf_display is not None else "PF: n/d",
        f"max DD 90d: {c.max_dd_90d_pct:.1f}%" if c.max_dd_90d_pct is not None else "DD: n/d",
        f"hold mediano: {c.median_hold_hours:.1f}h" if c.median_hold_hours is not None
        else "hold: n/d",
        # v7: copiabilidade real (posições abertas + simulação)
        f"margem disponível: {c.available_margin_pct:.0f}%"
        if c.available_margin_pct is not None else "margem: n/d",
        f"lev atual: {c.max_current_leverage:.1f}x"
        if c.max_current_leverage is not None else "lev atual: sem posição",
        f"cópia simulada 30d: US$ {c.sim_net_pnl_usd:+.2f} líquido"
        if c.sim_net_pnl_usd is not None else "cópia simulada: n/d",
        *(f"ajuste {name}: {val:+.0f}" for name, val in comps.adjustments),
    ]
    return c.score


def assign_cohort(c: Candidate, cfg: dict[str, Any]) -> None:
    bands = cfg["cohorts"]
    size = M.size_cohort(c.equity, {k: float(v) for k, v in bands["size_bands"].items()})
    pnl_acc = c.windows_pnl.get("90d", c.windows_pnl.get("30d", 0.0))
    pnl = M.pnl_cohort(pnl_acc, {k: float(v) for k, v in bands["pnl_bands"].items()})
    label = "Money Printer" if pnl == "Printer" else pnl
    c.cohort = f"{size} · {label}"


def _sort_for_deep_dive(candidates: list[Candidate], sort_by: str) -> None:
    if sort_by == "pnl_7d":
        candidates.sort(key=lambda c: -c.windows_pnl.get("7d", 0.0))
    elif sort_by == "equity_asc":
        candidates.sort(key=lambda c: (c.equity <= 0, c.equity, -c.roi_30d_pct))
    else:
        candidates.sort(key=lambda c: -c.roi_30d_pct)


def _valid_addresses(addresses: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for addr in addresses:
        addr = addr.lower()
        if addr.startswith("0x") and len(addr) == 42 and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def _external_candidates_by_source(client: DataClient, cfg: dict[str, Any],
                                   logger: Any | None,
                                   stats: dict[str, int]) -> dict[str, list[str]]:
    sources = cfg.get("sources") or {}
    by_source_fn = getattr(client, "external_candidates_by_source", None)
    external_fn = getattr(client, "external_candidates", None)
    if by_source_fn is None and external_fn is None:
        return {}
    try:
        if by_source_fn is not None:
            by_source = by_source_fn(sources)
        else:
            by_source = {"external": external_fn(sources)}
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warning("discovery.external_sources_failed",
                           {"error": str(exc)[:200]})
        return {}

    out: dict[str, list[str]] = {}
    for source, addresses in by_source.items():
        out[source] = _valid_addresses(list(addresses or []))

    hyper = out.get("hypertracker", [])
    if "hypertracker" in out:
        stats["hypertracker_coletados"] = len(hyper)
        ht = sources.get("hypertracker") or {}
        key = os.environ.get(str(ht.get("api_key_env", "HYPERTRACKER_API_KEY")), "")
        if ht.get("enabled") and key and not hyper and logger:
            logger.warning("discovery.hypertracker_empty",
                           {"api_key_env": ht.get("api_key_env", "HYPERTRACKER_API_KEY")})
    return out


def _interleave_after(base: list[Candidate], extras: list[Candidate],
                      after: int = 100) -> list[Candidate]:
    if not extras:
        return base
    after = max(0, min(after, len(base)))
    return base[:after] + extras + base[after:]


# ----------------------------------------------------------------------------
def run_scan(client: DataClient, db: Database, cfg: dict[str, Any] | None = None,
             *, logger: Any | None = None,
             now_ms: float | None = None) -> ScanResult:
    cfg = cfg or load_config()
    col = cfg["collection"]
    t0 = time.monotonic()
    scan_id = uuid.uuid4().hex[:12]
    now_ms = now_ms or time.time() * 1000
    stats: dict[str, int] = {}

    rows = client.leaderboard()
    # v6: ordenar por PnL 7d (atividade recente) em vez de pegar os primeiros N
    # (leaderboard vem por PnL all-time — baleias inativas dominam o topo)
    sort_by = col.get("sort_by", "all_time")
    if sort_by == "pnl_7d":
        rows.sort(key=lambda r: -float(dict(r.get("windowPerformances", []))
                                       .get("week", {}).get("pnl", 0) or 0))
    rows = rows[: int(col["leaderboard_top_n"])]
    stats["coletados"] = len(rows)
    candidates = [parse_leaderboard_row(r) for r in rows]
    top20 = {c.address for c in candidates[:int(cfg["score_adjustments"]["crowding_top_n"])]}

    # corte barato: 30d positiva + (opcional) banda de equity aproximada do leaderboard.
    min_eq = float(col.get("min_equity_usd", 0) or 0)
    cheap_30d = [c for c in candidates
                 if c.windows_pnl.get("30d", 0.0) > 0 and c.equity >= min_eq]
    stats["corte_barato_30d"] = len(candidates) - len(cheap_30d)
    # v14: F20 no corte barato é opt-in (cheap_cut_equity_filter). Por padrão o
    # F20 só corta no hard filter, com equity REAL do deep dive — o leaderboard
    # traz equity aproximada e reprovava traders bons cedo demais.
    apply_f20 = bool(col.get("cheap_cut_equity_filter", False))
    cheap = [c for c in cheap_30d
             if not apply_f20 or _equity_in_band(c.equity, cfg)]
    stats["corte_barato_f20"] = len(cheap_30d) - len(cheap)
    # v14: corte de inativos ANTES do deep dive (opt-in) — evita gastar vagas de
    # aprofundamento com quem não opera há muito tempo. 1 request curto/candidato.
    cheap = _cut_inactive_cheap(cheap, client, cfg, stats, logger)
    _sort_for_deep_dive(cheap, str(col.get("deep_sort_by", "roi_30d")))
    deep_max = int(col["deep_dive_max"])
    external_quota = int(col.get("external_dive_quota", 0) or 0)
    base_deep = cheap[:deep_max]

    # v5: varredura ativa — adicionar endereços além do leaderboard
    by_source: dict[str, list[str]] = {}
    if col.get("active_scan_enabled", False):
        try:
            active_addrs = client.active_addresses(
                window_hours=int(col.get("active_scan_window_hours", 48)),
                max_addresses=int(col.get("active_scan_max_addresses", 200)),
                min_notional_usd=float(col.get("active_scan_min_notional_usd", 1000)),
            )
            # remover endereços já no leaderboard (evita reprocessar)
            existing_addrs = {c.address for c in candidates}
            new_addrs = [a for a in active_addrs if a not in existing_addrs]
            stats["active_scan_novos"] = len(new_addrs)
            by_source["active_scan"] = _valid_addresses(new_addrs)
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.warning("discovery.active_scan_failed",
                               {"error": str(exc)[:200]})

    # v8: fontes externas opcionais (Nansen/Apify) — só alimentam ENDEREÇOS;
    # métricas e filtros continuam 100% nossos (HL pública = fonte de verdade)
    external_by_source = _external_candidates_by_source(client, cfg, logger, stats)
    by_source.update(external_by_source)

    existing_addrs = {c.address for c in candidates} | {c.address for c in base_deep}
    source_for_addr: dict[str, str] = {}
    source_candidates: list[Candidate] = []
    for source, addresses in by_source.items():
        selected_for_source = 0
        for addr in addresses:
            if addr in existing_addrs:
                continue
            existing_addrs.add(addr)
            source_for_addr[addr] = source
            source_candidates.append(Candidate(address=addr))
            selected_for_source += 1
        if source == "active_scan":
            stats["active_scan_novos"] = stats.get("active_scan_novos", selected_for_source)

    stats["fontes_externas_novos"] = len(source_candidates)
    selected_external = source_candidates[:external_quota]
    stats["fontes_externas_aprofundados"] = len(selected_external)
    stats["hypertracker_aprofundados"] = sum(
        1 for c in selected_external if source_for_addr.get(c.address) == "hypertracker")
    stats["active_scan_aprofundados"] = sum(
        1 for c in selected_external if source_for_addr.get(c.address) == "active_scan")
    quota_left = max(0, external_quota - len(selected_external))
    fallback = cheap[deep_max:deep_max + quota_left]
    stats["fallback_leaderboard_extra"] = len(fallback)
    deep = _interleave_after(base_deep, selected_external,
                             int(col.get("external_interleave_after", 100))) + fallback

    # UPDATE-0054: reprocessamento diário dos traders JÁ SALVOS. Injeta os
    # não-rejeitados (SUGERIDO/SALVO/TESTNET/MAINNET) que NÃO caíram no deep desta
    # rodada, para que copiados/salvos tenham as métricas recalculadas todo dia
    # mesmo fora do leaderboard. Prepend garante que processam primeiro — estouro
    # de orçamento nunca os pula. REJEITADO fica fora (sem recuperação automática).
    reprocess_set: set[str] = set()
    if col.get("reprocess_saved_traders", True):
        saved = list_traders(db, {"SUGERIDO", "SALVO", "TESTNET", "MAINNET"})
        already = {c.address for c in deep}
        reprocess_only = [Candidate(address=r["address"]) for r in saved
                          if r["address"] not in already]
        reprocess_set = {c.address for c in reprocess_only}
        deep = reprocess_only + deep
        stats["reprocessados"] = len(reprocess_only)
    stats["aprofundados"] = len(deep)

    # coorte de controle: perdedores consistentes (espelho invertido, barato)
    rekt = [c for c in candidates
            if c.windows_pnl.get("30d", 0.0) < 0 and c.windows_pnl.get("7d", 0.0) < 0]
    rekt = rekt[: int(col["rekt_sample"])]
    stats["rekt_sample"] = len(rekt)

    f8_share = cfg["hard_filters"].get("f8_min_liquid_volume_share")
    f8_top_n = cfg["hard_filters"].get("f8_liquid_assets_top_n")
    liquid = client.liquid_assets(int(f8_top_n)) if f8_share is not None and \
        f8_top_n is not None else set()

    approved: list[Candidate] = []
    rejected: list[Candidate] = []
    from engine.strategies.copy_trade.hl_data import RequestBudgetExceeded

    for c in deep:
        try:
            # F1 primeiro e barato (1 request): reprova sem gastar o deep dive.
            # UPDATE-0054: para traders reprocessados (já salvos), NÃO damos
            # short-circuit — seguimos ao deep dive para recalcular métricas; o
            # motivo do F1 fica só informativo em reject_reasons.
            f1_reason = precheck_activity(c, client, cfg, now_ms)
            if f1_reason and c.address not in reprocess_set:
                c.reject_reason = f1_reason
                c.reject_reasons = [f1_reason]
                rejected.append(c)
                stats["reprovados_F1"] = stats.get("reprovados_F1", 0) + 1
                continue
            if f1_reason:
                c.reject_reasons = [f1_reason]
            deep_dive(c, client, cfg, liquid, now_ms)
        except RequestBudgetExceeded:
            stats["interrompidos_por_orcamento"] = len(deep) - len(approved) - len(rejected)
            if logger:
                logger.warning("discovery.budget_exceeded",
                               {"scan_id": scan_id, "requests": client.requests_used})
            break
        except Exception as exc:  # noqa: BLE001 — um candidato ruim não para o scan
            if logger:
                logger.warning("discovery.candidate_error",
                               {"address": c.address, "error": str(exc)[:200]})
            continue

        if not entry_rule_ok(c, cfg):
            required = " e ".join(cfg["entry_rule"]["required_windows"])
            c.reject_reason = (f"entrada: janelas {c.windows_positive} "
                               f"({required} obrigatória(s), mín. "
                               f"{cfg['entry_rule']['min_positive_windows']}/4)")
            c.reject_reasons = [c.reject_reason]
            rejected.append(c)
            stats["reprovados_entrada"] = stats.get("reprovados_entrada", 0) + 1
            continue
        reasons = hard_filters_all(c, cfg, now_ms)
        c.reject_reasons = reasons
        reason = reasons[0] if reasons else None
        if reason:
            c.reject_reason = reason
            rejected.append(c)
            fkey = reason.split(":")[0]
            stats[f"reprovados_{fkey}"] = stats.get(f"reprovados_{fkey}", 0) + 1
            continue
        c.is_top20_alltime = c.address in top20
        score_candidate(c, cfg)
        assign_cohort(c, cfg)
        # v4: score mínimo para SUGERIDO — abaixo vira REJEITADO
        min_score = float(cfg.get("score_adjustments", {}).get("min_score_for_suggestion", 0))
        if min_score > 0 and c.score < min_score:
            c.reject_reason = f"score {c.score:.1f} < mínimo {min_score:.0f}"
            c.reject_reasons = [c.reject_reason]
            rejected.append(c)
            stats["reprovados_score_min"] = stats.get("reprovados_score_min", 0) + 1
            continue
        approved.append(c)

    # v8: ESTÁGIO 4 — a simulação de cópia é o critério FINAL de ranking.
    # "Bom trader ≠ boa cópia": net negativo rebaixa a REJEITADO mesmo com
    # score alto; positivos são ordenados por score × fator da simulação.
    stage4 = cfg.get("copy_simulation")
    if stage4:
        survivors: list[Candidate] = []
        for c in approved:
            if c.sim_stage4_net_usd is not None and c.sim_stage4_net_usd <= 0:
                c.reject_reason = (
                    f"copy_sim_negativa: replay {stage4.get('window_days', 60)}d "
                    f"com US$ {cfg['hard_filters']['f11_mirror_capital_usd']:.0f} "
                    f"→ net US$ {c.sim_stage4_net_usd:.2f} (score {c.score:.1f})")
                c.reject_reasons = [c.reject_reason]
                rejected.append(c)
                stats["rebaixados_copy_sim"] = stats.get("rebaixados_copy_sim", 0) + 1
                continue
            c.sim_factor = M.copy_sim_factor(
                c.sim_stage4_net_usd if c.sim_stage4_net_usd is not None else 0.0,
                float(cfg["hard_filters"]["f11_mirror_capital_usd"]),
                floor=float(stage4.get("factor_floor", 0.5)),
                cap=float(stage4.get("factor_cap", 1.2)))
            c.rationale.append(
                f"cópia simulada: net US$ {c.sim_stage4_net_usd:+.2f}, "
                f"expectância US$ {c.sim_expectancy_usd:+.2f}/trade, "
                f"DD da cópia {c.sim_max_dd_pct:.1f}%, "
                f"cobertura {c.coverage_days:.0f}d, metades "
                f"{c.sim_half_old_net if c.sim_half_old_net is not None else 'n/d'}"
                f"/{c.sim_half_new_net if c.sim_half_new_net is not None else 'n/d'}"
                if c.sim_stage4_net_usd is not None else
                "cópia simulada: sem dados")
            survivors.append(c)
        approved = survivors
        # v9: RANKING FINAL = net da cópia simulada (score é informativo).
        # Evidência: rank por net superou score×fator no walk-forward do lab.
        approved.sort(key=lambda c: -(c.sim_stage4_net_usd
                                      if c.sim_stage4_net_usd is not None
                                      else -1e12))
    else:
        approved.sort(key=lambda c: -c.score)
    stats["aprovados"] = len(approved)
    return ScanResult(scan_id=scan_id, approved=approved, rejected=rejected,
                      funnel_stats=stats, rekt_sample=rekt,
                      requests_used=getattr(client, "requests_used", 0),
                      duration_s=round(time.monotonic() - t0, 1))


_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")


# ----------------------------------------------------------------------------
def analyze_single_wallet(address: str, client: DataClient, cfg: dict[str, Any],
                          logger: Any | None = None) -> Candidate:
    """Roda o pipeline de discovery COMPLETO para UMA wallet, SEM gravar.

    Diferente do scan em massa (`for c in deep`), NUNCA dá short-circuit nos
    filtros: `score`/`cohort`/`sim_*` são SEMPRE calculados quando há dados. Os
    filtros que reprovariam ficam acumulados em `c.reject_reasons` (informativo);
    `c.reject_reason` fica `None` — curadoria manual não marca REJEITADO e o
    operador pode forçar salvar mesmo o que "reprova". Endereço inválido levanta
    ValueError (erro do chamador); qualquer outra falha vira um único
    `erro_na_analise` em `reject_reasons` (não derruba a análise dos demais).

    Limita `fills_max_pages=2` numa CÓPIA do cfg para proteger o orçamento de
    requests da venue (o scan em massa usa o valor cheio).
    """
    import copy as _copy

    address = (address or "").strip().lower()
    if not _ADDRESS_RE.match(address):
        raise ValueError(f"endereço inválido: {address!r}")

    cfg = _copy.deepcopy(cfg)
    cfg["collection"]["fills_max_pages"] = 2

    f8_share = cfg["hard_filters"].get("f8_min_liquid_volume_share")
    f8_top_n = cfg["hard_filters"].get("f8_liquid_assets_top_n")
    liquid = client.liquid_assets(int(f8_top_n)) \
        if f8_share is not None and f8_top_n is not None else set()

    c = Candidate(address=address)
    now_ms = time.time() * 1000
    reasons: list[str] = []
    try:
        f1 = precheck_activity(c, client, cfg, now_ms)
        if f1:
            reasons.append(f1)  # informativo — segue mesmo sem atividade recente
        deep_dive(c, client, cfg, liquid, now_ms)  # roda compute_copy_sims
        if not entry_rule_ok(c, cfg):
            required = " e ".join(cfg["entry_rule"]["required_windows"])
            reasons.append(
                f"entrada: janelas {c.windows_positive} "
                f"({required} obrigatória(s), mín. "
                f"{cfg['entry_rule']['min_positive_windows']}/4)")
        reasons += hard_filters_all(c, cfg, now_ms)
        score_candidate(c, cfg)
        assign_cohort(c, cfg)
        min_score = float(cfg.get("score_adjustments", {}).get(
            "min_score_for_suggestion", 0))
        if min_score > 0 and c.score < min_score:
            reasons.append(f"score {c.score:.1f} < mínimo {min_score:.0f}")
        stage4 = cfg.get("copy_simulation")
        if stage4:
            if c.sim_stage4_net_usd is not None and c.sim_stage4_net_usd <= 0:
                reasons.append(
                    f"copy_sim_negativa: replay "
                    f"{stage4.get('window_days', 60)}d com US$ "
                    f"{cfg['hard_filters']['f11_mirror_capital_usd']:.0f} → net "
                    f"US$ {c.sim_stage4_net_usd:.2f} (score {c.score:.1f})")
            c.sim_factor = M.copy_sim_factor(
                c.sim_stage4_net_usd if c.sim_stage4_net_usd is not None else 0.0,
                float(cfg["hard_filters"]["f11_mirror_capital_usd"]),
                floor=float(stage4.get("factor_floor", 0.5)),
                cap=float(stage4.get("factor_cap", 1.2)))
    except Exception as exc:  # noqa: BLE001 — 1 wallet ruim não derruba a análise
        if logger:
            logger.warning("suggestion.analyze_error",
                           {"address": address, "error": str(exc)[:200]})
        reasons = [f"erro_na_analise: {str(exc)[:200]}"]

    c.reject_reasons = reasons
    c.reject_reason = None  # informativo apenas; nunca marca REJEITADO
    return c


# ----------------------------------------------------------------------------
def persist_scan(db: Database, result: ScanResult, cfg: dict[str, Any],
                 client: DataClient | None = None,
                 logger: Any | None = None) -> None:
    """Upsert aprovados + reprovados (REJEITADO com motivo, sem rebaixar quem
    opera) e snapshots de posicionamento por coorte/ativo."""
    lv = int(cfg["logic_version"])
    for c in result.approved + result.rejected:
        # Bloco 3 — flag inviolável: trader com copy_pinned = 1 (em cópia) é
        # protegido pelo gate humano. O re-scan ATUALIZA métricas (score,
        # janelas, simulações) mas NUNCA escreve reject_reason, nunca chama
        # set_status e nunca rebaixa. Apenas registramos no report que o
        # pinned reprovaria nos filtros (informativo, sem efeito colateral).
        pin_rows = db.query(
            "SELECT copy_pinned, origin FROM traders WHERE address = ?",
            (c.address.lower(),))
        row_exists = bool(pin_rows)
        is_pinned = bool(pin_rows and pin_rows[0]["copy_pinned"] == 1)
        # UPDATE-0054: sugestões manuais (origin="usuário", UPDATE-0053) são
        # curadoria humana e NUNCA podem ser rebaixadas pelo reprocessamento —
        # protegidas como se estivessem pinned (só atualizam métricas).
        is_manual = bool(pin_rows and pin_rows[0]["origin"] == "usuário")
        protected = is_pinned or is_manual

        # UPDATE-0054 — guarda anti-wipe: reprocessados que reprovaram no F1 rodam
        # o deep dive, mas se a wallet não tem fills recentes o deep dive volta
        # vazio. Fazer upsert com métricas todas nulas APAGARIA as métricas boas
        # de uma linha existente. Se a linha já existe e o candidato veio sem
        # dados de deep dive, pulamos o upsert (preserva o histórico).
        no_deep_data = (c.coverage_days is None and not c.n_trades_30d
                        and c.sim_net_pnl_usd is None)
        if row_exists and no_deep_data:
            if logger:
                logger.info("discovery.reprocess_no_data", {"address": c.address})
            continue

        if protected and c.reject_reason:
            # métricas continuam sendo upsertadas abaixo, mas reject_reason
            # não persiste; o status/reject_reason anteriores ficam intactos.
            pinned_would_reject = c.reject_reason
            c.reject_reason = None
        else:
            pinned_would_reject = None

        extras = {
            "n_trades_30d": c.n_trades_30d,
            "n_trades_7d": c.n_trades_7d,
            "win_rate_30d": c.win_rate_30d,
            "avg_holding_hours": c.median_hold_hours,
            "avg_leverage": c.avg_leverage,
            "equity": c.equity,
            "top_assets": json.dumps(c.top_assets, ensure_ascii=False),
            "last_activity": c.last_activity,
            "windows_positive": c.windows_positive,
            "history_truncated": 1 if c.history_truncated else 0,
            "max_current_leverage": c.max_current_leverage,
            "available_margin_pct": c.available_margin_pct,
            "sim_net_pnl_usd": c.sim_net_pnl_usd,
            "sim_expectancy_usd": c.sim_expectancy_usd,
            "sim_max_dd_pct": c.sim_max_dd_pct,
            "sim_factor": c.sim_factor,
            "coverage_days": c.coverage_days,
            "sim_half_old_net": c.sim_half_old_net,
            "sim_half_new_net": c.sim_half_new_net,
            # Parte 2 (reclassify): persiste os 7 componentes normalizados [0,1]
            # + adjustments p/ recomputar o score sem refazer o deep dive.
            "score_components": serialize_components(c.components)
            if c.components is not None else None,
        }
        # protegido (pinned/manual): NUNCA escreve reject_reason — o valor
        # anterior fica intacto.
        if not protected:
            extras["reject_reason"] = c.reject_reason
        upsert_candidate(
            db, address=c.address, name=c.name, score=c.score if not c.reject_reason else None,
            cohort=c.cohort or None, twrr_30d=c.twrr_30d_pct,
            pnl_30d=c.windows_pnl.get("30d"),
            windows=c.windows_pnl, profit_factor=c.pf, win_rate=c.win_rate,
            max_drawdown=c.max_dd_90d_pct, liq_distance=c.liq_distance_pct,
            logic_version=lv,
            extras=extras,
        )
        if protected:
            # NUNCA chama set_status em protegido (pinned/manual) — status e
            # reject_reason anteriores permanecem intactos. Apenas log informativo.
            if pinned_would_reject and logger:
                logger.info("discovery.pinned_would_reject",
                            {"address": c.address, "reason": pinned_would_reject,
                             "manual": is_manual})
            continue
        if c.reject_reason:
            set_status(db, c.address, "REJEITADO", by=f"discovery_v{lv}", logger=logger)
        else:
            # aprovado que estava REJEITADO volta a ser candidato
            row = db.query("SELECT status FROM traders WHERE address = ?", (c.address,))
            if row and row[0]["status"] == "REJEITADO":
                set_status(db, c.address, "SUGERIDO", by=f"discovery_v{lv}", logger=logger)

    # posicionamento smart vs. rekt por ativo (amostra limitada por orçamento)
    if client is not None:
        sample_n = int(cfg["collection"]["positioning_sample"])
        for cohort_name, group in (("smart", result.approved[:sample_n]),
                                   ("rekt", result.rekt_sample[:sample_n])):
            per_asset: dict[str, dict[str, Any]] = {}
            wallets = 0
            for c in group:
                try:
                    ch = client.clearinghouse(c.address)
                except Exception:  # noqa: BLE001
                    continue
                wallets += 1
                for ap in ch.get("assetPositions", []):
                    p = ap["position"]
                    coin = str(p.get("coin"))
                    szi = float(p.get("szi", 0) or 0)
                    notional = float(p.get("positionValue", 0) or 0)
                    lev = float(p.get("leverage", {}).get("value", 0) or 0)
                    agg = per_asset.setdefault(coin, {"long": 0.0, "short": 0.0,
                                                      "levs": [], "wallets": 0})
                    agg["long" if szi >= 0 else "short"] += notional
                    if lev:
                        agg["levs"].append(lev)
                    agg["wallets"] += 1
            for asset, agg in per_asset.items():
                gross = agg["long"] + agg["short"]
                db.insert("cohort_snapshots", {
                    "scan_ts": utcnow(),
                    "scan_id": result.scan_id,
                    "logic_version": lv,
                    "cohort": cohort_name,
                    "asset": asset,
                    "net_bias_pct": round((agg["long"] - agg["short"]) / gross * 100, 2)
                    if gross > 0 else 0.0,
                    "avg_leverage": round(statistics.mean(agg["levs"]), 2)
                    if agg["levs"] else None,
                    "n_wallets": agg["wallets"],
                    "n_traders": len(group),
                    "avg_score": round(statistics.mean([x.score for x in group]), 2)
                    if group and cohort_name == "smart" else None,
                    "payload": json.dumps(agg | {"levs": len(agg["levs"])},
                                          ensure_ascii=False, default=str),
                })


def _components_from_row(row: dict[str, Any], cfg: dict[str, Any],
                         ) -> tuple[M.ScoreComponents, bool]:
    """Reconstrói ScoreComponents de uma linha de `traders`.

    Se `score_components` está persistido → reusa os componentes normalizados e
    apenas RECOMPUTA `sim_net` (para o novo peso valer). Caso contrário (legado)
    → best-effort a partir das métricas cruas persistidas, marcando approx=True.
    Os adjustments são sempre reaplicados do que está salvo na linha (4/4 de
    janelas + risco de liquidação); crowding não é persistido → omitido.
    """
    adj = cfg["score_adjustments"]

    def _sim_net_norm() -> float:
        v = row.get("sim_net_pnl_usd")
        return max(0.0, min(1.0, (float(v) if v is not None else 0.0) / 5000.0))

    stored = deserialize_components(row.get("score_components"))
    if stored is not None:
        stored.sim_net = _sim_net_norm()
        approx = False
        comps = stored
    else:
        approx = True
        # windows_positive é uma string "x/4"
        wp_raw = str(row.get("windows_positive") or "0/4")
        try:
            positive = int(wp_raw.split("/")[0])
        except (ValueError, IndexError):
            positive = 0
        pf = float(row.get("profit_factor") or 0.0)
        n_trades = int(row.get("n_trades_30d") or 0)
        twrr = row.get("twrr_30d")
        max_dd = row.get("max_drawdown")
        comps = M.ScoreComponents(
            consistency=M.consistency_score(positive, 4, 0.5),
            profit_factor=M.pf_score_credit(pf, n_trades),
            roi_log=M.roi_log_score(float(twrr) if twrr is not None else 0.0),
            drawdown_quality=_dd_quality_approx(
                float(max_dd) if max_dd is not None else 0.0,
                cfg["hard_filters"].get("f5_dd_quality_bands")),
            copyability=0.5,           # sem hold/liquidez persistidos: neutro
            net_expectancy=0.0,        # avg_trade_pnl_pct não persistido → 0
            sim_net=_sim_net_norm(),
        )
        # adjustments best-effort do que está salvo
        if positive == 4:
            comps.adjustments.append(
                ("consistencia_4/4", float(adj["full_consistency_bonus"])))
        liq = row.get("liq_distance")
        if liq is not None and float(liq) < float(adj["liq_distance_threshold_pct"]):
            comps.adjustments.append(
                ("risco_liquidacao", float(adj["liq_distance_penalty"])))
    return comps, approx


def _dd_quality_approx(max_dd_pct: float, bands: list[list[float]] | None) -> float:
    """Aproxima drawdown_quality só pela magnitude (sem o termo de recuperação,
    que exige a curva de equity). Usado no reclassify best-effort de legados."""
    if not bands:
        return max(0.0, 1.0 - max_dd_pct / 25.0)
    magnitude = 0.0
    exceeded = True
    for lo, hi, mult in bands:
        if max_dd_pct <= lo:
            exceeded = False
            break
        seg = min(max_dd_pct, hi) - lo
        if seg > 0:
            seg_frac = seg / (hi - lo)
            magnitude = max(magnitude, mult * (1.0 - seg_frac * 0.5))
        if max_dd_pct <= hi:
            exceeded = False
    return 0.0 if exceeded else magnitude


def reclassify_all(db: Database, cfg: dict[str, Any],
                   logger: Any | None = None) -> dict[str, Any]:
    """Recomputa o score de TODOS os traders com os pesos ATUAIS, sem refazer o
    deep dive (Parte 2 — AJUSTES 2026-07-11).

    - Usa `score_components` persistido; legados caem no best-effort (approx).
    - copy_pinned = 1 → NUNCA mexe no status (só recomputa score).
    - min_score_for_suggestion > 0 → rebaixa/repromove SUGERIDO↔REJEITADO.
    Retorna resumo {total, approx, status_changes}.
    """
    weights = cfg["score_weights"]
    min_score = float(cfg.get("score_adjustments", {})
                      .get("min_score_for_suggestion", 0) or 0)
    rows = db.query("SELECT * FROM traders")
    total = 0
    approx_n = 0
    status_changes = 0
    for row in rows:
        addr = row["address"]
        old_score = row.get("score")
        old_status = row.get("status")
        pinned = row.get("copy_pinned") == 1

        comps, approx = _components_from_row(row, cfg)
        new_score = M.composite_score(comps, weights)
        db.execute("UPDATE traders SET score = ?, score_components = ?, updated_at = ? "
                   "WHERE address = ?",
                   (new_score, serialize_components(comps), utcnow(), addr))
        total += 1
        if approx:
            approx_n += 1

        to_status = old_status
        # gate humano: pinned nunca muda de status por processo automático.
        if not pinned and min_score > 0:
            if new_score < min_score and old_status == "SUGERIDO":
                res = set_status(db, addr, "REJEITADO",
                                 by="reclassify", logger=logger)
                if res.get("ok") and not res.get("noop"):
                    to_status = "REJEITADO"
                    status_changes += 1
            elif new_score >= min_score and old_status == "REJEITADO":
                res = set_status(db, addr, "SUGERIDO",
                                 by="reclassify", logger=logger)
                if res.get("ok") and not res.get("noop"):
                    to_status = "SUGERIDO"
                    status_changes += 1

        if logger:
            logger.info("trader.reclassified", {
                "address": addr,
                "old_score": old_score,
                "new_score": new_score,
                "approx": approx,
                "from_status": old_status,
                "to_status": to_status,
                "pinned": pinned,
            })
    summary = {"total": total, "approx": approx_n, "status_changes": status_changes}
    if logger:
        logger.info("discovery.reclassify_done", summary)
    return summary


def _near_miss_rows(result: ScanResult, limit: int = 15) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in result.rejected:
        reasons = c.reject_reasons or ([c.reject_reason] if c.reject_reason else [])
        if len(reasons) != 1:
            continue
        reason = reasons[0]
        rows.append({
            "address": c.address,
            "reason": reason,
            "config_key": _filter_key(reason),
            "score": c.score,
            "sim_stage4_net_usd": c.sim_stage4_net_usd,
            "equity": c.equity,
        })
    rows.sort(key=lambda r: (
        -(r["sim_stage4_net_usd"] if r["sim_stage4_net_usd"] is not None else -1e12),
        -(r["score"] or 0),
    ))
    return rows[:limit]


def render_report(result: ScanResult, cfg: dict[str, Any]) -> tuple[str, str]:
    """(json_str, markdown) — top 10 com justificativa + estatísticas do funil."""
    top = result.approved[:10]
    near_miss = _near_miss_rows(result)
    payload = {
        "scan_id": result.scan_id,
        "logic_version": cfg["logic_version"],
        "generated_at": utcnow(),
        "funnel_stats": result.funnel_stats,
        "requests_used": result.requests_used,
        "duration_s": result.duration_s,
        "top": [{
            "rank": i + 1, "address": c.address, "score": c.score,
            "cohort": c.cohort, "windows": c.windows_positive,
            "twrr_30d_pct": c.twrr_30d_pct, "pf": c.pf,
            "available_margin_pct": c.available_margin_pct,
            "max_current_leverage": c.max_current_leverage,
            "sim_net_pnl_usd": c.sim_net_pnl_usd,
            "sim_stage4_net_usd": c.sim_stage4_net_usd,
            "sim_expectancy_usd": c.sim_expectancy_usd,
            "sim_max_dd_pct": c.sim_max_dd_pct,
            "sim_factor": c.sim_factor,
            "coverage_days": c.coverage_days,
            "sim_half_old_net": c.sim_half_old_net,
            "sim_half_new_net": c.sim_half_new_net,
            "rationale": c.rationale,
        } for i, c in enumerate(top)],
        "rejected_reasons": {c.address: c.reject_reason for c in result.rejected},
        "near_miss": near_miss,
    }
    lines = [
        f"# Discovery v{cfg['logic_version']} — varredura {result.scan_id}",
        "",
        f"Funil: {json.dumps(result.funnel_stats, ensure_ascii=False)}",
        f"Requests: {result.requests_used} · duração: {result.duration_s}s",
        "",
        "| # | Endereço | Score | Coorte | Janelas | TWRR 30d | PF |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(top, 1):
        lines.append(
            f"| {i} | `{c.address}` | {c.score} | {c.cohort} | {c.windows_positive} "
            f"| {c.twrr_30d_pct:.1f}% | {c.pf:.2f} |"
            if c.twrr_30d_pct is not None and c.pf is not None else
            f"| {i} | `{c.address}` | {c.score} | {c.cohort} | {c.windows_positive} | n/d | n/d |")
    lines.append("")
    for i, c in enumerate(top, 1):
        lines.append(f"### {i}. `{c.address}` — {c.score}")
        lines.extend(f"- {r}" for r in c.rationale)
        lines.append("")
    if near_miss:
        lines.extend([
            "## Near-miss — reprovados por exatamente 1 filtro",
            "",
            "| Endereço | Filtro | Chave YAML | Score | Net sim | Equity |",
            "|---|---|---|---|---|---|",
        ])
        for row in near_miss:
            sim = row["sim_stage4_net_usd"]
            lines.append(
                f"| `{row['address']}` | {row['reason']} | `{row['config_key'] or 'n/d'}` | "
                f"{row['score']:.1f} | "
                f"{sim:.2f}" if sim is not None else
                f"| `{row['address']}` | {row['reason']} | `{row['config_key'] or 'n/d'}` | "
                f"{row['score']:.1f} | n/d"
            )
            lines[-1] += f" | {row['equity']:.0f} |"
        lines.append("")
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str), "\n".join(lines)
