"""Funil v2 do discovery (logic_version 2 — spec PROMPT_DISCOVERY_TRADERS_v5).

3 estágios: coleta ampla (4 janelas) → 11 hard filters (baratos primeiro) →
score ponderado + ajustes + coortes (bidimensional e controle rekt).
Read-only: este módulo nunca importa signer nem envia ordem.
"""
from __future__ import annotations

import json
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
    f1_days = int(cfg["hard_filters"]["f1_recent_activity_days"])
    recent, _ = client.fills_by_time(c.address, window_days=f1_days, max_pages=1)
    if not recent:
        return f"F1: sem trade nos últimos {f1_days}d"
    c.last_activity = utcnow_from_ms(max(float(f["time"]) for f in recent))
    return None


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
        liquid_vol = sum(abs(float(f.get("sz", 0)) * float(f.get("px", 0)))
                         for f in fills if str(f.get("coin")) in liquid)
        c.liquid_volume_share = (liquid_vol / volume) if volume > 0 else 0.0
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
    c.max_dd_90d_pct, c.dd_quality = M.drawdown_quality(
        curve_90d,
        max_dd_cap_pct=float(cfg["hard_filters"]["f5_max_drawdown_90d_pct"]),
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
def hard_filters(c: Candidate, cfg: dict[str, Any],
                 now_ms: float | None = None) -> str | None:
    """F1–F20 (v7: F7b/F12/F13 posição aberta · v9: F16–F20 copiabilidade real).
    Retorna o motivo da PRIMEIRA reprovação, ou None (passou).
    Threshold null = filtro desabilitado. Referência canônica de cada variável:
    docs/discovery_logic_v9.md."""
    f = cfg["hard_filters"]
    now_ms = now_ms or time.time() * 1000

    if not c.last_activity:
        return "F1: sem atividade recente"
    from datetime import datetime

    last_ms = datetime.fromisoformat(c.last_activity).timestamp() * 1000
    if now_ms - last_ms > float(f["f1_recent_activity_days"]) * DAY_MS:
        return f"F1: último trade há mais de {f['f1_recent_activity_days']}d"

    if c.n_trades < int(f["f2_min_closed_trades"]):
        return f"F2: amostra {c.n_trades} < {f['f2_min_closed_trades']} trades fechados"

    # v5: F2b — trader sem atividade recente não tem o que copiar
    f2b = f.get("f2b_min_trades_30d")
    if f2b is not None and c.n_trades_30d < int(f2b):
        return f"F2b: {c.n_trades_30d} trades fechados nos últimos 30d < {f2b}"

    # v10: F2c — trader sem atividade nas últimas 48h/7d não tem o que copiar AGORA
    f2c = f.get("f2c_min_trades_7d")
    if f2c is not None and c.n_trades_7d < int(f2c):
        return f"F2c: {c.n_trades_7d} trades fechados nos últimos 7d < {f2c} (inativo)"

    # v9 — F16: cobertura mínima de histórico (dias entre 1º e último fill).
    # Auditoria do "top 1" do lab: 5 dias de atividade geravam +250% irreal.
    if f.get("f16_min_coverage_days") is not None and \
            c.coverage_days is not None and \
            c.coverage_days < float(f["f16_min_coverage_days"]):
        return (f"F16: histórico de {c.coverage_days:.0f}d < "
                f"{f['f16_min_coverage_days']}d (wallet nova demais p/ julgar)")

    # v9 — F20: teto de equity do trader — preditor nº 1 do laboratório
    # (Spearman −0.227): quanto MENOR a conta, melhor a cópia com $1k.
    if f.get("f20_max_trader_equity_usd") is not None and \
            c.equity > float(f["f20_max_trader_equity_usd"]):
        return (f"F20: equity US$ {c.equity:,.0f} > "
                f"{f['f20_max_trader_equity_usd']:,.0f} (grande demais p/ espelhar)")

    # F3 anti-scalper (threshold null = desabilitado): exige EVIDÊNCIA positiva
    # (hold None nunca reprova sozinho)
    if f.get("f3_max_trades_per_day") is not None and \
            c.trades_per_day > float(f["f3_max_trades_per_day"]):
        return f"F3: {c.trades_per_day:.1f} trades/dia > {f['f3_max_trades_per_day']}"
    if f.get("f3_min_avg_holding_hours") is not None and \
            c.median_hold_hours is not None and \
            c.median_hold_hours < float(f["f3_min_avg_holding_hours"]):
        return f"F3: hold mediano {c.median_hold_hours:.2f}h < {f['f3_min_avg_holding_hours']}h"

    if f.get("f4_min_twrr_30d_pct") is not None and \
            c.twrr_30d_pct is not None and \
            c.twrr_30d_pct < float(f["f4_min_twrr_30d_pct"]):
        return f"F4: TWRR 30d {c.twrr_30d_pct:.1f}% < {f['f4_min_twrr_30d_pct']}%"

    if c.max_dd_90d_pct is not None and \
            c.max_dd_90d_pct > float(f["f5_max_drawdown_90d_pct"]):
        return f"F5: max DD 90d {c.max_dd_90d_pct:.1f}% > {f['f5_max_drawdown_90d_pct']}%"

    # v7 — F13: posição aberta perto demais da liquidação (medida do mark price)
    if f.get("f13_min_liq_distance_pct") is not None and \
            c.liq_distance_pct is not None and \
            c.liq_distance_pct < float(f["f13_min_liq_distance_pct"]):
        return (f"F13: dist. liquidação {c.liq_distance_pct:.1f}% < "
                f"{f['f13_min_liq_distance_pct']}%")

    if c.top3_concentration > float(f["f6_max_top3_pnl_concentration"]):
        return f"F6: top-3 trades = {c.top3_concentration * 100:.0f}% do PnL"

    if c.avg_leverage is not None and c.avg_leverage > float(f["f7_max_avg_leverage"]):
        return f"F7: alavancagem média {c.avg_leverage:.1f}x > {f['f7_max_avg_leverage']}x"

    # v7 — F7b: alavancagem ATUAL das posições abertas (a média esconde o agora)
    if f.get("f7b_max_current_leverage") is not None and \
            c.max_current_leverage is not None and \
            c.max_current_leverage > float(f["f7b_max_current_leverage"]):
        return (f"F7b: alavancagem atual {c.max_current_leverage:.1f}x > "
                f"{f['f7b_max_current_leverage']}x")

    # v7 — F12: margem 100% comprometida = qualquer movimento contra liquida
    if f.get("f12_min_available_margin_pct") is not None and \
            c.available_margin_pct is not None and \
            c.available_margin_pct < float(f["f12_min_available_margin_pct"]):
        return (f"F12: margem disponível {c.available_margin_pct:.1f}% < "
                f"{f['f12_min_available_margin_pct']}%")

    if c.liquid_volume_share < float(f["f8_min_liquid_volume_share"]):
        return f"F8: só {c.liquid_volume_share * 100:.0f}% do volume em ativos líquidos"

    if M.looks_like_mm(c.fills_per_day, c.pnl_over_volume, c.net_exposure_share,
                       max_tpd=float(f["f9_mm_max_trades_per_day"]),
                       max_pnl_vol=float(f["f9_mm_max_pnl_over_volume"])):
        return "F9: padrão de MM/arb/delta-neutro"

    if c.deposit_share > float(f["f10_max_deposit_growth_share"]):
        return f"F10: {c.deposit_share * 100:.0f}% do crescimento veio de aporte"

    # v7 — F11 corrigido: notional REAL dos fills (o placeholder de 5% do equity
    # estimava US$ 50 de cópia onde o real era US$ 1.80 — dossiê #6 do Hermes)
    if c.equity > 0 and c.median_fill_notional is not None:
        copy_notional = c.median_fill_notional * \
            float(f["f11_mirror_capital_usd"]) / c.equity
        if copy_notional < float(f["f11_min_mirror_notional_usd"]):
            return (f"F11: cópia estimada US$ {copy_notional:.2f} < "
                    f"{f['f11_min_mirror_notional_usd']} com capital configurado")

    # v7 — F15: simulação retroativa — cópia que não paga taxa+slippage não serve
    if f.get("f15_sim_window_days") is not None and c.sim_net_pnl_usd is not None \
            and c.sim_net_pnl_usd <= float(f.get("f15_min_net_pnl_usd", 0.0)):
        return (f"F15: cópia simulada {f['f15_sim_window_days']}d com "
                f"US$ {f['f11_mirror_capital_usd']:.0f} → PnL líquido "
                f"US$ {c.sim_net_pnl_usd:.2f}")

    # v9 — F17: a cópia simulada (com latência e teto de alavancagem) precisa
    # RENDER, não só não perder. Quintis do lab: top +$71 em B vs +$0.3 no 2º.
    if f.get("f17_min_sim_net_usd") is not None and \
            c.sim_stage4_net_usd is not None and \
            c.sim_stage4_net_usd <= float(f["f17_min_sim_net_usd"]):
        return (f"F17: cópia simulada rende US$ {c.sim_stage4_net_usd:.2f} <= "
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
            return f"F18: metades da cópia (antiga US$ {old_s} / recente US$ {new_s})"

    # v9 — F19: DD máximo da curva da CÓPIA (risco da cópia, não do trader).
    # Lab: perdedores fora da amostra tinham DD de cópia 56–75% já visível aqui.
    if f.get("f19_max_sim_dd_pct") is not None and \
            c.sim_max_dd_pct is not None and \
            c.sim_max_dd_pct > float(f["f19_max_sim_dd_pct"]):
        return (f"F19: DD da cópia simulada {c.sim_max_dd_pct:.1f}% > "
                f"{f['f19_max_sim_dd_pct']}%")
    return None


# ----------------------------------------------------------------------------
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
    )
    if positive == 4:
        comps.adjustments.append(("consistencia_4/4", float(adj["full_consistency_bonus"])))
    if c.liq_distance_pct is not None and \
            c.liq_distance_pct < float(adj["liq_distance_threshold_pct"]):
        comps.adjustments.append(("risco_liquidacao", float(adj["liq_distance_penalty"])))
    if c.is_top20_alltime:
        comps.adjustments.append(("crowding_top20", float(adj["crowding_penalty"])))

    # v5: penalizar PF absurdo (> 10 = ausência de perdas realizadas, não habilidade)
    pf_absurd = float(adj.get("pf_absurd_threshold", 0))
    if pf_absurd > 0 and c.pf is not None and c.pf > pf_absurd:
        comps.adjustments.append(("pf_absurdo", float(adj.get("pf_absurd_penalty", 0))))

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

    # corte barato: 30d positiva (janela obrigatória) + piso de equity.
    # Prioridade do aprofundamento por ROI 30d: PnL absoluto puro só traria
    # mega-baleias holders (reprovam em F1/F2 — validação real de 2026-07-03).
    min_eq = float(col.get("min_equity_usd", 0))
    cheap = [c for c in candidates
             if c.windows_pnl.get("30d", 0.0) > 0 and c.equity >= min_eq]
    stats["corte_barato_30d"] = len(candidates) - len(cheap)
    cheap.sort(key=lambda c: -c.roi_30d_pct)
    deep = cheap[: int(col["deep_dive_max"])]
    stats["aprofundados"] = len(deep)

    # v5: varredura ativa — adicionar endereços além do leaderboard
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
            # criar candidates vazios para os novos endereços (sem dados do leaderboard)
            for addr in new_addrs[:int(col["deep_dive_max"]) - len(deep)]:
                deep.append(Candidate(address=addr))
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.warning("discovery.active_scan_failed",
                               {"error": str(exc)[:200]})

    # v8: fontes externas opcionais (Nansen/Apify) — só alimentam ENDEREÇOS;
    # métricas e filtros continuam 100% nossos (HL pública = fonte de verdade)
    external_fn = getattr(client, "external_candidates", None)
    if external_fn is not None and cfg.get("sources"):
        try:
            ext_addrs = external_fn(cfg["sources"])
            existing_addrs = {c.address for c in candidates} | {c.address for c in deep}
            new_ext = [a for a in ext_addrs if a not in existing_addrs]
            stats["fontes_externas_novos"] = len(new_ext)
            for addr in new_ext[:max(0, int(col["deep_dive_max"]) - len(deep))]:
                deep.append(Candidate(address=addr))
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.warning("discovery.external_sources_failed",
                               {"error": str(exc)[:200]})

    # coorte de controle: perdedores consistentes (espelho invertido, barato)
    rekt = [c for c in candidates
            if c.windows_pnl.get("30d", 0.0) < 0 and c.windows_pnl.get("7d", 0.0) < 0]
    rekt = rekt[: int(col["rekt_sample"])]
    stats["rekt_sample"] = len(rekt)

    liquid = client.liquid_assets(int(cfg["hard_filters"]["f8_liquid_assets_top_n"]))

    approved: list[Candidate] = []
    rejected: list[Candidate] = []
    from engine.strategies.copy_trade.hl_data import RequestBudgetExceeded

    for c in deep:
        try:
            # F1 primeiro e barato (1 request): reprova sem gastar o deep dive
            f1_reason = precheck_activity(c, client, cfg, now_ms)
            if f1_reason:
                c.reject_reason = f1_reason
                rejected.append(c)
                stats["reprovados_F1"] = stats.get("reprovados_F1", 0) + 1
                continue
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
            rejected.append(c)
            stats["reprovados_entrada"] = stats.get("reprovados_entrada", 0) + 1
            continue
        reason = hard_filters(c, cfg, now_ms)
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


# ----------------------------------------------------------------------------
def persist_scan(db: Database, result: ScanResult, cfg: dict[str, Any],
                 client: DataClient | None = None,
                 logger: Any | None = None) -> None:
    """Upsert aprovados + reprovados (REJEITADO com motivo, sem rebaixar quem
    opera) e snapshots de posicionamento por coorte/ativo."""
    lv = int(cfg["logic_version"])
    for c in result.approved + result.rejected:
        upsert_candidate(
            db, address=c.address, name=c.name, score=c.score if not c.reject_reason else None,
            cohort=c.cohort or None, twrr_30d=c.twrr_30d_pct,
            pnl_30d=c.windows_pnl.get("30d"),
            windows=c.windows_pnl, profit_factor=c.pf, win_rate=c.win_rate,
            max_drawdown=c.max_dd_90d_pct, liq_distance=c.liq_distance_pct,
            logic_version=lv,
            extras={
                "n_trades_30d": c.n_trades_30d,
                "n_trades_7d": c.n_trades_7d,
                "win_rate_30d": c.win_rate_30d,
                "avg_holding_hours": c.median_hold_hours,
                "avg_leverage": c.avg_leverage,
                "equity": c.equity,
                "top_assets": json.dumps(c.top_assets, ensure_ascii=False),
                "last_activity": c.last_activity,
                "windows_positive": c.windows_positive,
                "reject_reason": c.reject_reason,
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
            },
        )
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


def render_report(result: ScanResult, cfg: dict[str, Any]) -> tuple[str, str]:
    """(json_str, markdown) — top 10 com justificativa + estatísticas do funil."""
    top = result.approved[:10]
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
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str), "\n".join(lines)
