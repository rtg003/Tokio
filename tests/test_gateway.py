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
    # Uma ordem abaixo do mínimo é rejeitada de vez (não há o que truncar) e
    # nunca chega à venue.
    register_strategy(gateway_state.db, "dm_small")
    before = len(paper.placed_orders)
    resp = client.post("/intent", json={
        "strategy_id": "dm_small", "symbol": "ETH", "side": "buy",
        "notional_usd": 0.4,
    }).json()
    assert resp["ok"] is False and "below_min_notional" in resp["reason"]
    assert len(paper.placed_orders) == before


def test_intent_truncated_to_cap(client, gateway_state, paper) -> None:
    """Ordem grande NÃO é rejeitada: entra truncada até o cap (não zera a
    posição). Cap por estratégia = $500 (default); pedido ~ $2240 @ BTC=100k."""
    register_strategy(gateway_state.db, "ct_trunc", module="copy_trade")
    resp = client.post("/intent", json={
        "strategy_id": "ct_trunc", "symbol": "BTC", "side": "buy",
        "notional_usd": 2240.0,
    }).json()
    assert resp["ok"] is True
    assert resp["status"] == "filled"
    # 500 / 100_000 = 0.005 (floor a szDecimals=4), notional final $500 (≤ cap).
    assert paper.placed_orders[-1].size == 0.005
    row = gateway_state.db.query(
        "SELECT size FROM orders WHERE cloid = ?", (resp["cloid"],))[0]
    assert row["size"] == 0.005


def test_intent_rejected_when_cap_full(client, gateway_state, paper) -> None:
    """Sem espaço no cap (0) → rejeita 'strategy_cap_full'; espaço < mínimo →
    'cap_room_below_min'. Nada é enviado à venue nos dois casos."""
    register_strategy(gateway_state.db, "ct_full", module="copy_trade")
    before = len(paper.placed_orders)
    full = client.post("/intent", json={
        "strategy_id": "ct_full", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0, "strategy_cap_usd": 0.0,
    }).json()
    assert full["ok"] is False and full["reason"] == "strategy_cap_full"
    below = client.post("/intent", json={
        "strategy_id": "ct_full", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0, "strategy_cap_usd": 5.0,
    }).json()
    assert below["ok"] is False and below["reason"] == "cap_room_below_min"
    assert len(paper.placed_orders) == before


def test_api_pnl_summary_realized_plus_unrealized(client, gateway_state, paper) -> None:
    """KPI = realizado (fills) + não-realizado (posições abertas na venue)."""
    from engine.exchanges.base import Position

    register_strategy(gateway_state.db, "ct_pnl", module="copy_trade")
    gateway_state.db.insert("fills", {
        "cloid": "0xpnl1", "strategy_id": "ct_pnl", "symbol": "BTC",
        "side": "sell", "price": 100_000.0, "size": 0.001, "fee": 0.5,
        "realized_pnl": 50.0, "ts": "2026-07-05T10:00:00Z",
    })
    paper._positions["BTC"] = Position(
        symbol="BTC", size=0.01, entry_price=100_000.0, unrealized_pnl=123.0)
    s = client.get("/api/pnl/summary?strategy_id=ct_pnl").json()
    assert s["n_trades"] == 1
    assert s["realized_pnl"] == 50.0
    assert s["unrealized_pnl"] == 123.0
    assert s["total_pnl"] == 173.0


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


def _two_env_state(settings, db, name):
    """GatewayState com adapters testnet+mainnet (default=testnet)."""
    from engine.core.logger import EventLogger
    from engine.exchanges.paper import PaperAdapter
    from engine.gateway.server import GatewayState

    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name = "hyperliquid"
    testnet.network = "testnet"
    mainnet = PaperAdapter(prices={"BTC": 200_000.0})
    mainnet.name = "hyperliquid"
    mainnet.network = "mainnet"
    state = GatewayState(
        settings, testnet, db,
        adapters={"testnet": testnet, "mainnet": mainnet},
        logger=EventLogger(name, settings.logs_dir, db=db),
    )
    return state, testnet, mainnet


