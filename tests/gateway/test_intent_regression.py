"""Baseline de REGRESSÃO do gateway (protocolo §8.4.1, passo 1).

Fotografa o comportamento ATUAL de `/intent` e `/cancel` ANTES de qualquer
mudança do TV-Executor F1 (campos opcionais `stop_loss`/`take_profit` +
`bbo(symbol)` no adapter). A regra é inegociável: esta suíte fica verde AGORA,
e DEPOIS da mudança aditiva ela tem que continuar verde **sem edição**. Se um
teste precisar mudar para "passar", a mudança quebrou contrato → reverter, nunca
consertar por cima.

Escopo = o contrato de código do caminho de ordem (determinístico, sobre o
`PaperAdapter` e um adapter gravador para os ramos de erro). O canário de venue
na testnet real é passo SEPARADO de aceite da F1 (operador), não desta suíte.

Cobre: market via `notional_usd` e via `size`; limit com `price`; `reduce_only`;
cap de alavancagem (min do pedido × maxLev do ativo × cap global); `dry_run`;
roteamento por `environment`; ambiente não configurado; `/cancel` por cloid;
parsing de `on_own_fill` (B/A→buy/sell); IOC-sem-match ⇒ skipped vs erro de
negócio ⇒ registrado; truncagem ao cap; below-min; size→0; sem preço; kill
switch; ordem de submissão preservada.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from engine.core.logger import EventLogger
from engine.exchanges.base import OrderRequest, OrderResult, Position
from engine.exchanges.paper import PaperAdapter
from engine.gateway.server import GatewayState, build_app
from tests.conftest import register_strategy


# -- adapter gravador para os ramos de erro que o PaperAdapter não expõe -------
class RecordingAdapter(PaperAdapter):
    """PaperAdapter que devolve um OrderResult fixo (sem preencher) — usado para
    fotografar os ramos IOC-sem-match e erro-de-negócio do `handle_intent`."""

    name = "hyperliquid"
    network = "testnet"

    def __init__(self, result: OrderResult, **kw: Any) -> None:
        super().__init__(**kw)
        self._forced = result

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.placed_orders.append(request)
        res = self._forced
        return OrderResult(ok=res.ok, cloid=request.cloid, status=res.status,
                           error=res.error, filled_size=res.filled_size,
                           avg_price=res.avg_price)


def _state(settings, db, adapter, name="gw-regress"):
    return GatewayState(settings, adapter, db,
                        logger=EventLogger(name, settings.logs_dir, db=db))


# -- market ---------------------------------------------------------------------
def test_market_via_notional_usd(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_m1", module="copy_trade")
    r = client.post("/intent", json={
        "strategy_id": "ct_m1", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0,
    }).json()
    assert r["ok"] is True and r["status"] == "filled"
    # 100 / 100_000 = 0.001 (szDecimals=4); notional < cap $500.
    assert paper.placed_orders[-1].size == 0.001
    assert paper.placed_orders[-1].order_type == "market"
    order = gateway_state.db.query(
        "SELECT type, size, status FROM orders WHERE cloid = ?", (r["cloid"],))[0]
    assert order["type"] == "market" and order["size"] == 0.001
    assert order["status"] == "filled"
    fills = gateway_state.db.query(
        "SELECT strategy_id, fee FROM fills WHERE cloid = ?", (r["cloid"],))
    assert len(fills) == 1 and fills[0]["strategy_id"] == "ct_m1"
    assert fills[0]["fee"] > 0


def test_market_via_size(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_m2", module="copy_trade")
    r = client.post("/intent", json={
        "strategy_id": "ct_m2", "symbol": "BTC", "side": "buy", "size": 0.002,
    }).json()
    assert r["ok"] is True and r["status"] == "filled"
    assert paper.placed_orders[-1].size == 0.002


def test_limit_order_carries_price_and_type(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_lim", module="copy_trade")
    r = client.post("/intent", json={
        "strategy_id": "ct_lim", "symbol": "BTC", "side": "buy",
        "size": 0.001, "order_type": "limit", "price": 95_000.0,
    }).json()
    assert r["ok"] is True
    placed = paper.placed_orders[-1]
    assert placed.order_type == "limit" and placed.price == 95_000.0
    assert r["avg_price"] == 95_000.0
    order = gateway_state.db.query(
        "SELECT type, price FROM orders WHERE cloid = ?", (r["cloid"],))[0]
    assert order["type"] == "limit" and order["price"] == 95_000.0


def test_reduce_only_passthrough(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_ro", module="copy_trade")
    client.post("/intent", json={
        "strategy_id": "ct_ro", "symbol": "BTC", "side": "buy", "size": 0.003,
    })
    r = client.post("/intent", json={
        "strategy_id": "ct_ro", "symbol": "BTC", "side": "sell",
        "size": 0.001, "reduce_only": True,
    }).json()
    assert r["ok"] is True
    assert paper.placed_orders[-1].reduce_only is True


def test_leverage_capped_to_min_of_request_asset_global(
    settings, db, paper,
) -> None:
    """leverage passado ao enforcer = min(pedido, maxLev do ativo, cap global).
    Pedido 100 × asset(paper)=50 × global=10.0 ⇒ 10.0.
    UPDATE-0078: cap global subiu 5→10 (aprovado pelo operador)."""
    captured: dict[str, Any] = {}
    state = _state(settings, db, paper, "gw-lev")
    original = state.enforcer.check_intent

    def _spy(**kw: Any):
        captured.update(kw)
        return original(**kw)

    state.enforcer.check_intent = _spy  # type: ignore[assignment]
    register_strategy(db, "ct_lev", module="copy_trade")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "ct_lev", "symbol": "BTC", "side": "buy",
            "notional_usd": 100.0, "leverage": 100.0,
        }).json()
    assert r["ok"] is True
    assert captured["leverage"] == 10.0


# -- dry-run --------------------------------------------------------------------
def test_dry_run_records_but_never_hits_venue(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_dry", module="copy_trade")
    before = len(paper.placed_orders)
    r = client.post("/intent", json={
        "strategy_id": "ct_dry", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0, "dry_run": True,
    }).json()
    assert r["ok"] is True and r["dry_run"] is True
    assert "would_execute" in r
    assert len(paper.placed_orders) == before
    order = gateway_state.db.query(
        "SELECT status FROM orders WHERE cloid = ?", (r["cloid"],))[0]
    assert order["status"] == "dry_run"


# -- roteamento por ambiente ----------------------------------------------------
def test_environment_routes_to_correct_adapter(settings, db) -> None:
    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name, testnet.network = "hyperliquid", "testnet"
    mainnet = PaperAdapter(prices={"BTC": 200_000.0})
    mainnet.name, mainnet.network = "hyperliquid", "mainnet"
    state = GatewayState(settings, testnet, db,
                         adapters={"testnet": testnet, "mainnet": mainnet},
                         logger=EventLogger("gw-route", settings.logs_dir, db=db))
    register_strategy(db, "ct_route", module="copy_trade")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "ct_route", "symbol": "BTC", "side": "buy",
            "notional_usd": 200.0, "environment": "mainnet",
        }).json()
    assert r["ok"] is True
    assert len(mainnet.placed_orders) == 1 and len(testnet.placed_orders) == 0
    assert mainnet.placed_orders[0].size == 0.001   # 200 / 200_000


def test_unconfigured_environment_is_rejected(settings, db) -> None:
    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name, testnet.network = "hyperliquid", "testnet"
    state = GatewayState(settings, testnet, db, adapters={"testnet": testnet},
                         logger=EventLogger("gw-noenv", settings.logs_dir, db=db))
    register_strategy(db, "ct_noenv", module="copy_trade")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "ct_noenv", "symbol": "BTC", "side": "buy",
            "notional_usd": 100.0, "environment": "mainnet",
        }).json()
    assert r["ok"] is False
    assert r["reason"] == "ambiente não configurado: mainnet"


# -- cancel ---------------------------------------------------------------------
def test_cancel_by_cloid_marks_order_cancelled(client, gateway_state) -> None:
    from engine.core.db import utcnow

    register_strategy(gateway_state.db, "ct_cxl", module="copy_trade")
    gateway_state.db.insert("orders", {
        "cloid": "0xcxl1", "strategy_id": "ct_cxl", "symbol": "BTC",
        "side": "buy", "type": "limit", "size": 0.001, "price": 90_000.0,
        "status": "acked", "created_at": utcnow(),
    })
    r = client.post("/cancel", json={
        "strategy_id": "ct_cxl", "symbol": "BTC", "cloid": "0xcxl1",
    }).json()
    assert r["ok"] is True
    row = gateway_state.db.query(
        "SELECT status FROM orders WHERE cloid = '0xcxl1'")[0]
    assert row["status"] == "cancelled"


# -- on_own_fill ----------------------------------------------------------------
def test_on_own_fill_side_mapping_and_attribution(client, gateway_state) -> None:
    register_strategy(gateway_state.db, "ct_fill", module="copy_trade")
    gateway_state.db.insert("orders", {
        "cloid": "0xfill1", "strategy_id": "ct_fill", "symbol": "BTC",
        "side": "buy", "type": "market", "size": 0.001, "status": "created",
    })
    # side "B" (Hyperliquid) deve virar "buy".
    gateway_state.on_own_fill({
        "cloid": "0xfill1", "coin": "BTC", "side": "B",
        "px": 100_000.0, "sz": 0.001, "fee": 0.05,
    })
    fills = gateway_state.db.query(
        "SELECT side, strategy_id, network FROM fills WHERE cloid = '0xfill1'")
    assert len(fills) == 1
    assert fills[0]["side"] == "buy"
    assert fills[0]["strategy_id"] == "ct_fill"
    assert fills[0]["network"] in ("testnet", "mainnet")
    order = gateway_state.db.query(
        "SELECT status FROM orders WHERE cloid = '0xfill1'")[0]
    assert order["status"] == "filled"


# -- ramos de erro do place_order -----------------------------------------------
def test_ioc_no_match_is_skipped_and_row_deleted(settings, db) -> None:
    adapter = RecordingAdapter(
        OrderResult(ok=False, status="rejected",
                    error="BTC: could not immediately match against any resting orders"),
        prices={"BTC": 100_000.0})
    state = _state(settings, db, adapter, "gw-ioc")
    register_strategy(db, "ct_ioc", module="copy_trade")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "ct_ioc", "symbol": "BTC", "side": "buy",
            "notional_usd": 100.0,
        }).json()
    assert r["ok"] is False and r["status"] == "skipped"
    assert r["reason"] == "no_liquidity"
    # a linha `created` é apagada (não polui orders com rejected a cada reconcile).
    assert db.query("SELECT id FROM orders WHERE cloid = ?", (r["cloid"],)) == []


def test_business_error_is_recorded_not_deleted(settings, db) -> None:
    adapter = RecordingAdapter(
        OrderResult(ok=False, status="error", error="insufficient margin"),
        prices={"BTC": 100_000.0})
    state = _state(settings, db, adapter, "gw-bizerr")
    register_strategy(db, "ct_err", module="copy_trade")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "ct_err", "symbol": "BTC", "side": "buy",
            "notional_usd": 100.0,
        }).json()
    assert r["ok"] is False
    row = db.query(
        "SELECT status, reject_reason FROM orders WHERE cloid = ?",
        (r["cloid"],))
    assert len(row) == 1                       # NÃO apagado
    assert row[0]["reject_reason"] == "insufficient margin"


# -- cap / limites --------------------------------------------------------------
def test_truncated_to_cap(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_cap", module="copy_trade")
    r = client.post("/intent", json={
        "strategy_id": "ct_cap", "symbol": "BTC", "side": "buy",
        "notional_usd": 2240.0,
    }).json()
    assert r["ok"] is True and r["status"] == "filled"
    # cap $500 / 100_000 = 0.005 (floor a szDecimals=4).
    assert paper.placed_orders[-1].size == 0.005


def test_below_min_notional_rejected_not_sent(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_min", module="copy_trade")
    before = len(paper.placed_orders)
    r = client.post("/intent", json={
        "strategy_id": "ct_min", "symbol": "ETH", "side": "buy",
        "notional_usd": 0.4,
    }).json()
    assert r["ok"] is False and "below_min_notional" in r["reason"]
    assert len(paper.placed_orders) == before


def test_size_rounds_to_zero(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_zero", module="copy_trade")
    before = len(paper.placed_orders)
    # 0.4 / 100_000 = 4e-6 → round(,4) = 0.0 (arredonda a zero ANTES do enforcer).
    r = client.post("/intent", json={
        "strategy_id": "ct_zero", "symbol": "BTC", "side": "buy",
        "notional_usd": 0.4,
    }).json()
    assert r["ok"] is False and r["reason"] == "size_rounds_to_zero"
    assert len(paper.placed_orders) == before


def test_no_price_for_unknown_symbol(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_np", module="copy_trade")
    before = len(paper.placed_orders)
    r = client.post("/intent", json={
        "strategy_id": "ct_np", "symbol": "DOGE", "side": "buy",
        "notional_usd": 100.0,
    }).json()
    assert r["ok"] is False and r["reason"] == "no_price_for_DOGE"
    assert len(paper.placed_orders) == before


# -- kill switch ----------------------------------------------------------------
def test_kill_switch_blocks_intent(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_kill", module="copy_trade")
    client.post("/control/kill", headers={"X-Control-Token": "test-token"})
    before = len(paper.placed_orders)
    r = client.post("/intent", json={
        "strategy_id": "ct_kill", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0,
    }).json()
    assert r["ok"] is False and r["reason"] == "kill_switch_engaged"
    assert len(paper.placed_orders) == before


# -- ordem de submissão preservada ---------------------------------------------
def test_submission_order_is_preserved(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "ct_seq", module="copy_trade")
    sizes = [0.001, 0.002, 0.001]
    for s in sizes:
        client.post("/intent", json={
            "strategy_id": "ct_seq", "symbol": "BTC", "side": "buy", "size": s,
        })
    assert [o.size for o in paper.placed_orders] == sizes
