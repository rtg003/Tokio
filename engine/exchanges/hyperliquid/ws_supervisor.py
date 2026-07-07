"""Resilient WebSocket supervisor for the Hyperliquid SDK.

The official SDK's `WebsocketManager` has NO reconnect and NO `on_close`/`on_error`
handler: when Hyperliquid closes the socket (`Inactive`/`Expired`/`goodbye`), the
manager thread dies and every subscription is silently lost forever. Since the
executor mirrors trader fills over this socket, a single drop stops copy trading
until a manual restart (root cause of UPDATE-0020).

This supervisor wraps a WS-only `Info` instance and:
- re-establishes ALL subscriptions on a fresh `Info` when the manager thread dies
  (exponential backoff, 1s → max), logging `ws.reconnecting` / `ws.reconnected`;
- sends an application-level ping every ~20s (the SDK pings only every 50s, too
  slow — HL drops inactive sockets at ~30s). The ping doubles as a liveness probe:
  a failed `send` forces a reconnect.

It is transport-only and network-free in tests: `make_info` is injected, so a fake
`Info` (with `subscribe`, `disconnect_websocket`, and a `ws_manager` exposing
`is_alive()` and `ws.send()`) drives the whole state machine without a socket.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

PING_INTERVAL_S = 20.0
_CHECK_INTERVAL_S = 5.0


class WsSupervisor:
    def __init__(
        self,
        make_info: Callable[[], Any],
        *,
        max_backoff_s: float = 60.0,
        logger: Any | None = None,
        name: str = "ws-supervisor",
    ) -> None:
        self._make_info = make_info
        self._max_backoff = max_backoff_s
        self._logger = logger
        self._name = name
        self._subs: list[tuple[dict[str, Any], Callable[[dict[str, Any]], None]]] = []
        self._info: Any | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # -- public API ---------------------------------------------------------
    def subscribe(self, subscription: dict[str, Any],
                  callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a subscription; (re)sent verbatim on every (re)connect."""
        with self._lock:
            self._subs.append((subscription, callback))
            if self._info is not None:
                self._info.subscribe(subscription, callback)

    def start(self) -> None:
        """Open the socket, subscribe everything, and run the watchdog."""
        self._connect()
        threading.Thread(target=self._watchdog, daemon=True, name=self._name).start()

    def stop(self) -> None:
        self._stop.set()
        self._disconnect()

    # -- internals ----------------------------------------------------------
    def _connect(self) -> None:
        with self._lock:
            self._info = self._make_info()
            for subscription, callback in self._subs:
                self._info.subscribe(subscription, callback)

    def _disconnect(self) -> None:
        info = self._info
        if info is None:
            return
        try:
            info.disconnect_websocket()
        except Exception:  # noqa: BLE001 — already gone / never connected
            pass

    def _alive(self) -> bool:
        mgr = getattr(self._info, "ws_manager", None)
        if mgr is None:
            return False
        try:
            return bool(mgr.is_alive())
        except Exception:  # noqa: BLE001
            return False

    def _send_ping(self) -> bool:
        """Return False if the socket is unusable (forces a reconnect)."""
        mgr = getattr(self._info, "ws_manager", None)
        ws = getattr(mgr, "ws", None) if mgr is not None else None
        if ws is None:
            return False
        try:
            ws.send(json.dumps({"method": "ping"}))
            return True
        except Exception:  # noqa: BLE001 — socket closed/broken
            return False

    def _log(self, level: str, event: str, data: dict[str, Any]) -> None:
        if self._logger is not None:
            getattr(self._logger, level)(event, data)

    def _reconnect(self, backoff: float) -> None:
        self._log("warning", "ws.reconnecting",
                  {"subs": len(self._subs), "backoff_s": round(backoff, 1)})
        self._disconnect()
        self._stop.wait(backoff)
        if self._stop.is_set():
            return
        self._connect()
        self._log("info", "ws.reconnected", {"subs": len(self._subs)})

    def _watchdog(self) -> None:
        backoff = 1.0
        last_ping = time.monotonic()
        while not self._stop.wait(_CHECK_INTERVAL_S):
            now = time.monotonic()
            healthy = self._alive()
            if healthy and now - last_ping >= PING_INTERVAL_S:
                healthy = self._send_ping()
                last_ping = now
            if healthy:
                backoff = 1.0
                continue
            try:
                self._reconnect(backoff)
                backoff = 1.0
                last_ping = time.monotonic()
            except Exception as exc:  # noqa: BLE001 — retry with larger backoff
                self._log("warning", "ws.reconnect_failed", {"error": str(exc)[:200]})
                backoff = min(backoff * 2, self._max_backoff)
