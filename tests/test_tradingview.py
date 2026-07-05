"""Phase 4 acceptance: 2 simultaneous sub-strategies without collision; an
exception inside one sub-strategy never takes the webhook server down."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from engine.strategies.tradingview.webhook_server import build_app

TOKEN = "secret-tv-token"
STRATS = Path(__file__).resolve().parent / "fixtures/tv_strategies"


class RecordingGateway:
    def __init__(self) -> None:
        self.intents: list[dict[str, Any]] = []
        self.fail_for: set[str] = set()
        self.ledger_response: dict[str, Any] = {}
        outer = self

        class _C:
            def get(self, path: str):
                class R:
                    def json(_self) -> dict[str, Any]:
                        return outer.ledger_response
                return R()

        self._client = _C()

    def send_intent(self, **payload: Any) -> dict[str, Any]:
        if payload["strategy_id"] in self.fail_for:
            raise RuntimeError("boom inside sub-strategy")
        self.intents.append(payload)
        return {"ok": True, "cloid": "0xtv", "status": "dry_run"}


@pytest.fixture()
def tv(settings, db):
    gw = RecordingGateway()
    app = build_app(settings=settings, db=db, gateway=gw,
                    strategies_dir=STRATS, webhook_token=TOKEN)
    return TestClient(app), gw, db


def alert(**over: Any) -> dict[str, Any]:
    return {"token": TOKEN, "strategy_id": "tv_gap_fade", "symbol": "BTC",
            "action": "buy", **over}


def test_both_substrategies_registered_dry_run(tv) -> None:
    client, _, db = tv
    rows = db.query("SELECT id, status FROM strategies WHERE module = 'tradingview' ORDER BY id")
    assert [r["id"] for r in rows] == ["tv_funding_extreme", "tv_gap_fade"]
    assert all(r["status"] == "dry_run" for r in rows)
    assert client.get("/health").json()["substrategies"] == [
        "tv_funding_extreme", "tv_gap_fade"]


def test_invalid_token_rejected_and_logged(tv) -> None:
    client, gw, db = tv
    r = client.post("/webhook", json=alert(token="wrong"))
    assert r.status_code == 401
    assert gw.intents == []
    assert db.query("SELECT 1 FROM events WHERE event_type = 'signal.invalid_token'")


def test_malformed_payload_rejected_and_logged(tv) -> None:
    client, gw, db = tv
    r = client.post("/webhook", json={"token": TOKEN, "action": "yolo"})
    assert r.status_code == 422
    assert gw.intents == []
    assert db.query("SELECT 1 FROM events WHERE event_type = 'signal.malformed_payload'")


def test_routing_by_strategy_id_no_collision(tv) -> None:
    client, gw, _ = tv
    client.post("/webhook", json=alert())
    client.post("/webhook", json=alert(strategy_id="tv_funding_extreme",
                                       symbol="ETH", action="sell"))
    assert len(gw.intents) == 2
    assert gw.intents[0]["strategy_id"] == "tv_gap_fade"
    assert gw.intents[0]["notional_usd"] == 25.0        # default do config
    assert gw.intents[1]["strategy_id"] == "tv_funding_extreme"
    assert gw.intents[1]["side"] == "sell"
    assert gw.intents[1]["notional_usd"] == 20.0


def test_sizing_hint_capped_by_config(tv) -> None:
    client, gw, _ = tv
    client.post("/webhook", json=alert(sizing={"mode": "notional_usd", "value": 500.0}))
    assert gw.intents[0]["notional_usd"] == 100.0       # cap max_notional_usd


def test_symbol_not_allowed_is_skipped(tv) -> None:
    client, gw, _ = tv
    r = client.post("/webhook", json=alert(symbol="DOGE"))
    assert r.json()["reason"] == "symbol_not_allowed"
    assert gw.intents == []


def test_unknown_strategy_404(tv) -> None:
    client, _, _ = tv
    r = client.post("/webhook", json=alert(strategy_id="tv_ghost"))
    assert r.status_code == 404


def test_close_action_uses_ledger_position(tv) -> None:
    client, gw, _ = tv
    gw.ledger_response = {"tv_gap_fade": {"positions": {"BTC": {"size": 0.002}}}}
    client.post("/webhook", json=alert(action="close"))
    intent = gw.intents[0]
    assert intent["reduce_only"] is True
    assert intent["side"] == "sell" and intent["size"] == 0.002


def test_exception_in_one_substrategy_does_not_kill_server(tv) -> None:
    client, gw, db = tv
    gw.fail_for.add("tv_gap_fade")
    r1 = client.post("/webhook", json=alert())
    assert r1.status_code == 500
    assert r1.json()["reason"] == "substrategy_error"
    assert db.query("SELECT 1 FROM events WHERE event_type = 'strategy.substrategy_error'")
    # server alive and the OTHER sub-strategy still works
    r2 = client.post("/webhook", json=alert(strategy_id="tv_funding_extreme",
                                            symbol="BTC", action="buy"))
    assert r2.status_code == 200 and r2.json()["ok"] is True
