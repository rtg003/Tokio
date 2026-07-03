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
    fills_per_day: float = 0.0
    trades_per_day: float = 0.0
    median_hold_hours: float | None = None
    win_rate: float | None = None
    top3_concentration: float = 0.0
    avg_leverage: float | None = None
    liquid_volume_share: float = 1.0
    pnl_over_volume: float = 0.0
    net_exposure_share: float = 1.0
    deposit_share: float = 0.0
    liq_distance_pct: float | None = None
    avg_trade_pnl_pct: float = 0.0
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
        c.last_activity = utcnow_from_ms(max(times))
        episodes = M.position_episodes(fills)
        closed = [e for e in episodes if e.end_ms is not None]
        c.n_trades = len(closed)
        c.n_trades_30d = len([e for e in closed
                              if e.end_ms and e.end_ms >= now_ms - 30 * DAY_MS])
        c.trades_per_day = len(closed) / covered_days
        c.median_hold_hours = M.median_hold_hours(episodes)

        closed_pnls = [float(f.get("closedPnl", 0) or 0) for f in fills
                       if float(f.get("closedPnl", 0) or 0) != 0.0]
        wins = [p for p in closed_pnls if p > 0]
        c.win_rate = len(wins) / len(closed_pnls) if closed_pnls else None
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
    # distância de liquidação da posição mais próxima
    dists = []
    for p in positions:
        liq_px = p.get("liquidationPx")
        entry = float(p.get("entryPx", 0) or 0)
        if liq_px is None or entry <= 0:
            continue
        dists.append(abs(entry - float(liq_px)) / entry * 100)
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
    c.max_dd_90d_pct, c.dd_quality = M.drawdown_quality(
        curve_90d, max_dd_cap_pct=float(cfg["hard_filters"]["f5_max_drawdown_90d_pct"]))

    # -- consistência semanal ------------------------------------------------------
    pnl_30d_hist = _series(portfolio, "month", "pnlHistory")
    if len(pnl_30d_hist) >= 8:
        step = max(1, len(pnl_30d_hist) // 4)
        weekly = [pnl_30d_hist[min(i + step, len(pnl_30d_hist) - 1)][1] - pnl_30d_hist[i][1]
                  for i in range(0, len(pnl_30d_hist) - 1, step)]
        c.weekly_stability = M.weekly_stability(weekly)

    c.style = classify_style(c.median_hold_hours)


def utcnow_from_ms(ms: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ----------------------------------------------------------------------------
def hard_filters(c: Candidate, cfg: dict[str, Any],
                 now_ms: float | None = None) -> str | None:
    """F1–F11. Retorna o motivo da PRIMEIRA reprovação, ou None (passou)."""
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

    # F3 anti-scalper: exige EVIDÊNCIA positiva (hold None nunca reprova sozinho)
    if c.trades_per_day > float(f["f3_max_trades_per_day"]):
        return f"F3: {c.trades_per_day:.1f} trades/dia > {f['f3_max_trades_per_day']}"
    if c.median_hold_hours is not None and \
            c.median_hold_hours < float(f["f3_min_avg_holding_hours"]):
        return f"F3: hold mediano {c.median_hold_hours:.2f}h < {f['f3_min_avg_holding_hours']}h"

    if c.twrr_30d_pct is not None and c.twrr_30d_pct < float(f["f4_min_twrr_30d_pct"]):
        return f"F4: TWRR 30d {c.twrr_30d_pct:.1f}% < {f['f4_min_twrr_30d_pct']}%"

    if c.max_dd_90d_pct is not None and \
            c.max_dd_90d_pct > float(f["f5_max_drawdown_90d_pct"]):
        return f"F5: max DD 90d {c.max_dd_90d_pct:.1f}% > {f['f5_max_drawdown_90d_pct']}%"

    if c.top3_concentration > float(f["f6_max_top3_pnl_concentration"]):
        return f"F6: top-3 trades = {c.top3_concentration * 100:.0f}% do PnL"

    if c.avg_leverage is not None and c.avg_leverage > float(f["f7_max_avg_leverage"]):
        return f"F7: alavancagem média {c.avg_leverage:.1f}x > {f['f7_max_avg_leverage']}x"

    if c.liquid_volume_share < float(f["f8_min_liquid_volume_share"]):
        return f"F8: só {c.liquid_volume_share * 100:.0f}% do volume em ativos líquidos"

    if M.looks_like_mm(c.fills_per_day, c.pnl_over_volume, c.net_exposure_share,
                       max_tpd=float(f["f9_mm_max_trades_per_day"]),
                       max_pnl_vol=float(f["f9_mm_max_pnl_over_volume"])):
        return "F9: padrão de MM/arb/delta-neutro"

    if c.deposit_share > float(f["f10_max_deposit_growth_share"]):
        return f"F10: {c.deposit_share * 100:.0f}% do crescimento veio de aporte"

    if c.equity > 0 and c.n_trades > 0:
        median_notional = c.equity * 0.05 if not c.top_assets else c.equity * 0.05
        copy_notional = median_notional * float(f["f11_mirror_capital_usd"]) / c.equity
        if copy_notional < float(f["f11_min_mirror_notional_usd"]):
            return (f"F11: cópia estimada US$ {copy_notional:.2f} < "
                    f"{f['f11_min_mirror_notional_usd']} com capital configurado")
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

    c.components = comps
    c.score = M.composite_score(comps, w)
    c.rationale = [
        f"janelas positivas: {c.windows_positive}",
        f"TWRR 30d: {c.twrr_30d_pct:.1f}%" if c.twrr_30d_pct is not None else "TWRR: n/d",
        f"PF: {c.pf:.2f} (n={c.n_trades})" if c.pf is not None else "PF: n/d",
        f"max DD 90d: {c.max_dd_90d_pct:.1f}%" if c.max_dd_90d_pct is not None else "DD: n/d",
        f"hold mediano: {c.median_hold_hours:.1f}h" if c.median_hold_hours is not None
        else "hold: n/d",
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

    rows = client.leaderboard()[: int(col["leaderboard_top_n"])]
    stats["coletados"] = len(rows)
    candidates = [parse_leaderboard_row(r) for r in rows]
    top20 = {c.address for c in candidates[:int(cfg["score_adjustments"]["crowding_top_n"])]}

    # corte barato: 30d positiva (janela obrigatória visível no leaderboard)
    cheap = [c for c in candidates if c.windows_pnl.get("30d", 0.0) > 0]
    stats["corte_barato_30d"] = len(candidates) - len(cheap)
    cheap.sort(key=lambda c: -c.windows_pnl.get("30d", 0.0))
    deep = cheap[: int(col["deep_dive_max"])]
    stats["aprofundados"] = len(deep)

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
            c.reject_reason = f"entrada: janelas {c.windows_positive} (30d e 60d obrigatórias)"
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
        approved.append(c)

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
                "avg_holding_hours": c.median_hold_hours,
                "avg_leverage": c.avg_leverage,
                "equity": c.equity,
                "top_assets": json.dumps(c.top_assets, ensure_ascii=False),
                "last_activity": c.last_activity,
                "windows_positive": c.windows_positive,
                "reject_reason": c.reject_reason,
                "history_truncated": 1 if c.history_truncated else 0,
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
