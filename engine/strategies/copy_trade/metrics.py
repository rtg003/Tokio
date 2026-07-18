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


# --- v15: métricas de POSIÇÃO a partir de posições consolidadas do HyperTracker -
def _ht_first(pos: dict[str, Any], *keys: str) -> Any:
    """Primeiro valor não-nulo entre `keys` — tolera nomes camelCase/snake_case."""
    for k in keys:
        v = pos.get(k)
        if v is not None:
            return v
    return None


def _ht_ms(value: Any) -> float | None:
    """Timestamp de posição do HT → ms (aceita epoch s/ms ou ISO-8601)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v * 1000.0 if v < 1e12 else v      # segundos → ms
    try:
        from datetime import datetime as _dt
        s = str(value).replace("Z", "+00:00")
        return _dt.fromisoformat(s).timestamp() * 1000.0
    except (TypeError, ValueError):
        return None


def _ht_is_closed(pos: dict[str, Any]) -> bool:
    status = str(_ht_first(pos, "status", "state") or "").lower()
    if status in ("closed", "close"):
        return True
    if status in ("open", "opened"):
        return False
    # sem status explícito: fechada se tem timestamp de fechamento.
    return _ht_ms(_ht_first(pos, "closedAt", "closed_at", "closeTime", "endTime")) is not None


def position_metrics_from_ht(positions: list[dict[str, Any]], now_ms: float,
                             liquid: set[str] | None = None, *,
                             window_days_30: int = 30,
                             window_days_7: int = 7) -> dict[str, Any]:
    """v15: deriva as métricas de posição do candidato a partir das POSIÇÕES
    CONSOLIDADAS do HyperTracker (sem o teto de ~2.000 fills da HL).

    Espelha a semântica dos campos calculados hoje em `funnel.deep_dive` a partir
    de fills, mas sobre posições já agregadas (entrada/saída/pnl/hold por posição).
    Função PURA (sem I/O) — testável com posições sintéticas. NÃO toca em
    `simulate_copy`: a simulação de cópia (F15/F17/F18/F19) segue em fills HL.
    """
    liquid = liquid or set()
    closed = [p for p in positions if _ht_is_closed(p)]
    open_pos = [p for p in positions if not _ht_is_closed(p)]

    def _pnl(p: dict[str, Any]) -> float:
        return float(_ht_first(p, "realizedPnl", "realized_pnl", "closedPnl",
                               "pnl", "netPnl") or 0.0)

    def _notional(p: dict[str, Any]) -> float:
        n = _ht_first(p, "volume", "notional", "positionValue", "notionalUsd")
        if n is not None:
            return abs(float(n or 0.0))
        size = float(_ht_first(p, "size", "sz", "szi") or 0.0)
        price = float(_ht_first(p, "avgEntryPrice", "entryPx", "price", "avgPrice") or 0.0)
        return abs(size * price)

    def _coin(p: dict[str, Any]) -> str:
        return str(_ht_first(p, "coin", "symbol", "asset", "market") or "")

    def _close_ms(p: dict[str, Any]) -> float | None:
        return _ht_ms(_ht_first(p, "closedAt", "closed_at", "closeTime", "endTime"))

    def _open_ms(p: dict[str, Any]) -> float | None:
        return _ht_ms(_ht_first(p, "openedAt", "opened_at", "openTime", "startTime"))

    closed_pnls = [_pnl(p) for p in closed]
    wins = [p for p in closed_pnls if p > 0]

    def _closed_in(days: int) -> list[dict[str, Any]]:
        cutoff = now_ms - days * DAY_MS
        out = []
        for p in closed:
            cm = _close_ms(p)
            if cm is None or cm >= cutoff:
                out.append(p)
        return out

    closed_30d = _closed_in(window_days_30)
    closed_7d = _closed_in(window_days_7)
    pnls_30d = [_pnl(p) for p in closed_30d]
    wins_30d = [p for p in pnls_30d if p > 0]

    # hold: mediana entre openedAt e closedAt das posições fechadas.
    holds = []
    for p in closed:
        om, cm = _open_ms(p), _close_ms(p)
        if om is not None and cm is not None and cm >= om:
            holds.append((cm - om) / HOUR_MS)
    median_hold = statistics.median(holds) if holds else None

    gains = sum(wins)
    losses = abs(sum(p for p in closed_pnls if p < 0))
    unrealized = sum(float(_ht_first(p, "unrealizedPnl", "unrealized_pnl") or 0.0)
                     for p in open_pos)
    pf = profit_factor(gains, losses, unrealized) if (gains or losses or unrealized) else None

    levs = [float(_ht_first(p, "leverage", "lev") or 0.0) for p in open_pos]
    levs = [l for l in levs if l > 0]
    avg_leverage = statistics.mean(levs) if levs else None

    total_vol = sum(_notional(p) for p in positions)
    liquid_share = 1.0
    if liquid and total_vol > 0:
        liquid_vol = sum(_notional(p) for p in positions if _coin(p) in liquid)
        liquid_share = liquid_vol / total_vol

    opens = [om for p in positions if (om := _open_ms(p)) is not None]
    covered_days = max((now_ms - min(opens)) / DAY_MS, 0.0) if opens else None

    return {
        "n_trades": len(closed),
        "n_trades_30d": len(closed_30d),
        "n_trades_7d": len(closed_7d),
        "win_rate": (len(wins) / len(closed_pnls)) if closed_pnls else None,
        "win_rate_30d": (len(wins_30d) / len(pnls_30d)) if pnls_30d else None,
        "profit_factor": pf,
        "median_hold_hours": median_hold,
        "top3_concentration": top_n_concentration(closed_pnls, 3),
        "avg_leverage": avg_leverage,
        "liquid_volume_share": liquid_share,
        "covered_days": covered_days,
    }


# --- Simulação retroativa de cópia (v7 — F15 · v8 — Estágio 4) ----------------
@dataclass
class CopySimulation:
    gross_pnl_usd: float            # closedPnl da janela × ratio
    cost_usd: float                 # Σ notional_fill × ratio × custo por perna
    net_pnl_usd: float              # gross − cost
    median_copy_notional_usd: float  # mediana do notional espelhado por fill
    n_fills: int
    # v8 (Estágio 4): métricas da CÓPIA como carteira própria
    latency_cost_usd: float = 0.0   # parcela do custo vinda da latência
    expectancy_usd: float = 0.0     # net / nº de trades fechados na janela
    max_dd_pct: float = 0.0         # max DD da curva de equity da cópia
    n_closed: int = 0               # trades fechados (closedPnl != 0)


def simulate_copy(fills: list[dict[str, Any]], trader_equity: float,
                  mirror_capital: float, *, taker_fee_pct: float = 0.045,
                  slippage_pct: float = 0.02, latency_slippage_pct: float = 0.0,
                  window_days: float = 30.0, max_copy_leverage: float | None = None,
                  now_ms: float | None = None) -> CopySimulation | None:
    """Espelhamento retroativo: "se tivéssemos copiado este trader com
    `mirror_capital` na janela, qual seria o PnL LÍQUIDO de taxas+slippage?"

    UPDATE-0069 — SIZING PROPORCIONAL À EQUITY (fractional). Cada fill do trader
    é replicado como uma FRAÇÃO da NOSSA equity simulada CORRENTE, não do notional
    absoluto do trader. Modelo canônico de copy-trade com fração fixa:

      ron           = closedPnl / notional         # retorno-sobre-notional do trader
      fill_leverage = notional / trader_equity     # alavancagem do trader no fill
      copy_notional = min(equity * fill_leverage, equity * max_copy_leverage)
      pnl           = ron * copy_notional
      equity        = max(equity + pnl - custos, 0.0)   # PISO DE LIQUIDAÇÃO

    O `ron` é limpo (não depende do snapshot de equity); o snapshot só entra via
    `fill_leverage`, que é limitado por `max_copy_leverage`. O PISO em 0.0 modela
    liquidação: uma vez zerada a conta, `copy_notional = 0` e nada mais se move.

    Por que substitui o UPDATE-0067 (cap `ratio = min(mirror_capital/equity, 1.0)`):
    aquele cap NÃO resolvia o caso `trader_equity << mirror_capital` — copiávamos o
    `closedPnl` ABSOLUTO de milhares de fills abaixo do teto de notional, somando o
    PnL total do trader ($864k de um trader de $394 de equity) e gerando SIM NET de
    centenas de milhares e SIM DD > 100% (curva a negativo, impossível). O snapshot
    de equity não representa o capital girado (saques/aporte/anomaly) e não havia
    restrição de buying-power. O sizing fractional corrige a RAIZ.

    Garantias por construção:
      • DD ∈ [0, 100%]  — o piso de liquidação impede equity negativa.
      • net ≥ −mirror_capital  — não se perde mais do que se aloca.
      • net ∝ mirror_capital  — tudo é relativo à equity corrente, então
        equity_t = mirror_capital · Π(1+…); o SINAL do net independe do capital
        (invariância de capital preservada).
      • traders com `trader_equity ≥ mirror_capital` ficam ~inalterados: no 1º
        fill copy_notional = notional·(mirror_capital/trader_equity), idêntico ao
        modelo antigo; só há leve drift de composição em janelas multi-fill.

    `max_copy_leverage` (v9): agora limita a alavancagem SOBRE A NOSSA EQUITY
    (copy_notional ≤ equity × teto), não sobre o notional do trader. Um trader de
    equity minúscula é copiado no teto em todo fill; com o `ron` real (volátil) a
    alta alavancagem, a conta simulada normalmente LIQUIDA (net ≈ −capital, DD 100%)
    — a resposta honesta: "copiar esse trader te quebra".

    v8 — `latency_slippage_pct` modela o custo da latência de espelhamento (200ms–2s)
    como slippage EXTRA por perna (bps fixo, config). A curva de equity da cópia
    produz `max_dd_pct` e `expectancy_usd`.

    Aproximações documentadas: equity ATUAL do trader como denominador do
    `fill_leverage` (equity da janela não é conhecido ponto a ponto); só PnL
    REALIZADO (rejeitar lucro não-realizado é intencional — dossiê #1 do Hermes);
    funding ignorado.
    """
    if trader_equity <= 0 or mirror_capital <= 0:
        return None
    import time as _time
    now_ms = now_ms or _time.time() * 1000
    cutoff = now_ms - window_days * DAY_MS
    window = sorted((f for f in fills if float(f.get("time", 0)) >= cutoff),
                    key=lambda f: float(f.get("time", 0)))
    if not window:
        return None
    max_lev = float(max_copy_leverage) if max_copy_leverage else None
    base_rate = (taker_fee_pct + slippage_pct) / 100.0        # por perna
    lat_rate = latency_slippage_pct / 100.0                    # por perna
    gross = 0.0
    cost = 0.0
    lat_cost = 0.0
    n_closed = 0
    notionals = []
    equity = mirror_capital
    peak = equity
    max_dd = 0.0
    for f in window:
        notional = abs(float(f.get("sz", 0) or 0) * float(f.get("px", 0) or 0))
        if notional <= 0 or equity <= 0:      # sem notional / conta liquidada
            notionals.append(0.0)
            continue
        # UPDATE-0069: sizing fractional — cópia = fração da NOSSA equity corrente,
        # replicando a alavancagem do trader e limitada por max_copy_leverage.
        ron = float(f.get("closedPnl", 0) or 0) / notional
        fill_leverage = notional / trader_equity
        copy_notional = equity * fill_leverage
        if max_lev is not None:
            copy_notional = min(copy_notional, equity * max_lev)
        notionals.append(copy_notional)
        leg_cost = copy_notional * base_rate
        leg_lat = copy_notional * lat_rate
        cost += leg_cost + leg_lat
        lat_cost += leg_lat
        pnl = ron * copy_notional
        gross += pnl
        if pnl != 0.0:
            n_closed += 1
        equity = max(equity + pnl - leg_cost - leg_lat, 0.0)   # piso de liquidação
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    net = equity - mirror_capital     # honesto: ≥ −mirror_capital pelo piso
    return CopySimulation(
        gross_pnl_usd=round(gross, 4),
        cost_usd=round(cost, 4),
        net_pnl_usd=round(net, 4),
        median_copy_notional_usd=round(statistics.median(notionals), 4),
        n_fills=len(window),
        latency_cost_usd=round(lat_cost, 4),
        expectancy_usd=round(net / n_closed, 4) if n_closed else 0.0,
        max_dd_pct=round(max_dd * 100, 2),
        n_closed=n_closed,
    )


def copy_sim_factor(net_pnl_usd: float, mirror_capital: float,
                    *, floor: float = 0.5, cap: float = 1.2) -> float:
    """Fator multiplicativo do ranking final (Estágio 4, v8).

    factor = 1 + ROI da cópia simulada, clampado em [floor, cap] — cópia que
    rende 10% na janela multiplica o score por 1.10; cópia no zero fica
    neutra (1.0). Net NEGATIVO nem chega aqui: o candidato é rebaixado a
    REJEITADO (copy_sim_negativa) antes do ranking.
    """
    if mirror_capital <= 0:
        return 1.0
    return max(floor, min(cap, 1.0 + net_pnl_usd / mirror_capital))


# --- Anti-MM / vault / arb ---------------------------------------------------
def looks_like_mm(trades_per_day: float, pnl_over_volume: float,
                  avg_abs_net_exposure_share: float,
                  *, max_tpd: float | None = 200.0,
                  max_pnl_vol: float | None = 0.0001,
                  min_tpd_for_pnl_vol: float | None = 50.0,
                  max_neutral_exposure: float | None = 0.02,
                  min_tpd_for_neutral: float | None = 20.0) -> bool:
    """F9: market maker / arb / delta-neutro persistente."""
    if max_tpd is not None and trades_per_day > max_tpd:
        return True
    if max_pnl_vol is not None and min_tpd_for_pnl_vol is not None and \
            abs(pnl_over_volume) < max_pnl_vol and trades_per_day > min_tpd_for_pnl_vol:
        return True
    if max_neutral_exposure is not None and min_tpd_for_neutral is not None and \
            avg_abs_net_exposure_share < max_neutral_exposure and \
            trades_per_day > min_tpd_for_neutral:
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
    sim_net: float = 0.0            # [0,1] — cópia simulada líquida 30d normalizada
    adjustments: list[tuple[str, float]] = field(default_factory=list)


def composite_score(c: ScoreComponents, weights: dict[str, float]) -> float:
    base = 100.0 * (
        weights["consistency"] * c.consistency
        + weights["profit_factor"] * c.profit_factor
        + weights["roi_log"] * c.roi_log
        + weights["drawdown_quality"] * c.drawdown_quality
        + weights["copyability"] * c.copyability
        + weights["net_expectancy"] * c.net_expectancy
        + weights.get("sim_net", 0.0) * c.sim_net
    )
    base += sum(v for _, v in c.adjustments)
    return max(0.0, min(100.0, round(base, 2)))
