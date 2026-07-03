"""Funil v2 ponta a ponta com dados sintéticos — incl. o teste OBRIGATÓRIO de
separação de coortes smart vs. rekt (aceite da spec v5)."""
from __future__ import annotations

import json
import statistics
import time
from typing import Any

import pytest

from engine.strategies.copy_trade.funnel import (
    entry_rule_ok,
    load_config,
    parse_leaderboard_row,
    persist_scan,
    render_report,
    run_scan,
    score_candidate,
    Candidate,
)

NOW_MS = time.time() * 1000
DAY_MS = 86_400_000.0
H_MS = 3_600_000.0

CFG = load_config()


# ----------------------------------------------------------------------------
# fixtures sintéticas
# ----------------------------------------------------------------------------
def lb_row(address: str, *, pnl_7d: float, pnl_30d: float, roi_30d: float,
           equity: float) -> dict[str, Any]:
    return {"ethAddress": address, "accountValue": equity, "displayName": None,
            "windowPerformances": [
                ["week", {"pnl": pnl_7d, "roi": 0.01}],
                ["month", {"pnl": pnl_30d, "roi": roi_30d}],
                ["allTime", {"pnl": pnl_30d * 4, "roi": roi_30d * 2}],
            ]}


def swing_fills(n: int = 40, *, hold_h: float = 24.0, pnl_each: float = 120.0,
                start_ms: float | None = None) -> list[dict[str, Any]]:
    """n trades fechados, hold configurável, PnL distribuído — trader saudável."""
    start_ms = start_ms or (NOW_MS - 55 * DAY_MS)
    fills = []
    t = start_ms
    for i in range(n):
        fills.append({"coin": "BTC", "time": t, "side": "B", "sz": 0.5,
                      "startPosition": 0.0, "px": 100_000, "closedPnl": 0})
        fills.append({"coin": "BTC", "time": t + hold_h * H_MS, "side": "A",
                      "sz": 0.5, "startPosition": 0.5, "px": 100_500,
                      "closedPnl": pnl_each + (i % 5)})
        t += 30 * H_MS
    return fills


def growing_curve(days: int = 90, *, start: float = 50_000.0,
                  daily_gain: float = 0.004) -> list[list[float]]:
    out = []
    v = start
    for d in range(days):
        out.append([NOW_MS - (days - d) * DAY_MS, v])
        v *= 1 + daily_gain + (0.001 if d % 7 else -0.002)
    out.append([NOW_MS, v])
    return out


def pnl_hist_from_curve(curve: list[list[float]]) -> list[list[float]]:
    base = curve[0][1]
    return [[t, v - base] for t, v in curve]


class FakeClient:
    """DataClient sintético configurável por endereço."""

    def __init__(self, rows: list[dict[str, Any]],
                 profiles: dict[str, dict[str, Any]]) -> None:
        self.rows = rows
        self.profiles = profiles
        self.requests_used = 0

    def leaderboard(self):
        self.requests_used += 1
        return self.rows

    def _p(self, address: str) -> dict[str, Any]:
        return self.profiles.get(address.lower(), {})

    def fills_by_time(self, address, *, window_days=60, max_pages=4):
        self.requests_used += 1
        return self._p(address).get("fills", []), False

    def portfolio(self, address):
        self.requests_used += 1
        curve = self._p(address).get("curve", growing_curve())
        return {"month": {"accountValueHistory": [c for c in curve if c[0] >= NOW_MS - 30 * DAY_MS],
                          "pnlHistory": [p for p in pnl_hist_from_curve(curve)
                                         if p[0] >= NOW_MS - 30 * DAY_MS]},
                "allTime": {"accountValueHistory": curve,
                            "pnlHistory": pnl_hist_from_curve(curve)}}

    def clearinghouse(self, address):
        self.requests_used += 1
        return self._p(address).get("clearinghouse",
                                    {"marginSummary": {"accountValue": 50_000},
                                     "assetPositions": []})

    def ledger_updates(self, address, *, window_days=35):
        self.requests_used += 1
        return self._p(address).get("ledger", [])

    def liquid_assets(self, top_n=25):
        self.requests_used += 1
        return {"BTC", "ETH", "SOL"}


GOOD = "0x" + "aa" * 20     # swing saudável — deve APROVAR
SCALP = "0x" + "bb" * 20    # scalper lucrativo — deve REPROVAR (F3)
DEPOSIT = "0x" + "cc" * 20  # inflado por aporte — deve REPROVAR (F4/F10)
REKT1 = "0x" + "dd" * 20
REKT2 = "0x" + "ee" * 20


