"""F2 — gestão autenticada de estratégias TV pelo Hermes (§9).

O Hermes tem autonomia total SOBRE ESTRATÉGIAS (nunca no hot path) via a MESMA
API de controle da dashboard. A identificação `actor: "hermes"` é o único ponto
de diferença: ela vira `changed_by='hermes'` na auditoria e, pela view tv_events,
a mudança aparece como evento HERMES (controle compensatório).

Perímetro FORA do alcance do Hermes (por construção, sem endpoint aqui): kill
switch global, caps globais, wallets/credenciais e a promoção de ambiente
(testnet↔mainnet, que é a fonte de verdade em tv_strategy_meta).
"""
from __future__ import annotations

HDR = {"X-Control-Token": "test-token"}


def _create(client, sid="tv_h", env="testnet", **extra):
    body = {"strategy_id": sid, "name": "Hermes Strat", "environment": env,
            "symbols_allowed": ["BTC"], "timeframes_allowed": ["4h"],
            "allocation_usd": 1000, "stop_loss_pct": 1.2, "actor": "hermes", **extra}
    return client.post("/control/tv/strategies", json=body, headers=HDR)


def test_hermes_create_is_audited_as_hermes(client, gateway_state) -> None:
    assert _create(client, sid="tv_hc").json()["ok"] is True
    ver = gateway_state.db.query(
        "SELECT version, changed_by FROM tv_strategy_versions WHERE strategy_id='tv_hc'")
    assert ver == [{"version": 1, "changed_by": "hermes"}]
    # a view tv_events transforma changed_by='hermes' em kind HERMES.
    ev = gateway_state.db.query(
        "SELECT kind FROM tv_events WHERE ref_id='tv_hc' AND kind IN ('HERMES','USER')")
    assert {"kind": "HERMES"} in ev


def test_config_edit_bumps_version_and_audits(client, gateway_state) -> None:
    _create(client, sid="tv_edit")
    r = client.post("/control/tv/strategies/tv_edit/config", headers=HDR,
                    json={"actor": "hermes", "allocation_usd": 500,
                          "stop_loss_pct": 0.9, "justification": "reduzir risco"}).json()
    assert r["ok"] is True and r["version"] == 2
    # config viva atualizada + versão 2 auditada como HERMES.
    cfg = gateway_state.db.query(
        "SELECT config_snapshot FROM strategies WHERE id='tv_edit'")[0]["config_snapshot"]
    assert '"allocation_usd": 500' in cfg
    v2 = gateway_state.db.query(
        "SELECT version, changed_by, change_summary FROM tv_strategy_versions "
        "WHERE strategy_id='tv_edit' ORDER BY version DESC")[0]
    assert v2["version"] == 2 and v2["changed_by"] == "hermes"
    assert v2["change_summary"] == "reduzir risco"
    # meta.version acompanha o bump.
    mv = gateway_state.db.query(
        "SELECT version FROM tv_strategy_meta WHERE strategy_id='tv_edit'")[0]
    assert mv["version"] == 2


def test_config_edit_unknown_is_404(client) -> None:
    r = client.post("/control/tv/strategies/nope/config", headers=HDR,
                    json={"actor": "hermes", "allocation_usd": 10})
    assert r.status_code == 404


def test_pause_sets_paused_and_logs_actor(client, gateway_state) -> None:
    _create(client, sid="tv_pause")
    client.post("/control/tv/strategies/tv_pause/activate", headers=HDR,
                json={"actor": "hermes"})
    r = client.post("/control/tv/strategies/tv_pause/pause", headers=HDR,
                    json={"actor": "hermes"}).json()
    assert r["ok"] is True and r["status"] == "paused"
    st = gateway_state.db.query("SELECT status FROM strategies WHERE id='tv_pause'")[0]
    assert st["status"] == "paused"
    # pausa é reversível pela ativação (paused→active).
    back = client.post("/control/tv/strategies/tv_pause/activate", headers=HDR,
                       json={"actor": "hermes"}).json()
    assert back["status"] == "active"


def test_management_requires_control_token(client) -> None:
    assert client.post("/control/tv/strategies/x/config", json={}).status_code == 401
    assert client.post("/control/tv/strategies/x/pause", json={}).status_code == 401


def test_mainnet_change_emits_notification_event(client, gateway_state) -> None:
    # cria mainnet (draft não ativa sem adapter, mas edição de config é permitida).
    _create(client, sid="tv_mn", env="mainnet")
    client.post("/control/tv/strategies/tv_mn/config", headers=HDR,
                json={"actor": "hermes", "allocation_usd": 300,
                      "justification": "ajuste mainnet"})
    notif = gateway_state.db.query(
        "SELECT COUNT(*) AS n FROM events "
        "WHERE strategy_id='tv_mn' AND event_type='tv.notify.mainnet_change'")[0]
    assert notif["n"] >= 1
