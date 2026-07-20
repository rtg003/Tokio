"""UPDATE-0083 — guardrail da wallet executora (`executor.wallet_mismatch`).

`_master_addr` grava SEMPRE a verdade da venue (`adapter.account_address`) em
fills/orders e, quando essa wallet diverge da wallet RESPEITADA do ambiente,
apenas LOGA `executor.wallet_mismatch` — nunca bloqueia o caminho de ordem
(§8.4.1). A referência tem precedência: (1) wallet canônica do keyring
(agente `active` de `hl_agents`, via `_expected_wallet`); (2) senão, a 1ª wallet
já vista por (strategy_id, network) — invariante "uma wallet por strategy+env".

Contexto do bug (ct_1a5db900/testnet): 199 fills em 0x4124 + 5 em 0x83c8 (split
de ledger). As wallets são INDIVIDUAIS e os fills são FIÉIS — o defeito era o
executor ter flipado a meio da vida da strategy. O guardrail detecta isso.
"""
from __future__ import annotations

import os
from typing import Any

from engine.core.logger import EventLogger
from engine.gateway.server import GatewayState

from .conftest import register_strategy

# Wallets INDIVIDUAIS (sem relação agent↔master entre si), como no caso real.
A = "0x" + "83" * 20   # 0x8383…83  (era o "esperado" em ct_1a5db900)
B = "0x" + "41" * 20   # 0x4141…41  (a wallet que flipou/executou)
TOKEN = "test-token"


class FakeAdapter:
    """Adapter mínimo: expõe `account_address` (verdade da venue) e um
    `subscribe_own_fills` no-op (GatewayState registra o callback de fills)."""

    def __init__(self, network: str, account_address: str) -> None:
        self.name = "hyperliquid"
        self.network = network
        self.account_address = account_address

    def subscribe_own_fills(self, callback: Any) -> None:  # noqa: D401
        pass


def _seed_active_agent(db, env: str, master: str) -> None:
    """Grava um agente `active` (a wallet canônica/esperada do ambiente). Não
    precisa de chave real: `_refresh_expected_wallet` só lê `master_address`."""
    db.insert("hl_agents", {
        "id": f"{env}-{master[-4:]}",
        "env": env,
        "master_address": master,
        "agent_address": "0xagent" + master[-6:],
        "agent_name": "engine_gateway",
        "privkey_enc": "enc-not-used",
        "status": "active",
        "created_at": "2026-07-20T00:00:00Z",
    })


def _state(settings, db, adapter: FakeAdapter) -> GatewayState:
    os.environ["GATEWAY_CONTROL_TOKEN"] = TOKEN
    logger = EventLogger("guardrail-test", settings.logs_dir, db=db)
    return GatewayState(settings, adapter, db,
                        adapters={adapter.network: adapter}, logger=logger)


def _mismatches(db) -> list[dict[str, Any]]:
    return db.query(
        "SELECT payload, strategy_id FROM events "
        "WHERE event_type = 'executor.wallet_mismatch' ORDER BY id")


# ---------------------------------------------------------------------------
# 1. Consistente: keyring/active == adapter → grava a wallet; SEM mismatch.
# ---------------------------------------------------------------------------
def test_consistent_no_mismatch(settings, db) -> None:
    _seed_active_agent(db, "testnet", A)
    st = _state(settings, db, FakeAdapter("testnet", A))
    assert st._expected_wallet["testnet"] == A.lower()

    got = st._master_addr(st.adapter, "testnet", strategy_id="ct_1", cloid="c1")
    assert got == A                       # grava a verdade da venue
    assert _mismatches(db) == []          # nada divergiu


# ---------------------------------------------------------------------------
# 2. Adapter stale: active=A, adapter=B → mismatch {expected:A, actual:B};
#    o fill grava B (verdade da venue), não A.
# ---------------------------------------------------------------------------
def test_stale_adapter_logs_mismatch_and_records_venue_truth(settings, db) -> None:
    _seed_active_agent(db, "testnet", A)   # esperado = A
    st = _state(settings, db, FakeAdapter("testnet", B))  # executando em B

    got = st._master_addr(st.adapter, "testnet", strategy_id="ct_1", cloid="c1")
    assert got == B                        # grava a wallet que REALMENTE executou

    ev = _mismatches(db)
    assert len(ev) == 1
    assert f'"expected": "{A.lower()}"' in ev[0]["payload"]
    assert f'"actual": "{B.lower()}"' in ev[0]["payload"]
    assert ev[0]["strategy_id"] == "ct_1"


