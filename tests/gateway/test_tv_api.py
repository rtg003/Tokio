"""F3 (dashboard) — endpoints read-only do módulo TV no gateway.

Cobrem os dois GETs aditivos que alimentam a tela /trading-view a partir das
views da migração 0019, respeitando o isolamento de observabilidade (§5.1):

* /api/tv/strategies  — view tv_strategies; NUNCA expõe secret_hash/url_secret_hash;
                        filtro opcional ?environment=testnet|mainnet.
* /api/tv/events      — view tv_events; ordena por ts DESC; ?kind= e ?before= cursor;
                        só enxerga eventos do módulo TV (não vaza events genéricos).

Read-only: nenhum toca o hot path de /intent ou /cancel.
"""
from __future__ import annotations

import hashlib
import json


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _register_tv(db, sid: str, *, environment: str = "testnet",
                 status: str = "active", secret: str = "psecret",
                 url_secret: str = "urlsecret") -> None:
    db.upsert("strategies", {
        "id": sid, "module": "tradingview", "name": sid, "status": status,
        "config_snapshot": json.dumps({"strategy_id": sid}), "thresholds": "{}",
    }, ("id",))
    db.upsert("tv_strategy_meta", {
        "strategy_id": sid, "environment": environment,
        "secret_hash": _sha(secret), "url_secret_hash": _sha(url_secret),
        "version": 1,
    }, ("strategy_id",))


# -- /api/tv/strategies ---------------------------------------------------------
def test_tv_strategies_excludes_secrets_and_is_module_scoped(client, gateway_state) -> None:
    _register_tv(gateway_state.db, "tv_a", environment="testnet")
    # estratégia de OUTRO módulo não deve aparecer (isolamento §5.1).
    gateway_state.db.upsert("strategies", {
        "id": "ct_x", "module": "copy_trade", "name": "ct_x", "status": "active",
    }, ("id",))

    rows = client.get("/api/tv/strategies").json()
    ids = {r["strategy_id"] for r in rows}
    assert ids == {"tv_a"}
    only = rows[0]
    # segredos NUNCA no payload.
    assert "secret_hash" not in only and "url_secret_hash" not in only
    assert only["environment"] == "testnet" and only["version"] == 1


def test_tv_strategies_filters_by_environment(client, gateway_state) -> None:
    _register_tv(gateway_state.db, "tv_test", environment="testnet")
    _register_tv(gateway_state.db, "tv_main", environment="mainnet")

    testnet = client.get("/api/tv/strategies", params={"environment": "testnet"}).json()
    mainnet = client.get("/api/tv/strategies", params={"environment": "mainnet"}).json()
    assert {r["strategy_id"] for r in testnet} == {"tv_test"}
    assert {r["strategy_id"] for r in mainnet} == {"tv_main"}
    # filtro inválido cai no ramo sem WHERE ⇒ retorna ambas.
    all_rows = client.get("/api/tv/strategies", params={"environment": "bogus"}).json()
    assert {r["strategy_id"] for r in all_rows} == {"tv_test", "tv_main"}


# -- /api/tv/events -------------------------------------------------------------
def test_tv_events_surfaces_hermes_version_and_stays_isolated(client, gateway_state) -> None:
    db = gateway_state.db
    _register_tv(db, "tv_a")
    db.upsert("strategies", {
        "id": "ct_x", "module": "copy_trade", "name": "ct_x", "status": "active",
    }, ("id",))
    # alteração Hermes ⇒ vira linha HERMES na view.
    db.execute(
        "INSERT INTO tv_strategy_versions (strategy_id, version, config, "
        "changed_by, change_summary, created_at) VALUES (?,?,?,?,?,?)",
        ("tv_a", 2, "{}", "hermes", "ajuste de sizing", "2026-07-12T10:00:00.000Z"))
    # evento genérico de OUTRA estratégia NÃO pode aparecer (isolamento §5.1).
    db.execute(
        "INSERT INTO events (ts, strategy_id, event_type, level, payload) "
        "VALUES (?,?,?,?,?)",
        ("2026-07-12T10:01:00.000Z", "ct_x", "order.filled", "info", "{}"))

    rows = client.get("/api/tv/events").json()
    kinds = [r["kind"] for r in rows]
    assert "HERMES" in kinds
    hermes = next(r for r in rows if r["kind"] == "HERMES")
    assert hermes["ref_id"] == "tv_a" and "ajuste de sizing" in hermes["summary"]
    # nenhuma linha do copy_trade vazou.
    assert all("order.filled" not in (r["summary"] or "") for r in rows)


def test_tv_events_kind_filter_and_before_cursor(client, gateway_state) -> None:
    db = gateway_state.db
    _register_tv(db, "tv_a")
    for v, ts in ((2, "2026-07-12T10:00:00.000Z"), (3, "2026-07-12T11:00:00.000Z")):
        db.execute(
            "INSERT INTO tv_strategy_versions (strategy_id, version, config, "
            "changed_by, change_summary, created_at) VALUES (?,?,?,?,?,?)",
            ("tv_a", v, "{}", "hermes", f"v{v}", ts))

    hermes = client.get("/api/tv/events", params={"kind": "HERMES"}).json()
    assert len(hermes) == 2
    # DESC ⇒ mais recente primeiro.
    assert hermes[0]["ts"] > hermes[1]["ts"]
    # cursor: só o que é anterior ao ts mais recente.
    older = client.get("/api/tv/events",
                       params={"kind": "HERMES", "before": "2026-07-12T11:00:00.000Z"}).json()
    assert [r["ts"] for r in older] == ["2026-07-12T10:00:00.000Z"]