def test_intent_env_alias_routes_mainnet(settings, db) -> None:
    """`env` (alias) roteia p/ mainnet — o que o operador enviava e caía em testnet."""
    from fastapi.testclient import TestClient

    from engine.gateway.server import build_app

    state, testnet, mainnet = _two_env_state(settings, db, "gw-env-alias")
    register_strategy(db, "ct_alias")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "ct_alias",
            "symbol": "BTC",
            "side": "buy",
            "notional_usd": 200.0,
            "env": "mainnet",
        }).json()
    assert r["ok"] is True
    assert len(mainnet.placed_orders) == 1
    assert len(testnet.placed_orders) == 0


def test_intent_environment_key_still_works(settings, db) -> None:
    """A chave canônica `environment` (Copy Trade, in-process) segue válida
    com populate_by_name=True — invariante do alias, protege o hot path."""
    from fastapi.testclient import TestClient

    from engine.gateway.server import build_app

    state, testnet, mainnet = _two_env_state(settings, db, "gw-env-key")
    register_strategy(db, "ct_canon")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "ct_canon",
            "symbol": "BTC",
            "side": "buy",
            "notional_usd": 200.0,
            "environment": "mainnet",
        }).json()
    assert r["ok"] is True
    assert len(mainnet.placed_orders) == 1
    assert len(testnet.placed_orders) == 0


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


def test_market_meta_exposes_bbo(settings, db) -> None:
    """/api/market-meta devolve bid/ask (l2Book) além do mid — habilita o spread
    guard do TV-Executor no caminho ao vivo (UPDATE-0039)."""
    from fastapi.testclient import TestClient

    from engine.core.logger import EventLogger
    from engine.exchanges.paper import PaperAdapter
    from engine.gateway.server import GatewayState, build_app

    adapter = PaperAdapter(prices={"BTC": 100_000.0})
    adapter.name = "hyperliquid"
    adapter.network = "testnet"
    state = GatewayState(
        settings, adapter, db, adapters={"testnet": adapter},
        logger=EventLogger("gateway-bbo", settings.logs_dir, db=db),
    )
    with TestClient(build_app(state)) as c:
        meta = c.get("/api/market-meta?symbol=BTC").json()
    assert meta["ok"] is True
    assert meta["mid"] == 100_000.0
    assert meta["bid"] == 100_000.0 and meta["ask"] == 100_000.0  # PaperAdapter bbo


def test_fill_network_matches_order_exchange_id(settings, db) -> None:
    """O network do fill vem do exchange_id da ordem (fonte determinística),
    não do `_network` do callback do websocket. Regressão do bug: fill de
    ordem mainnet gravado como testnet quando o callback vem com env errado."""
    from engine.core.logger import EventLogger
    from engine.exchanges.paper import PaperAdapter
    from engine.gateway.server import GatewayState

    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name = "hyperliquid"
    testnet.network = "testnet"
    mainnet = PaperAdapter(prices={"BTC": 200_000.0})
    mainnet.name = "hyperliquid"
    mainnet.network = "mainnet"
    state = GatewayState(
        settings, testnet, db,
        adapters={"testnet": testnet, "mainnet": mainnet},
        logger=EventLogger("gateway-fill-net", settings.logs_dir, db=db),
    )
    register_strategy(db, "ct_fillnet", module="copy_trade")

    mainnet_ex = db.query(
        "SELECT id FROM exchanges WHERE name = 'hyperliquid' AND network = 'mainnet'"
    )[0]["id"]
    db.insert("orders", {
        "cloid": "0xmainfill", "strategy_id": "ct_fillnet",
        "exchange_id": mainnet_ex, "symbol": "BTC", "side": "buy",
        "type": "market", "size": 0.001, "price": 200_000.0, "status": "created",
    })

    # Callback do adapter mainnet chega com `_network` ERRADO (simula bug de
    # borda: adapter não re-registrado / reload). A fonte da verdade é a ordem.
    state.on_own_fill({
        "cloid": "0xmainfill", "coin": "BTC", "side": "B",
        "px": 200_000.0, "sz": 0.001, "fee": 0.02, "_network": "testnet",
    })

    fills = db.query("SELECT network FROM fills WHERE cloid = '0xmainfill'")
    assert len(fills) == 1
    assert fills[0]["network"] == "mainnet"  # não "testnet" do callback

    # O divergência é auditada como diagnóstico.
    mismatch = db.query(
        "SELECT COUNT(*) AS n FROM events WHERE event_type = 'fill.network_mismatch'"
    )[0]["n"]
    assert mismatch >= 1


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


