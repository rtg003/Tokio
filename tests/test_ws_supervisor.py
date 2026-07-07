"""WsSupervisor: reconnect + re-subscribe + ping (UPDATE-0020).

The official Hyperliquid SDK never reconnects; a dropped socket silently loses
every subscription. The supervisor is transport-only and network-free here — a
fake `Info` drives the whole state machine without a socket.
"""
from __future__ import annotations

from typing import Any, Callable

from engine.exchanges.hyperliquid.ws_supervisor import WsSupervisor


class FakeWs:
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.sent: list[str] = []
        self.raise_on_send = False

    def send(self, payload: str) -> None:
        if self.raise_on_send:
            raise ConnectionError("socket closed")
        self.sent.append(payload)


class FakeManager:
    def __init__(self, alive: bool = True) -> None:
        self._alive = alive
        self.ws = FakeWs()

    def is_alive(self) -> bool:
        return self._alive


class FakeInfo:
    """Mimics the SDK's WS-only Info: subscribe(), disconnect_websocket(),
    and a ws_manager exposing is_alive()/ws.send()."""

    instances: list["FakeInfo"] = []

    def __init__(self, alive: bool = True) -> None:
        self.subs: list[dict[str, Any]] = []
        self.ws_manager = FakeManager(alive=alive)
        self.disconnected = False
        FakeInfo.instances.append(self)

    def subscribe(self, subscription: dict[str, Any],
                  callback: Callable[[dict[str, Any]], None]) -> None:
        self.subs.append(subscription)

    def disconnect_websocket(self) -> None:
        self.disconnected = True


def _make_supervisor() -> WsSupervisor:
    FakeInfo.instances = []
    return WsSupervisor(make_info=lambda: FakeInfo(), name="test-ws")


def test_connect_subscribes_all_registered() -> None:
    sup = _make_supervisor()
    sup.subscribe({"type": "userFills", "user": "0xaaa"}, lambda m: None)
    sup.subscribe({"type": "userFills", "user": "0xbbb"}, lambda m: None)
    sup._connect()
    info = FakeInfo.instances[-1]
    assert [s["user"] for s in info.subs] == ["0xaaa", "0xbbb"]


def test_subscribe_after_connect_is_live() -> None:
    sup = _make_supervisor()
    sup._connect()
    sup.subscribe({"type": "userFills", "user": "0xccc"}, lambda m: None)
    info = FakeInfo.instances[-1]
    assert info.subs[-1]["user"] == "0xccc"


def test_reconnect_resubscribes_all_on_fresh_info() -> None:
    sup = _make_supervisor()
    sup.subscribe({"type": "userFills", "user": "0xaaa"}, lambda m: None)
    sup.subscribe({"type": "userFills", "user": "0xbbb"}, lambda m: None)
    sup._connect()
    first = FakeInfo.instances[-1]
    sup._reconnect(0.0)  # backoff 0 -> immediate
    second = FakeInfo.instances[-1]
    assert first is not second
    assert first.disconnected is True
    # ALL subscriptions come back on the fresh Info (the SDK loses them forever)
    assert {s["user"] for s in second.subs} == {"0xaaa", "0xbbb"}


def test_alive_reflects_manager() -> None:
    sup = _make_supervisor()
    sup._connect()
    assert sup._alive() is True
    FakeInfo.instances[-1].ws_manager._alive = False
    assert sup._alive() is False


def test_send_ping_false_forces_reconnect_when_socket_broken() -> None:
    sup = _make_supervisor()
    sup._connect()
    assert sup._send_ping() is True
    FakeInfo.instances[-1].ws_manager.ws.raise_on_send = True
    assert sup._send_ping() is False


def test_send_ping_false_when_no_ws() -> None:
    sup = _make_supervisor()
    sup._connect()
    FakeInfo.instances[-1].ws_manager.ws = None
    assert sup._send_ping() is False