# ---------------------------------------------------------------------------
# 2b. Integração: on_own_fill com adapter stale grava a wallet da venue no
#     row de `fills` e emite o mismatch (chokepoint no caminho de gravação).
# ---------------------------------------------------------------------------
def test_fill_records_venue_wallet_on_divergence(settings, db) -> None:
    _seed_active_agent(db, "testnet", A)
    st = _state(settings, db, FakeAdapter("testnet", B))
    register_strategy(db, "ct_f", module="copy_trade")
    db.insert("orders", {
        "cloid": "0xo1", "strategy_id": "ct_f", "symbol": "BTC",
        "side": "buy", "type": "market", "size": 1.0, "status": "created",
    })
    st.ledger.register_order("0xo1", "ct_f")

    st.on_own_fill({"cloid": "0xo1", "coin": "BTC", "side": "B", "px": 100.0,
                    "sz": 1.0, "fee": 0.0, "tid": "tt1", "_network": "testnet"})

    row = db.query("SELECT master_address FROM fills WHERE tid = 'tt1'")[0]
    assert row["master_address"] == B     # verdade da venue, não o esperado A
    ev = _mismatches(db)
    assert len(ev) == 1 and f'"actual": "{B.lower()}"' in ev[0]["payload"]


# ---------------------------------------------------------------------------
# 3. Hot path (ordem): divergência é logada mas NUNCA bloqueia — `_master_addr`
#    devolve a wallet (não levanta) e o cloid da ordem vai no evento.
# ---------------------------------------------------------------------------
def test_order_hot_path_not_blocked(settings, db) -> None:
    _seed_active_agent(db, "testnet", A)
    st = _state(settings, db, FakeAdapter("testnet", B))

    # Mesmo caminho do site de gravação de ordem (server.py): passa o cloid.
    got = st._master_addr(st.adapter, st.adapter.network,
                          strategy_id="ct_hot", cloid="order-cloid-9")
    assert got == B                        # ordem prossegue com a wallet real
    ev = _mismatches(db)
    assert len(ev) == 1
    assert '"cloid": "order-cloid-9"' in ev[0]["payload"]


# ---------------------------------------------------------------------------
# 4. Sem agente active: invariante "1 wallet por strategy+network". 1ª fill
#    estabelece; 2ª igual (sem evento); 3ª diferente → mismatch.
# ---------------------------------------------------------------------------
def test_per_strategy_invariant_without_active_agent(settings, db) -> None:
    st = _state(settings, db, FakeAdapter("testnet", A))
    assert "testnet" not in st._expected_wallet   # sem agente canônico

    # 1ª ocorrência: estabelece a wallet da strategy (seed lazy) — sem evento.
    assert st._master_addr(FakeAdapter("testnet", A), "testnet",
                           strategy_id="ct_inv") == A
    assert _mismatches(db) == []

    # 2ª igual: continua consistente — sem evento.
    assert st._master_addr(FakeAdapter("testnet", A), "testnet",
                           strategy_id="ct_inv") == A
    assert _mismatches(db) == []

    # 3ª diferente (a strategy flipou de executor): mismatch (expected=A).
    assert st._master_addr(FakeAdapter("testnet", B), "testnet",
                           strategy_id="ct_inv") == B
    ev = _mismatches(db)
    assert len(ev) == 1
    assert f'"expected": "{A.lower()}"' in ev[0]["payload"]
    assert f'"actual": "{B.lower()}"' in ev[0]["payload"]


# ---------------------------------------------------------------------------
# 4b. O seed lazy respeita o histórico: a 1ª wallet vem de `fills` já gravados,
#     não da wallet atual do adapter (a strategy foi "vista" antes em A).
# ---------------------------------------------------------------------------
def test_invariant_seeds_from_existing_fills(settings, db) -> None:
    st = _state(settings, db, FakeAdapter("testnet", B))
    register_strategy(db, "ct_seed", module="copy_trade")
    # Histórico: a strategy já operou em A (fill mais antigo).
    db.insert("fills", {
        "cloid": "0xold", "strategy_id": "ct_seed", "symbol": "BTC",
        "side": "buy", "price": 1.0, "size": 1.0, "fee": 0.0,
        "network": "testnet", "master_address": A, "ts": "2026-07-19T00:00:00Z",
    })
    # Agora o adapter executa em B → diverge da wallet histórica A.
    got = st._master_addr(st.adapter, "testnet", strategy_id="ct_seed")
    assert got == B
    ev = _mismatches(db)
    assert len(ev) == 1 and f'"expected": "{A.lower()}"' in ev[0]["payload"]


# ---------------------------------------------------------------------------
# 5. Regressão: sem hl_agents active e sem fills prévios → sem referência,
#    a 1ª wallet vira o baseline; comportamento inalterado (nenhum evento).
# ---------------------------------------------------------------------------
def test_regression_no_reference_no_event(settings, db) -> None:
    st = _state(settings, db, FakeAdapter("testnet", A))
    got = st._master_addr(st.adapter, "testnet", strategy_id="ct_new", cloid="c9")
    assert got == A
    assert _mismatches(db) == []          # sem referência prévia = sem alarme falso

    # Sem strategy_id e sem expected: grava a verdade da venue sem alarme.
    assert st._master_addr(FakeAdapter("testnet", B), "testnet") == B
    assert _mismatches(db) == []