def _seed_loss_fill(db, *, strategy_id, wallet, network, pnl, day,
                    forced_close=0, synthetic=0) -> None:
    """Insere um fill perdedor atribuído a (wallet, network) no dia `day`."""
    from engine.core.db import utcnow
    db.insert("fills", {
        "cloid": None, "strategy_id": strategy_id, "symbol": "BTC",
        "side": "sell", "price": 90_000.0, "size": 0.01, "fee": 0.0,
        "fee_asset": "USDC", "realized_pnl": pnl, "network": network,
        "master_address": wallet, "forced_close": forced_close,
        "synthetic": synthetic, "ts": f"{day}T12:00:00.000Z",
    })


def _today() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_circuit_breaker_isolates_wallet_scope(client, gateway_state, settings) -> None:
    # Fix 2: isolamento de wallet — perda além do cap em (0x4124, testnet) abre SÓ
    # esse escopo e pausa SÓ as estratégias dele; (0xd2c7, mainnet) fica intacto.
    settings.risk.max_daily_loss_usd = 100.0
    day = _today()
    register_strategy(gateway_state.db, "ct_1a5db900", module="copy_trade")
    register_strategy(gateway_state.db, "ct_mainnet", module="copy_trade")
    _seed_loss_fill(gateway_state.db, strategy_id="ct_1a5db900",
                    wallet="0x4124", network="testnet", pnl=-150.0, day=day)
    _seed_loss_fill(gateway_state.db, strategy_id="ct_mainnet",
                    wallet="0xd2c7", network="mainnet", pnl=-5.0, day=day)

    gateway_state._evaluate_circuit_breakers(day)

    assert gateway_state.enforcer.is_open("0x4124", "testnet")
    assert not gateway_state.enforcer.is_open("0xd2c7", "mainnet")
    statuses = {
        r["id"]: r["status"] for r in gateway_state.db.query(
            "SELECT id, status FROM strategies WHERE id IN ('ct_1a5db900','ct_mainnet')")
    }
    assert statuses["ct_1a5db900"] == "auto_paused"  # escopo estourado
    assert statuses["ct_mainnet"] == "active"          # wallet/ambiente intactos
    # /health expõe só o escopo aberto.
    scopes = client.get("/health").json()["circuit_breakers"]
    assert len(scopes) == 1 and scopes[0]["wallet"] == "0x4124"


def test_circuit_breaker_excludes_forced_and_synthetic(client, gateway_state, settings) -> None:
    # Regressão: forced_close=1 e synthetic=1 NÃO entram no net_pnl do breaker.
    settings.risk.max_daily_loss_usd = 100.0
    day = _today()
    register_strategy(gateway_state.db, "ct_fc", module="copy_trade")
    _seed_loss_fill(gateway_state.db, strategy_id="ct_fc", wallet="0x9999",
                    network="testnet", pnl=-500.0, day=day, forced_close=1)
    _seed_loss_fill(gateway_state.db, strategy_id="ct_fc", wallet="0x9999",
                    network="testnet", pnl=-500.0, day=day, synthetic=1)
    gateway_state._evaluate_circuit_breakers(day)
    assert not gateway_state.enforcer.is_open("0x9999", "testnet")
    assert gateway_state.db.query(
        "SELECT status FROM strategies WHERE id = 'ct_fc'")[0]["status"] == "active"


def _link_copyable_trader(db, strategy_id: str, address: str,
                          status: str = "TESTNET") -> None:
    """UPDATE-0064: vincula uma strategy a um trader copiável — grava o trader e
    injeta o endereço em strategies.config_snapshot (json_extract $.address)."""
    import json
    db.upsert("traders", {"address": address.lower(), "name": strategy_id,
                          "status": status, "dry_run": 0}, ("address",))
    db.execute("UPDATE strategies SET config_snapshot = ? WHERE id = ?",
               (json.dumps({"address": address.lower()}), strategy_id))


