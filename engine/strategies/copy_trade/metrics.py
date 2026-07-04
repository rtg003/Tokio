"""Métricas do discovery v2 (spec PROMPT_DISCOVERY_TRADERS_v5) — funções puras.

Este módulo concentra as funções de métrica exigidas pela spec, com testes
unitários obrigatórios. Nenhum I/O: entradas sintéticas nos testes.
"""
from __future__ import annotations

# --- Profit factor (patch de scoring aplicado 2026-07-03) --------------------
# Crédito gradativo no score (peso 20% do composto):
#   - integral até PF 3.0;
#   - meio-crédito de 3.0 a 5.0, valendo APENAS se n_trades >= 60 na janela
#     (PF extremo com amostra pequena é variância, não habilidade);
#   - acima de 5.0 não pontua.
# PF é calculado INCLUINDO o PnL não realizado das posições abertas no
# fechamento da janela — PF só de realizados é inflável ao não fechar
# perdedores.

PF_FULL_CREDIT_CAP = 3.0
PF_EXTENDED_CAP = 5.0
PF_EXTENDED_MIN_TRADES = 60
_PF_MAX_EFFECTIVE = PF_FULL_CREDIT_CAP + 0.5 * (PF_EXTENDED_CAP - PF_FULL_CREDIT_CAP)


def profit_factor(gross_gains: float, gross_losses: float,
                  unrealized_pnl: float = 0.0) -> float:
    """PF da janela incluindo o não realizado das posições abertas no fechamento.

    gross_gains: soma dos trades fechados vencedores (>= 0)
    gross_losses: soma ABSOLUTA dos trades fechados perdedores (>= 0)
    unrealized_pnl: PnL aberto no fechamento da janela (com sinal)
    """
    if gross_gains < 0 or gross_losses < 0:
        raise ValueError("gross_gains/gross_losses devem ser >= 0")
    gains = gross_gains + max(unrealized_pnl, 0.0)
    losses = gross_losses + max(-unrealized_pnl, 0.0)
    if losses == 0.0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def pf_effective(pf: float, n_trades: int) -> float:
    """PF efetivo para o score, com o crédito gradativo do patch."""
    if pf <= 0:
        return 0.0
    effective = min(pf, PF_FULL_CREDIT_CAP)
    if pf > PF_FULL_CREDIT_CAP and n_trades >= PF_EXTENDED_MIN_TRADES:
        effective += 0.5 * (min(pf, PF_EXTENDED_CAP) - PF_FULL_CREDIT_CAP)
    return effective


def pf_score_credit(pf: float, n_trades: int) -> float:
    """Crédito normalizado [0, 1] do componente profit factor do score."""
    return min(1.0, pf_effective(pf, n_trades) / _PF_MAX_EFFECTIVE)


# ============================================================================
# Métricas do funil v2 (spec v5) — funções puras sobre dados sintetizáveis
# ============================================================================
import math  # noqa: E402
import statistics  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any  # noqa: E402

HOUR_MS = 3_600_000.0
DAY_MS = 86_400_000.0


# --- TWRR (retorno ponderado pelo tempo, neutro a aportes) --------------------
def twrr(curve: list[tuple[float, float]],
         flows: list[tuple[float, float]] | None = None) -> float:
    """TWRR da janela: divide a curva em subperíodos a cada fluxo (depósito/
    saque) e encadeia os retornos — crescimento por aporte NÃO conta.

    curve: [(ts_ms, equity_usd)] ordenado · flows: [(ts_ms, valor_com_sinal)]
    Retorno em fração (0.05 = 5%).
    """
    if len(curve) < 2:
        return 0.0
    flows = sorted(flows or [])
    total = 1.0
    period_start_value = curve[0][1]
    fi = 0
    for (t_prev, _v_prev), (t_cur, v_cur) in zip(curve, curve[1:]):
        flow_in_period = 0.0
        while fi < len(flows) and t_prev < flows[fi][0] <= t_cur:
            flow_in_period += flows[fi][1]
            fi += 1
        if flow_in_period:
            # fecha o subperíodo ANTES do fluxo e reabre a base após o fluxo
            base = period_start_value
            if base > 0:
                total *= (v_cur - flow_in_period) / base
            period_start_value = v_cur
        # sem fluxo: o subperíodo continua acumulando
    if period_start_value > 0:
        total *= curve[-1][1] / period_start_value
    return total - 1.0


