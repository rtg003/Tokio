from __future__ import annotations

from typing import Any

import pytest

from engine.strategies.copy_trade.discovery import (
    CandidateMetrics,
    classify_style,
    consistency_from_fills,
    max_drawdown_pct,
    median_hold_minutes,
    render_markdown,
    run_discovery,
    score_candidate,
    trade_frequency,
    win_rate_from_fills,
)

DAY_MS = 86_400_000.0
NOW_MS = 100 * DAY_MS


def make_fill(coin: str, time_ms: float, closed_pnl: float = 0.0,
              start_position: float = 0.0) -> dict[str, Any]:
    return {"coin": coin, "time": time_ms, "closedPnl": str(closed_pnl),
            "startPosition": str(start_position), "sz": "1", "px": "100", "side": "B"}


class FakeSource:
    def __init__(self, rows: list[dict[str, Any]], fills: dict[str, list[dict[str, Any]]],
                 curves: dict[str, list[float]]) -> None:
        self.rows = rows
        self.fills = fills
        self.curves = curves

    def leaderboard(self) -> list[dict[str, Any]]:
        return self.rows

    def user_fills(self, address: str) -> list[dict[str, Any]]:
        return self.fills.get(address, [])

    def portfolio(self, address: str) -> dict[str, Any]:
        curve = self.curves.get(address, [])
        return {"month": {"accountValueHistory": [[i, v] for i, v in enumerate(curve)]}}


def leaderboard_row(address: str, equity: float, pnl_month: float, roi: float) -> dict[str, Any]:
    return {
        "ethAddress": address, "accountValue": equity, "displayName": None,
        "windowPerformances": [
            ["month", {"pnl": pnl_month, "roi": roi}],
            ["allTime", {"pnl": pnl_month * 4, "roi": roi * 2}],
        ],
    }


def swing_fills(base_ms: float = 0.0) -> list[dict[str, Any]]:
    """~1 trade a cada 5 dias, holds de 2 dias, PnL bem distribuído."""
    fills = []
    for i in range(10):
        t_open = base_ms + i * 5 * DAY_MS
        fills.append(make_fill("BTC", t_open, 0.0, start_position=0.0))
        fills.append(make_fill("BTC", t_open + 2 * DAY_MS, 100.0 + i, start_position=1.0))
    return fills


def test_max_drawdown() -> None:
    assert max_drawdown_pct([100, 120, 60, 90]) == pytest.approx(50.0)
    assert max_drawdown_pct([100, 110, 120]) == 0.0
    assert max_drawdown_pct([]) == 0.0


def test_win_rate_and_consistency() -> None:
    fills = [make_fill("BTC", 0, pnl) for pnl in (10, 20, -5, 30, -10)]
    assert win_rate_from_fills(fills) == pytest.approx(3 / 5)
    # gains 10+20+30=60; top2 = 50 -> consistency ~0.167
    assert consistency_from_fills(fills) == pytest.approx(1 - 50 / 60)


def test_frequency_and_style_classification() -> None:
    fills = swing_fills()
    freq = trade_frequency(fills, now_ms=NOW_MS)
    hold = median_hold_minutes(fills)
    assert freq < 1.0
    assert hold == pytest.approx(2 * 24 * 60)
    assert classify_style(freq, hold) == "position"
    assert classify_style(100.0, 5.0) == "scalper"
    assert classify_style(2.0, 5.0) == "scalper"       # tiny holds
    assert classify_style(3.0, 600.0) == "swing"


def test_scalpers_are_filtered_with_reason() -> None:
    scalper_fills = [make_fill("ETH", NOW_MS - i * 60_000, 5.0, start_position=1.0)
                     for i in range(200)]
    source = FakeSource(
        rows=[leaderboard_row("0xscalper", 50_000, 9_000, 0.4)],
        fills={"0xscalper": scalper_fills},
        curves={"0xscalper": [100, 110, 120]},
    )
    result = run_discovery(source, now_ms=NOW_MS)
    assert len(result) == 1
    assert result[0].excluded and "scalper" in result[0].exclusion_reason


def test_ranked_report_orders_by_score(tmp_path) -> None:
    source = FakeSource(
        rows=[
            leaderboard_row("0xgood", 100_000, 20_000, 0.30),
            leaderboard_row("0xmeh", 50_000, 1_000, 0.02),
            leaderboard_row("0xloser", 60_000, -5_000, -0.10),
        ],
        fills={
            "0xgood": swing_fills(),
            "0xmeh": swing_fills(),
            "0xloser": swing_fills(),
        },
        curves={
            "0xgood": [100, 105, 102, 130],
            "0xmeh": [100, 80, 90, 101],
            "0xloser": [100, 70, 60, 55],
        },
    )
    result = run_discovery(source, now_ms=NOW_MS)
    ranked = [c for c in result if not c.excluded]
    excluded = [c for c in result if c.excluded]
    assert [c.address for c in ranked] == ["0xgood", "0xmeh"]
    assert ranked[0].score > ranked[1].score
    assert excluded[0].address == "0xloser"
    assert "PnL 30d não positivo" in excluded[0].exclusion_reason

    md = render_markdown(result)
    assert "0xgood" in md and "Excluídos" in md and "Score" in md


def test_score_zero_when_excluded() -> None:
    m = CandidateMetrics(address="0x1", display_name=None, equity=1, pnl_30d=1,
                         roi_30d=1, pnl_alltime=1, max_drawdown_pct=1, win_rate=1,
                         trades_per_day=1, median_hold_minutes=1, consistency=1,
                         n_fills_analyzed=1, excluded=True)
    assert score_candidate(m) == 0.0