def test_circuit_breaker_reset_reactivates_only_breaker_paused(
    client, gateway_state, settings) -> None:
    # Fix 2b: reset reativa SÓ estratégias pausadas PELO breaker (by=circuit_breaker),
    # deixa pausa manual intacta, marca acknowledged_day e emite circuit_breaker.reset.
    # UPDATE-0064: reativação exige trader copiável — ct_auto vinculado a TESTNET.
    settings.risk.max_daily_loss_usd = 100.0
    day = _today()
    register_strategy(gateway_state.db, "ct_auto", module="copy_trade")
    _link_copyable_trader(gateway_state.db, "ct_auto",
                          "0x00000000000000000000000000000000000000a1")
    register_strategy(gateway_state.db, "ct_manual", module="copy_trade",
                      status="auto_paused")  # pausa manual pré-existente
    _seed_loss_fill(gateway_state.db, strategy_id="ct_auto", wallet="0x4124",
                    network="testnet", pnl=-150.0, day=day)
    gateway_state._evaluate_circuit_breakers(day)
    assert gateway_state.db.query(
        "SELECT status FROM strategies WHERE id = 'ct_auto'")[0]["status"] == "auto_paused"

    r = client.post("/control/circuit-breaker/reset",
                    headers={"X-Control-Token": "test-token"},
                    json={"wallet": "0x4124", "environment": "testnet"}).json()
    assert r["ok"] and "ct_auto" in r["reactivated"]
    assert "ct_manual" not in r["reactivated"]
    statuses = {
        row["id"]: row["status"] for row in gateway_state.db.query(
            "SELECT id, status FROM strategies WHERE id IN ('ct_auto','ct_manual')")
    }
    assert statuses["ct_auto"] == "active"          # reativada
    assert statuses["ct_manual"] == "auto_paused"   # pausa manual intacta
    assert not gateway_state.enforcer.is_open("0x4124", "testnet")
    # acknowledged_day marcado.
    ack = gateway_state.db.query(
        "SELECT acknowledged_day FROM circuit_breaker_state "
        "WHERE wallet = '0x4124' AND environment = 'testnet' AND day = ?", (day,))
    assert ack and ack[0]["acknowledged_day"] == day
    assert gateway_state.db.query(
        "SELECT COUNT(*) AS n FROM events WHERE event_type = 'circuit_breaker.reset'"
    )[0]["n"] >= 1


def test_circuit_breaker_reset_idempotent_until_rollover(
    client, gateway_state, settings) -> None:
    # Fix 2b: após reset, novo fill perdedor no MESMO dia UTC não reabre o breaker
    # (reconhece até o rollover); virando o dia, volta a poder abrir.
    settings.risk.max_daily_loss_usd = 100.0
    day = _today()
    register_strategy(gateway_state.db, "ct_idem", module="copy_trade")
    _seed_loss_fill(gateway_state.db, strategy_id="ct_idem", wallet="0x4124",
                    network="testnet", pnl=-150.0, day=day)
    gateway_state._evaluate_circuit_breakers(day)
    client.post("/control/circuit-breaker/reset",
                headers={"X-Control-Token": "test-token"},
                json={"wallet": "0x4124", "environment": "testnet"})
    # novo fill perdedor no mesmo dia
    _seed_loss_fill(gateway_state.db, strategy_id="ct_idem", wallet="0x4124",
                    network="testnet", pnl=-200.0, day=day)
    gateway_state._evaluate_circuit_breakers(day)
    assert not gateway_state.enforcer.is_open("0x4124", "testnet")  # NÃO reabre
    # novo dia → reconhecimento não vale mais; volta a abrir
    tomorrow = "2999-01-01"
    _seed_loss_fill(gateway_state.db, strategy_id="ct_idem", wallet="0x4124",
                    network="testnet", pnl=-300.0, day=tomorrow)
    gateway_state._evaluate_circuit_breakers(tomorrow)
    assert gateway_state.enforcer.is_open("0x4124", "testnet")