def deposit_growth_share(equity_start: float, equity_end: float,
                         net_deposits: float) -> float:
    """Fração do crescimento de equity explicada por aporte (F10)."""
    growth = equity_end - equity_start
    if growth <= 0 or net_deposits <= 0:
        return 0.0
    return min(1.0, net_deposits / growth)


# --- Episódios de posição (fix do bug do startPosition) -----------------------
@dataclass
class Episode:
    coin: str
    start_ms: float | None      # None = posição já existia antes da janela
    end_ms: float | None = None  # None = ainda aberta no fim da janela
    known_start: bool = True

    @property
    def hold_hours(self) -> float | None:
        if self.start_ms is None or self.end_ms is None or not self.known_start:
            return None
        return (self.end_ms - self.start_ms) / HOUR_MS


def position_episodes(fills: list[dict[str, Any]]) -> list[Episode]:
    """Reconstrói episódios de posição por ativo a partir de `startPosition`
    e `sz` de cada fill — SEM exigir startPosition == 0 (bug da v1):

    - abre episódio quando a posição sai de zero;
    - primeiro fill do ativo com startPosition != 0 → episódio com início
      DESCONHECIDO (posição pré-existente à janela; excluído da mediana);
    - fecha quando a posição volta a zero; flip fecha e abre no mesmo fill.
    """
    by_coin: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(fills, key=lambda x: float(x["time"])):
        by_coin.setdefault(str(f.get("coin")), []).append(f)

    episodes: list[Episode] = []
    for coin, coin_fills in by_coin.items():
        current: Episode | None = None
        for f in coin_fills:
            t = float(f["time"])
            pre = float(f.get("startPosition", 0) or 0)
            side = {"B": 1.0, "A": -1.0, "buy": 1.0, "sell": -1.0}.get(
                str(f.get("side")), 0.0)
            post = pre + side * abs(float(f.get("sz", 0)))
            if abs(post) < 1e-12:
                post = 0.0

            if current is None:
                if pre != 0.0:
                    # posição pré-existente: episódio com início desconhecido
                    current = Episode(coin, start_ms=None, known_start=False)
                elif post != 0.0:
                    current = Episode(coin, start_ms=t)
            if current is not None and post == 0.0:
                current.end_ms = t
                episodes.append(current)
                current = None
            elif current is not None and pre != 0.0 and (pre > 0) != (post > 0):
                # flip: fecha o episódio e abre outro no mesmo instante
                current.end_ms = t
                episodes.append(current)
                current = Episode(coin, start_ms=t)
        if current is not None:
            episodes.append(current)   # aberto no fim da janela
    return episodes


def median_hold_hours(episodes: list[Episode]) -> float | None:
    """Mediana APENAS dos episódios completos com início conhecido.
    None = sem evidência (nunca tratar como 0 / scalper)."""
    holds = [e.hold_hours for e in episodes if e.hold_hours is not None]
    return statistics.median(holds) if holds else None


# --- Drawdown: magnitude + velocidade de recuperação -----------------------------
def drawdown_quality(curve: list[tuple[float, float]],
                     max_dd_cap_pct: float = 25.0,
                     bands: list[list[float]] | None = None) -> tuple[float, float]:
    """Retorna (max_dd_pct, quality [0,1]).

    quality = 70% magnitude + 30% recuperação.

    v4: magnitude é piecewise por faixas de DD (em vez de linear):
      bands = [[0, 20, 1.0], [20, 30, 0.7], [30, 40, 0.4]]
      DD 0-20% = quality cheio; 20-30% = ×0.7; 30-40% = ×0.4.
    Sem bands: decai linear (1 − dd/cap) — comportamento v2/v3.
    """
    if len(curve) < 2:
        return (0.0, 1.0)
    values = [v for _, v in curve]
    peak = values[0]
    max_dd = 0.0
    dd_periods = 0
    recovered_periods = 0
    in_dd = False
    for v in values:
        if v >= peak:
            if in_dd:
                recovered_periods += 1
                in_dd = False
            peak = v
        else:
            dd = (peak - v) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
            dd_periods += 1
            in_dd = True
    max_dd_pct = max_dd * 100

    # v4: magnitude piecewise por faixas
    if bands:
        magnitude = 0.0
        for lo, hi, mult in bands:
            if max_dd_pct <= lo:
                break
            seg = min(max_dd_pct, hi) - lo
            if seg > 0:
                # dentro da faixa [lo, hi], o decai é proporcional ao segmento
                seg_frac = seg / (hi - lo)
                magnitude = max(magnitude, mult * (1.0 - seg_frac * 0.5))
        # se DD excede o teto (última faixa), magnitude = 0
        if max_dd_pct > bands[-1][1]:
            magnitude = 0.0
    else:
        magnitude = max(0.0, 1.0 - max_dd_pct / max_dd_cap_pct)

    recovery = recovered_periods / max(1, recovered_periods + (1 if in_dd else 0))
    return (max_dd_pct, 0.7 * magnitude + 0.3 * recovery)


