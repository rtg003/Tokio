"""F3 — escrita autenticada de estratégias TradingView (wizard §4 + modal §5).

Superfície de escrita BOUNDED e testnet-first:
* criação nasce 'draft' (disabled-first) ⇒ handshake bate STRATEGY_DISABLED;
* segredos gerados no servidor, devolvidos UMA vez; só o hash persiste;
* ativação promove draft→active (mainnet exige adapter configurado);
* auditoria: versão 1 gravada em tv_strategy_versions (aparece nos Logs).

Nenhum destes toca o hot path de /intent nem o gate de status do Copy Trade.
"""
from __future__ import annotations

from engine.tv.store import sha256_hex

HDR = {"X-Control-Token": "test-token"}


def _create(client, sid="tv_gap", env="testnet", **extra):
    body = {"strategy_id": sid, "name": "Gap Fade", "environment": env,
            "symbols_allowed": ["BTC"], "timeframes_allowed": ["4h"],
            "allocation_usd": 1000, "stop_loss_pct": 1.2, **extra}
    return client.post("/control/tv/strategies", json=body, headers=HDR)


# -- criação --------------------------------------------------------------------
def test_create_is_draft_and_returns_secret_once(client, gateway_state) -> None:
    r = _create(client).json()
    assert r["ok"] is True and r["status"] == "draft"
    assert r["webhook_url"].endswith(r["webhook_url"].rsplit("/", 1)[1])
    assert "/tv/" in r["webhook_url"]
    secret = r["secret"]
    assert secret and r["alert_json"]["secret"] == secret
    # persistência: draft + só o HASH do secret guardado (nunca o claro).
    row = gateway_state.db.query(
        "SELECT s.status, m.secret_hash, m.url_secret_hash "
        "FROM strategies s JOIN tv_strategy_meta m ON m.strategy_id = s.id "
        "WHERE s.id = 'tv_gap'")[0]
    assert row["status"] == "draft"
    assert row["secret_hash"] == sha256_hex(secret)
    assert secret not in row["secret_hash"]
    # versão 1 na auditoria ⇒ vira linha USER na view tv_events.
    ver = gateway_state.db.query(
        "SELECT version, changed_by FROM tv_strategy_versions WHERE strategy_id='tv_gap'")
    assert ver == [{"version": 1, "changed_by": "dashboard_humano"}]


def test_create_rejects_bad_slug_and_duplicates(client) -> None:
    assert _create(client, sid="TV Gap").json()["reason"] == "strategy_id_invalido"
    assert _create(client).json()["ok"] is True
    assert _create(client).json()["reason"] == "strategy_id_em_uso"


def test_create_requires_control_token(client) -> None:
    r = client.post("/control/tv/strategies", json={"strategy_id": "tv_x"})
    assert r.status_code == 401


# -- ativação -------------------------------------------------------------------
def test_activate_promotes_draft_to_active(client, gateway_state) -> None:
    _create(client, sid="tv_act")
    r = client.post("/control/tv/strategies/tv_act/activate", headers=HDR).json()
    assert r["ok"] is True and r["status"] == "active"
    st = gateway_state.db.query("SELECT status FROM strategies WHERE id='tv_act'")[0]
    assert st["status"] == "active"


def test_activate_unknown_is_404(client) -> None:
    assert client.post("/control/tv/strategies/nope/activate", headers=HDR).status_code == 404


def test_activate_mainnet_without_adapter_is_refused(client, gateway_state) -> None:
    # paper adapter é testnet-only ⇒ mainnet não configurado.
    _create(client, sid="tv_main", env="mainnet")
    r = client.post("/control/tv/strategies/tv_main/activate", headers=HDR).json()
    assert r["ok"] is False and r["reason"] == "mainnet_nao_configurado"
    st = gateway_state.db.query("SELECT status FROM strategies WHERE id='tv_main'")[0]
    assert st["status"] == "draft"  # não promovido


# -- handshake ------------------------------------------------------------------
def test_handshake_empty_then_sees_signal(client, gateway_state) -> None:
    _create(client, sid="tv_hs")
    empty = client.get("/api/tv/strategies/tv_hs/handshake").json()
    assert empty["received"] is False and empty["signal"] is None
    # simula chegada do sinal de teste (o receiver faria isso em produção).
    sid_row = gateway_state.db.insert("tv_signals", {
        "source": "test", "strategy_id": "tv_hs", "environment": "testnet",
        "raw_payload": "{}", "state": "BLOCKED"})
    gateway_state.db.insert("tv_signal_decisions", {
        "signal_id": sid_row, "outcome": "BLOCKED", "block_code": "STRATEGY_DISABLED"})
    got = client.get("/api/tv/strategies/tv_hs/handshake").json()
    assert got["received"] is True
    assert got["signal"]["block_code"] == "STRATEGY_DISABLED"