# ---------------------------------------------------------------------------
# UPDATE-0064 (Parte 2): reset do breaker revalida a invariante strategy↔trader
# ---------------------------------------------------------------------------
def test_reset_reactivates_when_trader_copyable(client, gateway_state, settings) -> None:
    """Reset reativa a strategy pausada pelo breaker QUANDO o trader vinculado
    continua copiável (TESTNET)."""
    settings.risk.max_daily_loss_usd = 100.0
    day = _today()
    register_strategy(gateway_state.db, "ct_ok", module="copy_trade")
    _link_copyable_trader(gateway_state.db, "ct_ok",
                          "0x00000000000000000000000000000000000000b1",
                          status="TESTNET")
    _seed_loss_fill(gateway_state.db, strategy_id="ct_ok", wallet="0x4124",
                    network="testnet", pnl=-150.0, day=day)
    gateway_state._evaluate_circuit_breakers(day)

    r = client.post("/control/circuit-breaker/reset",
                    headers={"X-Control-Token": "test-token"},
                    json={"wallet": "0x4124", "environment": "testnet"}).json()
    assert r["ok"] and "ct_ok" in r["reactivated"] and r["skipped"] == []
    assert gateway_state.db.query(
        "SELECT status FROM strategies WHERE id = 'ct_ok'")[0]["status"] == "active"


def test_reset_skips_reactivation_when_trader_demoted(
    client, gateway_state, settings) -> None:
    """Trader rebaixado (SALVO) enquanto o breaker estava aberto: o reset NÃO
    reativa — mantém pausada, devolve em `skipped` e emite
    strategy.reactivation_skipped (invariante strategy↔trader, UPDATE-0064)."""
    settings.risk.max_daily_loss_usd = 100.0
    day = _today()
    register_strategy(gateway_state.db, "ct_demoted", module="copy_trade")
    _link_copyable_trader(gateway_state.db, "ct_demoted",
                          "0x00000000000000000000000000000000000000b2",
                          status="TESTNET")
    _seed_loss_fill(gateway_state.db, strategy_id="ct_demoted", wallet="0x4124",
                    network="testnet", pnl=-150.0, day=day)
    gateway_state._evaluate_circuit_breakers(day)
    # trader rebaixado para NÃO-copiável após o breaker abrir
    gateway_state.db.execute(
        "UPDATE traders SET status = 'SALVO' WHERE address = ?",
        ("0x00000000000000000000000000000000000000b2",))

    r = client.post("/control/circuit-breaker/reset",
                    headers={"X-Control-Token": "test-token"},
                    json={"wallet": "0x4124", "environment": "testnet"}).json()
    assert r["ok"] and "ct_demoted" in r["skipped"]
    assert "ct_demoted" not in r["reactivated"]
    assert gateway_state.db.query(
        "SELECT status FROM strategies WHERE id = 'ct_demoted'")[0]["status"] == "auto_paused"
    assert gateway_state.db.query(
        "SELECT COUNT(*) AS n FROM events "
        "WHERE event_type = 'strategy.reactivation_skipped'")[0]["n"] >= 1


# ---------------------------------------------------------------------------
# UPDATE-0064 (Parte 3): atribuição EXPLÍCITA de trader em fills/orders
# ---------------------------------------------------------------------------
def test_record_fill_sets_trader_address(client, gateway_state) -> None:
    """record_fill grava trader_address resolvido da strategy (≠ master_address)."""
    register_strategy(gateway_state.db, "ct_attr")
    _link_copyable_trader(gateway_state.db, "ct_attr",
                          "0x00000000000000000000000000000000000000c1",
                          status="TESTNET")
    gateway_state.db.insert("orders", {
        "cloid": "0xattr1", "strategy_id": "ct_attr", "symbol": "BTC",
        "side": "buy", "type": "market", "size": 0.001, "status": "created",
    })
    gateway_state.on_own_fill({
        "cloid": "0xattr1", "coin": "BTC", "side": "buy",
        "px": 100_000.0, "sz": 0.001, "fee": 0.01,
    })
    row = gateway_state.db.query(
        "SELECT trader_address, master_address FROM fills WHERE cloid = '0xattr1'")[0]
    assert row["trader_address"] == "0x00000000000000000000000000000000000000c1"


