"""Trader discovery (CLI) — ranked candidates for copy trading.

Collects candidates from the Hyperliquid leaderboard/stats API plus wallet
analysis (fills, portfolio history, equity), computes per-candidate metrics,
FILTERS high-frequency scalpers (copy trade has structural latency — only
edges that survive seconds of delay are rankable) and emits a ranked report
(JSON + markdown) with numeric justification.

The decision to copy is ALWAYS human (gate operated via the Hermes runbook).

Usage:
    python -m engine.strategies.copy_trade.discovery --top 10 \
        --out docs/reports/discovery
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"

# Style filter thresholds (see strategy.md: scalper edge dies with latency)
MAX_TRADES_PER_DAY = 40.0
MIN_MEDIAN_HOLD_MINUTES = 30.0


class DiscoverySource(Protocol):
    def leaderboard(self) -> list[dict[str, Any]]: ...
    def user_fills(self, address: str) -> list[dict[str, Any]]: ...
    def portfolio(self, address: str) -> dict[str, Any]: ...


class HyperliquidDiscoverySource:
    """Live source — mainnet stats (leaderboard only exists on mainnet).

    Rate-limit friendly (exigência da spec): intervalo mínimo entre requests
    (limite por IP é 1.200 weight/min; endpoints de info pesam ~20 → ~60
    req/min máx.) + backoff exponencial em 429.
    """

    def __init__(self, *, min_interval_s: float = 1.3,
                 max_retries: int = 4) -> None:
        import httpx

        self._http = httpx.Client(timeout=30.0)
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self._last_request_ts = 0.0

    def _throttled(self, do_request: Any) -> Any:
        """Aplica intervalo mínimo + retry com backoff exponencial em 429."""
        import httpx

        backoff = 5.0
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last_request_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_request_ts = time.monotonic()
            try:
                resp = do_request()
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429 or attempt == self.max_retries:
                    raise
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError("unreachable")

    def leaderboard(self) -> list[dict[str, Any]]:
        resp = self._throttled(lambda: self._http.get(LEADERBOARD_URL))
        return resp.json().get("leaderboardRows", [])

    def user_fills(self, address: str) -> list[dict[str, Any]]:
        resp = self._throttled(
            lambda: self._http.post(INFO_URL, json={"type": "userFills", "user": address}))
        return resp.json()

    def portfolio(self, address: str) -> dict[str, Any]:
        resp = self._throttled(
            lambda: self._http.post(INFO_URL, json={"type": "portfolio", "user": address}))
        return dict(resp.json())


@dataclass
class CandidateMetrics:
    address: str
    display_name: str | None
    equity: float
    pnl_30d: float
    roi_30d: float
    pnl_alltime: float
    max_drawdown_pct: float
    win_rate: float
    trades_per_day: float
    median_hold_minutes: float
    consistency: float          # 1 - share of positive PnL in the top 2 trades
    n_fills_analyzed: int
    style: str = "unknown"      # swing | position | scalper
    excluded: bool = False
    exclusion_reason: str | None = None
    score: float = 0.0
    rationale: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# pure metric helpers (unit-tested)
# ---------------------------------------------------------------------------
def max_drawdown_pct(equity_curve: list[float]) -> float:
    peak = float("-inf")
    mdd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd * 100.0


def win_rate_from_fills(fills: list[dict[str, Any]]) -> float:
    closed = [float(f["closedPnl"]) for f in fills if float(f.get("closedPnl", 0)) != 0]
    if not closed:
        return 0.0
    return sum(1 for p in closed if p > 0) / len(closed)


def consistency_from_fills(fills: list[dict[str, Any]]) -> float:
    """1.0 = evenly distributed wins; ~0.0 = PnL concentrated in 1-2 lucky trades."""
    gains = sorted((float(f["closedPnl"]) for f in fills
                    if float(f.get("closedPnl", 0)) > 0), reverse=True)
    if not gains:
        return 0.0
    total = sum(gains)
    top2 = sum(gains[:2])
    return max(0.0, 1.0 - top2 / total) if total > 0 else 0.0


def trade_frequency(fills: list[dict[str, Any]], now_ms: float | None = None) -> float:
    if not fills:
        return 0.0
    times = sorted(float(f["time"]) for f in fills)
    now_ms = now_ms or time.time() * 1000
    span_days = max((now_ms - times[0]) / 86_400_000, 1e-9)
    return len(times) / span_days


def median_hold_minutes(fills: list[dict[str, Any]]) -> float:
    """Median time between a position-opening fill and the first closing fill
    per coin — a cheap but effective proxy for holding style."""
    by_coin: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(fills, key=lambda x: float(x["time"])):
        by_coin.setdefault(str(f.get("coin")), []).append(f)
    holds: list[float] = []
    for coin_fills in by_coin.values():
        open_time: float | None = None
        for f in coin_fills:
            start = float(f.get("startPosition", 0))
            if start == 0 and open_time is None:
                open_time = float(f["time"])
            elif float(f.get("closedPnl", 0)) != 0 and open_time is not None:
                holds.append((float(f["time"]) - open_time) / 60_000)
                open_time = None
    return statistics.median(holds) if holds else 0.0


def classify_style(trades_per_day: float, hold_minutes: float) -> str:
    if trades_per_day > MAX_TRADES_PER_DAY or (0 < hold_minutes < MIN_MEDIAN_HOLD_MINUTES):
        return "scalper"
    if hold_minutes >= 24 * 60:
        return "position"
    return "swing"


def score_candidate(m: CandidateMetrics) -> float:
    """Composite score in [0, 100]; only meaningful for non-excluded candidates."""
    if m.excluded:
        return 0.0
    roi_component = max(min(m.roi_30d, 100.0), -100.0) / 100.0        # [-1, 1]
    dd_component = max(0.0, 1.0 - m.max_drawdown_pct / 50.0)          # 0 at 50% DD
    return round(
        100 * (0.35 * max(roi_component, 0)
               + 0.25 * dd_component
               + 0.20 * m.consistency
               + 0.20 * m.win_rate), 2)


# ---------------------------------------------------------------------------
def analyze_candidate(
    source: DiscoverySource,
    row: dict[str, Any],
    *,
    min_equity: float = 10_000.0,
    now_ms: float | None = None,
) -> CandidateMetrics:
    address = row["ethAddress"]
    perfs = dict(row.get("windowPerformances", []))
    month = perfs.get("month", {})
    alltime = perfs.get("allTime", {})
    equity = float(row.get("accountValue", 0))

    fills = source.user_fills(address)
    portfolio = source.portfolio(address)
    month_hist = dict(portfolio.get("month", portfolio.get("allTime", {})) or {})
    curve = [float(v) for _, v in (month_hist.get("accountValueHistory") or [])]

    m = CandidateMetrics(
        address=address,
        display_name=row.get("displayName"),
        equity=equity,
        pnl_30d=float(month.get("pnl", 0)),
        roi_30d=float(month.get("roi", 0)) * 100,
        pnl_alltime=float(alltime.get("pnl", 0)),
        max_drawdown_pct=max_drawdown_pct(curve),
        win_rate=win_rate_from_fills(fills),
        trades_per_day=trade_frequency(fills, now_ms),
        median_hold_minutes=median_hold_minutes(fills),
        consistency=consistency_from_fills(fills),
        n_fills_analyzed=len(fills),
    )
    m.style = classify_style(m.trades_per_day, m.median_hold_minutes)

    if m.style == "scalper":
        m.excluded, m.exclusion_reason = True, (
            f"scalper: {m.trades_per_day:.1f} trades/dia, hold mediano "
            f"{m.median_hold_minutes:.0f} min — edge não sobrevive à latência do espelhamento")
    elif m.equity < min_equity:
        m.excluded, m.exclusion_reason = True, f"equity {m.equity:.0f} < mínimo {min_equity:.0f}"
    elif m.pnl_30d <= 0:
        m.excluded, m.exclusion_reason = True, f"PnL 30d não positivo ({m.pnl_30d:.0f})"
    elif m.consistency < 0.2 and m.n_fills_analyzed >= 10:
        m.excluded, m.exclusion_reason = True, (
            f"resultado concentrado: top-2 trades = {(1 - m.consistency) * 100:.0f}% do PnL positivo")

    m.score = score_candidate(m)
    m.rationale = [
        f"PnL 30d: {m.pnl_30d:,.0f} USD (ROI {m.roi_30d:.1f}%)",
        f"max drawdown: {m.max_drawdown_pct:.1f}%",
        f"win rate: {m.win_rate * 100:.0f}% em {m.n_fills_analyzed} fills",
        f"consistência: {m.consistency:.2f} (1 = distribuído; 0 = 1-2 trades de sorte)",
        f"estilo: {m.style} ({m.trades_per_day:.1f} trades/dia, hold {m.median_hold_minutes:.0f} min)",
    ]
    return m


def run_discovery(
    source: DiscoverySource,
    *,
    top: int = 10,
    max_candidates: int = 30,   # 2 requests/candidato; manter o scan < ~90s
    min_equity: float = 10_000.0,
    now_ms: float | None = None,
) -> list[CandidateMetrics]:
    rows = source.leaderboard()[:max_candidates]
    metrics = [analyze_candidate(source, r, min_equity=min_equity, now_ms=now_ms)
               for r in rows]
    ranked = sorted((m for m in metrics if not m.excluded),
                    key=lambda m: m.score, reverse=True)
    excluded = [m for m in metrics if m.excluded]
    return ranked[:top] + excluded


def render_markdown(candidates: list[CandidateMetrics]) -> str:
    ranked = [c for c in candidates if not c.excluded]
    excluded = [c for c in candidates if c.excluded]
    lines = [
        "# Discovery — candidatos a copy trade",
        "",
        "> A decisão de copiar é sempre humana. Este relatório apenas ranqueia com",
        "> justificativa numérica. Scalpers são filtrados por definição.",
        "",
        "## Ranking",
        "",
        "| # | Endereço | Score | PnL 30d | ROI 30d | Max DD | Win rate | Estilo |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(ranked, 1):
        lines.append(
            f"| {i} | `{c.address}` | {c.score} | {c.pnl_30d:,.0f} | {c.roi_30d:.1f}% "
            f"| {c.max_drawdown_pct:.1f}% | {c.win_rate * 100:.0f}% | {c.style} |")
    lines.append("")
    for i, c in enumerate(ranked, 1):
        lines.append(f"### {i}. `{c.address}`")
        lines.extend(f"- {r}" for r in c.rationale)
        lines.append("")
    if excluded:
        lines.append("## Excluídos (com motivo)")
        lines.append("")
        for c in excluded:
            lines.append(f"- `{c.address}`: {c.exclusion_reason}")
    return "\n".join(lines)


# v1 da lógica em produção (ver docs/discovery_changelog.md). A spec v2
# (PROMPT_DISCOVERY_TRADERS_v4) substituirá score/coorte/TWRR quando aplicada.
LOGIC_VERSION = 1


def persist_candidates(db: Any, candidates: list[CandidateMetrics]) -> None:
    """Upsert dos candidatos na tabela `traders` (fonte única, ADR 0008) +
    snapshot agregado por coorte. Nunca rebaixa status de quem já opera."""
    from engine.strategies.copy_trade.traders_store import (
        upsert_candidate,
        write_cohort_snapshot,
    )

    ranked = [c for c in candidates if not c.excluded]
    for c in ranked:
        upsert_candidate(
            db,
            address=c.address,
            name=c.display_name,
            score=c.score,
            cohort=c.style,               # v1: coorte = estilo (unidimensional)
            twrr_30d=c.roi_30d,           # v1: aproximação — ROI da janela 30d
            pnl_30d=c.pnl_30d,
            windows={"pnl_30d": c.pnl_30d, "pnl_alltime": c.pnl_alltime},
            win_rate=c.win_rate,
            max_drawdown=c.max_drawdown_pct,
            logic_version=LOGIC_VERSION,
        )
    cohorts: dict[str, dict[str, Any]] = {}
    for c in ranked:
        agg = cohorts.setdefault(c.style, {"n": 0, "scores": []})
        agg["n"] += 1
        agg["scores"].append(c.score)
    write_cohort_snapshot(db, logic_version=LOGIC_VERSION, cohorts={
        k: {"n": v["n"], "avg_score": sum(v["scores"]) / v["n"]}
        for k, v in cohorts.items()
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="copy_trade.discovery")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--max-candidates", type=int, default=50)
    parser.add_argument("--min-equity", type=float, default=10_000.0)
    parser.add_argument("--out", default="docs/reports/discovery")
    parser.add_argument("--no-db", action="store_true",
                        help="não gravar na tabela traders (só relatório)")
    args = parser.parse_args(argv)

    source = HyperliquidDiscoverySource()
    candidates = run_discovery(source, top=args.top,
                               max_candidates=args.max_candidates,
                               min_equity=args.min_equity)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    (out / f"discovery-{stamp}.json").write_text(
        json.dumps([asdict(c) for c in candidates], indent=2, ensure_ascii=False))
    md = render_markdown(candidates)
    (out / f"discovery-{stamp}.md").write_text(md)
    if not args.no_db:
        from engine.core.config import get_settings
        from engine.core.db import Database

        db = Database(get_settings().sqlite_path)
        db.migrate()
        persist_candidates(db, candidates)
        print(f"[traders] {len([c for c in candidates if not c.excluded])} candidatos "
              f"upsertados (logic_version={LOGIC_VERSION}) + cohort_snapshot gravado")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
