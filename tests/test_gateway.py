"""Gateway end-to-end over the paper adapter: full order lifecycle in DB/logs."""
from __future__ import annotations

from tests.conftest import register_strategy


def test_health(client) -> None:
    data = client.get("/health").json()
    assert data["ok"] is True
    assert data["kill_switch"] is False


def test_balance_endpoint(client) -> None:
    data = client.get("/balance").json()
    assert data["ok"] is True
    assert data["equity_usd"] == 10_000.0   # PaperAdapter fixture
    assert data["network"] == "paper"
    # cached response stays consistent
    assert client.get("/balance").json()["equity_usd"] == 10_000.0


def test_intent_full_lifecycle(client, gateway_state) -> None:
    register_strategy(gateway_state.db, "dm_test")
    resp = client.post("/intent", json={
        "strategy_id": "dm_test", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0,
    }).json()
    assert resp["ok"] is True
    assert resp["status"] == "filled"
    assert resp["latency_ms"] > 0

    orders = gateway_state.db.query("SELECT * FROM orders WHERE cloid = ?", (resp["cloid"],))
    assert len(orders) == 1 and orders[0]["status"] == "filled"
    fills = gateway_state.db.query("SELECT * FROM fills WHERE cloid = ?", (resp["cloid"],))
    assert len(fills) == 1
    assert fills[0]["strategy_id"] == "dm_test"
    assert fills[0]["fee"] > 0  # net-of-fees accounting from the first fill

    snapshot = client.get("/ledger").json()
    assert "dm_test" in snapshot
    assert snapshot["dm_test"]["positions"]["BTC"]["size"] > 0

    metrics = gateway_state.db.query(
        "SELECT * FROM strategy_metrics_daily WHERE strategy_id = 'dm_test'")
    assert metrics and metrics[0]["n_trades"] == 1


def test_dry_run_intent_records_but_never_hits_venue(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "dm_dry", status="dry_run")
    before = len(paper.placed_orders)
    resp = client.post("/intent", json={
        "strategy_id": "dm_dry", "symbol": "BTC", "side": "buy",
        "notional_usd": 50.0, "dry_run": True,
    }).json()
    assert resp["ok"] is True and resp["dry_run"] is True
    assert len(paper.placed_orders) == before  # venue untouched
    orders = gateway_state.db.query(
        "SELECT status FROM orders WHERE strategy_id = 'dm_dry'")
    assert orders[0]["status"] == "dry_run"


def test_rejected_intent_is_logged_not_sent(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "dm_big")
    before = len(paper.placed_orders)
    resp = client.post("/intent", json={
        "strategy_id": "dm_big", "symbol": "BTC", "side": "buy",
        "notional_usd": 999_999.0,
    }).json()
    assert resp["ok"] is False and "max_order_notional" in resp["reason"]
    assert len(paper.placed_orders) == before


def test_control_api_requires_token(client, gateway_state) -> None:
    register_strategy(gateway_state.db, "dm_ctl", status="paused")
    r = client.post("/control/strategy/dm_ctl/activate")
    assert r.status_code == 401
    r = client.post("/control/strategy/dm_ctl/activate",
                    headers={"X-Control-Token": "test-token"})
    assert r.status_code == 200
    rows = gateway_state.db.query("SELECT status FROM strategies WHERE id = 'dm_ctl'")
    assert rows[0]["status"] == "active"


def test_control_api_cannot_promote_dry_run(client, gateway_state) -> None:
    register_strategy(gateway_state.db, "dm_gate", status="dry_run")
    r = client.post("/control/strategy/dm_gate/activate",
                    headers={"X-Control-Token": "test-token"})
    assert r.status_code == 409  # dry_run -> active is a human gate


def test_trader_control_api_status_combobox(client, gateway_state) -> None:
    from engine.strategies.copy_trade.traders_store import upsert_candidate

    addr = "0x" + "ab" * 20
    upsert_candidate(gateway_state.db, address=addr, score=70.0)
    # A dashboard autenticada é o ato humano: SALVO/TESTNET passam.
    r = client.post(f"/control/trader/{addr}/status?new_status=SALVO",
                    headers={"X-Control-Token": "test-token"}).json()
    assert r["ok"] is True and r["status"] == "SALVO"
    r = client.post(f"/control/trader/{addr}/status?new_status=TESTNET",
                    headers={"X-Control-Token": "test-token"}).json()
    assert r["ok"] is True and r["status"] == "TESTNET"
    # MAINNET é recusado até o adapter/credenciais mainnet existirem.
    r = client.post(f"/control/trader/{addr}/status?new_status=MAINNET",
                    headers={"X-Control-Token": "test-token"}).json()
    assert r["ok"] is False and r["reason"] == "mainnet_nao_configurado"
    # config nunca aceita dry_run=false por aqui
    addr2 = "0x" + "cd" * 20
    upsert_candidate(gateway_state.db, address=addr2)
    r = client.post(f"/control/trader/{addr2}/config",
                    json={"value": 75.0, "dry_run": False},
                    headers={"X-Control-Token": "test-token"}).json()
    assert r["ok"] is True
    row = gateway_state.db.query("SELECT value, dry_run FROM traders WHERE address = ?",
                                 (addr2,))[0]
    assert row["value"] == 75.0 and row["dry_run"] == 1
    # listagem pública interna
    assert any(t["address"] == addr2 for t in client.get("/traders").json())