# --- Consistência ------------------------------------------------------------
def weekly_stability(weekly_pnls: list[float]) -> float:
    """[0,1]: 1 = PnL semanal estável e positivo; 0 = errático/negativo."""
    if len(weekly_pnls) < 2:
        return 0.5
    mean = statistics.mean(weekly_pnls)
    if mean <= 0:
        return 0.0
    cv = statistics.pstdev(weekly_pnls) / abs(mean)
    return 1.0 / (1.0 + cv)


def consistency_score(windows_positive: int, total_windows: int,
                      stability: float) -> float:
    """[0,1]: 60% janelas positivas + 40% estabilidade semanal."""
    return 0.6 * (windows_positive / max(1, total_windows)) + 0.4 * stability


def top_n_concentration(trade_pnls: list[float], n: int = 3) -> float:
    """Fração do PnL positivo total nos top-N trades (F6)."""
    gains = sorted((p for p in trade_pnls if p > 0), reverse=True)
    total = sum(gains)
    if total <= 0:
        return 0.0
    return sum(gains[:n]) / total


# --- ROI em log-scale (não premiar alavancagem) -----------------------------------
def roi_log_score(roi_pct: float, saturation_pct: float = 50.0) -> float:
    """[0,1] com crescimento logarítmico; satura em `saturation_pct` (30d)."""
    if roi_pct <= 0:
        return 0.0
    return min(1.0, math.log1p(roi_pct) / math.log1p(saturation_pct))


# --- Copiabilidade -----------------------------------------------------------
def copyability_score(hold_hours: float | None, trades_per_day: float,
                      liquid_volume_share: float,
                      *, sweet_spot: tuple[float, float] = (4.0, 72.0),
                      freq_spot: tuple[float, float] = (0.3, 20.0)) -> float:
    """[0,1]: 40% holding no sweet spot + 30% liquidez + 30% frequência."""
    if hold_hours is None:
        hold_component = 0.4          # sem evidência: neutro, não zero
    elif sweet_spot[0] <= hold_hours <= sweet_spot[1]:
        hold_component = 1.0
    else:
        # decai proporcionalmente à distância do intervalo (em log)
        edge = sweet_spot[0] if hold_hours < sweet_spot[0] else sweet_spot[1]
        ratio = min(hold_hours, edge) / max(hold_hours, edge)
        hold_component = max(0.0, ratio)
    freq_component = 1.0 if freq_spot[0] <= trades_per_day <= freq_spot[1] else 0.3
    return 0.4 * hold_component + 0.3 * liquid_volume_share + 0.3 * freq_component


# --- Expectância líquida do custo de cópia ---------------------------------------
def net_expectancy_score(avg_trade_pnl_pct: float, cost_per_trade_pct: float,
                         saturation_pct: float = 1.0) -> float:
    """[0,1]: expectância % por trade LÍQUIDA do custo de cópia (ida+volta).
    Não paga o custo → 0 (spec)."""
    net = avg_trade_pnl_pct - cost_per_trade_pct
    if net <= 0:
        return 0.0
    return min(1.0, net / saturation_pct)


# --- Simulação retroativa de cópia (v7 — F15) --------------------------------
@dataclass
class CopySimulation:
    gross_pnl_usd: float            # closedPnl da janela × ratio
    cost_usd: float                 # Σ notional_fill × ratio × (fee+slip) por perna
    net_pnl_usd: float              # gross − cost
    median_copy_notional_usd: float  # mediana do notional espelhado por fill
    n_fills: int


