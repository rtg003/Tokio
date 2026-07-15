"""UPDATE-0053 — endpoints de controle das Sugestões manuais.

`/control/suggestions/analyze` roda o pipeline COMPLETO por wallet SEM gravar;
`/control/suggestions/save` FORÇA-SALVAR as selecionadas como SUGERIDO com
`origin="usuário"` (curadoria humana prevalece sobre os filtros automáticos).

Para não bater na venue, monkeypatcha `funnel.analyze_single_wallet` por uma
versão que roda o pipeline REAL contra o `FakeClient` sintético do funil — a
lógica de análise permanece exercitada, sem rede."""
from __future__ import annotations

import re

import pytest

from engine.strategies.copy_trade import funnel

from tests.test_discovery_funnel import CFG, DEPOSIT, GOOD, make_client

HDR = {"X-Control-Token": "test-token"}
_RE = re.compile(r"^0x[0-9a-f]{40}$")
BAD = "nao-e-endereco"


@pytest.fixture()
def fake_analyze(monkeypatch):
    """Substitui analyze_single_wallet por uma que ignora o HLDataClient real e
    usa o FakeClient — mantém score/filtros/sim reais, sem rede."""
    real = funnel.analyze_single_wallet  # captura antes de patchar (evita recursão)

    def _fake(address, _client, _cfg, _logger=None):
        addr = (address or "").strip().lower()
        if not _RE.match(addr):
            raise ValueError(f"endereço inválido: {addr!r}")
        return real(addr, make_client(), CFG)

    monkeypatch.setattr(funnel, "analyze_single_wallet", _fake)
    return _fake


# --------------------------------------------------------------------------- #
# analyze — não grava, nunca dá short-circuit                                  #
# --------------------------------------------------------------------------- #
def test_analyze_passing_wallet(client, gateway_state, fake_analyze) -> None:
    r = client.post("/control/suggestions/analyze",
                    json={"addresses": [GOOD]}, headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    row = body["results"][0]
    assert row["passes_filters"] is True
    assert row["score"] is not None
    assert body["summary"] == {"total": 1, "passa_filtros": 1, "reprova_filtros": 0}
    # invariante: analyze NÃO escreve em traders
    n = gateway_state.db.query("SELECT COUNT(*) AS n FROM traders")[0]["n"]
    assert n == 0


def test_analyze_failing_wallet_still_scored(client, gateway_state,
                                             fake_analyze) -> None:
    """DEPOSIT reprova F10, mas a análise não bloqueia: score presente e
    reject_reasons populado (rótulo passes_filters=False, sem short-circuit)."""
    r = client.post("/control/suggestions/analyze",
                    json={"addresses": [DEPOSIT]}, headers=HDR)
    assert r.status_code == 200
    row = r.json()["results"][0]
    assert row["passes_filters"] is False
    assert row["score"] is not None
    assert row["reject_reasons"]
    n = gateway_state.db.query("SELECT COUNT(*) AS n FROM traders")[0]["n"]
    assert n == 0


def test_analyze_invalid_address_no_500(client, fake_analyze) -> None:
    r = client.post("/control/suggestions/analyze",
                    json={"addresses": [BAD]}, headers=HDR)
    assert r.status_code == 200
    row = r.json()["results"][0]
    assert row["reject_reasons"] == ["endereco_invalido"]
    assert row["score"] is None
    assert row["passes_filters"] is False


def test_analyze_requires_token(client, fake_analyze) -> None:
    r = client.post("/control/suggestions/analyze", json={"addresses": [GOOD]})
    assert r.status_code == 401


def test_analyze_rejects_more_than_10(client, fake_analyze) -> None:
    addrs = ["0x" + f"{i:02x}" * 20 for i in range(11)]
    r = client.post("/control/suggestions/analyze",
                    json={"addresses": addrs}, headers=HDR)
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# save — força-salvar como SUGERIDO/origin="usuário"                           #
# --------------------------------------------------------------------------- #
def test_save_passing_wallet(client, gateway_state, fake_analyze) -> None:
    r = client.post("/control/suggestions/save",
                    json={"addresses": [GOOD]}, headers=HDR)
    assert r.status_code == 200
    assert r.json()["summary"]["salvos"] == 1
    row = gateway_state.db.query(
        "SELECT status, origin, score FROM traders WHERE address = ?", (GOOD,))[0]
    assert row["status"] == "SUGERIDO"
    assert row["origin"] == "usuário"
    assert row["score"] is not None


def test_save_force_saves_failing_wallet(client, gateway_state,
                                         fake_analyze) -> None:
    """TESTE-CHAVE da decisão do operador: DEPOSIT reprova F10 mas é salva
    assim mesmo — SUGERIDO, origin="usuário", score preservado, sem REJEITADO."""
    r = client.post("/control/suggestions/save",
                    json={"addresses": [DEPOSIT]}, headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["salvos"] == 1
    assert body["saved"][0]["passes_filters"] is False
    row = gateway_state.db.query(
        "SELECT status, origin, score FROM traders WHERE address = ?",
        (DEPOSIT,))[0]
    assert row["status"] == "SUGERIDO"       # nunca REJEITADO
    assert row["origin"] == "usuário"
    assert row["score"] is not None          # score preservado


def test_save_invalid_address_is_skipped(client, gateway_state,
                                         fake_analyze) -> None:
    r = client.post("/control/suggestions/save",
                    json={"addresses": [BAD]}, headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["salvos"] == 0
    assert body["skipped"][0]["reason"] == "endereco_invalido"
    n = gateway_state.db.query("SELECT COUNT(*) AS n FROM traders")[0]["n"]
    assert n == 0


def test_save_is_idempotent(client, gateway_state, fake_analyze) -> None:
    for _ in range(2):
        client.post("/control/suggestions/save",
                    json={"addresses": [GOOD]}, headers=HDR)
    n = gateway_state.db.query(
        "SELECT COUNT(*) AS n FROM traders WHERE address = ?", (GOOD,))[0]["n"]
    assert n == 1


def test_save_requires_token(client, fake_analyze) -> None:
    r = client.post("/control/suggestions/save", json={"addresses": [GOOD]})
    assert r.status_code == 401
