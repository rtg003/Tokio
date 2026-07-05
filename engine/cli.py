"""Tokio operational CLI.

    python -m engine.cli db migrate
    python -m engine.cli strategy list            # dynamic source of truth (reads DB)
    python -m engine.cli strategy archive <id> [--close-positions] [--yes]
    python -m engine.cli report --daily
    python -m engine.cli report --strategy <id>
    python -m engine.cli trader unpin <addr> [--yes]   # remove copy_pinned (human gate)
    python -m engine.cli kill [--reason txt] / unkill
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from engine.core.config import get_settings
from engine.core.db import Database, utcnow
from engine.core.logger import EventLogger

REPO_ROOT = Path(__file__).resolve().parents[1]


def _db() -> Database:
    settings = get_settings()
    db = Database(settings.sqlite_path)
    db.migrate()
    return db


# --------------------------------------------------------------------------
def _migrate_trader_yamls(db: Database) -> None:
    """Migração única (ADR 0008): YAMLs por trader -> tabela `traders`.
    Após importar, os arquivos são REMOVIDOS — a tabela é a fonte única."""
    import yaml as _yaml

    from engine.strategies.copy_trade.traders_store import import_yaml_trader

    tdir = REPO_ROOT / "engine" / "strategies" / "copy_trade" / "traders"
    if not tdir.exists():
        return
    for f in sorted(tdir.glob("*.yaml")):
        cfg = _yaml.safe_load(f.read_text()) or {}
        address = str(cfg.get("address", "")).lower()
        if address and address != "0x" + "0" * 40:  # placeholder do template: ignora
            import_yaml_trader(db, cfg)
            print(f"trader migrado p/ tabela: {cfg.get('name')} ({address[:10]}…)")
        f.unlink()
        print(f"yaml removido: {f.name}")
    shutil.rmtree(tdir, ignore_errors=True)


def cmd_db_migrate(_: argparse.Namespace) -> int:
    settings = get_settings()
    db = Database(settings.sqlite_path)
    ran = db.migrate()
    _migrate_trader_yamls(db)
    print(f"migrations aplicadas: {ran or 'nenhuma nova'} (db: {settings.sqlite_path})")
    return 0


def cmd_strategy_list(_: argparse.Namespace) -> int:
    db = _db()
    rows = db.query(
        "SELECT id, module, status, created_at, archived_at FROM strategies ORDER BY module, id"
    )
    if not rows:
        print("nenhuma estratégia registrada")
        return 0
    fmt = "{:<18} {:<12} {:<12} {:<26}"
    print(fmt.format("ID", "MODULE", "STATUS", "CREATED_AT"))
    for r in rows:
        print(fmt.format(r["id"], r["module"], r["status"], r["created_at"] or ""))
    return 0


def _archive_folder(strategy_id: str) -> str | None:
    """Move the strategy folder out of the runtime tree (history stays in DB)."""
    candidates = [
        REPO_ROOT / "engine" / "strategies" / "standalone" / strategy_id,
        REPO_ROOT / "engine" / "strategies" / "tradingview" / "strategies" / strategy_id,
    ]
    archive_dir = REPO_ROOT / "engine" / "strategies" / "archive"
    for src in candidates:
        if src.exists():
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest = archive_dir / src.name
            shutil.move(str(src), str(dest))
            return str(dest)
    return None


def cmd_strategy_archive(args: argparse.Namespace) -> int:
    db = _db()
    settings = get_settings()
    logger = EventLogger("cli", settings.logs_dir, db=db)
    sid = args.strategy_id
    rows = db.query("SELECT * FROM strategies WHERE id = ?", (sid,))
    if not rows:
        print(f"estratégia desconhecida: {sid}", file=sys.stderr)
        return 1
    if rows[0]["status"] == "archived":
        print(f"{sid} já está arquivada")
        return 0

    open_orders = db.query(
        "SELECT cloid, symbol FROM orders WHERE strategy_id = ? "
        "AND status IN ('created','sent','acked','partially_filled')", (sid,))

    if not args.yes:
        print(f"arquivar {sid}: {len(open_orders)} ordens abertas serão canceladas; "
              "o processo do runner sairá no próximo ciclo; histórico PERMANECE no banco.")
        answer = input("confirmar? [y/N] ").strip().lower()
        if answer != "y":
            print("abortado")
            return 1

    # 1) cancel open orders via gateway (best effort — gateway may be down)
    cancelled = 0
    if open_orders:
        try:
            from engine.strategies.base_runner import GatewayClient

            gw = GatewayClient()
            for o in open_orders:
                res = gw.cancel(strategy_id=sid, symbol=o["symbol"], cloid=o["cloid"])
                if res.get("ok"):
                    cancelled += 1
        except Exception as exc:  # noqa: BLE001
            print(f"aviso: gateway indisponível para cancelamentos ({exc}); "
                  "cancele manualmente se houver ordens vivas na corretora")

    # 2) close/flag positions (requires explicit confirmation flag)
    if args.close_positions:
        try:
            from engine.strategies.base_runner import GatewayClient

            gw = GatewayClient()
            ledger = gw._client.get("/ledger").json()  # noqa: SLF001 — CLI convenience
            for sym, pos in (ledger.get(sid, {}).get("positions") or {}).items():
                side = "sell" if pos["size"] > 0 else "buy"
                gw.send_intent(strategy_id=sid, symbol=sym, side=side,
                               size=abs(pos["size"]), reduce_only=True, dry_run=False)
                print(f"posição {sym} ({pos['size']}) fechada via gateway")
        except Exception as exc:  # noqa: BLE001
            print(f"aviso: não foi possível fechar posições ({exc}) — flageadas para revisão")

    # 3) mark archived in DB (runner exits on next cycle) — history is never deleted
    db.execute(
        "UPDATE strategies SET status = 'archived', archived_at = ? WHERE id = ?",
        (utcnow(), sid),
    )
    # 4) move folder out of the runtime tree
    moved = _archive_folder(sid)
    logger.info("strategy.archived", {
        "cancelled_orders": cancelled, "folder_moved_to": moved,
        "positions_closed": bool(args.close_positions),
    }, strategy_id=sid)
    print(f"{sid} arquivada (ordens canceladas: {cancelled}; pasta: {moved or 'n/a'}). "
          f"Lembrete: escreva o post-mortem em docs/post_mortems/{sid}.md")
    return 0


def cmd_trader_list(_: argparse.Namespace) -> int:
    db = _db()
    from engine.strategies.copy_trade.traders_store import list_traders

    rows = list_traders(db)
    if not rows:
        print("nenhum trader na tabela — rode o discovery")
        return 0
    fmt = "{:<4} {:<14} {:<44} {:<10} {:<9} {:<12} {}"
    print(fmt.format("#", "NAME", "ADDRESS", "STATUS", "SCORE", "COHORT", "LOGIC"))
    for i, r in enumerate(rows, 1):
        print(fmt.format(i, (r["name"] or "")[:13], r["address"], r["status"],
                         f"{r['score']:.1f}" if r["score"] is not None else "—",
                         r["cohort"] or "—", f"v{r['logic_version']}"))
    return 0


def cmd_trader_approve(args: argparse.Namespace) -> int:
    """GATE 2 (humano): SUGERIDO -> DRY_RUN (ou DRY_RUN -> COPIANDO com --live)."""
    db = _db()
    settings = get_settings()
    logger = EventLogger("cli", settings.logs_dir, db=db)
    from engine.strategies.copy_trade.traders_store import set_status, update_exec_config

    target = "COPIANDO" if args.live else "DRY_RUN"
    if args.live and not args.evidence:
        print("ERRO: --live (dinheiro de verdade no espelhamento) exige --evidence "
              "docs/<arquivo> com a expectância positiva do dry-run.", file=sys.stderr)
        return 1
    if not args.yes:
        print(f"GATE 2 — aprovar trader {args.address} para {target}?")
        if input("confirmar? [y/N] ").strip().lower() != "y":
            print("abortado")
            return 1
    res = set_status(db, args.address, target, by="cli_gate2_humano",
                     logger=logger, human_gate=True)
    if res.get("ok") and args.live:
        update_exec_config(db, args.address, by="cli_gate2_humano",
                           logger=logger, dry_run=0)
        logger.info("trader.gate2_live", {"address": args.address,
                                          "evidence": args.evidence})
    print(res)
    return 0 if res.get("ok") else 1


def cmd_trader_reject(args: argparse.Namespace) -> int:
    db = _db()
    settings = get_settings()
    logger = EventLogger("cli", settings.logs_dir, db=db)
    from engine.strategies.copy_trade.traders_store import set_status

    res = set_status(db, args.address, "REJEITADO", by="cli_humano", logger=logger)
    print(res)
    return 0 if res.get("ok") else 1


def cmd_trader_unpin(args: argparse.Namespace) -> int:
    """Remove a flag copy_pinned (Bloco 3). Exige human_gate + cópia pausada."""
    db = _db()
    settings = get_settings()
    logger = EventLogger("cli", settings.logs_dir, db=db)
    from engine.strategies.copy_trade.traders_store import unpin_trader

    if not args.yes:
        print(f"UNPIN — remover copy_pinned de {args.address}?")
        print("Requer que o trader esteja PAUSADO/REJEITADO/ARQUIVADO (não em cópia).")
        if input("confirmar? [y/N] ").strip().lower() != "y":
            print("abortado")
            return 1
    try:
        res = unpin_trader(db, args.address, by="hermes", human_gate=True,
                           logger=logger)
    except ValueError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    print(res)
    return 0 if res.get("ok") else 1


def cmd_strategy_activate(args: argparse.Namespace) -> int:
    """HUMAN GATE tool: promotes dry_run -> active (the control API refuses this
    on purpose). Requires --evidence pointing to the docs/ file with net
    positive expectancy, and interactive confirmation unless --yes."""
    db = _db()
    settings = get_settings()
    logger = EventLogger("cli", settings.logs_dir, db=db)
    sid = args.strategy_id
    rows = db.query("SELECT status FROM strategies WHERE id = ?", (sid,))
    if not rows:
        print(f"estratégia desconhecida: {sid}", file=sys.stderr)
        return 1
    status = rows[0]["status"]
    if status == "active":
        print(f"{sid} já está ativa")
        return 0
    if status not in ("dry_run", "paused", "auto_paused"):
        print(f"não é possível ativar a partir de '{status}'", file=sys.stderr)
        return 1
    if status == "dry_run" and not args.evidence:
        print("ERRO: promover de dry_run exige --evidence docs/<arquivo> com a "
              "expectância positiva líquida registrada (gate humano).", file=sys.stderr)
        return 1
    if not args.yes:
        print(f"ATIVAR {sid} (status atual: {status}). Ordens REAIS serão enviadas "
              f"à corretora ({get_settings().exchange.network}).")
        if input("confirmar? [y/N] ").strip().lower() != "y":
            print("abortado")
            return 1
    db.execute("UPDATE strategies SET status = 'active' WHERE id = ?", (sid,))
    logger.info("strategy.activated",
                {"by": "cli_human_gate", "from": status, "evidence": args.evidence},
                strategy_id=sid)
    print(f"{sid} ativa (evidência: {args.evidence or 'n/a — reativação de pausa'})")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    db = _db()
    if args.strategy:
        rows = db.query(
            "SELECT * FROM strategy_metrics_daily WHERE strategy_id = ? ORDER BY day DESC LIMIT 30",
            (args.strategy,),
        )
        print(json.dumps({"strategy": args.strategy, "daily": rows}, indent=2, default=str))
        return 0

    # Daily report BY EXCEPTION: aggregate portfolio + only what breached,
    # changed state or sits at top/bottom.
    day = args.day or utcnow()[:10]
    agg = db.query(
        """SELECT COALESCE(SUM(net_pnl),0) AS pnl, COALESCE(SUM(fees),0) AS fees,
                  COALESCE(SUM(n_trades),0) AS trades
           FROM strategy_metrics_daily WHERE day = ?""", (day,))[0]
    active = db.query("SELECT COUNT(*) AS n FROM strategies WHERE status = 'active'")[0]["n"]
    dry = db.query("SELECT COUNT(*) AS n FROM strategies WHERE status = 'dry_run'")[0]["n"]
    paused = db.query(
        "SELECT id, status FROM strategies WHERE status IN ('paused','auto_paused')")
    per_strategy = db.query(
        "SELECT strategy_id, net_pnl, n_trades FROM strategy_metrics_daily "
        "WHERE day = ? ORDER BY net_pnl DESC", (day,))

    lines = [
        f"# Relatório diário — {day}",
        f"PnL líquido total: {agg['pnl']:.2f} USD · taxas: {agg['fees']:.2f} · trades: {agg['trades']}",
        f"Estratégias: {active} ativas · {dry} em dry-run",
    ]
    if paused:
        lines.append("Pausadas/auto-pausadas: " + ", ".join(f"{p['id']} ({p['status']})" for p in paused))
    if per_strategy:
        top, bottom = per_strategy[0], per_strategy[-1]
        lines.append(f"Top: {top['strategy_id']} ({top['net_pnl']:.2f}) · "
                     f"Bottom: {bottom['strategy_id']} ({bottom['net_pnl']:.2f})")
    if not per_strategy and not paused:
        lines.append("Sem exceções: nenhum trade e nenhuma mudança de estado hoje.")
    print("\n".join(lines))
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = _db()
    logger = EventLogger("cli", settings.logs_dir, db=db)
    settings.kill_file.write_text(f"{utcnow()}: {args.reason}\n")
    logger.error("killswitch.engaged", {"reason": args.reason, "via": "cli"})
    print(f"KILL switch acionado ({settings.kill_file}). Runners param no próximo ciclo; "
          "gateway recusa novas ordens.")
    return 0


def cmd_unkill(_: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.kill_file.exists():
        settings.kill_file.unlink()
        print("KILL removido — reinicie os runners para retomar")
    else:
        print("KILL não estava acionado")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="engine.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    db_p = sub.add_parser("db")
    db_sub = db_p.add_subparsers(dest="db_cmd", required=True)
    db_sub.add_parser("migrate").set_defaults(func=cmd_db_migrate)

    st = sub.add_parser("strategy")
    st_sub = st.add_subparsers(dest="st_cmd", required=True)
    st_sub.add_parser("list").set_defaults(func=cmd_strategy_list)
    arch = st_sub.add_parser("archive")
    arch.add_argument("strategy_id")
    arch.add_argument("--close-positions", action="store_true")
    arch.add_argument("--yes", action="store_true")
    arch.set_defaults(func=cmd_strategy_archive)
    act = st_sub.add_parser("activate")
    act.add_argument("strategy_id")
    act.add_argument("--evidence", help="docs/<arquivo> com expectância positiva líquida")
    act.add_argument("--yes", action="store_true")
    act.set_defaults(func=cmd_strategy_activate)

    tr = sub.add_parser("trader")
    tr_sub = tr.add_subparsers(dest="tr_cmd", required=True)
    tr_sub.add_parser("list").set_defaults(func=cmd_trader_list)
    appr = tr_sub.add_parser("approve")
    appr.add_argument("address")
    appr.add_argument("--live", action="store_true",
                      help="DRY_RUN -> COPIANDO (exige --evidence)")
    appr.add_argument("--evidence")
    appr.add_argument("--yes", action="store_true")
    appr.set_defaults(func=cmd_trader_approve)
    rej = tr_sub.add_parser("reject")
    rej.add_argument("address")
    rej.set_defaults(func=cmd_trader_reject)
    unp = tr_sub.add_parser("unpin")
    unp.add_argument("address")
    unp.add_argument("--yes", action="store_true",
                    help="pula a confirmação interativa")
    unp.set_defaults(func=cmd_trader_unpin)

    rep = sub.add_parser("report")
    rep.add_argument("--daily", action="store_true")
    rep.add_argument("--day")
    rep.add_argument("--strategy")
    rep.set_defaults(func=cmd_report)

    kill = sub.add_parser("kill")
    kill.add_argument("--reason", default="manual")
    kill.set_defaults(func=cmd_kill)
    sub.add_parser("unkill").set_defaults(func=cmd_unkill)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
