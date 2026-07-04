"""Análise de PODER PREDITIVO: quais features em A preveem o net da cópia em B?

Em vez de girar thresholds no escuro, mede a correlação (Spearman) e a análise
por decis de cada feature de qualificação contra o resultado fora da amostra.

    .venv/bin/python -m research.discovery_lab.analyze
"""
from __future__ import annotations

import json
import statistics
from typing import Any

from research.discovery_lab import store
from research.discovery_lab.evaluate import load_lab_config, sim_copy_in_b
from research.discovery_lab.qualify import build_candidate_pit, liquid_assets_pit

DAY_MS = 86_400_000.0

FEATURES = [
    "score", "copyability", "consistency", "profit_factor_comp", "roi_log",
    "dd_quality", "net_expectancy_comp",
    "sim_stage4_net_A", "sim_expectancy_A", "sim_dd_A",
    "twrr_30d", "max_dd_90d", "windows_pos", "positive_weeks",
    "median_hold_h", "trades_per_day", "n_trades", "equity_log",
    "top3_conc", "liquid_share", "win_rate",
]


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 10:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for rank, i in enumerate(order):
            r[i] = rank
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else None


def collect_rows(conn, cfg: dict[str, Any], t_end: float) -> list[dict[str, Any]]:
    import math

    t_qual = t_end - 30 * DAY_MS
    liquid = liquid_assets_pit(conn, t_qual - 60 * DAY_MS, t_qual,
                               top_n=int(cfg["hard_filters"]["f8_liquid_assets_top_n"]))
    from engine.strategies.copy_trade.funnel import score_candidate

    rows = []
    for w in store.wallets(conn, kind="candidate"):
        c = build_candidate_pit(conn, w["address"], t_qual, cfg, liquid)
        if c is None or c.equity < 2000 or c.n_trades < 10:
            continue
        score_candidate(c, cfg)
        sim_b = sim_copy_in_b(conn, w["address"], c.equity, t_qual, t_end, cfg)
        if sim_b is None:
            continue
        comp = c.components
        rows.append({
            "address": w["address"],
            "net_B": sim_b.net_pnl_usd,
            "score": c.score,
            "copyability": comp.copyability if comp else None,
            "consistency": comp.consistency if comp else None,
            "profit_factor_comp": comp.profit_factor if comp else None,
            "roi_log": comp.roi_log if comp else None,
            "dd_quality": comp.drawdown_quality if comp else None,
            "net_expectancy_comp": comp.net_expectancy if comp else None,
            "sim_stage4_net_A": c.sim_stage4_net_usd,
            "sim_expectancy_A": c.sim_expectancy_usd,
            "sim_dd_A": c.sim_max_dd_pct,
            "twrr_30d": c.twrr_30d_pct,
            "max_dd_90d": c.max_dd_90d_pct,
            "windows_pos": int(c.windows_positive.split("/")[0]),
            "positive_weeks": getattr(c, "positive_weeks", None),
            "median_hold_h": c.median_hold_hours,
            "trades_per_day": c.trades_per_day,
            "n_trades": c.n_trades,
            "equity_log": math.log10(max(c.equity, 1)),
            "top3_conc": c.top3_concentration,
            "liquid_share": c.liquid_volume_share,
            "win_rate": c.win_rate,
        })
    return rows


def decile_table(rows: list[dict[str, Any]], feature: str) -> list[tuple[str, int, float]]:
    pts = [(r[feature], r["net_B"]) for r in rows if r[feature] is not None]
    if len(pts) < 30:
        return []
    pts.sort(key=lambda x: x[0])
    out = []
    n = len(pts)
    for q in range(5):   # quintis
        chunk = pts[q * n // 5: (q + 1) * n // 5]
        nets = [y for _, y in chunk]
        lo, hi = chunk[0][0], chunk[-1][0]
        out.append((f"[{lo:.2f},{hi:.2f}]", len(chunk),
                    round(statistics.median(nets), 2)))
    return out


def main() -> int:
    conn = store.connect()
    cfg = load_lab_config(None)
    t0 = conn.execute("SELECT MAX(fills_to_ms) m FROM wallets").fetchone()["m"]
    cuts = [t0 - i * 7 * DAY_MS for i in range(3)]

    all_rows: list[dict[str, Any]] = []
    per_cut_corr: dict[str, list[float | None]] = {f: [] for f in FEATURES}
    for t_end in cuts:
        rows = collect_rows(conn, cfg, t_end)
        all_rows.extend(rows)
        for f in FEATURES:
            pts = [(r[f], r["net_B"]) for r in rows if r[f] is not None]
            per_cut_corr[f].append(
                spearman([x for x, _ in pts], [y for _, y in pts]))
        print(f"corte t_end={t_end:.0f}: {len(rows)} wallets analisáveis")

    print("\n== Spearman(feature em A, net da cópia em B) por corte ==")
    ranked = []
    for f, cs in per_cut_corr.items():
        vals = [c for c in cs if c is not None]
        avg = statistics.mean(vals) if vals else None
        ranked.append((f, cs, avg))
    ranked.sort(key=lambda x: -(abs(x[2]) if x[2] is not None else 0))
    for f, cs, avg in ranked:
        cs_s = ", ".join("—" if c is None else f"{c:+.3f}" for c in cs)
        print(f"{f:<22} média={avg:+.3f}  [{cs_s}]" if avg is not None
              else f"{f:<22} n/d")

    print("\n== Quintis (pooled, 3 cortes) — mediana do net_B por faixa ==")
    for f in ("sim_expectancy_A", "sim_stage4_net_A", "copyability", "score",
              "trades_per_day", "median_hold_h", "windows_pos", "twrr_30d"):
        table = decile_table(all_rows, f)
        if table:
            cells = " | ".join(f"{rng} n={n} med={med}" for rng, n, med in table)
            print(f"{f}: {cells}")

    (store.LAB_DIR / "runs" / "feature_analysis.json").write_text(
        json.dumps({"rows": all_rows}, indent=1, default=str))
    print(f"\n{len(all_rows)} observações salvas em runs/feature_analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
