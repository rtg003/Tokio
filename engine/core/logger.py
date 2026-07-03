"""Structured JSONL logging + operational events queued for the `events` table.

One event per line: UTC ISO-8601 timestamp, strategy_id, event_type, level,
full payload, latency_ms when applicable. Daily rotation by filename.

FORBIDDEN at every level: private keys, secrets, tokens. `_redact` is a last
line of defense — callers must never pass secrets in the first place.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REDACT_KEY_MARKERS = ("key", "secret", "token", "password", "private")

_DB_LEVELS = {"info", "warning", "error", "critical"}
# High-volume debug stays in local JSONL only; operational events go to the DB.
_DB_EVENT_PREFIXES = (
    "order.", "fill.", "signal.", "decision.", "strategy.", "risk.",
    "killswitch", "circuit_breaker", "config.", "health.", "replication.",
    "ws.", "drift.", "trader.", "discovery.", "logic_",
)


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if any(m in k.lower() for m in _REDACT_KEY_MARKERS) else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class EventLogger:
    """JSONL writer + optional DB sink for operational events."""

    def __init__(self, name: str, logs_dir: Path, db: Any | None = None) -> None:
        self.name = name
        self.logs_dir = logs_dir
        self.db = db  # engine.core.db.Database | None
        self._lock = threading.Lock()

    def _file_for_today(self) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.logs_dir / f"{self.name}-{day}.jsonl"

    def log(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        level: str = "info",
        strategy_id: str | None = None,
        latency_ms: float | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": self.name,
            "strategy_id": strategy_id,
            "event_type": event_type,
            "level": level,
            "payload": _redact(payload or {}),
        }
        if latency_ms is not None:
            record["latency_ms"] = latency_ms

        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            with self._file_for_today().open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

        if self.db is not None and self._should_persist(event_type, level):
            try:
                self.db.insert_event(
                    ts=record["timestamp"],
                    strategy_id=strategy_id,
                    event_type=event_type,
                    level=level,
                    payload=record["payload"],
                )
            except Exception:
                # The logger must never take down the hot path.
                pass
        return record

    @staticmethod
    def _should_persist(event_type: str, level: str) -> bool:
        if level not in _DB_LEVELS:
            return False
        return event_type.startswith(_DB_EVENT_PREFIXES) or level in ("error", "critical")

    def debug(self, event_type: str, payload: dict[str, Any] | None = None, **kw: Any) -> None:
        self.log(event_type, payload, level="debug", **kw)

    def info(self, event_type: str, payload: dict[str, Any] | None = None, **kw: Any) -> None:
        self.log(event_type, payload, level="info", **kw)

    def warning(self, event_type: str, payload: dict[str, Any] | None = None, **kw: Any) -> None:
        self.log(event_type, payload, level="warning", **kw)

    def error(self, event_type: str, payload: dict[str, Any] | None = None, **kw: Any) -> None:
        self.log(event_type, payload, level="error", **kw)
