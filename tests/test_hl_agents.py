"""Provisionamento de agent wallets HL (keyring): typed data == V1 (SDK 0.24.0),
prepare/activate/revoke, unique-active por env, reload_adapter e auth de endpoint."""
from __future__ import annotations

import pytest

from engine.core.db import Database
from engine.gateway import hl_agents


SECRET = "unit-test-keyring-secret"
MASTER = "0x1111111111111111111111111111111111111111"


@pytest.fixture()
def kdb(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setenv("TOKIO_KEYRING_SECRET", SECRET)
    d = Database(tmp_path / "k.db")
    d.migrate()
    # activate não deve tocar a rede nos testes.
    monkeypatch.setattr(hl_agents, "_fetch_valid_until", lambda *a, **k: None)
    yield d
    d.close()


def _ok_submit(env, action, sig, nonce):
    return {"status": "ok", "response": {"type": "default"}}


# -- V1: equivalência de assinatura com o SDK instalado ---------------------
def test_typed_data_signs_identically_to_sdk() -> None:
    """A prova mais forte de "typed data == V1": assinar o typed data que
    entregamos à MetaMask produz a MESMA assinatura que o `sign_agent` do SDK."""
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from eth_utils import to_hex
    from hyperliquid.utils.signing import sign_agent

    acct = Account.create()
    agent_addr = "0x000000000000000000000000000000000000dEaD"
    name = "engine_gateway"
    nonce = 1_700_000_000_000

    for env, is_mainnet in (("testnet", False), ("mainnet", True)):
        sdk_action = {
            "type": "approveAgent",
            "agentAddress": agent_addr,
            "agentName": name,
            "nonce": nonce,
        }
        sdk_sig = sign_agent(acct, sdk_action, is_mainnet)

        td = hl_agents.build_typed_data(env, agent_addr, name, nonce)
        signed = acct.sign_message(encode_typed_data(full_message=td))
        mine = {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}
        assert mine == sdk_sig, f"assinatura divergiu do SDK em {env}"


def test_action_matches_sdk_fields() -> None:
    act = hl_agents.build_action("testnet", "0xAbc", "engine_gateway", 42)
    assert act["type"] == "approveAgent"
    assert act["signatureChainId"] == "0x66eee"       # V2 opção (a)
    assert act["hyperliquidChain"] == "Testnet"
    assert act["agentAddress"] == "0xAbc"
    assert act["nonce"] == 42


# -- prepare ----------------------------------------------------------------
def test_prepare_persists_pending_without_leaking_key(kdb: Database) -> None:
    res = hl_agents.prepare(kdb, "testnet", MASTER)
    assert res["ok"] and res["env"] == "testnet"
    assert res["master_address"] == MASTER
    assert "typed_data" in res and "nonce" in res
    # A resposta NÃO carrega chave privada.
    assert "privkey" not in res and "agent_key" not in res

    rows = kdb.query("SELECT * FROM hl_agents WHERE agent_address = ?", (res["agent_address"],))
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "pending"
    # privkey_enc é cifrado (não contém a chave em claro nem é vazio).
    assert row["privkey_enc"] and not row["privkey_enc"].startswith("0x")

    audit = kdb.query("SELECT * FROM hl_auth_audit WHERE action = 'agent_prepare'")
    assert len(audit) == 1 and audit[0]["env"] == "testnet"


def test_prepare_supersedes_previous_pending(kdb: Database) -> None:
    a = hl_agents.prepare(kdb, "testnet", MASTER)
    b = hl_agents.prepare(kdb, "testnet", MASTER)
    pend = kdb.query("SELECT agent_address FROM hl_agents WHERE status = 'pending'")
    assert len(pend) == 1 and pend[0]["agent_address"] == b["agent_address"]
    assert a["agent_address"] != b["agent_address"]


# -- activate ---------------------------------------------------------------
def test_activate_ok_marks_active_and_resolves_key(kdb: Database) -> None:
    prep = hl_agents.prepare(kdb, "testnet", MASTER)
    res = hl_agents.activate(
        kdb, "testnet", prep["agent_address"], "0x" + "ab" * 65, prep["nonce"],
        submit=_ok_submit,
    )
    assert res["ok"] and res["status"] == "active"
    resolved = hl_agents.resolve_active_key(kdb, "testnet")
    assert resolved is not None
    master, key = resolved
    assert master == MASTER
    assert key.startswith("0x") and len(key) == 66  # 32-byte agent key


def test_activate_error_keeps_pending(kdb: Database) -> None:
    prep = hl_agents.prepare(kdb, "testnet", MASTER)

    def _err_submit(env, action, sig, nonce):
        return {"status": "err", "response": "Insufficient balance"}

    res = hl_agents.activate(
        kdb, "testnet", prep["agent_address"], "0x" + "ab" * 65, prep["nonce"],
        submit=_err_submit,
    )
    assert res["ok"] is False
    rows = kdb.query("SELECT status FROM hl_agents WHERE agent_address = ?", (prep["agent_address"],))
    assert rows[0]["status"] == "pending"
    assert hl_agents.resolve_active_key(kdb, "testnet") is None


def test_rotation_revokes_previous_active(kdb: Database) -> None:
    p1 = hl_agents.prepare(kdb, "testnet", MASTER)
    hl_agents.activate(kdb, "testnet", p1["agent_address"], "0x" + "ab" * 65, p1["nonce"], submit=_ok_submit)
    p2 = hl_agents.prepare(kdb, "testnet", MASTER)
    hl_agents.activate(kdb, "testnet", p2["agent_address"], "0x" + "cd" * 65, p2["nonce"], submit=_ok_submit)

    active = kdb.query("SELECT agent_address FROM hl_agents WHERE env='testnet' AND status='active'")
    assert len(active) == 1 and active[0]["agent_address"] == p2["agent_address"]
    revoked = kdb.query("SELECT agent_address FROM hl_agents WHERE status='revoked'")
    assert p1["agent_address"] in {r["agent_address"] for r in revoked}


def test_unique_active_index_enforced(kdb: Database) -> None:
    import sqlite3

    hl_agents.prepare(kdb, "testnet", MASTER)
    a1 = kdb.query("SELECT agent_address FROM hl_agents WHERE status='pending'")[0]["agent_address"]
    hl_agents.activate(kdb, "testnet", a1, "0x" + "ab" * 65, 1, submit=_ok_submit)
    # Um segundo active para o MESMO env deve violar o índice único parcial.
    with pytest.raises(sqlite3.IntegrityError):
        kdb.insert("hl_agents", {
            "id": "dup", "env": "testnet", "master_address": MASTER,
            "agent_address": "0xdup", "agent_name": "engine_gateway",
            "privkey_enc": "x", "status": "active", "created_at": "now",
        })


# -- revoke -----------------------------------------------------------------
def test_revoke_marks_revoked(kdb: Database) -> None:
    prep = hl_agents.prepare(kdb, "testnet", MASTER)
    hl_agents.activate(kdb, "testnet", prep["agent_address"], "0x" + "ab" * 65, prep["nonce"], submit=_ok_submit)
    res = hl_agents.revoke(kdb, "testnet")
    assert res["ok"] and res["agent_address"] == prep["agent_address"]
    assert hl_agents.resolve_active_key(kdb, "testnet") is None
    assert hl_agents.revoke(kdb, "testnet")["ok"] is False  # nada mais ativo


# -- list (sem segredos) ----------------------------------------------------
def test_list_agents_has_no_secret(kdb: Database) -> None:
    hl_agents.prepare(kdb, "testnet", MASTER)
    agents = hl_agents.list_agents(kdb)
    assert len(agents) == 1
    assert "privkey_enc" not in agents[0]


# -- reload_adapter (isolado de rede via monkeypatch do builder) ------------
def test_reload_adapter_swaps_and_removes(gateway_state, monkeypatch: pytest.MonkeyPatch) -> None:
    import engine.gateway.server as server

    class _FakeAdapter:
        name = "hyperliquid"
        network = "testnet"
        account_address = "0xMASTER"

        def __init__(self):
            self.subscribed = False

        def subscribe_own_fills(self, cb):
            self.subscribed = True

        def close(self):
            pass

    fake = _FakeAdapter()
    monkeypatch.setattr(server, "_build_env_adapter", lambda s, d, env: fake)
    assert gateway_state.reload_adapter("testnet") is True
    assert gateway_state.adapters["testnet"] is fake
    assert fake.subscribed is True

    # Sem signer → remove o adapter do ambiente (revogação/expiração).
    monkeypatch.setattr(server, "_build_env_adapter", lambda s, d, env: None)
    assert gateway_state.reload_adapter("testnet") is False
    assert "testnet" not in gateway_state.adapters


# -- endpoints: auth de controle -------------------------------------------
def test_endpoints_require_control_token(client) -> None:
    # Leitura é aberta (shape sem chaves).
    r = client.get("/hl/agents")
    assert r.status_code == 200
    assert "agents" in r.json()
    # Mutação sem token → 401.
    r = client.post("/control/hl/agents/prepare",
                    json={"env": "testnet", "master_address": MASTER})
    assert r.status_code == 401
