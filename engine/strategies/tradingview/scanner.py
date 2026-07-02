"""Market scanner (CLI) — crypto is 24/7; these are the recurring dislocations:

1. CME gap on BTC (weekend): CME closes Friday ~21:00 UTC and reopens Sunday
   ~22:00 UTC while crypto keeps trading — the gap tends to get filled.
2. Funding rate anomalies: extreme funding = crowded positioning.
3. Abnormal moves in low liquidity: large 24h move on thin volume.

Output: JSON + markdown ready for a briefing. Scheduling/news are the Hermes
Agent's job at operation time (cron suggestions in the HANDOFF).

Usage:
    python -m engine.strategies.tradingview.scanner --out docs/reports/scanner
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

INFO_URL_MAINNET = "https://api.hyperliquid.xyz/info"

# Hourly funding of 0.00125% ~= 11%/yr is the neutral baseline on HL perps.
FUNDING_ANNUALIZED_THRESHOLD = 0.50   # |annualized| > 50% flagged
MOVE_PCT_THRESHOLD = 10.0             # |24h move| > 10%
CME_GAP_MIN_PCT = 0.5                 # report weekend gaps > 0.5%


class ScannerSource(Protocol):
    def meta_and_asset_ctxs(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...
    def candles(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]: ...


class HyperliquidScannerSource:
    def __init__(self, info_url: str = INFO_URL_MAINNET) -> None:
        import httpx

        self.info_url = info_url
        self._http = httpx.Client(timeout=30.0)

    def meta_and_asset_ctxs(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        resp = self._http.post(self.info_url, json={"type": "metaAndAssetCtxs"})
        resp.raise_for_status()
        meta, ctxs = resp.json()
        return meta["universe"], ctxs

    def candles(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        resp = self._http.post(self.info_url, json={
            "type": "candleSnapshot",
            "req": {"coin": symbol, "interval": interval,
                    "startTime": start_ms, "endTime": end_ms},
        })
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# pure detectors (unit-tested)
# ---------------------------------------------------------------------------
def last_cme_close(now: datetime) -> datetime:
    """Most recent CME close (Friday 21:00 UTC) at or before `now`."""
    days_back = (now.weekday() - 4) % 7
    candidate = (now - timedelta(days=days_back)).replace(
        hour=21, minute=0, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=7)
    return candidate


def is_cme_closed(now: datetime) -> bool:
    """True between Friday 21:00 UTC and Sunday 22:00 UTC."""
    close = last_cme_close(now)
    reopen = close + timedelta(days=2, hours=1)
    return close <= now < reopen


def detect_cme_gap(candles: list[dict[str, Any]], now: datetime,
                   current_price: float) -> dict[str, Any] | None:
    """Gap between BTC price at the last CME close and the current price,
    reported only while CME is closed (the tradable window)."""
    if not is_cme_closed(now) or not candles or current_price <= 0:
        return None
    close_ms = last_cme_close(now).timestamp() * 1000
    anchor = min(candles, key=lambda c: abs(float(c["t"]) - close_ms))
    ref = float(anchor["c"])
    if ref <= 0:
        return None
    gap_pct = (current_price - ref) / ref * 100
    if abs(gap_pct) < CME_GAP_MIN_PCT:
        return None
    return {
        "type": "cme_gap",
        "symbol": "BTC",
        "cme_close_price": ref,
        "current_price": current_price,
        "gap_pct": round(gap_pct, 3),
        "note": "gaps tendem a preencher na reabertura do CME (domingo 22:00 UTC)",
    }


def detect_funding_anomalies(universe: list[dict[str, Any]],
                             ctxs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for asset, ctx in zip(universe, ctxs):
        funding = float(ctx.get("funding", 0) or 0)
        annualized = funding * 24 * 365
        if abs(annualized) >= FUNDING_ANNUALIZED_THRESHOLD:
            out.append({
                "type": "funding_anomaly",
                "symbol": asset["name"],
                "funding_hourly": funding,
                "funding_annualized_pct": round(annualized * 100, 1),
                "crowded_side": "long" if funding > 0 else "short",
            })
    return sorted(out, key=lambda a: -abs(a["funding_annualized_pct"]))


def detect_low_liquidity_moves(universe: list[dict[str, Any]],
                               ctxs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    volumes = [float(c.get("dayNtlVlm", 0) or 0) for c in ctxs]
    positive = [v for v in volumes if v > 0]
    if not positive:
        return []
    median_vol = statistics.median(positive)
    out: list[dict[str, Any]] = []
    for asset, ctx in zip(universe, ctxs):
        mark = float(ctx.get("markPx", 0) or 0)
        prev = float(ctx.get("prevDayPx", 0) or 0)
        vol = float(ctx.get("dayNtlVlm", 0) or 0)
        if prev <= 0 or mark <= 0:
            continue
        move_pct = (mark - prev) / prev * 100
        if abs(move_pct) >= MOVE_PCT_THRESHOLD and 0 < vol < median_vol:
            out.append({
                "type": "low_liquidity_move",
                "symbol": asset["name"],
                "move_24h_pct": round(move_pct, 2),
                "volume_24h_usd": round(vol, 0),
                "median_volume_usd": round(median_vol, 0),
            })
    return sorted(out, key=lambda a: -abs(a["move_24h_pct"]))


def run_scan(source: ScannerSource, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    universe, ctxs = source.meta_and_asset_ctxs()

    cme_gap = None
    btc_idx = next((i for i, a in enumerate(universe) if a["name"] == "BTC"), None)
    if btc_idx is not None and is_cme_closed(now):
        close_ms = int(last_cme_close(now).timestamp() * 1000)
        candles = source.candles("BTC", "1h", close_ms - 3_600_000,
                                 close_ms + 2 * 3_600_000)
        current = float(ctxs[btc_idx].get("markPx", 0) or 0)
        cme_gap = detect_cme_gap(candles, now, current)

    return {
        "generated_at": now.isoformat(),
        "cme_gap": cme_gap,
        "funding_anomalies": detect_funding_anomalies(universe, ctxs),
        "low_liquidity_moves": detect_low_liquidity_moves(universe, ctxs),
    }


def render_markdown(scan: dict[str, Any]) -> str:
    lines = [f"# Scanner — {scan['generated_at']}", ""]
    gap = scan.get("cme_gap")
    lines.append("## Gap do CME (BTC)")
    if gap:
        lines.append(f"- Gap de **{gap['gap_pct']}%** vs. fechamento do CME "
                     f"({gap['cme_close_price']:.0f} → {gap['current_price']:.0f}). {gap['note']}")
    else:
        lines.append("- Sem gap relevante (ou CME aberto agora).")
    lines.append("")
    lines.append("## Anomalias de funding")
    if scan["funding_anomalies"]:
        lines.append("| Símbolo | Funding/h | Anualizado | Lado lotado |")
        lines.append("|---|---|---|---|")
        for a in scan["funding_anomalies"]:
            lines.append(f"| {a['symbol']} | {a['funding_hourly']:.6f} "
                         f"| {a['funding_annualized_pct']}% | {a['crowded_side']} |")
    else:
        lines.append("- Nenhuma anomalia acima do limiar.")
    lines.append("")
    lines.append("## Movimentos anormais em baixa liquidez")
    if scan["low_liquidity_moves"]:
        lines.append("| Símbolo | Movimento 24h | Volume 24h | Mediana |")
        lines.append("|---|---|---|---|")
        for a in scan["low_liquidity_moves"]:
            lines.append(f"| {a['symbol']} | {a['move_24h_pct']}% "
                         f"| {a['volume_24h_usd']:,.0f} | {a['median_volume_usd']:,.0f} |")
    else:
        lines.append("- Nenhum movimento anormal em baixa liquidez.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradingview.scanner")
    parser.add_argument("--out", default="docs/reports/scanner")
    args = parser.parse_args(argv)

    scan = run_scan(HyperliquidScannerSource())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M")
    (out / f"scan-{stamp}.json").write_text(json.dumps(scan, indent=2, ensure_ascii=False))
    md = render_markdown(scan)
    (out / f"scan-{stamp}.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