def simulate_copy(fills: list[dict[str, Any]], trader_equity: float,
                  mirror_capital: float, *, taker_fee_pct: float = 0.045,
                  slippage_pct: float = 0.02, window_days: float = 30.0,
                  now_ms: float | None = None) -> CopySimulation | None:
    """Espelhamento retroativo: "se tivéssemos copiado este trader com
    `mirror_capital` na janela, qual seria o PnL LÍQUIDO de taxas+slippage?"

    Cada fill do trader vira uma perna copiada com size proporcional
    (ratio = mirror_capital / trader_equity); o PnL escala linearmente com o
    ratio, então o SINAL do net independe do capital — o capital afeta a
    executabilidade (notional mínimo por ordem, checada no F11).

    Aproximações documentadas: equity ATUAL como denominador (equity da janela
    não é conhecido ponto a ponto); só PnL REALIZADO (rejeitar lucro 100%
    não-realizado é intencional — dossiê #1 do Hermes); funding ignorado.
    """
    if trader_equity <= 0 or mirror_capital <= 0:
        return None
    import time as _time
    now_ms = now_ms or _time.time() * 1000
    cutoff = now_ms - window_days * DAY_MS
    window = [f for f in fills if float(f.get("time", 0)) >= cutoff]
    if not window:
        return None
    ratio = mirror_capital / trader_equity
    cost_rate = (taker_fee_pct + slippage_pct) / 100.0   # por perna
    gross = 0.0
    cost = 0.0
    notionals = []
    for f in window:
        notional = abs(float(f.get("sz", 0) or 0) * float(f.get("px", 0) or 0))
        copy_notional = notional * ratio
        notionals.append(copy_notional)
        cost += copy_notional * cost_rate
        gross += float(f.get("closedPnl", 0) or 0) * ratio
    return CopySimulation(
        gross_pnl_usd=round(gross, 4),
        cost_usd=round(cost, 4),
        net_pnl_usd=round(gross - cost, 4),
        median_copy_notional_usd=round(statistics.median(notionals), 4),
        n_fills=len(window),
    )


# --- Anti-MM / vault / arb ---------------------------------------------------
def looks_like_mm(trades_per_day: float, pnl_over_volume: float,
                  avg_abs_net_exposure_share: float,
                  *, max_tpd: float = 200.0, max_pnl_vol: float = 0.0001,
                  max_neutral_exposure: float = 0.02) -> bool:
    """F9: market maker / arb / delta-neutro persistente."""
    if trades_per_day > max_tpd:
        return True
    if abs(pnl_over_volume) < max_pnl_vol and trades_per_day > 50:
        return True
    if avg_abs_net_exposure_share < max_neutral_exposure and trades_per_day > 20:
        return True
    return False


# --- Coortes -------------------------------------------------------------------
def size_cohort(equity: float, bands: dict[str, float]) -> str:
    for label, ceiling in bands.items():
        if equity < float(ceiling):
            return label
    return list(bands)[-1]


def pnl_cohort(pnl_alltime: float, bands: dict[str, float]) -> str:
    for label, ceiling in bands.items():
        if pnl_alltime < float(ceiling):
            return label
    return list(bands)[-1]


# --- Score composto -------------------------------------------------------------
@dataclass
class ScoreComponents:
    consistency: float = 0.0        # [0,1]
    profit_factor: float = 0.0      # [0,1] (pf_score_credit)
    roi_log: float = 0.0            # [0,1]
    drawdown_quality: float = 0.0   # [0,1]
    copyability: float = 0.0        # [0,1]
    net_expectancy: float = 0.0     # [0,1]
    adjustments: list[tuple[str, float]] = field(default_factory=list)


def composite_score(c: ScoreComponents, weights: dict[str, float]) -> float:
    base = 100.0 * (
        weights["consistency"] * c.consistency
        + weights["profit_factor"] * c.profit_factor
        + weights["roi_log"] * c.roi_log
        + weights["drawdown_quality"] * c.drawdown_quality
        + weights["copyability"] * c.copyability
        + weights["net_expectancy"] * c.net_expectancy
    )
    base += sum(v for _, v in c.adjustments)
    return max(0.0, min(100.0, round(base, 2)))
