"""Seleção ATUAL: aplica a config vencedora no instante mais recente do dataset
e imprime o dossiê dos aprovados (o que iria para a tabela `traders` hoje).

    .venv/bin/python -m research.discovery_lab.select_now --config <yaml>
"""
from __future__ import annotations

import argparse
import json

from research.discovery_lab import sources, store
from research.discovery_lab.evaluate import load_lab_config
from research.discovery_lab.qualify import liquid_assets_pit, qualify

DAY_MS = 86_400_000.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="research/discovery_lab/configs/h15_dd_gate.yaml")
    ap.add_argument("--ht-check", action="store_true",
                    help="cruza coorte nossa vs HyperTracker (gasta ~1 req/wallet)")
    args = ap.parse_args(argv)

    conn = store.connect()
    cfg = load_lab_config(args.config)
    t0 = conn.execute("SELECT MAX(fills_to_ms) m FROM wallets").fetchone()["m"]
    liquid = liquid_assets_pit(conn, t0 - 60 * DAY_MS, t0,
                               top_n=int(cfg["hard_filters"]["f8_liquid_assets_top_n"]))

    approved = []
    deaths: dict[str, int] = {}
    for w in store.wallets(conn, kind="candidate"):
        c, reason = qualify(conn, w["address"], t0, cfg, liquid)
        if reason is not None:
            deaths[reason.split(":")[0]] = deaths.get(reason.split(":")[0], 0) + 1
            continue
        approved.append((c, w))

    rank_by = (cfg.get("lab") or {}).get("rank_by", "score_factor")
    approved.sort(key=lambda x: -(x[0].sim_stage4_net_usd or 0)
                  if rank_by == "stage4_net" else -x[0].score)

    print(f"# Seleção atual — {len(approved)} aprovados "
          f"(config {args.config})\n")
    print("mortes:", json.dumps(dict(sorted(deaths.items(), key=lambda x: -x[1])),
                                ensure_ascii=False), "\n")
    from engine.strategies.copy_trade.funnel import assign_cohort
    for i, (c, w) in enumerate(approved, 1):
        assign_cohort(c, cfg)
        ht_seg = ""
        if args.ht_check:
            prof = sources.hypertracker_wallet(c.address)
            if prof:
                segs = prof.get("segments") or prof.get("cohorts") or []
                ht_seg = f" · HT: {segs}"
        print(f"{i}. {c.address}")
        print(f"   equity ${c.equity:,.0f} · coorte {c.cohort}{ht_seg}")
        print(f"   score {c.score} · sim A: net ${c.sim_stage4_net_usd:+,.2f} "
              f"(exp ${c.sim_expectancy_usd:+.2f}/trade, DD {c.sim_max_dd_pct:.1f}%)")
        print(f"   metades A: antiga ${getattr(c, 'sim_half_old_net', 0) or 0:+.2f} "
              f"/ recente ${getattr(c, 'sim_half_new_net', 0) or 0:+.2f}")
        print(f"   janelas {c.windows_positive} · TWRR {c.twrr_30d_pct:.1f}% · "
              f"DD90 {c.max_dd_90d_pct:.1f}% · hold {c.median_hold_hours or 0:.1f}h · "
              f"{c.trades_per_day:.1f} tr/dia · n={c.n_trades} · "
              f"ativos {c.top_assets} · fontes {json.loads(w['sources'])}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
