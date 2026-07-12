"""Validação funcional da F1 (protocolo §8.4.1, passo 4): brackets SL/TP.

Cobre o comportamento NOVO, ativado só quando `stop_loss`/`take_profit` chegam
no `IntentRequest` — o caminho sem esses campos é o da baseline de regressão
(`test_intent_regression.py`), que continua verde sem edição.

T10  gatilho SL colocado (reduce_only, lado de fechamento, posição segue aberta)
T11  grupo feliz entrada+SL+TP (duas pernas colocadas)
T12  short: pernas de gatilho no lado oposto (buy) e posição negativa
T13  STOP rejeitado ⇒ rollback: posição fechada + INCIDENT_UNPROTECTED_POSITION
T13b TP-only sem stop ⇒ posição protegida, SEM rollback (default confirmado)

Determinístico sobre o PaperAdapter (gatilho fica resting, não preenche). O
canário de venue na testnet real é passo SEPARADO de aceite (operador).
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from engine.core.logger import EventLogger
from engine.exchanges.base import OrderResult
from engine.exchanges.paper import PaperAdapter
from engine.gateway.server import GatewayState, build_app
from tests.conftest import register_strategy


def _state(settings, db, adapter, name="gw-brackets"):
    return GatewayState(settings, adapter, db,
                        logger=EventLogger(name, settings.logs_dir, db=db))


class StopFailAdapter(PaperAdapter):
    """PaperAdapter cujo gatilho de STOP sempre falha — para o ramo de rollback."""

    name = "hyperliquid"
    network = "testnet"

    def place_trigger(self, symbol: str, side: str, size: float, trigger_px: float,
                      tpsl: str, *, reduce_only: bool = True,
                      cloid: str | None = None) -> OrderResult:
        if tpsl == "sl":
            return OrderResult(ok=False, cloid=cloid, status="rejected",
                               error="trigger rejected")
        return super().place_trigger(symbol, side, size, trigger_px, tpsl,
                                     reduce_only=reduce_only, cloid=cloid)


# -- T10 ------------------------------------------------------------------------
def test_stop_loss_only_places_reduce_only_trigger(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "tv_sl", module="tradingview")
    r = client.post("/intent", json={
        "strategy_id": "tv_sl", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0, "stop_loss": 95_000.0,
    }).json()
    assert r["ok"] is True and r["status"] == "filled"
    # entrada (market buy) + gatilho SL (sell, trigger, reduce_only).
    entry, sl = paper.placed_orders[-2], paper.placed_orders[-1]
    assert entry.order_type == "market" and entry.side == "buy"
    assert sl.order_type == "trigger" and sl.side == "sell"
    assert sl.reduce_only is True and sl.price == 95_000.0
    assert r["brackets"]["rolled_back"] is False
    assert r["brackets"]["legs"]["sl"]["ok"] is True
    # gatilho fica resting ⇒ posição long segue aberta.
    assert paper.positions()[0].size == 0.001
    rows = gateway_state.db.query(
        "SELECT type FROM orders WHERE strategy_id = 'tv_sl' ORDER BY id")
    assert [x["type"] for x in rows] == ["market", "trigger"]


# -- T11 ------------------------------------------------------------------------
def test_entry_with_sl_and_tp_happy_group(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "tv_bracket", module="tradingview")
    r = client.post("/intent", json={
        "strategy_id": "tv_bracket", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0, "stop_loss": 95_000.0, "take_profit": 110_000.0,
    }).json()
    assert r["ok"] is True and r["status"] == "filled"
    legs = r["brackets"]["legs"]
    assert legs["sl"]["ok"] is True and legs["tp"]["ok"] is True
    assert r["brackets"]["rolled_back"] is False
    # entrada + 2 gatilhos, ambos no lado de fechamento (sell).
    triggers = [o for o in paper.placed_orders if o.order_type == "trigger"]
    assert len(triggers) == 2
    assert all(t.side == "sell" and t.reduce_only for t in triggers)
    rows = gateway_state.db.query(
        "SELECT type FROM orders WHERE strategy_id = 'tv_bracket'")
    assert sorted(x["type"] for x in rows) == ["market", "trigger", "trigger"]


# -- T12 ------------------------------------------------------------------------
def test_short_bracket_uses_opposite_closing_side(client, gateway_state, paper) -> None:
    register_strategy(gateway_state.db, "tv_short", module="tradingview")
    r = client.post("/intent", json={
        "strategy_id": "tv_short", "symbol": "BTC", "side": "sell",
        "notional_usd": 100.0, "stop_loss": 105_000.0, "take_profit": 90_000.0,
    }).json()
    assert r["ok"] is True
    triggers = [o for o in paper.placed_orders if o.order_type == "trigger"]
    assert len(triggers) == 2 and all(t.side == "buy" for t in triggers)
    # short aberto ⇒ posição negativa.
    assert paper.positions()[0].size == -0.001


# -- T13 ------------------------------------------------------------------------
def test_stop_rejected_rolls_back_and_emits_incident(settings, db) -> None:
    adapter = StopFailAdapter(prices={"BTC": 100_000.0})
    state = _state(settings, db, adapter, "gw-rollback")
    register_strategy(db, "tv_roll", module="tradingview")
    with TestClient(build_app(state)) as c:
        r = c.post("/intent", json={
            "strategy_id": "tv_roll", "symbol": "BTC", "side": "buy",
            "notional_usd": 100.0, "stop_loss": 95_000.0, "take_profit": 110_000.0,
        }).json()
    assert r["ok"] is False
    assert r["status"] == "rolled_back"
    assert r["reason"] == "INCIDENT_UNPROTECTED_POSITION"
    assert r["brackets"]["rolled_back"] is True
    # posição foi fechada a mercado (reduce_only) ⇒ nenhuma posição aberta.
    assert adapter.positions() == []
    # incidente persistido em events (level critical).
    ev = db.query(
        "SELECT event_type, level FROM events "
        "WHERE event_type = 'incident.unprotected_position'")
    assert len(ev) == 1 and ev[0]["level"] == "critical"


# -- T13b -----------------------------------------------------------------------
def test_take_profit_only_is_protected_no_rollback(client, gateway_state, paper) -> None:
    """Default confirmado: TP-only NÃO exige stop e não dispara rollback."""
    register_strategy(gateway_state.db, "tv_tp", module="tradingview")
    r = client.post("/intent", json={
        "strategy_id": "tv_tp", "symbol": "BTC", "side": "buy",
        "notional_usd": 100.0, "take_profit": 110_000.0,
    }).json()
    assert r["ok"] is True and r["status"] == "filled"
    assert r["brackets"]["rolled_back"] is False
    assert "sl" not in r["brackets"]["legs"]
    assert r["brackets"]["legs"]["tp"]["ok"] is True
    assert paper.positions()[0].size == 0.001
