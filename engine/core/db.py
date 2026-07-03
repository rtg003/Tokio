"""Local-first persistence: SQLite (WAL) as the operational source of truth.

Every write that must reach Supabase is ALSO appended to `replication_queue`
(outbox pattern) in the same transaction. The `Replicator` worker drains the
queue in batches, asynchronously — a Supabase outage never blocks the engine
(ADR 0005).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_REPLICATED_TABLES = {
    "exchanges", "strategies", "orders", "fills", "events",
    "strategy_metrics_daily", "traders", "cohort_snapshots",
}

# Primary key columns per replicated table — used for batch deduplication
# (prevents PostgREST 400 when multiple versions of the same row accumulate
# in the replication queue between drain cycles).
_PK_COLUMNS: dict[str, tuple[str, ...]] = {
    "exchanges": ("id",),
    "strategies": ("id",),
    "orders": ("id",),
    "fills": ("id",),
    "events": ("id",),
    "strategy_metrics_daily": ("strategy_id", "day"),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite wrapper with outbox-based replication."""

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

    def _enqueue_replication(
        self, cur: sqlite3.Cursor, table: str, row: dict[str, Any]
    ) -> None:
        if table not in _REPLICATED_TABLES:
            return
        # Coalesce: if there's already a pending queue entry for the same PK,
        # update its payload in-place instead of inserting a duplicate.
        # This prevents PK collisions in the batch that cause PostgREST 400.
        pk_cols = _PK_COLUMNS.get(table, ("id",))
        where = " AND ".join(
            f"json_extract(payload, '$.{c}') = ?" for c in pk_cols
        )
        pk_vals = [row.get(c) for c in pk_cols]
        existing = cur.execute(
            f"SELECT id FROM replication_queue "
            f"WHERE table_name=? AND op='upsert' AND {where} "
            f"ORDER BY id DESC LIMIT 1",
            [table, *pk_vals],
        ).fetchone()
        if existing:
            cur.execute(
                "UPDATE replication_queue SET payload=? WHERE id=?",
                (json.dumps(row, ensure_ascii=False, default=str), existing[0]),
            )
        else:
            cur.execute(
                "INSERT INTO replication_queue (table_name, op, payload) VALUES (?, 'upsert', ?)",
                (table, json.dumps(row, ensure_ascii=False, default=str)),
            )

    def upsert(self, table: str, row: dict[str, Any], conflict_keys: tuple[str, ...]) -> int | None:
        """Insert-or-replace by conflict keys; enqueues the row for replication."""
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
            self._enqueue_replication(cur, table, row)
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
            replicated = dict(row)
            replicated.setdefault("id", rowid)
            self._enqueue_replication(cur, table, replicated)
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
        rows = self.query("SELECT * FROM orders WHERE cloid = ?", (cloid,))
        if rows:
            with self._lock:
                cur = self._conn.cursor()
                self._enqueue_replication(cur, "orders", rows[0])
                self._conn.commit()

    # -- replication queue ---------------------------------------------------
    def queue_batch(self, limit: int) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM replication_queue ORDER BY id LIMIT ?", (limit,)
        )

    def queue_delete(self, ids: list[int]) -> None:
        if not ids:
            return
        marks = ", ".join("?" for _ in ids)
        self.execute(f"DELETE FROM replication_queue WHERE id IN ({marks})", ids)

    def queue_mark_failed(self, ids: list[int], error: str) -> None:
        if not ids:
            return
        marks = ", ".join("?" for _ in ids)
        self.execute(
            f"UPDATE replication_queue SET attempts = attempts + 1, last_error = ? "
            f"WHERE id IN ({marks})",
            [error[:500], *ids],
        )

    def queue_depth(self) -> int:
        return self.query("SELECT COUNT(*) AS n FROM replication_queue")[0]["n"]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class SupabaseSink:
    """Batch upserts to Supabase via PostgREST. Raises on failure (caller retries)."""

    def __init__(self, url: str | None = None, service_key: str | None = None) -> None:
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = service_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    def upsert_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        import httpx

        # Specify on_conflict columns so PostgREST knows which PK to merge on.
        on_conflict = ",".join(_PK_COLUMNS.get(table, ("id",)))

        resp = httpx.post(
            f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}",
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            content=json.dumps(rows, ensure_ascii=False, default=str),
            timeout=15.0,
        )
        resp.raise_for_status()


class Replicator:
    """Dedicated worker draining `replication_queue` to Supabase in batches.

    Failures leave rows in the queue with attempt count + last error; the
    engine keeps running regardless (acceptance test: simulated outage).
    """

    def __init__(
        self,
        db: Database,
        sink: SupabaseSink,
        *,
        batch_size: int = 200,
        interval_seconds: float = 5.0,
        logger: Any | None = None,
    ) -> None:
        self.db = db
        self.sink = sink
        self.batch_size = batch_size
        self.interval = interval_seconds
        self.logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def replicate_once(self) -> tuple[int, int]:
        """Drain one batch. Returns (synced, failed)."""
        batch = self.db.queue_batch(self.batch_size)
        if not batch:
            return (0, 0)
        by_table: dict[str, list[dict[str, Any]]] = {}
        for item in batch:
            by_table.setdefault(item["table_name"], []).append(item)

        synced = failed = 0
        for table, items in by_table.items():
            # Deduplicate by primary key, keeping the LAST (most recent) payload
            # per key. This prevents PostgREST 400 when multiple versions of the
            # same row accumulate in the queue between replication cycles.
            pk_cols = _PK_COLUMNS.get(table, ("id",))
            seen: dict[tuple, dict[str, Any]] = {}
            for i in items:
                payload = json.loads(i["payload"])
                key = tuple(payload.get(c) for c in pk_cols)
                seen[key] = payload  # last one wins (most recent state)
            rows = list(seen.values())
            all_ids = [i["id"] for i in items]  # delete ALL enqueued versions on success

            try:
                self.sink.upsert_rows(table, rows)
                self.db.queue_delete(all_ids)
                synced += len(all_ids)
            except Exception as exc:  # noqa: BLE001 — outage must not crash the engine
                self.db.queue_mark_failed(all_ids, str(exc))
                failed += len(all_ids)
                if self.logger:
                    self.logger.warning(
                        "replication.batch_failed",
                        {"table": table, "count": len(all_ids), "error": str(exc)[:200]},
                    )
        if self.logger and synced:
            self.logger.info(
                "replication.batch_synced",
                {"synced": synced, "queue_depth": self.db.queue_depth()},
            )
        return (synced, failed)

    def run_forever(self) -> None:
        while not self._stop.is_set():
            if self.sink.configured:
                try:
                    self.replicate_once()
                except Exception as exc:  # noqa: BLE001
                    if self.logger:
                        self.logger.error("replication.loop_error", {"error": str(exc)[:200]})
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="replicator")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def replication_lag_seconds(db: Database) -> float:
    """Age of the oldest queued row — health-check metric for the Hermes cron."""
    rows = db.query("SELECT MIN(created_at) AS oldest FROM replication_queue")
    oldest = rows[0]["oldest"]
    if not oldest:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    return max(0.0, time.time() - dt.timestamp())