def test_intent_routes_by_environment(settings, db) -> None:
    from fastapi.testclient import TestClient

    from engine.core.logger import EventLogger
    from engine.exchanges.paper import PaperAdapter
    from engine.gateway.server import GatewayState, build_app

    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name = "hyperliquid"
    testnet.network = "testnet"
    mainnet = PaperAdapter(prices={"BTC": 200_000.0})
    mainnet.name = "hyperliquid"
    mainnet.network = "mainnet"
    state = GatewayState(
        settings,
        testnet,
        db,
        adapters={"testnet": testnet, "mainnet": mainnet},
        logger=EventLogger("gateway-env-test", settings.logs_dir, db=db),
    )
    register_strategy(db, "ct_env")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "ct_env",
            "symbol": "BTC",
            "side": "buy",
            "notional_usd": 200.0,
            "environment": "mainnet",
        }).json()
    assert r["ok"] is True
    assert len(mainnet.placed_orders) == 1
    assert len(testnet.placed_orders) == 0
    assert mainnet.placed_orders[0].size == 0.001


def test_api_fills_and_orders_filter_by_network(settings, db) -> None:
    from fastapi.testclient import TestClient

    from engine.core.logger import EventLogger
    from engine.exchanges.paper import PaperAdapter
    from engine.gateway.server import GatewayState, build_app

    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name = "hyperliquid"
    testnet.network = "testnet"
    mainnet = PaperAdapter(prices={"BTC": 200_000.0})
    mainnet.name = "hyperliquid"
    mainnet.network = "mainnet"
    state = GatewayState(
        settings,
        testnet,
        db,
        adapters={"testnet": testnet, "mainnet": mainnet},
        logger=EventLogger("gateway-net-filter", settings.logs_dir, db=db),
    )
    register_strategy(db, "ct_net", module="copy_trade")

    testnet_ex = db.query(
        "SELECT id FROM exchanges WHERE name = 'hyperliquid' AND network = 'testnet'"
    )[0]["id"]
    mainnet_ex = db.query(
        "SELECT id FROM exchanges WHERE name = 'hyperliquid' AND network = 'mainnet'"
    )[0]["id"]

    for i in range(6):
        db.insert("orders", {
            "cloid": f"0xtest{i}",
            "strategy_id": "ct_net",
            "exchange_id": testnet_ex,
            "symbol": "BTC",
            "side": "buy",
            "type": "market",
            "size": 0.001,
            "price": 100_000.0,
            "status": "filled",
        })
        db.insert("fills", {
            "cloid": f"0xtest{i}",
            "strategy_id": "ct_net",
            "symbol": "BTC",
            "side": "buy",
            "price": 100_000.0,
            "size": 0.001,
            "fee": 0.01,
            "network": "testnet",
            "ts": "2026-07-05T10:00:00Z",
        })
    for i in range(2):
        db.insert("orders", {
            "cloid": f"0xmain{i}",
            "strategy_id": "ct_net",
            "exchange_id": mainnet_ex,
            "symbol": "BTC",
            "side": "buy",
            "type": "market",
            "size": 0.001,
            "price": 200_000.0,
            "status": "filled",
        })
        db.insert("fills", {
            "cloid": f"0xmain{i}",
            "strategy_id": "ct_net",
            "symbol": "BTC",
            "side": "buy",
            "price": 200_000.0,
            "size": 0.001,
            "fee": 0.02,
            "network": "mainnet",
            "ts": "2026-07-05T11:00:00Z",
        })

    with TestClient(build_app(state)) as c:
        all_fills = c.get("/api/fills?strategy_id=ct_net&limit=50").json()
        assert len(all_fills) == 8

        testnet_fills = c.get(
            "/api/fills?strategy_id=ct_net&network=testnet&limit=50"
        ).json()
        assert len(testnet_fills) == 6

        mainnet_fills = c.get(
            "/api/fills?strategy_id=ct_net&network=mainnet&limit=50"
        ).json()
        assert len(mainnet_fills) == 2

        all_orders = c.get("/api/orders?strategy_id=ct_net&limit=50").json()
        assert len(all_orders) == 8

        testnet_orders = c.get(
            "/api/orders?strategy_id=ct_net&network=testnet&limit=50"
        ).json()
        assert len(testnet_orders) == 6

        mainnet_orders = c.get(
            "/api/orders?strategy_id=ct_net&network=mainnet&limit=50"
        ).json()
        assert len(mainnet_orders) == 2

        summary_all = c.get("/api/fills/summary?strategy_id=ct_net").json()
        assert summary_all["n_trades"] == 8

        summary_testnet = c.get(
            "/api/fills/summary?strategy_id=ct_net&network=testnet"
        ).json()
        assert summary_testnet["n_trades"] == 6

        summary_mainnet = c.get(
            "/api/fills/summary?strategy_id=ct_net&network=mainnet"
        ).json()
        assert summary_mainnet["n_trades"] == 2