def make_client() -> FakeClient:
    rows = [
        lb_row(GOOD, pnl_7d=800, pnl_30d=9_000, roi_30d=0.18, equity=50_000),
        lb_row(SCALP, pnl_7d=2_000, pnl_30d=25_000, roi_30d=0.40, equity=80_000),
        lb_row(DEPOSIT, pnl_7d=100, pnl_30d=5_000, roi_30d=0.08, equity=200_000),
        lb_row(REKT1, pnl_7d=-500, pnl_30d=-8_000, roi_30d=-0.20, equity=30_000),
        lb_row(REKT2, pnl_7d=-100, pnl_30d=-2_000, roi_30d=-0.05, equity=5_000),
    ]
    flat_curve = [[NOW_MS - (90 - d) * DAY_MS, 200_000.0 + d * 550] for d in range(91)]
    scalper_fills = []
    t = NOW_MS - 20 * DAY_MS
    for i in range(900):
        scalper_fills.append({"coin": "BTC", "time": t, "side": "B", "sz": 1,
                              "startPosition": 0.0, "px": 100_000, "closedPnl": 0})
        scalper_fills.append({"coin": "BTC", "time": t + 0.2 * H_MS, "side": "A",
                              "sz": 1, "startPosition": 1.0, "px": 100_030,
                              "closedPnl": 30.0})
        t += 0.5 * H_MS
    profiles = {
        GOOD: {"fills": swing_fills()},
        SCALP: {"fills": scalper_fills},
        DEPOSIT: {"fills": swing_fills(n=35, pnl_each=20.0),
                  "curve": flat_curve,
                  "ledger": [{"time": NOW_MS - 15 * DAY_MS,
                              "delta": {"type": "deposit", "usdc": 40_000}}]},
    }
    return FakeClient(rows, profiles)


# ----------------------------------------------------------------------------
def test_scan_approves_swing_rejects_traps(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)

    approved = {c.address for c in result.approved}
    rejected = {c.address: c.reject_reason for c in result.rejected}

    assert GOOD in approved
    assert SCALP in rejected and rejected[SCALP].startswith("F3")   # scalper lucrativo
    assert DEPOSIT in rejected                                       # aporte/TWRR
    assert result.funnel_stats["aprovados"] == len(result.approved)
    assert result.funnel_stats["coletados"] == 5
    # rekt não entra no funil de aprovação, vira coorte de controle
    assert {c.address for c in result.rekt_sample} == {REKT1, REKT2}


def test_approved_scores_use_full_scale(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    good = next(c for c in result.approved if c.address == GOOD)
    assert good.score >= 55, f"score punitivo demais: {good.score}"
    assert good.windows_positive == "4/4"
    assert good.cohort.startswith("Dolphin")


def test_smart_vs_rekt_separation(db) -> None:
    """Aceite da spec: o score separa CLARAMENTE smart de rekt."""
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    smart_scores = [c.score for c in result.approved]
    # pontua os rekt pela MESMA régua (sem filtros; pior cenário p/ o teste)
    rekt_scores = []
    for c in result.rekt_sample:
        score_candidate(c, CFG)
        rekt_scores.append(c.score)
    assert smart_scores and rekt_scores
    sep = statistics.mean(smart_scores) - statistics.mean(rekt_scores)
    assert sep >= 20, f"separação insuficiente: {sep:.1f} pontos"


def test_persist_scan_populates_traders_and_snapshots(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result, CFG, client=client)

    rows = {r["address"]: r for r in db.query("SELECT * FROM traders")}
    good = rows[GOOD]
    assert good["status"] == "SUGERIDO"
    assert good["logic_version"] == 2
    assert good["windows_positive"] == "4/4"
    assert good["reject_reason"] is None
    assert good["n_trades_30d"] > 0
    assert json.loads(good["top_assets"]) == ["BTC"]

    scalp = rows[SCALP]
    assert scalp["status"] == "REJEITADO"
    assert scalp["reject_reason"].startswith("F3")

    snaps = db.query("SELECT DISTINCT cohort FROM cohort_snapshots")
    assert {s["cohort"] for s in snaps} <= {"smart", "rekt"}


def test_rescan_reinstates_rejected_that_now_passes(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result, CFG)
    # scalper reprovado vira swing no scan seguinte (mudou de comportamento)
    client2 = make_client()
    client2.profiles[SCALP] = {"fills": swing_fills()}
    result2 = run_scan(client2, db, CFG, now_ms=NOW_MS)
    persist_scan(db, result2, CFG)
    row = db.query("SELECT status, reject_reason FROM traders WHERE address = ?",
                   (SCALP,))[0]
    assert row["status"] == "SUGERIDO"
    assert row["reject_reason"] is None


def test_report_contains_funnel_stats_and_ranking(db) -> None:
    client = make_client()
    result = run_scan(client, db, CFG, now_ms=NOW_MS)
    js, md = render_report(result, CFG)
    payload = json.loads(js)
    assert payload["logic_version"] == 2
    assert payload["funnel_stats"]["coletados"] == 5
    assert "F3" in json.dumps(payload["rejected_reasons"])
    assert "| 1 |" in md and "Funil:" in md


def test_entry_rule_windows() -> None:
    c = Candidate(address="0x1", windows_pnl={"7d": -10, "30d": 100, "60d": 50, "90d": 80})
    assert entry_rule_ok(c, CFG) is True and c.windows_positive == "3/4"
    c2 = Candidate(address="0x2", windows_pnl={"7d": 10, "30d": 100, "60d": -5, "90d": 80})
    assert entry_rule_ok(c2, CFG) is False        # 60d obrigatória negativa
