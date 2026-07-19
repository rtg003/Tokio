"""UPDATE-0082: rebaixar um trader de TESTNET/MAINNET cancela, no ato (síncrono,
no endpoint `/control/trader/{address}/status`), TODAS as ordens abertas da
strategy NAQUELE ambiente. Best-effort (uma falha isolada não aborta as demais
nem derruba o endpoint); só toca ordens abertas
(`created/sent/acked/partially_filled`) — filled/cancelled/rejected ficam
intactas. A rede da ordem vem de `orders JOIN exchanges` (não há coluna
`network` em orders). O gate humano de status permanece inalterado.
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from engine.core.logger import EventLogger
from engine.gateway.server import GatewayState, build_app
from engine.strategies.copy_trade.funnel import load_config
from engine.strategies.copy_trade.traders_store import (
    set_status, strategy_id_for, upsert_candidate,
)

CFG = load_config()
LV = int(CFG["logic_version"])
ADDR = "0x" + "aa" * 20
OTHER = "0x" + "bb" * 20
TOKEN = "test-token"
HEADERS = {"X-Control-Token": TOKEN}


class FakeAdapter:
    """Adapter mínimo: conta cancels e pode falhar num cloid específico."""

    def __init__(self, network: str, *, fail_cloids: set[str] | None = None) -> None:
        self.name = "hyperliquid"
        self.network = network
        self.fail_cloids = fail_cloids or set()
        self.cancelled: list[str] = []

    def subscribe_own_fills(self, callback: Any) -> None:
        """No-op: GatewayState registra o callback de fills em cada adapter."""

    def cancel(self, symbol: str, exchange_order_id: str | None = None,
               cloid: str | None = None) -> bool:
        if cloid in self.fail_cloids:
            raise RuntimeError(f"venue recusou cancel (simulado) {cloid}")
        self.cancelled.append(cloid or "")
        return True


def _seed_exchanges(db) -> dict[str, int]:
    """Cria linhas de exchange p/ testnet e mainnet e devolve name→id."""
    ids: dict[str, int] = {}
    for net in ("testnet", "mainnet"):
        db.execute(
            "INSERT INTO exchanges (name, network, status) VALUES (?, ?, 'active')",
            ("hyperliquid", net))
        row = db.query(
            "SELECT id FROM exchanges WHERE name = ? AND network = ?",
            ("hyperliquid", net))[0]
        ids[net] = row["id"]
    return ids


def _seed_trader(db, address: str, status: str) -> str:
    """Cria trader e o leva ao status operante desejado (gate humano). Retorna sid."""
    upsert_candidate(db, address=address, score=10.0, logic_version=LV,
                     extras={"metrics_confidence": "complete"})
    res = set_status(db, address, status, by="dashboard_humano", human_gate=True)
    assert res.get("ok"), res
    return strategy_id_for(address, None)


def _add_order(db, *, cloid: str, sid: str, exchange_id: int, status: str,
               symbol: str = "BTC") -> None:
    db.execute(
        "INSERT INTO orders (cloid, strategy_id, exchange_id, symbol, side, "
        "type, size, status) VALUES (?, ?, ?, ?, 'buy', 'limit', 0.1, ?)",
        (cloid, sid, exchange_id, symbol, status))


def _make_client(settings, db, adapters):
    os.environ["GATEWAY_CONTROL_TOKEN"] = TOKEN
    logger = EventLogger("demote-test", settings.logs_dir, db=db)
    state = GatewayState(settings, adapters["testnet"], db,
                         adapters=adapters, logger=logger)
    return TestClient(build_app(state))


def _post_status(client, address: str, new_status: str):
    return client.post(f"/control/trader/{address}/status",
                       params={"new_status": new_status}, headers=HEADERS)


# ---------------------------------------------------------------------------


def test_testnet_to_salvo_cancels_open_orders(settings, db) -> None:
    ex = _seed_exchanges(db)
    sid = _seed_trader(db, ADDR, "TESTNET")
    for c in ("c1", "c2", "c3"):
        _add_order(db, cloid=c, sid=sid, exchange_id=ex["testnet"], status="sent")
    ad = FakeAdapter("testnet")
    client = _make_client(settings, db, {"testnet": ad})

    r = _post_status(client, ADDR, "SALVO")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["cancelled_orders"] == 3
    assert set(ad.cancelled) == {"c1", "c2", "c3"}
    rows = db.query("SELECT status FROM orders WHERE strategy_id = ?", (sid,))
    assert all(row["status"] == "cancelled" for row in rows)
    ev = db.query("SELECT payload FROM events WHERE event_type = 'order.cancel_bulk'")
    assert len(ev) == 1 and '"count": 3' in ev[0]["payload"]
    assert '"trader_demoted"' in ev[0]["payload"]


def test_mainnet_to_salvo_cancels_open_orders(settings, db) -> None:
    ex = _seed_exchanges(db)
    sid = _seed_trader(db, ADDR, "MAINNET")
    for c in ("m1", "m2"):
        _add_order(db, cloid=c, sid=sid, exchange_id=ex["mainnet"], status="acked")
    ad_m = FakeAdapter("mainnet")
    ad_t = FakeAdapter("testnet")
    client = _make_client(settings, db,
                          {"testnet": ad_t, "mainnet": ad_m})

    body = _post_status(client, ADDR, "SALVO").json()
    assert body["cancelled_orders"] == 2
    assert set(ad_m.cancelled) == {"m1", "m2"}
    assert ad_t.cancelled == []


def test_promotion_does_not_cancel(settings, db) -> None:
    ex = _seed_exchanges(db)
    sid = _seed_trader(db, ADDR, "SALVO")
    _add_order(db, cloid="p1", sid=sid, exchange_id=ex["testnet"], status="sent")
    ad = FakeAdapter("testnet")
    client = _make_client(settings, db, {"testnet": ad})

    body = _post_status(client, ADDR, "TESTNET").json()
    assert body["ok"] is True
    assert "cancelled_orders" not in body       # promoção não cancela
    assert ad.cancelled == []
    assert db.query("SELECT status FROM orders WHERE cloid = 'p1'")[0]["status"] == "sent"
    assert db.query("SELECT event_type FROM events "
                    "WHERE event_type = 'order.cancel_bulk'") == []


def test_no_open_orders_counts_zero(settings, db) -> None:
    ex = _seed_exchanges(db)
    sid = _seed_trader(db, ADDR, "TESTNET")
    # ordens JÁ terminais: não devem ser tocadas
    _add_order(db, cloid="f1", sid=sid, exchange_id=ex["testnet"], status="filled")
    _add_order(db, cloid="x1", sid=sid, exchange_id=ex["testnet"], status="cancelled")
    ad = FakeAdapter("testnet")
    client = _make_client(settings, db, {"testnet": ad})

    body = _post_status(client, ADDR, "SALVO").json()
    assert body["cancelled_orders"] == 0
    assert ad.cancelled == []
    assert db.query("SELECT status FROM orders WHERE cloid = 'f1'")[0]["status"] == "filled"


def test_one_cancel_fails_others_proceed(settings, db) -> None:
    ex = _seed_exchanges(db)
    sid = _seed_trader(db, ADDR, "TESTNET")
    for c in ("ok1", "boom", "ok2"):
        _add_order(db, cloid=c, sid=sid, exchange_id=ex["testnet"], status="sent")
    ad = FakeAdapter("testnet", fail_cloids={"boom"})
    client = _make_client(settings, db, {"testnet": ad})

    body = _post_status(client, ADDR, "SALVO").json()
    assert body["cancelled_orders"] == 2
    by_cloid = {r["cloid"]: r["status"]
                for r in db.query("SELECT cloid, status FROM orders WHERE strategy_id = ?",
                                  (sid,))}
    assert by_cloid["ok1"] == "cancelled" and by_cloid["ok2"] == "cancelled"
    assert by_cloid["boom"] == "sent"           # falhou → status original preservado
    failed = db.query("SELECT payload FROM events "
                      "WHERE event_type = 'order.cancel_failed'")
    assert len(failed) == 1 and '"boom"' in failed[0]["payload"]


def test_scope_only_own_strategy_and_network(settings, db) -> None:
    ex = _seed_exchanges(db)
    sid = _seed_trader(db, ADDR, "TESTNET")
    other_sid = _seed_trader(db, OTHER, "TESTNET")
    # alvo: 1 ordem testnet da própria strategy
    _add_order(db, cloid="mine", sid=sid, exchange_id=ex["testnet"], status="sent")
    # ruído: outra strategy (mesmo network) + mesma strategy noutro network
    _add_order(db, cloid="foreign", sid=other_sid, exchange_id=ex["testnet"], status="sent")
    _add_order(db, cloid="mainnet_mine", sid=sid, exchange_id=ex["mainnet"], status="sent")
    ad_t = FakeAdapter("testnet")
    ad_m = FakeAdapter("mainnet")
    client = _make_client(settings, db, {"testnet": ad_t, "mainnet": ad_m})

    body = _post_status(client, ADDR, "SALVO").json()
    assert body["cancelled_orders"] == 1
    assert ad_t.cancelled == ["mine"]
    assert ad_m.cancelled == []
    assert db.query("SELECT status FROM orders WHERE cloid = 'foreign'")[0]["status"] == "sent"
    assert db.query("SELECT status FROM orders WHERE cloid = 'mainnet_mine'")[0]["status"] == "sent"