def test_network_filter_backfills_legacy_orders_without_exchange_id(
    settings, db,
) -> None:
    from fastapi.testclient import TestClient

    from engine.core.logger import EventLogger
    from engine.exchanges.paper import PaperAdapter
    from engine.gateway.server import GatewayState, build_app

    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name = "hyperliquid"
    testnet.network = "testnet"
    state = GatewayState(
        settings,
        testnet,
        db,
        adapters={"testnet": testnet},
        logger=EventLogger("gateway-legacy-net", settings.logs_dir, db=db),
    )
    register_strategy(db, "ct_legacy", module="copy_trade")

    # Ordem legada sem exchange_id + fill sem network (cenário pré-migração).
    db.insert("orders", {
        "cloid": "0xlegacy1",
        "strategy_id": "ct_legacy",
        "symbol": "BTC",
        "side": "buy",
        "type": "market",
        "size": 0.001,
        "price": 100_000.0,
        "status": "filled",
    })
    db.insert("fills", {
        "cloid": "0xlegacy1",
        "strategy_id": "ct_legacy",
        "symbol": "BTC",
        "side": "buy",
        "price": 100_000.0,
        "size": 0.001,
        "fee": 0.01,
        "ts": "2026-07-05T12:00:00Z",
    })

    # Simula backfill da migração 0013.
    testnet_ex = db.query(
        "SELECT id FROM exchanges WHERE name = 'hyperliquid' AND network = 'testnet'"
    )[0]["id"]
    db.execute(
        "UPDATE orders SET exchange_id = ? WHERE cloid = '0xlegacy1'",
        (testnet_ex,),
    )
    db.execute(
        "UPDATE fills SET network = 'testnet' WHERE cloid = '0xlegacy1'",
    )

    with TestClient(build_app(state)) as c:
        rows = c.get(
            "/api/fills?strategy_id=ct_legacy&network=testnet&limit=20"
        ).json()
        assert len(rows) == 1
        assert rows[0]["network"] == "testnet"
        assert c.get(
            "/api/fills?strategy_id=ct_legacy&network=mainnet&limit=20"
        ).json() == []


def test_fill_attribution_falls_back_to_order_strategy(client, gateway_state) -> None:
    register_strategy(gateway_state.db, "ct_48295497")
    gateway_state.db.insert("orders", {
        "cloid": "0xlatefill",
        "strategy_id": "ct_48295497",
        "symbol": "BTC",
        "side": "buy",
        "type": "market",
        "size": 0.001,
        "status": "created",
    })
    gateway_state.on_own_fill({
        "cloid": "0xlatefill",
        "coin": "BTC",
        "side": "buy",
        "px": 100_000.0,
        "sz": 0.001,
        "fee": 0.01,
    })

    fills = client.get("/api/fills?strategy_id=ct_48295497").json()
    assert len(fills) == 1
    assert fills[0]["strategy_id"] == "ct_48295497"
    orders = client.get("/api/orders?strategy_id=ct_48295497").json()
    assert orders[0]["status"] == "filled"


def test_kill_switch_cancels_open_orders(client, gateway_state) -> None:
    from engine.core.db import utcnow

    register_strategy(gateway_state.db, "dm_kill")
    gateway_state.db.insert("orders", {
        "cloid": "0xopen1", "strategy_id": "dm_kill", "symbol": "BTC",
        "side": "buy", "type": "limit", "size": 0.001, "price": 90_000.0,
        "status": "acked", "created_at": utcnow(),
    })
    r = client.post("/control/kill", headers={"X-Control-Token": "test-token"}).json()
    assert r["ok"] is True
    assert r["open_orders_cancelled"] == 1
    row = gateway_state.db.query("SELECT status FROM orders WHERE cloid = '0xopen1'")[0]
    assert row["status"] == "cancelled"
    # engaged switch blocks any new intent
    resp = client.post("/intent", json={
        "strategy_id": "dm_kill", "symbol": "BTC", "side": "buy",
        "notional_usd": 50.0,
    }).json()
    assert resp["ok"] is False and resp["reason"] == "kill_switch_engaged"


def test_circuit_breaker_auto_pauses_all(client, gateway_state, settings) -> None:
    register_strategy(gateway_state.db, "dm_loss")
    register_strategy(gateway_state.db, "dm_other")
    settings.risk.max_daily_loss_usd = 1.0
    # open then close at a loss big enough to trip the breaker
    r1 = client.post("/intent", json={
        "strategy_id": "dm_loss", "symbol": "BTC", "side": "buy", "notional_usd": 400.0,
    }).json()
    assert r1["ok"]
    gateway_state.adapter.set_price("BTC", 90_000.0)
    r2 = client.post("/intent", json={
        "strategy_id": "dm_loss", "symbol": "BTC", "side": "sell",
        "size": 0.004, "reduce_only": True,
    }).json()
    assert r2["ok"]
    assert gateway_state.enforcer.circuit_open
    rows = gateway_state.db.query(
        "SELECT id, status FROM strategies WHERE id IN ('dm_loss','dm_other')")
    assert all(r["status"] == "auto_paused" for r in rows)
