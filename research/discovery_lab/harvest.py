"""Coleta ÚNICA do dataset real do laboratório (HL pública + fontes externas).

    .venv/bin/python -m research.discovery_lab.harvest [--dry] [--max-candidates N]

Roda uma vez (~1-2h com throttle de 1.3s/req); re-execuções pulam wallets já
coletadas (resume). Cache HTTP bruto em cache.db (TTL 14d); dataset
normalizado em lab.db (store.py).
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from engine.core.db import Database
from engine.strategies.copy_trade.funnel import parse_leaderboard_row
from engine.strategies.copy_trade.hl_data import HLDataClient, RequestBudgetExceeded

from research.discovery_lab import sources, store

FILLS_WINDOW_DAYS = 90
FILLS_MAX_PAGES = 5
LEDGER_WINDOW_DAYS = 95


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_client(budget: int = 6000) -> HLDataClient:
    cache = Database(store.LAB_DIR / "cache.db")
    cache.migrate()
    return HLDataClient(cache, request_budget=budget, cache_ttl_hours=24 * 14)


def select_universe(client: HLDataClient, conn, *, max_candidates: int,
                    rekt_n: int) -> None:
    """Estágio 0 do laboratório: montar o universo multi-fonte em `wallets`."""
    rows = client.leaderboard()
    print(f"leaderboard HL: {len(rows)} rows")
    cands = [parse_leaderboard_row(r) for r in rows]

    # pré-corte barato (mesmo espírito da produção: equity mínimo + mês positivo)
    pool = [c for c in cands if c.equity >= 2000 and c.windows_pnl.get("30d", 0) > 0]
    print(f"pool pré-corte (equity>=2k, 30d>0): {len(pool)}")

    # mix de prioridades: atividade recente (7d), eficiência (ROI) e magnitude (30d)
    by_7d = sorted(pool, key=lambda c: -c.windows_pnl.get("7d", 0))[: max_candidates // 2]
    by_roi = sorted(pool, key=lambda c: -c.roi_30d_pct)[: max_candidates // 2]
    by_30d = sorted(pool, key=lambda c: -c.windows_pnl.get("30d", 0))[: max_candidates // 4]
    picked: dict[str, object] = {}
    for c in by_7d + by_roi + by_30d:
        picked.setdefault(c.address, c)
    picked_list = list(picked.values())[:max_candidates]
    for c in picked_list:
        store.upsert_wallet(conn, c.address, sources=["hl_leaderboard"],
                            kind="candidate", equity=c.equity,
                            pnl_7d=c.windows_pnl.get("7d"),
                            pnl_30d=c.windows_pnl.get("30d"),
                            roi_30d=c.roi_30d_pct)
    print(f"candidatos HL selecionados: {len(picked_list)}")

    # coorte rekt (controle): perdedores consistentes com equity relevante
    rekt = [c for c in cands
            if c.equity >= 2000 and c.windows_pnl.get("30d", 0) < 0
            and c.windows_pnl.get("7d", 0) < 0]
    rekt.sort(key=lambda c: c.windows_pnl.get("30d", 0))
    for c in rekt[:rekt_n]:
        store.upsert_wallet(conn, c.address, sources=["hl_leaderboard"],
                            kind="rekt", equity=c.equity,
                            pnl_7d=c.windows_pnl.get("7d"),
                            pnl_30d=c.windows_pnl.get("30d"),
                            roi_30d=c.roi_30d_pct)
    print(f"rekt selecionados: {min(len(rekt), rekt_n)}")

    # fontes externas — só endereços; métricas serão da HL
    ht = sources.hypertracker_candidates()
    ht_new = 0
    for item in ht:
        addr = item["address"]
        if addr not in picked:
            ht_new += 1
        store.upsert_wallet(conn, addr, sources=["hypertracker"],
                            kind="candidate",
                            equity=float(item.get("equity") or 0) or None)
    print(f"hypertracker: {len(ht)} endereços ({ht_new} novos) · "
          f"requests HT usados: {sources.ht_requests_used()}")

    cop = sources.copin_candidates()
    for addr in cop:
        store.upsert_wallet(conn, addr, sources=["copin"], kind="candidate")
    print(f"copin: {len(cop)} endereços (esperado 0 sem chave — ver sources.py)")

    segs = sources.hypertracker_segments()
    if segs:
        store.set_meta(conn, "hypertracker_segments", segs)
        print(f"hypertracker segments salvos: {len(segs)}")


def curve_from_portfolio(portfolio: dict) -> list[tuple[float, float, float]]:
    """Merge allTime+month accountValueHistory/pnlHistory → (t, equity, pnl)."""
    points: dict[float, list[float | None]] = {}
    for window in ("allTime", "month"):
        data = dict(portfolio).get(window) or {}
        for t, v in (data.get("accountValueHistory") or []):
            points.setdefault(float(t), [None, None])[0] = float(v)
        for t, v in (data.get("pnlHistory") or []):
            points.setdefault(float(t), [None, None])[1] = float(v)
    out = []
    last_eq = last_pnl = None
    for t in sorted(points):
        eq, pnl = points[t]
        last_eq = eq if eq is not None else last_eq
        last_pnl = pnl if pnl is not None else last_pnl
        if last_eq is not None:
            out.append((t, last_eq, last_pnl if last_pnl is not None else 0.0))
    return out


def harvest_wallet(client: HLDataClient, conn, address: str) -> None:
    fills, truncated = client.fills_by_time(
        address, window_days=FILLS_WINDOW_DAYS, max_pages=FILLS_MAX_PAGES)
    portfolio = client.portfolio(address)
    ch = client.clearinghouse(address)
    ledger_raw = client.ledger_updates(address, window_days=LEDGER_WINDOW_DAYS)
    flows: list[tuple[float, float]] = []
    for u in ledger_raw:
        delta = u.get("delta", {})
        kind = str(delta.get("type", ""))
        amount = float(delta.get("usdc", 0) or 0)
        if kind == "deposit":
            flows.append((float(u.get("time", 0)), amount))
        elif kind in ("withdraw", "withdrawal"):
            flows.append((float(u.get("time", 0)), -abs(amount)))
    store.save_wallet_data(conn, address, fills=fills, truncated=truncated,
                           curve=curve_from_portfolio(portfolio), ledger=flows,
                           clearinghouse=ch, harvested_at=utcnow())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="só seleção, sem deep dive")
    ap.add_argument("--max-candidates", type=int, default=600)
    ap.add_argument("--rekt", type=int, default=60)
    ap.add_argument("--budget", type=int, default=6000)
    args = ap.parse_args(argv)

    conn = store.connect()
    client = make_client(args.budget)

    if store.get_meta(conn, "universe_selected") is None:
        select_universe(client, conn, max_candidates=args.max_candidates,
                        rekt_n=args.rekt)
        store.set_meta(conn, "universe_selected", utcnow())
    else:
        print("universo já selecionado — resume da coleta")

    if args.dry:
        return 0

    todo = [w["address"] for w in conn.execute(
        "SELECT address FROM wallets WHERE harvested_at IS NULL").fetchall()]
    total_all = conn.execute("SELECT COUNT(*) c FROM wallets").fetchone()["c"]
    print(f"a coletar: {len(todo)} de {total_all} wallets "
          f"(requests usados: {client.requests_used}/{client.request_budget})")
    t0 = time.monotonic()
    done = errors = 0
    for i, addr in enumerate(todo):
        try:
            harvest_wallet(client, conn, addr)
            done += 1
        except RequestBudgetExceeded:
            print(f"[{i}] ORÇAMENTO ESGOTADO — {done} coletadas, resume depois")
            break
        except Exception as exc:  # noqa: BLE001
            store.mark_error(conn, addr, str(exc))
            errors += 1
        if (i + 1) % 25 == 0:
            el = time.monotonic() - t0
            print(f"[{i+1}/{len(todo)}] ok={done} err={errors} "
                  f"req={client.requests_used} elapsed={el/60:.1f}min", flush=True)
    store.set_meta(conn, "harvest_finished", utcnow())
    n = conn.execute("SELECT COUNT(*) c FROM wallets WHERE harvested_at IS NOT NULL"
                     " AND error IS NULL").fetchone()["c"]
    nf = conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"]
    print(json.dumps({"wallets_ok": n, "fills_total": nf, "erros": errors,
                      "requests": client.requests_used,
                      "duracao_min": round((time.monotonic() - t0) / 60, 1)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
