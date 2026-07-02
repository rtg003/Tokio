from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from engine.strategies.tradingview.scanner import (
    detect_cme_gap,
    detect_funding_anomalies,
    detect_low_liquidity_moves,
    is_cme_closed,
    last_cme_close,
    render_markdown,
    run_scan,
)

SATURDAY = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)   # CME closed
TUESDAY = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)   # CME open


def test_cme_calendar() -> None:
    close = last_cme_close(SATURDAY)
    assert close.weekday() == 4 and close.hour == 21           # Friday 21:00 UTC
    assert is_cme_closed(SATURDAY)
    assert not is_cme_closed(TUESDAY)


def test_cme_gap_detected_on_weekend() -> None:
    close_ms = last_cme_close(SATURDAY).timestamp() * 1000
    candles = [{"t": close_ms, "c": "100000"}]
    gap = detect_cme_gap(candles, SATURDAY, current_price=103_000.0)
    assert gap and gap["gap_pct"] == pytest.approx(3.0)
    # small gap ignored
    assert detect_cme_gap(candles, SATURDAY, current_price=100_200.0) is None
    # weekday ignored
    assert detect_cme_gap(candles, TUESDAY, current_price=103_000.0) is None


def universe_ctxs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    universe = [{"name": n} for n in ("BTC", "ETH", "MEME", "ALT")]
    ctxs = [
        {"funding": "0.0000125", "markPx": "100000", "prevDayPx": "99000",
         "dayNtlVlm": "2000000000"},                         # BTC normal
        {"funding": "0.0001", "markPx": "4000", "prevDayPx": "4100",
         "dayNtlVlm": "800000000"},                          # ETH: 87.6%/ano crowded long
        {"funding": "-0.0002", "markPx": "1.15", "prevDayPx": "1.0",
         "dayNtlVlm": "50000"},                              # MEME: +15% em volume mínimo
        {"funding": "0.0000125", "markPx": "10", "prevDayPx": "10.1",
         "dayNtlVlm": "10000000"},                           # ALT normal
    ]
    return universe, ctxs


def test_funding_anomalies() -> None:
    universe, ctxs = universe_ctxs()
    anomalies = detect_funding_anomalies(universe, ctxs)
    symbols = [a["symbol"] for a in anomalies]
    assert "ETH" in symbols and "MEME" in symbols and "BTC" not in symbols
    eth = next(a for a in anomalies if a["symbol"] == "ETH")
    assert eth["crowded_side"] == "long"
    assert eth["funding_annualized_pct"] == pytest.approx(87.6)


def test_low_liquidity_moves() -> None:
    universe, ctxs = universe_ctxs()
    moves = detect_low_liquidity_moves(universe, ctxs)
    assert len(moves) == 1 and moves[0]["symbol"] == "MEME"
    assert moves[0]["move_24h_pct"] == pytest.approx(15.0)


def test_run_scan_and_markdown() -> None:
    class FakeSource:
        def meta_and_asset_ctxs(self):
            return universe_ctxs()

        def candles(self, symbol, interval, start_ms, end_ms):
            return [{"t": start_ms + 3_600_000, "c": "99000"}]

    scan = run_scan(FakeSource(), now=SATURDAY)
    assert scan["cme_gap"] is not None
    assert scan["funding_anomalies"]
    md = render_markdown(scan)
    assert "Gap do CME" in md and "ETH" in md and "MEME" in md
