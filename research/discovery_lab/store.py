"""lab.db — dataset NORMALIZADO do laboratório (separado do cache HTTP).

Tabelas orientadas ao walk-forward: fills/curvas/ledger por wallet, com
cobertura temporal explícita para cortes point-in-time honestos.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

LAB_DIR = Path(__file__).resolve().parent
LAB_DB = LAB_DIR / "lab.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    sources TEXT NOT NULL,              -- json: ["hl_leaderboard","hypertracker",...]
    kind TEXT NOT NULL,                 -- candidate | rekt
    equity REAL,                        -- do leaderboard/clearinghouse no harvest
    pnl_7d REAL, pnl_30d REAL, roi_30d REAL,
    fills_truncated INTEGER DEFAULT 0,
    fills_from_ms REAL, fills_to_ms REAL,
    n_fills INTEGER DEFAULT 0,
    clearinghouse TEXT,                 -- snapshot json (só vale p/ corte atual)
    error TEXT,
    harvested_at TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    address TEXT NOT NULL,
    t_ms REAL NOT NULL,
    coin TEXT, px REAL, sz REAL, side TEXT,
    closed_pnl REAL, start_position REAL
);
CREATE INDEX IF NOT EXISTS idx_fills_addr_t ON fills(address, t_ms);
CREATE TABLE IF NOT EXISTS curves (
    address TEXT NOT NULL,
    t_ms REAL NOT NULL,
    equity REAL, pnl REAL
);
CREATE INDEX IF NOT EXISTS idx_curves_addr_t ON curves(address, t_ms);
CREATE TABLE IF NOT EXISTS ledger (
    address TEXT NOT NULL,
    t_ms REAL NOT NULL,
    amount REAL NOT NULL                -- + depósito / − saque
);
CREATE INDEX IF NOT EXISTS idx_ledger_addr_t ON ledger(address, t_ms);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or LAB_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 (key, json.dumps(value, default=str)))
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def upsert_wallet(conn: sqlite3.Connection, address: str, *, sources: list[str],
                  kind: str, equity: float | None = None,
                  pnl_7d: float | None = None, pnl_30d: float | None = None,
                  roi_30d: float | None = None) -> None:
    row = conn.execute("SELECT sources FROM wallets WHERE address = ?",
                       (address,)).fetchone()
    if row:
        merged = sorted(set(json.loads(row["sources"])) | set(sources))
        conn.execute("UPDATE wallets SET sources = ? WHERE address = ?",
                     (json.dumps(merged), address))
    else:
        conn.execute(
            "INSERT INTO wallets(address, sources, kind, equity, pnl_7d, pnl_30d, roi_30d)"
            " VALUES (?,?,?,?,?,?,?)",
            (address, json.dumps(sorted(set(sources))), kind, equity,
             pnl_7d, pnl_30d, roi_30d))
    conn.commit()


def save_wallet_data(conn: sqlite3.Connection, address: str, *,
                     fills: list[dict[str, Any]], truncated: bool,
                     curve: list[tuple[float, float, float]],
                     ledger: list[tuple[float, float]],
                     clearinghouse: dict[str, Any],
                     harvested_at: str) -> None:
    conn.execute("DELETE FROM fills WHERE address = ?", (address,))
    conn.executemany(
        "INSERT INTO fills(address, t_ms, coin, px, sz, side, closed_pnl, start_position)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(address, float(f.get("time", 0)), str(f.get("coin")),
          float(f.get("px", 0) or 0), float(f.get("sz", 0) or 0),
          str(f.get("side", "")), float(f.get("closedPnl", 0) or 0),
          float(f.get("startPosition", 0) or 0)) for f in fills])
    conn.execute("DELETE FROM curves WHERE address = ?", (address,))
    conn.executemany(
        "INSERT INTO curves(address, t_ms, equity, pnl) VALUES (?,?,?,?)",
        [(address, t, eq, pnl) for t, eq, pnl in curve])
    conn.execute("DELETE FROM ledger WHERE address = ?", (address,))
    conn.executemany("INSERT INTO ledger(address, t_ms, amount) VALUES (?,?,?)",
                     [(address, t, a) for t, a in ledger])
    times = [float(f.get("time", 0)) for f in fills]
    conn.execute(
        "UPDATE wallets SET fills_truncated = ?, fills_from_ms = ?, fills_to_ms = ?,"
        " n_fills = ?, clearinghouse = ?, harvested_at = ?, error = NULL"
        " WHERE address = ?",
        (1 if truncated else 0, min(times) if times else None,
         max(times) if times else None, len(fills),
         json.dumps(clearinghouse, default=str), harvested_at, address))
    conn.commit()


def mark_error(conn: sqlite3.Connection, address: str, error: str) -> None:
    conn.execute("UPDATE wallets SET error = ? WHERE address = ?",
                 (error[:300], address))
    conn.commit()


# -- leitura p/ o avaliador ----------------------------------------------------
def wallets(conn: sqlite3.Connection, *, kind: str | None = None,
            harvested_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM wallets WHERE 1=1"
    args: list[Any] = []
    if kind:
        q += " AND kind = ?"
        args.append(kind)
    if harvested_only:
        q += " AND harvested_at IS NOT NULL AND error IS NULL"
    return conn.execute(q, args).fetchall()


def wallet_fills(conn: sqlite3.Connection, address: str,
                 t_from: float | None = None,
                 t_to: float | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM fills WHERE address = ?"
    args: list[Any] = [address]
    if t_from is not None:
        q += " AND t_ms >= ?"
        args.append(t_from)
    if t_to is not None:
        q += " AND t_ms <= ?"
        args.append(t_to)
    q += " ORDER BY t_ms"
    return [{"time": r["t_ms"], "coin": r["coin"], "px": r["px"], "sz": r["sz"],
             "side": r["side"], "closedPnl": r["closed_pnl"],
             "startPosition": r["start_position"]}
            for r in conn.execute(q, args).fetchall()]


def wallet_curve(conn: sqlite3.Connection, address: str,
                 t_to: float | None = None) -> list[tuple[float, float, float]]:
    q = "SELECT t_ms, equity, pnl FROM curves WHERE address = ?"
    args: list[Any] = [address]
    if t_to is not None:
        q += " AND t_ms <= ?"
        args.append(t_to)
    q += " ORDER BY t_ms"
    return [(r["t_ms"], r["equity"], r["pnl"])
            for r in conn.execute(q, args).fetchall()]


def wallet_ledger(conn: sqlite3.Connection, address: str, t_from: float,
                  t_to: float) -> list[tuple[float, float]]:
    return [(r["t_ms"], r["amount"]) for r in conn.execute(
        "SELECT t_ms, amount FROM ledger WHERE address = ? AND t_ms BETWEEN ? AND ?"
        " ORDER BY t_ms", (address, t_from, t_to)).fetchall()]
