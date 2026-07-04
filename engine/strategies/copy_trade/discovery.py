"""Discovery de traders — CLI (logic_version 2, spec PROMPT_DISCOVERY_TRADERS_v5).

    python -m engine.strategies.copy_trade.discovery scan [--no-db]
    python -m engine.strategies.copy_trade.discovery inspect <address>
    python -m engine.strategies.copy_trade.discovery positioning
    python -m engine.strategies.copy_trade.discovery token <ativo>
    python -m engine.strategies.copy_trade.discovery report --last

Read-only na corretora (nunca importa signer). A lógica v1 foi aposentada em
2026-07-03 (docs/discovery_changelog.md); thresholds/pesos em
config/discovery_config.yaml.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from engine.core.config import get_settings
from engine.core.db import Database
from engine.core.logger import EventLogger
from engine.strategies.copy_trade import funnel
from engine.strategies.copy_trade.hl_data import HLDataClient


def _db() -> Database:
    db = Database(get_settings().sqlite_path)
    db.migrate()
    return db


def _client(db: Database, cfg: dict[str, Any]) -> HLDataClient:
    col = cfg["collection"]
    return HLDataClient(db, request_budget=int(col["request_budget"]),
                        cache_ttl_hours=float(col["cache_ttl_hours"]))


def reports_dir() -> Path:
    p = get_settings().data_dir / "reports" / "discovery"
    p.mkdir(parents=True, exist_ok=True)
    return p


def emit_logic_updated_if_needed(db: Database, logger: EventLogger,
                                 cfg: dict[str, Any]) -> None:
    """Evento `logic_updated` na primeira varredura de uma logic_version nova."""
    lv = int(cfg["logic_version"])
    rows = db.query("SELECT MAX(logic_version) AS v FROM traders")
    prev = rows[0]["v"] or 0
    if prev and prev < lv:
        logger.info("logic_updated", {
            "from": prev, "to": lv,
            "spec": "docs/specs/PROMPT_DISCOVERY_TRADERS_v5.md",
            "changelog": "docs/discovery_changelog.md",
        })


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = funnel.load_config()
    db = _db()
    logger = EventLogger("discovery", get_settings().logs_dir, db=db)
    client = _client(db, cfg)

    emit_logic_updated_if_needed(db, logger, cfg)
    logger.info("discovery.scan_started",
                {"logic_version": cfg["logic_version"], "reason": args.reason})
    result = funnel.run_scan(client, db, cfg, logger=logger)
    if not args.no_db:
        funnel.persist_scan(db, result, cfg, client=client, logger=logger)

    js, md = funnel.render_report(result, cfg)
    stamp = time.strftime("%Y-%m-%d-%H%M")
    out = reports_dir()
    (out / f"scan-{stamp}-{result.scan_id}.json").write_text(js)
    (out / f"scan-{stamp}-{result.scan_id}.md").write_text(md)

    logger.info("discovery.scan_completed", {
        "scan_id": result.scan_id,
        "logic_version": cfg["logic_version"],
        "approved": len(result.approved),
        "rejected": len(result.rejected),
        "funnel_stats": result.funnel_stats,
        "requests_used": result.requests_used,
        "duration_s": result.duration_s,
        "reason": args.reason,
    })
    print(md)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Dossiê completo de um endereço (fora do leaderboard inclusive).

    Com --persist: roda a MESMA régua (deep_dive → hard_filters → score →
    simulação) e grava via upsert_candidate + set_status SUGERIDO/REJEITADO.
    Sem via lateral: reprovado persiste como REJEITADO.
    """
    cfg = funnel.load_config()
    db = _db()
    client = _client(db, cfg)
    address = args.address.lower()

    c = funnel.Candidate(address=address)
    ch = client.clearinghouse(address)
    c.equity = float(ch.get("marginSummary", {}).get("accountValue", 0) or 0)
    portfolio = client.portfolio(address)
    week = funnel._series(portfolio, "week", "pnlHistory")
    month = funnel._series(portfolio, "month", "pnlHistory")
    c.windows_pnl["7d"] = (week[-1][1] - week[0][1]) if len(week) >= 2 else 0.0
    c.windows_pnl["30d"] = (month[-1][1] - month[0][1]) if len(month) >= 2 else 0.0
    liquid = client.liquid_assets(int(cfg["hard_filters"]["f8_liquid_assets_top_n"]))
    funnel.deep_dive(c, client, cfg, liquid)
    funnel.entry_rule_ok(c, cfg)
    reject = funnel.hard_filters(c, cfg)
    funnel.score_candidate(c, cfg)
    funnel.assign_cohort(c, cfg)

    positions = [p["position"] for p in ch.get("assetPositions", [])]
    dossier = {
        "address": address,
        "equity_usd": c.equity,
        "cohort": c.cohort,
        "score": c.score,
        "veredito_funil": reject or "APROVADO",
        "janelas_pnl": c.windows_pnl,
        "janelas_positivas": c.windows_positive,
        "twrr_30d_pct": c.twrr_30d_pct,
        "profit_factor_incl_nao_realizado": c.pf,
        "max_dd_90d_pct": c.max_dd_90d_pct,
        "win_rate": c.win_rate,
        "win_rate_30d": getattr(c, "win_rate_30d", None),
        "n_trades_fechados": c.n_trades,
        "n_trades_30d": c.n_trades_30d,
        "n_trades_7d": getattr(c, "n_trades_7d", 0),
        "hold_mediano_horas": c.median_hold_hours,
        "estilo": c.style,
        "alavancagem_media": c.avg_leverage,
        "alavancagem_atual_max": getattr(c, "max_current_leverage", None),
        "margem_disponivel_pct": getattr(c, "available_margin_pct", None),
        "distancia_liquidacao_pct": c.liq_distance_pct,
        "alerta_liquidacao": (c.liq_distance_pct is not None and
                              c.liq_distance_pct < cfg["score_adjustments"]
                              ["liq_distance_threshold_pct"]),
        "top_ativos": c.top_assets,
        "ultima_atividade": c.last_activity,
        "historico_truncado": c.history_truncated,
        "posicoes_abertas": [
            {"coin": p.get("coin"), "szi": p.get("szi"),
             "entryPx": p.get("entryPx"), "liquidationPx": p.get("liquidationPx"),
             "unrealizedPnl": p.get("unrealizedPnl")} for p in positions],
        "justificativa": c.rationale,
        "sim_net_pnl_usd": getattr(c, "sim_net_pnl_usd", None),
        "sim_expectancy_usd": getattr(c, "sim_expectancy_usd", None),
        "sim_max_dd_pct": getattr(c, "sim_max_dd_pct", None),
        "sim_factor": getattr(c, "sim_factor", None),
        "coverage_days": getattr(c, "coverage_days", None),
    }

    # --persist: grava na tabela traders
    if getattr(args, "persist", False):
        origin = getattr(args, "origin", "manual")
        c.reject_reason = reject
        # Verificar se é copy_pinned (não rebaixar pinned)
        pinned = db.query(
            "SELECT copy_pinned FROM traders WHERE address = ?", (address,))
        is_pinned = bool(pinned and pinned[0].get("copy_pinned"))
        if not is_pinned:
            funnel.persist_scan(db, funnel.ScanResult(
                scan_id=f"inspect_{int(time.time())}",
                approved=[c] if not reject else [],
                rejected=[c] if reject else [],
                funnel_stats={"inspect_manual": 1},
                rekt_sample=[],
            ), cfg, client=client)
            if reject:
                from engine.strategies.copy_trade.traders_store import set_status
                set_status(db, address, "REJEITADO", by=f"inspect:{origin}")
            print(f"[persist] {address} → {'REJEITADO' if reject else 'SUGERIDO'} "
                  f"(origin={origin})", file=sys.stderr)
        else:
            # Pinned: só atualiza métricas, não rebaixa
            funnel.persist_scan(db, funnel.ScanResult(
                scan_id=f"inspect_pinned_{int(time.time())}",
                approved=[c], rejected=[], funnel_stats={},
                rekt_sample=[]), cfg, client=client)
            print(f"[persist] {address} → pinned (métricas atualizadas, "
                  f"status preservado)", file=sys.stderr)

    print(json.dumps(dossier, indent=2, ensure_ascii=False, default=str))
    return 0


