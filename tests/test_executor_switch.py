"""UPDATE-0083 — troca NÃO-DESTRUTIVA do executor por ambiente.

Selecionar no topo do dashboard uma wallet COM agente provisionado troca o
executor daquele ambiente: o `active` anterior vira `standby` (a aprovação
on-chain persiste — dá p/ voltar sem nova assinatura) e o alvo vira `active`.
`POST /control/hl/agents/select` orquestra `set_active` + `reload_adapter`, o que
auto-cura `_expected_wallet[env]` (o guardrail passa a esperar o novo master).
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from engine.core import keyring
from engine.core.db import Database
from engine.core.logger import EventLogger
from engine.gateway import hl_agents
from engine.gateway.server import GatewayState, build_app
import engine.gateway.server as server

SECRET = "unit-test-keyring-secret"
A = "0x" + "83" * 20
B = "0x" + "41" * 20
TOKEN = "test-token"
HEADERS = {"X-Control-Token": TOKEN}


class FakeAdapter:
    def __init__(self, network: str, account_address: str) -> None:
        self.name = "hyperliquid"
        self.network = network
        self.account_address = account_address

    def subscribe_own_fills(self, callback: Any) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture()
def kdb(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setenv("TOKIO_KEYRING_SECRET", SECRET)
    d = Database(tmp_path / "k.db")
    d.migrate()
    yield d
    d.close()


def _seed(db: Database, env: str, master: str, status: str, *,
          key: str, revoked_at: str | None = None) -> None:
    db.insert("hl_agents", {
        "id": f"{env}-{master[-4:]}-{status}",
        "env": env,
        "master_address": master,
        "agent_address": "0xagent" + master[-6:] + status[:2],
        "agent_name": "engine_gateway",
        "privkey_enc": keyring.encrypt(key),
        "status": status,
        "revoked_at": revoked_at,
        "created_at": "2026-07-20T00:00:00Z",
    })


# ---------------------------------------------------------------------------
# 6. set_active: anterior active→standby, alvo →active; resolve_active_key
#    passa a devolver o novo master; audit executor_switch {from,to}.
# ---------------------------------------------------------------------------
def test_set_active_switches_reversibly_and_audits(kdb: Database) -> None:
    _seed(kdb, "testnet", A, "active", key="0x" + "aa" * 32)
    _seed(kdb, "testnet", B, "standby", key="0x" + "bb" * 32)

    res = hl_agents.set_active(kdb, "testnet", B)
    assert res["ok"] and res["from"] == A and res["master_address"] == B

    status = {r["master_address"]: r["status"] for r in kdb.query(
        "SELECT master_address, status FROM hl_agents WHERE env = 'testnet'")}
    assert status[A] == "standby"          # NÃO revogado (reversível)
    assert status[B] == "active"

    resolved = hl_agents.resolve_active_key(kdb, "testnet")
    assert resolved is not None and resolved[0] == B

    audit = kdb.query(
        "SELECT detail FROM hl_auth_audit WHERE action = 'executor_switch'")
    assert len(audit) == 1
    assert A in audit[0]["detail"] and B in audit[0]["detail"]


def test_set_active_noop_when_already_active(kdb: Database) -> None:
    _seed(kdb, "testnet", A, "active", key="0x" + "aa" * 32)
    res = hl_agents.set_active(kdb, "testnet", A)
    assert res["ok"] and res.get("noop") is True
    # sem audit de troca (nada mudou).
    assert kdb.query(
        "SELECT 1 FROM hl_auth_audit WHERE action = 'executor_switch'") == []


# ---------------------------------------------------------------------------
# 7. set_active p/ wallet sem agente provisionado/elegível → HlAgentError;
#    nada muda (o executor anterior permanece).
# ---------------------------------------------------------------------------
def test_set_active_ineligible_raises_and_no_change(kdb: Database) -> None:
    _seed(kdb, "testnet", A, "active", key="0x" + "aa" * 32)
    # B só existe como revogado (aprovação on-chain morta) → não elegível.
    _seed(kdb, "testnet", B, "revoked", key="0x" + "bb" * 32,
          revoked_at="2026-07-19T00:00:00Z")

    with pytest.raises(hl_agents.HlAgentError):
        hl_agents.set_active(kdb, "testnet", B)
    # A segue active; B segue revoked.
    status = {r["master_address"]: r["status"] for r in kdb.query(
        "SELECT master_address, status FROM hl_agents WHERE env = 'testnet'")}
    assert status[A] == "active" and status[B] == "revoked"


def test_set_active_unknown_wallet_raises(kdb: Database) -> None:
    _seed(kdb, "testnet", A, "active", key="0x" + "aa" * 32)
    with pytest.raises(hl_agents.HlAgentError):
        hl_agents.set_active(kdb, "testnet", B)   # nenhum row p/ B


# ---------------------------------------------------------------------------
# 8. POST /control/hl/agents/select → set_active + reload_adapter;
#    _expected_wallet[env] vira o novo master (auto-cura); loga
#    executor.wallet_switched; fills seguintes em B NÃO disparam mismatch.
# ---------------------------------------------------------------------------
def test_select_endpoint_switches_and_autocures_guardrail(
    settings, kdb: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(kdb, "testnet", A, "active", key="0x" + "aa" * 32)
    _seed(kdb, "testnet", B, "standby", key="0x" + "bb" * 32)

    os.environ["GATEWAY_CONTROL_TOKEN"] = TOKEN
    logger = EventLogger("switch-test", settings.logs_dir, db=kdb)
    state = GatewayState(settings, FakeAdapter("testnet", A), kdb,
                         adapters={"testnet": FakeAdapter("testnet", A)},
                         logger=logger)
    assert state._expected_wallet["testnet"] == A.lower()

    # reload reconstrói o adapter já como o NOVO executor (B).
    monkeypatch.setattr(server, "_build_env_adapter",
                        lambda s, d, env: FakeAdapter("testnet", B))
    client = TestClient(build_app(state))

    r = client.post("/control/hl/agents/select",
                    json={"env": "testnet", "master_address": B}, headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["master_address"] == B and body["from"] == A
    assert body["adapter_reloaded"] is True

    # Estado do keyring: B active, A standby (reversível).
    status = {row["master_address"]: row["status"] for row in kdb.query(
        "SELECT master_address, status FROM hl_agents WHERE env = 'testnet'")}
    assert status[B] == "active" and status[A] == "standby"

    # Auto-cura do guardrail: o esperado acompanha o novo executor.
    assert state._expected_wallet["testnet"] == B.lower()

    # Evento operacional persistido.
    ev = kdb.query(
        "SELECT payload FROM events WHERE event_type = 'executor.wallet_switched'")
    assert len(ev) == 1
    assert f'"to": "{B}"' in ev[0]["payload"]

    # Fills seguintes na nova wallet B NÃO disparam mismatch (o esperado é B).
    got = state._master_addr(FakeAdapter("testnet", B), "testnet",
                             strategy_id="ct_after", cloid="c-after")
    assert got == B
    assert kdb.query(
        "SELECT 1 FROM events WHERE event_type = 'executor.wallet_mismatch'") == []


def test_select_endpoint_requires_control_token(settings, kdb: Database) -> None:
    _seed(kdb, "testnet", A, "active", key="0x" + "aa" * 32)
    os.environ["GATEWAY_CONTROL_TOKEN"] = TOKEN
    logger = EventLogger("switch-auth-test", settings.logs_dir, db=kdb)
    state = GatewayState(settings, FakeAdapter("testnet", A), kdb,
                         adapters={"testnet": FakeAdapter("testnet", A)},
                         logger=logger)
    client = TestClient(build_app(state))
    r = client.post("/control/hl/agents/select",
                    json={"env": "testnet", "master_address": A})
    assert r.status_code == 401
