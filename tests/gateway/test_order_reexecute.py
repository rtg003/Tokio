"""UPDATE-0080: reexecução manual de ordem recusada (rejected/error) a preço de
mercado via /control/order/reexecute. O endpoint lê a ordem original por cloid,
consulta o mid atual e — só em execute (preview=False) — reusa handle_intent com
price=None (mercado). Guardas: só rejected/error, ordem tem de existir."""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from engine.core.db import Database
from engine.gateway.server import build_app

from tests.conftest import register_strategy
from tests.test_gateway import _two_env_state


def _insert_rejected_order(db: Database, *, cloid: str, sid: str,
                           status: str = "rejected") -> None:
    db.insert("orders", {
        "cloid": cloid,
        "strategy_id": sid,
        "symbol": "BTC",
        "side": "buy",
        "type": "market",
        # notional (0.004 * 100k = $400) < cap $500 ⇒ NÃO trunca no enforcer,
        # então o size reexecutado bate exatamente com o original.
        "size": 0.004,
        "price": 90_000.0,
        "leverage": 5.0,
        "status": status,
        "created_at": "2026-07-19T00:00:00+00:00",
    })


def _client(state) -> TestClient:
    os.environ["GATEWAY_CONTROL_TOKEN"] = "test-token"
    return TestClient(build_app(state))


def test_reexecute_preview_returns_market_price_without_placing(settings, db) -> None:
    state, testnet, _mainnet = _two_env_state(settings, db, "gw-reexec-preview")
    register_strategy(db, "ct_reexec")
    _insert_rejected_order(db, cloid="cl-1", sid="ct_reexec")
    with _client(state) as c:
        r = c.post("/control/order/reexecute",
                   headers={"X-Control-Token": "test-token"},
                   json={"strategy_id": "ct_reexec", "symbol": "BTC",
                         "cloid": "cl-1", "env": "testnet", "preview": True}).json()
    assert r["ok"] is True and r["preview"] is True
    assert r["market_price"] == 100_000.0        # mid do adapter testnet
    assert r["original_price"] == 90_000.0
    assert r["side"] == "buy" and r["size"] == 0.004 and r["leverage"] == 5.0
    # variação: (100000-90000)/90000*100 ≈ 11.11%
    assert abs(r["drift_pct"] - 11.111) < 0.01
    assert len(testnet.placed_orders) == 0        # preview NÃO envia ordem


def test_reexecute_execute_places_new_order_at_market(settings, db) -> None:
    state, testnet, _mainnet = _two_env_state(settings, db, "gw-reexec-exec")
    register_strategy(db, "ct_reexec2")
    _insert_rejected_order(db, cloid="cl-2", sid="ct_reexec2")
    with _client(state) as c:
        r = c.post("/control/order/reexecute",
                   headers={"X-Control-Token": "test-token"},
                   json={"strategy_id": "ct_reexec2", "symbol": "BTC",
                         "cloid": "cl-2", "env": "testnet", "preview": False}).json()
    assert r["ok"] is True
    assert r["cloid"] != "cl-2"                    # ordem NOVA (novo cloid)
    assert len(testnet.placed_orders) == 1
    placed = testnet.placed_orders[0]
    assert placed.side == "buy" and placed.size == 0.004
    assert placed.price is None                    # None ⇒ mercado (mid_price)
    # ordem original permanece rejected (não mutada)
    orig = db.query("SELECT status FROM orders WHERE cloid = 'cl-2'")[0]
    assert orig["status"] == "rejected"


def test_reexecute_refuses_non_rejected_order(settings, db) -> None:
    state, testnet, _mainnet = _two_env_state(settings, db, "gw-reexec-guard")
    register_strategy(db, "ct_reexec3")
    _insert_rejected_order(db, cloid="cl-3", sid="ct_reexec3", status="filled")
    with _client(state) as c:
        r = c.post("/control/order/reexecute",
                   headers={"X-Control-Token": "test-token"},
                   json={"strategy_id": "ct_reexec3", "symbol": "BTC",
                         "cloid": "cl-3", "env": "testnet", "preview": False}).json()
    assert r["ok"] is False and r["reason"] == "ordem_nao_reexecutavel"
    assert len(testnet.placed_orders) == 0        # nada enviado


def test_reexecute_unknown_order(settings, db) -> None:
    state, _testnet, _mainnet = _two_env_state(settings, db, "gw-reexec-unknown")
    register_strategy(db, "ct_reexec4")
    with _client(state) as c:
        r = c.post("/control/order/reexecute",
                   headers={"X-Control-Token": "test-token"},
                   json={"strategy_id": "ct_reexec4", "symbol": "BTC",
                         "cloid": "nope", "env": "testnet", "preview": False}).json()
    assert r["ok"] is False and r["reason"] == "ordem_nao_encontrada"
