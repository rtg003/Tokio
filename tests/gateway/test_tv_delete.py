"""Exclusão destrutiva de estratégias TradingView (§5.2).

Guardrails nunca contornáveis: recusa `active` (pausar antes) e recusa quando há
posição aberta no ambiente. A cascata apaga SÓ os dados do módulo TV (sinais/
decisões/incidentes/fila/versões/meta + linha `strategies`) e PRESERVA fills/
orders (registros reais de execução, base do ledger/reconciliação — decisão do
operador). Não toca o hot path de /intent nem o gate de status do Copy Trade.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from engine.core.db import utcnow
from engine.core.logger import EventLogger
from engine.exchanges.base import Position
from engine.exchanges.paper import PaperAdapter
from engine.gateway.server import GatewayState, build_app

HDR = {"X-Control-Token": "test-token"}


def _create(client, sid="tv_del", env="testnet", **extra):
    body = {"strategy_id": sid, "name": "To Delete", "environment": env,
            "symbols_allowed": ["BTC"], "timeframes_allowed": ["4h"],
            "allocation_usd": 1000, "stop_loss_pct": 1.2, **extra}
    return client.post("/control/tv/strategies", json=body, headers=HDR)


def test_delete_unknown_is_404(client) -> None:
    assert client.post("/control/tv/strategies/nope/delete",
                       headers=HDR).status_code == 404


def test_delete_refuses_active(client, gateway_state) -> None:
    _create(client, sid="tv_active")
    assert client.post("/control/tv/strategies/tv_active/activate",
                       headers=HDR).json()["ok"] is True
    r = client.post("/control/tv/strategies/tv_active/delete", headers=HDR).json()
    assert r["ok"] is False and r["reason"] == "ativa_pause_antes"
    # nada apagado
    assert gateway_state.db.query(
        "SELECT id FROM strategies WHERE id='tv_active'")


def test_delete_refuses_open_position(settings, db) -> None:
    testnet = PaperAdapter(prices={"BTC": 100_000.0})
    testnet.name, testnet.network = "hyperliquid", "testnet"
    state = GatewayState(settings, testnet, db, adapters={"testnet": testnet},
                         logger=EventLogger("gw-tvdel-pos", settings.logs_dir, db=db))
    with TestClient(build_app(state)) as c:
        _create(c, sid="tv_pos", env="testnet")
        # símbolo escopado pela estratégia (order) + posição aberta na venue.
        db.insert("orders", {
            "cloid": "0xpos1", "strategy_id": "tv_pos", "symbol": "BTC",
            "side": "buy", "type": "market", "size": 0.01, "status": "filled",
            "created_at": utcnow(),
        })
        testnet._positions["BTC"] = Position(symbol="BTC", size=0.01,
                                             entry_price=100_000.0)
        r = c.post("/control/tv/strategies/tv_pos/delete", headers=HDR).json()
    assert r["ok"] is False and r["reason"] == "posicao_aberta"
    assert db.query("SELECT id FROM strategies WHERE id='tv_pos'")


def _seed_tv_data(db, sid):
    """Semeia os dados do módulo TV que a exclusão deve apagar. Retorna signal_id."""
    sig = db.insert("tv_signals", {
        "source": "test", "strategy_id": sid, "environment": "testnet",
        "raw_payload": "{}", "state": "BLOCKED"})
    db.insert("tv_signal_decisions", {
        "signal_id": sig, "outcome": "BLOCKED", "block_code": "STRATEGY_DISABLED"})
    db.insert("tv_incidents", {"signal_id": sig, "type": "TEST", "details": "{}"})
    db.insert("tv_queue", {
        "signal_id": sig, "status": "done", "created_at": utcnow(),
        "updated_at": utcnow()})
    return sig


def _assert_tv_purged(db, sid, sig):
    assert db.query("SELECT id FROM tv_signals WHERE strategy_id=?", (sid,)) == []
    assert db.query("SELECT id FROM tv_signal_decisions WHERE signal_id=?", (sig,)) == []
    assert db.query("SELECT id FROM tv_incidents WHERE signal_id=?", (sig,)) == []
    assert db.query("SELECT id FROM tv_queue WHERE signal_id=?", (sig,)) == []
    assert db.query("SELECT strategy_id FROM tv_strategy_meta WHERE strategy_id=?",
                    (sid,)) == []
    assert db.query("SELECT version FROM tv_strategy_versions WHERE strategy_id=?",
                    (sid,)) == []
    # some da view operacional (INNER JOIN com meta, que foi apagada)
    assert db.query("SELECT strategy_id FROM tv_strategies WHERE strategy_id=?",
                    (sid,)) == []


def test_delete_hard_removes_strategies_row_when_no_execution(client, gateway_state) -> None:
    db = gateway_state.db
    _create(client, sid="tv_clean")
    sig = _seed_tv_data(db, "tv_clean")

    r = client.post("/control/tv/strategies/tv_clean/delete", headers=HDR).json()
    assert r["ok"] is True and r["deleted"] == "tv_clean" and r["outcome"] == "deleted"

    _assert_tv_purged(db, "tv_clean", sig)
    # sem execução ⇒ linha strategies HARD-DELETED
    assert db.query("SELECT id FROM strategies WHERE id='tv_clean'") == []


def test_delete_archives_and_preserves_fills_orders(client, gateway_state) -> None:
    db = gateway_state.db
    _create(client, sid="tv_gone")
    sig = _seed_tv_data(db, "tv_gone")
    # registros REAIS de execução que devem PERMANECER
    db.insert("orders", {
        "cloid": "0xkeep1", "strategy_id": "tv_gone", "symbol": "BTC",
        "side": "buy", "type": "market", "size": 0.01, "status": "filled",
        "created_at": utcnow()})
    db.insert("fills", {
        "cloid": "0xkeep1", "strategy_id": "tv_gone", "symbol": "BTC",
        "side": "buy", "price": 100_000.0, "size": 0.01, "fee": 0.45,
        "ts": utcnow()})

    r = client.post("/control/tv/strategies/tv_gone/delete", headers=HDR).json()
    assert r["ok"] is True and r["outcome"] == "archived"

    _assert_tv_purged(db, "tv_gone", sig)
    # com execução: a linha strategies é ARQUIVADA (FK preservada), fora da view TV
    row = db.query("SELECT status FROM strategies WHERE id='tv_gone'")
    assert row and row[0]["status"] == "archived"
    # fills/orders PRESERVADOS (base do ledger/reconciliação)
    assert len(db.query("SELECT cloid FROM orders WHERE cloid='0xkeep1'")) == 1
    assert len(db.query("SELECT cloid FROM fills WHERE cloid='0xkeep1'")) == 1