def test_order_insert_sets_trader_address_and_preserves_master(
    client, gateway_state) -> None:
    """handle_intent grava trader_address na ordem e PRESERVA master_address
    (wallet executora) — conceitos distintos e coexistentes."""
    register_strategy(gateway_state.db, "ct_ord", module="copy_trade")
    _link_copyable_trader(gateway_state.db, "ct_ord",
                          "0x00000000000000000000000000000000000000c2",
                          status="TESTNET")
    resp = client.post("/intent", json={
        "strategy_id": "ct_ord", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0,
    }).json()
    assert resp["ok"] is True
    row = gateway_state.db.query(
        "SELECT trader_address, master_address FROM orders WHERE cloid = ?",
        (resp["cloid"],))[0]
    assert row["trader_address"] == "0x00000000000000000000000000000000000000c2"
    # master_address (wallet executora) segue povoado independentemente.
    assert "master_address" in row


def test_api_positions_scoped_to_strategy_symbols(client, gateway_state) -> None:
    """§5.1: /api/positions só mostra posições dos símbolos que o strategy_id
    negocia — posição de outra estratégia (símbolo distinto) fica de fora."""
    register_strategy(gateway_state.db, "ct_pos", module="copy_trade")
    register_strategy(gateway_state.db, "dm_other")
    r1 = client.post("/intent", json={
        "strategy_id": "ct_pos", "symbol": "BTC", "side": "buy", "notional_usd": 100.0,
    }).json()
    assert r1["ok"] is True
    r2 = client.post("/intent", json={
        "strategy_id": "dm_other", "symbol": "ETH", "side": "buy", "notional_usd": 100.0,
    }).json()
    assert r2["ok"] is True

    positions = client.get("/api/positions?strategy_id=ct_pos").json()
    symbols = {p["symbol"] for p in positions}
    assert "BTC" in symbols        # ct_pos negociou BTC
    assert "ETH" not in symbols    # ETH é de outra estratégia (§5.1)
    btc = next(p for p in positions if p["symbol"] == "BTC")
    assert btc["network"] == "paper"


def test_api_positions_requires_strategy_id(client) -> None:
    assert client.get("/api/positions").status_code == 400


# -- /internal/ensure-margin: auto-transfer spot→perp intra-conta ------------

def test_ensure_margin_feature_off(client, gateway_state) -> None:
    """Flag desligada ⇒ não toca a venue; devolve feature_desligada."""
    gateway_state.settings.copy_trade.auto_transfer_margin = False
    try:
        resp = client.post("/internal/ensure-margin", json={
            "strategy_id": "ct_x", "required_usd": 100.0, "environment": None,
        }).json()
    finally:
        gateway_state.settings.copy_trade.auto_transfer_margin = True
    assert resp["transferred"] == 0.0
    assert resp["reason"] == "feature_desligada"


def test_ensure_margin_mainnet_requires_opt_in(client, gateway_state) -> None:
    """mainnet exige opt-in explícito: sem ele, recusa antes de resolver adapter."""
    assert gateway_state.settings.copy_trade.auto_transfer_margin is True
    assert gateway_state.settings.copy_trade.auto_transfer_margin_mainnet is False
    resp = client.post("/internal/ensure-margin", json={
        "strategy_id": "ct_x", "required_usd": 100.0, "environment": "mainnet",
    }).json()
    assert resp["transferred"] == 0.0
    assert resp["reason"] == "mainnet_opt_in_desligado"


def test_ensure_margin_routes_to_adapter(client) -> None:
    """Venue sem spot/perp separados (PaperAdapter) ⇒ no-op do base adapter."""
    resp = client.post("/internal/ensure-margin", json={
        "strategy_id": "ct_x", "required_usd": 100.0, "environment": None,
    }).json()
    assert resp["transferred"] == 0.0
    assert resp["reason"] == "nao_suportado"