def _latest_snapshots(db: Database) -> tuple[str | None, list[dict[str, Any]]]:
    rows = db.query(
        "SELECT scan_id FROM cohort_snapshots WHERE scan_id IS NOT NULL "
        "ORDER BY id DESC LIMIT 1")
    if not rows:
        return None, []
    scan_id = rows[0]["scan_id"]
    return scan_id, db.query(
        "SELECT * FROM cohort_snapshots WHERE scan_id = ?", (scan_id,))


def cmd_positioning(_: argparse.Namespace) -> int:
    """Posicionamento agregado smart vs. rekt por ativo (insumo de briefing)."""
    db = _db()
    scan_id, snaps = _latest_snapshots(db)
    if not snaps:
        print("sem snapshots — rode `discovery scan` primeiro")
        return 1
    by_asset: dict[str, dict[str, dict[str, Any]]] = {}
    for s in snaps:
        if s["asset"]:
            by_asset.setdefault(s["asset"], {})[s["cohort"]] = s
    print(f"# Posicionamento por coorte — scan {scan_id}\n")
    fmt = "{:<8} {:>12} {:>12} {:>10}  {}"
    print(fmt.format("ATIVO", "SMART bias", "REKT bias", "ALAV(s)", "DIVERGÊNCIA"))
    for asset, cohorts in sorted(by_asset.items()):
        smart = cohorts.get("smart", {})
        rekt = cohorts.get("rekt", {})
        sb, rb = smart.get("net_bias_pct"), rekt.get("net_bias_pct")
        divergent = sb is not None and rb is not None and (sb > 0) != (rb > 0)
        print(fmt.format(
            asset,
            f"{sb:+.0f}%" if sb is not None else "—",
            f"{rb:+.0f}%" if rb is not None else "—",
            f"{smart.get('avg_leverage') or '—'}",
            "⚠ smart e rekt em lados OPOSTOS" if divergent else ""))
    print("\n(divergência é insumo de briefing — NUNCA sinal de execução automática)")
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    """Deep dive invertido: o que a coorte qualificada faz num ativo."""
    db = _db()
    asset = args.asset.upper()
    scan_id, snaps = _latest_snapshots(db)
    snap = [s for s in snaps if s["asset"] == asset]
    print(f"# {asset} — coortes (scan {scan_id})\n")
    if snap:
        for s in snap:
            print(f"- {s['cohort']}: viés {s['net_bias_pct']:+.0f}% · "
                  f"alav média {s['avg_leverage'] or '—'} · {s['n_wallets']} wallets")
    else:
        print("- sem posicionamento registrado no último scan")
    holders = db.query(
        "SELECT address, score, status, top_assets FROM traders "
        "WHERE status NOT IN ('REJEITADO','ARQUIVADO') ORDER BY score DESC")
    with_asset = [h for h in holders
                  if asset in json.loads(h["top_assets"] or "[]")]
    if with_asset:
        print(f"\nqualificados com {asset} no top de volume:")
        for h in with_asset:
            print(f"- {h['address']} · score {h['score']} · {h['status']}")
    return 0


def cmd_report(_: argparse.Namespace) -> int:
    files = sorted(reports_dir().glob("scan-*.md"))
    if not files:
        print("nenhum relatório — rode `discovery scan`")
        return 1
    print(files[-1].read_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="copy_trade.discovery")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan")
    scan.add_argument("--no-db", action="store_true")
    scan.add_argument("--reason", default="cli_manual")
    scan.set_defaults(func=cmd_scan)

    insp = sub.add_parser("inspect")
    insp.add_argument("address")
    insp.add_argument("--persist", action="store_true",
                      help="grava resultado na tabela traders (SUGERIDO/REJEITADO)")
    insp.add_argument("--origin", choices=["manual", "hermes", "copin", "hyperx"],
                      default="manual", help="origem do candidato (default: manual)")
    insp.set_defaults(func=cmd_inspect)

    sub.add_parser("positioning").set_defaults(func=cmd_positioning)

    tok = sub.add_parser("token")
    tok.add_argument("asset")
    tok.set_defaults(func=cmd_token)

    rep = sub.add_parser("report")
    rep.add_argument("--last", action="store_true")
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
