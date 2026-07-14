"""Bug E — `_venue_cross_check` consulta a venue POR environment.

Estratégias podem operar em redes diferentes ao mesmo tempo (uma ct_* em
testnet, outra em mainnet). O código antigo consultava `positions()` com um único
network fixo (`watch_network`, que é a rede do trader-FONTE, não a nossa),
reportando `venue: 0.0` falso para posições que existiam de fato na testnet. O
fix agrupa as estratégias por `environment_for_status` e consulta cada grupo na
SUA rede; o payload do mismatch passa a incluir `"environment"`.
"""
from __future__ import annotations

from typing import Any

from tests.test_copy_trade import TARGET, make_executor

MAIN_ADDR = "0x00000000000000000000000000000000000000bb"


class SpyLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, payload: dict[str, Any], **kw: Any) -> None:
        self.warnings.append((event, payload))

    def info(self, *a: Any, **kw: Any) -> None: ...
    def error(self, *a: Any, **kw: Any) -> None: ...


def _setup(settings, db):
    """Testnet trader (ct_whale01) + mainnet trader (ct_mainwhale)."""
    ex, watcher, gw = make_executor(settings, db)  # TARGET/whale01 TESTNET
    db.upsert("traders", {
        "address": MAIN_ADDR, "name": "mainwhale", "status": "MAINNET",
        "mode": "fixed_usdc", "value": 100.0, "max_leverage": 3.0,
        "blocked_assets": "[]", "dry_run": 0, "thresholds": "{}",
    }, ("address",))
    ex.reload_traders()

    calls: list[tuple[tuple[str, ...], str | None]] = []
    venue_by_env: dict[str | None, list[dict[str, Any]]] = {}

    def positions(strategy_ids: list[str], network: str | None = None):
        calls.append((tuple(sorted(strategy_ids)), network))
        return venue_by_env.get(network, [])

    gw.positions = positions
    spy = SpyLogger()
    ex.logger = spy
    return ex, gw, calls, venue_by_env, spy


def test_cross_check_queries_each_env_separately(settings, db) -> None:
    ex, gw, calls, venue_by_env, spy = _setup(settings, db)
    # ledger casado com a venue em cada rede => nenhum mismatch esperado.
    ledger = {
        "ct_whale01": {"positions": {"AAVE": {"size": 12.16}}},
        "ct_mainwhale": {"positions": {"ETH": {"size": 3.0}}},
    }
    venue_by_env["testnet"] = [{"symbol": "AAVE", "size": 12.16}]
    venue_by_env["mainnet"] = [{"symbol": "ETH", "size": 3.0}]

    ex._venue_cross_check(ledger)

    # cada rede consultada uma vez, com SEU strategy_id e SEU env.
    assert (("ct_whale01",), "testnet") in calls
    assert (("ct_mainwhale",), "mainnet") in calls
    assert len(calls) == 2
    # venue casa com o ledger em ambas => zero venue_mismatch (nada de venue:0.0).
    assert not [e for e, _ in spy.warnings if e == "reconcile.venue_mismatch"]


def test_mismatch_payload_includes_environment(settings, db) -> None:
    ex, gw, calls, venue_by_env, spy = _setup(settings, db)
    # ledger diz 12.16 na testnet; a venue testnet devolve 0 => mismatch real.
    ledger = {
        "ct_whale01": {"positions": {"AAVE": {"size": 12.16}}},
        "ct_mainwhale": {"positions": {}},
    }
    venue_by_env["testnet"] = []          # divergência
    venue_by_env["mainnet"] = []

    ex._venue_cross_check(ledger)

    mismatches = [p for e, p in spy.warnings if e == "reconcile.venue_mismatch"]
    assert len(mismatches) == 1
    payload = mismatches[0]
    assert payload["symbol"] == "AAVE"
    assert payload["environment"] == "testnet"   # rede correta, não a do source
    assert payload["ledger_sum"] == 12.16
