"""Provisionamento e ciclo de vida das agent wallets HL (keyring cifrado).

Modelo (SPEC hl-auth v2.0 §8 + REQUISITO rtg003):

  - O gateway NÃO tem a master key (a master é a MetaMask do operador). Logo não
    dá para usar `Exchange.approve_agent` (que assina com a master). Em vez
    disso: o gateway gera o par do agent, monta o typed data EIP-712
    `approveAgent`, entrega à web p/ a MetaMask assinar, e submete a ação
    assinada ao `/exchange` do ambiente.
  - A agent key gerada é cifrada (AES-256-GCM) e guardada em `hl_agents`; nunca
    volta em resposta nem em log.
  - `master_address` = endereço que assinou o approveAgent = conta em que a
    engine passa a operar naquele ambiente (vira `account_address` do adapter).

V1/V2 (DISCOVERY): typed data copiado 1:1 do `sign_agent`/`approve_agent` do
SDK 0.24.0 instalado. `signatureChainId` fixo em `0x66eee` (decisão V2 opção a,
rtg003 2026-07-09); só `hyperliquidChain` ("Testnet"/"Mainnet") muda o ambiente.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

from engine.core import keyring
from engine.core.db import Database, utcnow

TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"
MAINNET_API_URL = "https://api.hyperliquid.xyz"

# V2 (opção a): a chain da assinatura pode ser qualquer uma; o próprio SDK fixa
# 0x66eee p/ testnet E mainnet. Quem decide o ambiente é `hyperliquidChain`.
SIGNATURE_CHAIN_ID = "0x66eee"

AGENT_NAME_DEFAULT = "engine_gateway"

# Estrutura EXATA dos payload_types do `sign_agent` (signing.py:412-424).
_APPROVE_AGENT_TYPES = [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "agentAddress", "type": "address"},
    {"name": "agentName", "type": "string"},
    {"name": "nonce", "type": "uint64"},
]
_PRIMARY_TYPE = "HyperliquidTransaction:ApproveAgent"


class HlAgentError(RuntimeError):
    pass


def base_url(env: str) -> str:
    return TESTNET_API_URL if env == "testnet" else MAINNET_API_URL


def hyperliquid_chain(env: str) -> str:
    return "Mainnet" if env == "mainnet" else "Testnet"


# -- typed data / ação (V1) -------------------------------------------------
def build_action(env: str, agent_address: str, agent_name: str, nonce_ms: int) -> dict[str, Any]:
    """Ação `approveAgent` submetida ao /exchange — inclui os campos que o
    `sign_user_signed_action` do SDK injeta (signatureChainId, hyperliquidChain)."""
    return {
        "type": "approveAgent",
        "signatureChainId": SIGNATURE_CHAIN_ID,
        "hyperliquidChain": hyperliquid_chain(env),
        "agentAddress": agent_address,
        "agentName": agent_name,
        "nonce": nonce_ms,
    }


def build_typed_data(env: str, agent_address: str, agent_name: str, nonce_ms: int) -> dict[str, Any]:
    """Typed data EIP-712 que a MetaMask assina (viem `signTypedData`).

    Espelha `user_signed_payload` (signing.py:217-237): domain
    HyperliquidSignTransaction v1, chainId = int(signatureChainId,16),
    verifyingContract 0x0; primaryType HyperliquidTransaction:ApproveAgent."""
    chain_id = int(SIGNATURE_CHAIN_ID, 16)
    return {
        "domain": {
            "name": "HyperliquidSignTransaction",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "HyperliquidTransaction:ApproveAgent": _APPROVE_AGENT_TYPES,
        },
        "primaryType": _PRIMARY_TYPE,
        "message": {
            "hyperliquidChain": hyperliquid_chain(env),
            "agentAddress": agent_address,
            "agentName": agent_name,
            "nonce": nonce_ms,
        },
    }


def _split_signature(signature: str) -> dict[str, Any]:
    """0x + 130 hex (r||s||v) → {"r","s","v"} no formato que a HL espera."""
    sig = signature[2:] if signature.startswith("0x") else signature
    if len(sig) != 130:
        raise HlAgentError("assinatura inválida (esperado 65 bytes)")
    r = "0x" + sig[0:64]
    s = "0x" + sig[64:128]
    v = int(sig[128:130], 16)
    if v < 27:
        v += 27
    return {"r": r, "s": s, "v": v}


# -- submissão à HL ---------------------------------------------------------
def _default_submit(env: str, action: dict[str, Any], signature: dict[str, Any], nonce: int) -> dict[str, Any]:
    import httpx

    payload = {
        "action": action,
        "nonce": nonce,
        "signature": signature,
        "vaultAddress": None,
        "expiresAfter": None,
    }
    resp = httpx.post(f"{base_url(env)}/exchange", json=payload, timeout=15.0)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 — resposta não-JSON vira erro estruturado
        return {"status": "error", "response": resp.text[:300]}


SubmitFn = Callable[[str, dict[str, Any], dict[str, Any], int], dict[str, Any]]


# -- auditoria --------------------------------------------------------------
def audit(db: Database, *, actor: str, action: str, env: str | None, detail: dict[str, Any] | None = None) -> None:
    db.insert("hl_auth_audit", {
        "at": utcnow(),
        "actor": actor,
        "action": action,
        "env": env,
        "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
    })


# -- casos de uso -----------------------------------------------------------
def prepare(
    db: Database,
    env: str,
    master_address: str,
    *,
    agent_name: str = AGENT_NAME_DEFAULT,
    actor: str = "control_api",
) -> dict[str, Any]:
    """Gera o par do agent, grava `pending` cifrado e devolve o typed data.

    Nenhuma chave (nem a do agent) volta na resposta."""
    if env not in ("testnet", "mainnet"):
        raise HlAgentError(f"env inválido: {env}")
    if not keyring.keyring_configured():
        raise HlAgentError("TOKIO_KEYRING_SECRET ausente — keyring não configurado")

    from eth_account import Account

    acct = Account.create()
    agent_address = acct.address
    agent_key = acct.key.hex()
    if not agent_key.startswith("0x"):
        agent_key = "0x" + agent_key
    nonce_ms = int(time.time() * 1000)

    # Uma preparação nova supera uma pendente anterior do mesmo ambiente.
    db.execute("DELETE FROM hl_agents WHERE env = ? AND status = 'pending'", (env,))
    db.insert("hl_agents", {
        "id": str(uuid.uuid4()),
        "env": env,
        "master_address": master_address,
        "agent_address": agent_address,
        "agent_name": agent_name,
        "privkey_enc": keyring.encrypt(agent_key),
        "status": "pending",
        "created_at": utcnow(),
    })
    audit(db, actor=actor, action="agent_prepare", env=env,
          detail={"agent_address": agent_address, "master_address": master_address})
    return {
        "ok": True,
        "env": env,
        "agent_address": agent_address,
        "agent_name": agent_name,
        "master_address": master_address,
        "nonce": nonce_ms,
        "signature_chain_id": SIGNATURE_CHAIN_ID,
        "typed_data": build_typed_data(env, agent_address, agent_name, nonce_ms),
    }


def activate(
    db: Database,
    env: str,
    agent_address: str,
    signature: str,
    nonce: int,
    *,
    actor: str = "control_api",
    submit: SubmitFn | None = None,
) -> dict[str, Any]:
    """Submete o approveAgent assinado à HL. `ok` ⇒ marca `active`; erro ⇒
    mantém `pending` e devolve o erro bruto da HL. NÃO faz reload do adapter —
    quem chama (server) orquestra o `reload_adapter(env)` após `ok`."""
    rows = db.query(
        "SELECT * FROM hl_agents WHERE env = ? AND agent_address = ? AND status = 'pending'",
        (env, agent_address),
    )
    if not rows:
        raise HlAgentError("nenhum agente 'pending' para este env/agent_address")
    row = rows[0]

    action = build_action(env, agent_address, row["agent_name"], nonce)
    sig = _split_signature(signature)
    submit_fn = submit or _default_submit
    resp = submit_fn(env, action, sig, nonce)

    if not isinstance(resp, dict) or resp.get("status") != "ok":
        audit(db, actor=actor, action="agent_activate", env=env,
              detail={"agent_address": agent_address, "ok": False,
                      "hl_response": str(resp)[:300]})
        return {"ok": False, "agent_address": agent_address, "error": resp}

    # Sucesso: este vira o único active/expiring do ambiente. Um anterior (ex.:
    # rotação) é marcado revoked — a HL já o substituiu ao aprovar o mesmo nome.
    now = utcnow()
    db.execute(
        "UPDATE hl_agents SET status = 'revoked', revoked_at = ? "
        "WHERE env = ? AND status IN ('active','expiring')",
        (now, env),
    )
    valid_until = _fetch_valid_until(env, row["master_address"], agent_address)
    db.execute(
        "UPDATE hl_agents SET status = 'active', approved_at = ?, valid_until = ?, "
        "revoked_at = NULL WHERE id = ?",
        (now, valid_until, row["id"]),
    )
    audit(db, actor=actor, action="agent_activate", env=env,
          detail={"agent_address": agent_address, "ok": True, "valid_until": valid_until})
    return {"ok": True, "agent_address": agent_address, "status": "active",
            "valid_until": valid_until}


def revoke(db: Database, env: str, *, actor: str = "control_api") -> dict[str, Any]:
    """Marca o agente vivo do ambiente como `revoked`. NÃO desativa a chave na
    HL (aviso na UI); só remove-a do keyring/adapter (o server tira do dict)."""
    rows = db.query(
        "SELECT * FROM hl_agents WHERE env = ? AND status IN ('active','expiring')",
        (env,),
    )
    if not rows:
        return {"ok": False, "reason": "sem_agente_ativo"}
    row = rows[0]
    db.execute(
        "UPDATE hl_agents SET status = 'revoked', revoked_at = ? WHERE id = ?",
        (utcnow(), row["id"]),
    )
    audit(db, actor=actor, action="agent_revoke", env=env,
          detail={"agent_address": row["agent_address"]})
    return {"ok": True, "agent_address": row["agent_address"]}


def resolve_active_key(db: Database, env: str) -> tuple[str, str] | None:
    """(master_address, agent_privkey) do agente `active` do ambiente, ou None.
    Decifra a chave — só chamado dentro do gateway. Nunca loga o retorno."""
    rows = db.query(
        "SELECT master_address, privkey_enc FROM hl_agents "
        "WHERE env = ? AND status = 'active' LIMIT 1",
        (env,),
    )
    if not rows:
        return None
    row = rows[0]
    return row["master_address"], keyring.decrypt(row["privkey_enc"])


def list_agents(db: Database) -> list[dict[str, Any]]:
    """Lista agentes SEM material secreto (privkey_enc jamais sai daqui)."""
    rows = db.query(
        "SELECT id, env, master_address, agent_address, agent_name, status, "
        "approved_at, valid_until, revoked_at, created_at FROM hl_agents "
        "ORDER BY created_at DESC"
    )
    return [dict(r) for r in rows]


def _fetch_valid_until(env: str, master_address: str, agent_address: str) -> str | None:
    """Best-effort: lê `extra_agents` do master e devolve o validUntil do agent
    recém-aprovado. Falha silenciosa (V5 fecha na testnet) — não bloqueia o
    activate se a info API estiver indisponível."""
    try:
        from hyperliquid.info import Info

        info = Info(base_url=base_url(env), skip_ws=True)
        for a in info.extra_agents(master_address):
            if str(a.get("address", "")).lower() == agent_address.lower():
                return a.get("validUntil")
    except Exception:  # noqa: BLE001 — validade é enriquecimento, não pré-condição
        return None
    return None
