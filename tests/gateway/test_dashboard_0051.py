"""UPDATE-0051 — ajustes de dashboard (backend).

Cobre os quatro pontos de backend do lote de 7 ajustes:
  * Fix do PnL por período: o não-realizado (snapshot ao vivo) NÃO pode ser
    somado quando a janela termina no passado (sintoma "ontem soma com hoje").
  * Fechar UMA posição via `/control/position/close` (reduce_only market).
  * Rótulos de wallet (`/api/wallet-labels` + `/control/wallet/{addr}/label`).
  * Alavancagem por ordem gravada em `orders.leverage` e herdada pelos fills.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from engine.core.db import utcnow
from engine.core.logger import EventLogger
from engine.exchanges.base import Position
from engine.exchanges.paper import PaperAdapter
from engine.gateway.server import GatewayState, build_app

from ..conftest import register_strategy

HDR = {"X-Control-Token": "test-token"}


# --------------------------------------------------------------------------- #
# PnL por período: unrealized só quando a janela alcança o presente            #
# --------------------------------------------------------------------------- #
def _seed_pnl(gateway_state, paper) -> tuple[str, str, str, str]:
    """Fill realizado (50) DENTRO da janela + posição aberta (unrealized 123).
    Usa tempos relativos ao `now` real para ser robusto ao relógio do CI."""
    register_strategy(gateway_state.db, "ct_pnl", module="copy_trade")
    now = datetime.now(timezone.utc)
    fill_ts = (now - timedelta(days=2)).isoformat()
    gateway_state.db.insert("fills", {
        "cloid": "0xpnl", "strategy_id": "ct_pnl", "symbol": "BTC",
        "side": "sell", "price": 100_000.0, "size": 0.001, "fee": 0.5,
        "realized_pnl": 50.0, "ts": fill_ts,
    })
    paper._positions["BTC"] = Position(
        symbol="BTC", size=0.01, entry_price=100_000.0, unrealized_pnl=123.0)
    since = (now - timedelta(days=10)).isoformat()
    until_past = (now - timedelta(days=1)).isoformat()
    until_future = (now + timedelta(days=1)).isoformat()
    return since, until_past, until_future, fill_ts


def test_pnl_period_excludes_unrealized_when_window_ends_in_past(
        client, gateway_state, paper) -> None:
    since, until_past, _, _ = _seed_pnl(gateway_state, paper)
    s = client.get("/api/pnl/summary", params={
        "strategy_id": "ct_pnl", "since": since, "until": until_past,
    }).json()
    # Janela fecha ONTEM ⇒ o mark-to-market de hoje NÃO entra.
    assert s["realized_pnl"] == 50.0
    assert s["unrealized_pnl"] == 0.0
    assert s["total_pnl"] == 50.0


def test_pnl_period_includes_unrealized_when_window_reaches_now(
        client, gateway_state, paper) -> None:
    since, _, until_future, _ = _seed_pnl(gateway_state, paper)
    s = client.get("/api/pnl/summary", params={
        "strategy_id": "ct_pnl", "since": since, "until": until_future,
    }).json()
    # Janela alcança o presente ⇒ soma o não-realizado das posições abertas.
    assert s["realized_pnl"] == 50.0
    assert s["unrealized_pnl"] == 123.0
    assert s["total_pnl"] == 173.0


def test_pnl_no_until_includes_unrealized(client, gateway_state, paper) -> None:
    _seed_pnl(gateway_state, paper)
    s = client.get("/api/pnl/summary?strategy_id=ct_pnl").json()
    assert s["unrealized_pnl"] == 123.0
    assert s["total_pnl"] == 173.0


# --------------------------------------------------------------------------- #
# Fechar UMA posição — endpoint /control/position/close                        #
# --------------------------------------------------------------------------- #
def _testnet_state(settings, db):
    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name, testnet.network = "hyperliquid", "testnet"
    state = GatewayState(settings, testnet, db, adapters={"testnet": testnet},
                         logger=EventLogger("gw-close", settings.logs_dir, db=db))
    return state, testnet


def test_close_position_sends_reduce_only_sell_for_long(settings, db) -> None:
    state, testnet = _testnet_state(settings, db)
    register_strategy(db, "ct_close", module="copy_trade")
    db.insert("orders", {
        "cloid": "0xseed", "strategy_id": "ct_close", "symbol": "BTC",
        "side": "buy", "type": "market", "size": 0.02, "status": "filled",
        "created_at": utcnow(),
    })
    testnet._positions["BTC"] = Position(
        symbol="BTC", size=0.02, entry_price=100_000.0)

    captured: dict = {}
    state.handle_intent = lambda req: captured.update({
        "symbol": req.symbol, "side": req.side, "size": req.size,
        "reduce_only": req.reduce_only, "env": req.environment,
    }) or {"ok": True}

    with TestClient(build_app(state)) as c:
        r = c.post("/control/position/close", headers=HDR, json={
            "strategy_id": "ct_close", "symbol": "BTC", "env": "testnet",
        }).json()
    assert r["ok"] is True and r["symbol"] == "BTC"
    # Long ⇒ fecha VENDENDO, reduce_only, tamanho absoluto, no ambiente pedido.
    assert captured == {"symbol": "BTC", "side": "sell", "size": 0.02,
                        "reduce_only": True, "env": "testnet"}


def test_close_position_sends_buy_for_short(settings, db) -> None:
    state, testnet = _testnet_state(settings, db)
    register_strategy(db, "ct_short", module="copy_trade")
    db.insert("orders", {
        "cloid": "0xseed2", "strategy_id": "ct_short", "symbol": "BTC",
        "side": "sell", "type": "market", "size": 0.03, "status": "filled",
        "created_at": utcnow(),
    })
    testnet._positions["BTC"] = Position(
        symbol="BTC", size=-0.03, entry_price=100_000.0)
    captured: dict = {}
    state.handle_intent = lambda req: captured.update({"side": req.side}) or {"ok": True}
    with TestClient(build_app(state)) as c:
        r = c.post("/control/position/close", headers=HDR, json={
            "strategy_id": "ct_short", "symbol": "BTC", "env": "testnet",
        }).json()
    assert r["ok"] is True and captured["side"] == "buy"


def test_close_position_unknown_strategy(settings, db) -> None:
    state, _ = _testnet_state(settings, db)
    with TestClient(build_app(state)) as c:
        r = c.post("/control/position/close", headers=HDR, json={
            "strategy_id": "nope", "symbol": "BTC", "env": "testnet",
        }).json()
    assert r["ok"] is False and r["reason"] == "strategy_desconhecida"


def test_close_position_no_open_position(settings, db) -> None:
    state, _ = _testnet_state(settings, db)
    register_strategy(db, "ct_flat", module="copy_trade")
    with TestClient(build_app(state)) as c:
        r = c.post("/control/position/close", headers=HDR, json={
            "strategy_id": "ct_flat", "symbol": "BTC", "env": "testnet",
        }).json()
    assert r["ok"] is False and r["reason"] == "posicao_nao_encontrada"


def test_close_position_requires_token(settings, db) -> None:
    state, _ = _testnet_state(settings, db)
    with TestClient(build_app(state)) as c:
        assert c.post("/control/position/close", json={
            "strategy_id": "x", "symbol": "BTC", "env": "testnet",
        }).status_code == 401


# --------------------------------------------------------------------------- #
# Rótulos de wallet                                                            #
# --------------------------------------------------------------------------- #
ADDR = "0x4124AbCdEf0000000000000000000000000000AB"


def test_wallet_label_upsert_and_get(client) -> None:
    r = client.post(f"/control/wallet/{ADDR}/label", headers=HDR,
                    json={"label": "Hyperliquid 1"}).json()
    assert r["ok"] is True and r["address"] == ADDR.lower()
    labels = client.get("/api/wallet-labels").json()
    assert labels[ADDR.lower()] == "Hyperliquid 1"


def test_wallet_label_empty_removes(client) -> None:
    client.post(f"/control/wallet/{ADDR}/label", headers=HDR,
                json={"label": "Temp"})
    client.post(f"/control/wallet/{ADDR}/label", headers=HDR, json={"label": ""})
    labels = client.get("/api/wallet-labels").json()
    assert ADDR.lower() not in labels


def test_wallet_label_requires_token(client) -> None:
    assert client.post(f"/control/wallet/{ADDR}/label",
                       json={"label": "x"}).status_code == 401


# --------------------------------------------------------------------------- #
# Alavancagem por ordem herdada pelos fills                                    #
# --------------------------------------------------------------------------- #
def test_orders_expose_leverage_column(client, gateway_state) -> None:
    register_strategy(gateway_state.db, "ct_lev", module="copy_trade")
    gateway_state.db.insert("orders", {
        "cloid": "0xlev", "strategy_id": "ct_lev", "symbol": "BTC",
        "side": "buy", "type": "market", "size": 0.01, "price": 100_000.0,
        "leverage": 5.0, "status": "filled", "created_at": utcnow(),
    })
    rows = client.get("/api/orders?strategy_id=ct_lev").json()
    assert rows[0]["leverage"] == 5.0


def test_fills_inherit_leverage_from_parent_order(client, gateway_state) -> None:
    register_strategy(gateway_state.db, "ct_levf", module="copy_trade")
    gateway_state.db.insert("orders", {
        "cloid": "0xlevf", "strategy_id": "ct_levf", "symbol": "BTC",
        "side": "buy", "type": "market", "size": 0.01, "price": 100_000.0,
        "leverage": 7.0, "status": "filled", "created_at": utcnow(),
    })
    gateway_state.db.insert("fills", {
        "cloid": "0xlevf", "strategy_id": "ct_levf", "symbol": "BTC",
        "side": "buy", "price": 100_000.0, "size": 0.01, "fee": 0.5,
        "ts": utcnow(),
    })
    fills = client.get("/api/fills?strategy_id=ct_levf").json()
    assert fills[0]["leverage"] == 7.0
