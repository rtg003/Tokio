"""Avaliador walk-forward do laboratório.

Para cada corte T: qualifica cada wallet em t_qual = T−30d (dados ANTERIORES
a t_qual apenas) e mede a CÓPIA na janela B = (t_qual, T] com nosso sizing,
taxas e latência. Métricas são da SELEÇÃO (a carteira aprovada), comparadas
com baselines aleatório e rekt.

    .venv/bin/python -m research.discovery_lab.evaluate --config <yaml> [--label x]
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path
from typing import Any

import yaml

from engine.strategies.copy_trade import metrics as M
from engine.strategies.copy_trade.funnel import load_config

from research.discovery_lab import store
from research.discovery_lab.qualify import liquid_assets_pit, qualify

DAY_MS = 86_400_000.0
B_WINDOW_DAYS = 30.0


def load_lab_config(path: str | None) -> dict[str, Any]:
    """Config candidata: base da produção + overrides do YAML do lab (merge raso
    por seção — cada seção do override substitui/mescla chaves da base)."""
    cfg = load_config()
    if path:
        override = yaml.safe_load(Path(path).read_text()) or {}
        for section, values in override.items():
            if isinstance(values, dict) and isinstance(cfg.get(section), dict):
                cfg[section] = {**cfg[section], **values}
            else:
                cfg[section] = values
    return cfg


def sim_copy_in_b(conn, address: str, equity_at_qual: float, t_qual: float,
                  t_end: float, cfg: dict[str, Any]) -> M.CopySimulation | None:
    fills_b = store.wallet_fills(conn, address, t_from=t_qual + 1, t_to=t_end)
    if not fills_b or equity_at_qual <= 0:
        return None
    cost = cfg["cost_of_copy"]
    stage4 = cfg.get("copy_simulation") or {}
    return M.simulate_copy(
        fills_b, equity_at_qual, float(cfg["hard_filters"]["f11_mirror_capital_usd"]),
        taker_fee_pct=float(cost["taker_fee_pct"]),
        slippage_pct=float(cost["slippage_pct"]),
        latency_slippage_pct=float(stage4.get("latency_slippage_pct", 0)),
        max_copy_leverage=stage4.get("max_copy_leverage"),   # v9: só cópia executável
        window_days=B_WINDOW_DAYS, now_ms=t_end)


def selection_metrics(nets: list[float], dds: list[float]) -> dict[str, Any]:
    if not nets:
        return {"n": 0, "mediana_net": None, "soma_net": None, "hit_rate": None,
                "dd_medio": None}
    return {
        "n": len(nets),
        "mediana_net": round(statistics.median(nets), 2),
        "soma_net": round(sum(nets), 2),
        "hit_rate": round(sum(1 for x in nets if x > 0) / len(nets), 3),
        "dd_medio": round(statistics.mean(dds), 2) if dds else None,
    }


def _in_split(address: str, split: str) -> bool:
    """Holdout transversal: paridade do endereço (even=calibração, odd=validação)."""
    if split == "all":
        return True
    parity = int(address, 16) % 2
    return parity == (0 if split == "even" else 1)


def evaluate_cut(conn, t_end: float, cfg: dict[str, Any], *, top_k: int,
                 rng: random.Random, split: str = "all") -> dict[str, Any]:
    t_qual = t_end - B_WINDOW_DAYS * DAY_MS
    liquid = liquid_assets_pit(
        conn, t_qual - 60 * DAY_MS, t_qual,
        top_n=int(cfg["hard_filters"]["f8_liquid_assets_top_n"]))

    approved: list[tuple[Any, float]] = []   # (candidate, rank_key)
    deaths: dict[str, int] = {}
    pool_with_data: list[tuple[str, float]] = []   # (address, equity_at_qual)

    for w in store.wallets(conn, kind="candidate"):
        if not _in_split(w["address"], split):
            continue
        c, reason = qualify(conn, w["address"], t_qual, cfg, liquid)
        if c is not None and c.equity > 0:
            pool_with_data.append((w["address"], c.equity))
        if reason is not None:
            key = reason.split(":")[0]
            deaths[key] = deaths.get(key, 0) + 1
            continue
        # v9: ranking padrão = net da cópia simulada (espelha run_scan)
        rank_by = (cfg.get("lab") or {}).get("rank_by", "stage4_net")
        if rank_by == "expectancy":
            rank = c.sim_expectancy_usd if c.sim_expectancy_usd is not None else -1e9
        elif rank_by == "score_factor":
            rank = c.score * (c.sim_factor or 1.0)
        else:
            rank = c.sim_stage4_net_usd if c.sim_stage4_net_usd is not None else -1e9
        approved.append((c, rank))

    approved.sort(key=lambda x: -x[1])
    picked = approved[:top_k]

    def run_group(items: list[tuple[str, float]]) -> dict[str, M.CopySimulation]:
        out: dict[str, M.CopySimulation] = {}
        for addr, eq in items:
            sim = sim_copy_in_b(conn, addr, eq, t_qual, t_end, cfg)
            if sim is not None:
                out[addr] = sim
        return out

    sel = run_group([(c.address, c.equity) for c, _ in picked])
    sel_nets = [s.net_pnl_usd for s in sel.values()]
    sel_dds = [s.max_dd_pct for s in sel.values()]

    # baseline aleatório: mesma cardinalidade, 20 seeds
    rand_medians = []
    if pool_with_data and picked:
        for _ in range(20):
            sample = rng.sample(pool_with_data, min(len(picked), len(pool_with_data)))
            sims = run_group(sample)
            if sims:
                rand_medians.append(statistics.median(
                    s.net_pnl_usd for s in sims.values()))
    # baseline rekt
    rekt_items = []
    for w in store.wallets(conn, kind="rekt"):
        curve = store.wallet_curve(conn, w["address"], t_to=t_qual)
        eq = curve[-1][1] if curve else 0
        if eq and eq >= 2000:   # piso: equity ínfimo explode o ratio da cópia
            rekt_items.append((w["address"], eq))
    rekt_sims = run_group(rekt_items)
    rekt_nets = [s.net_pnl_usd for s in rekt_sims.values()]
    rekt_dds = [s.max_dd_pct for s in rekt_sims.values()]

    per_source: dict[str, list[float]] = {}
    for c, _ in picked:
        if c.address not in sel:
            continue
        row = conn.execute("SELECT sources FROM wallets WHERE address = ?",
                           (c.address,)).fetchone()
        for src in json.loads(row["sources"]):
            per_source.setdefault(src, []).append(sel[c.address].net_pnl_usd)

    return {
        "t_end": t_end,
        "aprovados_total": len(approved),
        "sem_dados_B": len(picked) - len(sel),   # aprovados sem fills na janela B
        "carteira": selection_metrics(sel_nets, sel_dds),
        "baseline_aleatorio_mediana_das_medianas":
            round(statistics.median(rand_medians), 2) if rand_medians else None,
        "baseline_rekt": selection_metrics(rekt_nets, rekt_dds),
        "mortes_por_filtro": dict(sorted(deaths.items(), key=lambda x: -x[1])),
        "por_fonte": {k: {"n": len(v), "mediana": round(statistics.median(v), 2)}
                      for k, v in per_source.items()},
        "top": [{"address": c.address, "rank": round(r, 2), "score": c.score,
                 "sim_factor": c.sim_factor,
                 "net_B": sel[c.address].net_pnl_usd if c.address in sel else None,
                 "dd_B": sel[c.address].max_dd_pct if c.address in sel else None}
                for c, r in picked],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="yaml de overrides do lab")
    ap.add_argument("--label", default="v8_base")
    ap.add_argument("--cuts", type=int, default=3)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--split", choices=["all", "even", "odd"], default="all")
    args = ap.parse_args(argv)

    conn = store.connect()
    cfg = load_lab_config(args.config)
    rng = random.Random(args.seed)

    t0 = conn.execute("SELECT MAX(fills_to_ms) m FROM wallets").fetchone()["m"]
    if not t0:
        print("lab.db vazio — rode o harvest primeiro")
        return 1
    cuts = [t0 - i * 7 * DAY_MS for i in range(args.cuts)]

    results = [evaluate_cut(conn, t, cfg, top_k=args.top_k, rng=rng,
                            split=args.split)
               for t in cuts]
    medians = [r["carteira"]["mediana_net"] for r in results]
    summary = {
        "label": args.label,
        "config_override": args.config,
        "top_k": args.top_k,
        "cortes": results,
        "resumo": {
            "medianas_por_corte": medians,
            "todas_positivas": all(m is not None and m > 0 for m in medians),
            "hit_rates": [r["carteira"]["hit_rate"] for r in results],
            "aprovados": [r["aprovados_total"] for r in results],
        },
    }
    out = store.LAB_DIR / "runs"
    out.mkdir(exist_ok=True)
    (out / f"{args.label}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(summary["resumo"], indent=2, ensure_ascii=False))
    for i, r in enumerate(results):
        print(f"\n== corte {i} ==")
        print("carteira:", json.dumps(r["carteira"], ensure_ascii=False))
        print("aleatorio(mediana):", r["baseline_aleatorio_mediana_das_medianas"],
              "· rekt:", json.dumps(r["baseline_rekt"], ensure_ascii=False))
        print("mortes:", json.dumps(r["mortes_por_filtro"], ensure_ascii=False))
        print("por_fonte:", json.dumps(r["por_fonte"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
