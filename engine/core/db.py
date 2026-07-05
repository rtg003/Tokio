"""Local-first persistence: SQLite (WAL) as the operational source of truth."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite wrapper."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    # -- migrations ----------------------------------------------------------
    def migrate(self, migrations_dir: Path | None = None) -> list[str]:
        mdir = migrations_dir or MIGRATIONS_DIR
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
        )
        applied = {
            r["version"]
            for r in self._conn.execute("SELECT version FROM schema_migrations")
        }
        ran: list[str] = []
        for sql_file in sorted(mdir.glob("*.sql")):
            version = sql_file.stem
            if version in applied:
                continue
            with self._lock:
                self._conn.executescript(sql_file.read_text())
                self._conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (version,)
                )
                self._conn.commit()
            ran.append(version)
        return ran

    # -- generic helpers -----------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def upsert(self, table: str, row: dict[str, Any], conflict_keys: tuple[str, ...]) -> int | None:
        """Insert-or-replace by conflict keys."""
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in conflict_keys)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(conflict_keys)}) DO UPDATE SET {updates}"
            if updates
            else f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        )
        with self._lock:
            cur = self._conn.execute(sql, tuple(row.values()))
            rowid = cur.lastrowid
            self._conn.commit()
        return rowid

    def insert(self, table: str, row: dict[str, Any]) -> int:
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                tuple(row.values()),
            )
            rowid = int(cur.lastrowid or 0)
            self._conn.commit()
        return rowid

    # -- domain shortcuts ----------------------------------------------------
    def insert_event(
        self,
        *,
        ts: str,
        strategy_id: str | None,
        event_type: str,
        level: str,
        payload: dict[str, Any],
    ) -> int:
        return self.insert(
            "events",
            {
                "ts": ts,
                "strategy_id": strategy_id,
                "event_type": event_type,
                "level": level,
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
            },
        )

    def update_order_status(self, cloid: str, status: str, **extra: Any) -> None:
        sets = ["status = ?"]
        params: list[Any] = [status]
        for col, val in extra.items():
            sets.append(f"{col} = ?")
            params.append(val)
        params.append(cloid)
        self.execute(f"UPDATE orders SET {', '.join(sets)} WHERE cloid = ?", params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
