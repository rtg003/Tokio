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


# --------------------------------------------------------------------------- #
# UPDATE-0059 — reclassify: backfill de confiança legada (metrics_confidence    #
# NULL) SEM tocar status/copy_pinned/origin                                     #
# --------------------------------------------------------------------------- #
def test_reclassify_backfills_confidence_preserving_gate(client, gateway_state,
                                                         fake_analyze) -> None:
    """Uma linha LEGADA (metrics_confidence NULL) promovida a TESTNET pelo
    operador é reclassificada: grava confiança nova + sample_* MAS preserva
    status/copy_pinned/origin (upsert_candidate nunca toca a promoção humana)."""
    from engine.strategies.copy_trade.traders_store import (set_status,
                                                            upsert_candidate)
    db = gateway_state.db
    upsert_candidate(db, address=GOOD, origin="usuário", score=50.0)
    set_status(db, GOOD, "TESTNET", by="dashboard-humano", human_gate=True)
    before = db.query("SELECT metrics_confidence, status, copy_pinned, origin "
                      "FROM traders WHERE address = ?", (GOOD,))[0]
    assert before["metrics_confidence"] is None   # legado
    assert before["status"] == "TESTNET"
    assert before["copy_pinned"] == 1

    r = client.post("/control/discovery/reclassify",
                    json={"addresses": [GOOD]}, headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["reclassified"] == 1

    after = db.query(
        "SELECT metrics_confidence, status, copy_pinned, origin, "
        "sample_sim_net_usd, sample_sim_window_days "
        "FROM traders WHERE address = ?", (GOOD,))[0]
    assert after["metrics_confidence"] == "complete"   # confiança nova gravada
    assert after["status"] == "TESTNET"                # gate humano intacto
    assert after["copy_pinned"] == 1                   # pin preservado
    assert after["origin"] == "usuário"                # curadoria não vira discovery
    assert after["sample_sim_net_usd"] is not None     # família sample_* persistida
    assert after["sample_sim_window_days"] is not None


def test_reclassify_does_not_downgrade_complete(client, gateway_state,
                                                fake_analyze) -> None:
    """Guarda anti-sobrescrita: uma linha já `complete` não é rebaixada — só o
    legado (NULL) é alvo do backfill."""
    from engine.strategies.copy_trade.traders_store import upsert_candidate
    db = gateway_state.db
    # endereço SEM perfil no FakeClient → nova análise dá `insufficient`; como já
    # está gravado `complete`, a guarda anti-sobrescrita preserva a linha.
    unknown = "0x" + "ab" * 20
    upsert_candidate(db, address=unknown, origin="discovery", score=40.0,
                     extras={"metrics_confidence": "complete"})
    r = client.post("/control/discovery/reclassify",
                    json={"addresses": [unknown]}, headers=HDR)
    assert r.status_code == 200
    row = r.json()["results"][0]
    assert row["reclassified"] is False
    assert row["reason"] == "metricas_completas_preservadas"


def test_reclassify_no_addresses_targets_only_operational_null(
        client, gateway_state, fake_analyze) -> None:
    """Sem `addresses`, o backfill alcança SÓ linhas legadas (metrics_confidence
    NULL) em status operacional — REJEITADO fica FORA do escopo."""
    from engine.strategies.copy_trade.traders_store import (set_status,
                                                            upsert_candidate)
    db = gateway_state.db
    # legado operacional (SALVO) → alvo
    upsert_candidate(db, address=GOOD, origin="discovery", score=50.0)
    set_status(db, GOOD, "SALVO", by="dashboard-humano", human_gate=True)
    # legado REJEITADO → fora do escopo
    upsert_candidate(db, address=DEPOSIT, origin="discovery", score=10.0)
    set_status(db, DEPOSIT, "REJEITADO", by="discovery_test")

    r = client.post("/control/discovery/reclassify", json={}, headers=HDR)
    assert r.status_code == 200
    body = r.json()
    addrs = {row["address"] for row in body["results"]}
    assert GOOD in addrs          # SALVO legado é reclassificado
    assert DEPOSIT not in addrs   # REJEITADO nunca entra no backfill
    # REJEITADO segue NULL (não tocado)
    dep = db.query("SELECT metrics_confidence FROM traders WHERE address = ?",
                   (DEPOSIT,))[0]
    assert dep["metrics_confidence"] is None


def test_reclassify_requires_token(client, fake_analyze) -> None:
    r = client.post("/control/discovery/reclassify", json={"addresses": [GOOD]})
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# UPDATE-0076 — analyze/save/reclassify PERSISTEM e EXPÕEM sim_funded_share e   #
# sim_f15_net_usd (campos do 0074 que o scan em massa já gravava, mas o caminho #
# de curadoria individual — _suggestion_extras/_suggestion_report — esquecia).  #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def fake_analyze_funded(monkeypatch):
    """Como `fake_analyze`, mas força valores DETERMINÍSTICOS dos dois campos do
    0074 no Candidate retornado — sem depender do valor que o FakeClient produz."""
    real = funnel.analyze_single_wallet

    def _fake(address, _client, _cfg, _logger=None):
        addr = (address or "").strip().lower()
        if not _RE.match(addr):
            raise ValueError(f"endereço inválido: {addr!r}")
        c = real(addr, make_client(), CFG)
        c.sim_f15_net_usd = 1234.5
        c.sim_funded_share = 0.07   # < min_funded_share ⇒ "cópia parcial"
        return c

    monkeypatch.setattr(funnel, "analyze_single_wallet", _fake)
    return _fake


def test_save_persists_funded_share_and_f15(client, gateway_state,
                                            fake_analyze_funded) -> None:
    r = client.post("/control/suggestions/save",
                    json={"addresses": [GOOD]}, headers=HDR)
    assert r.status_code == 200
    row = gateway_state.db.query(
        "SELECT sim_funded_share, sim_f15_net_usd FROM traders WHERE address = ?",
        (GOOD,))[0]
    assert row["sim_f15_net_usd"] == 1234.5
    assert row["sim_funded_share"] == 0.07


def test_analyze_report_exposes_funded_share_and_f15(client,
                                                     fake_analyze_funded) -> None:
    r = client.post("/control/suggestions/analyze",
                    json={"addresses": [GOOD]}, headers=HDR)
    assert r.status_code == 200
    metrics = r.json()["results"][0]["metrics"]
    assert metrics["sim_f15_net_usd"] == 1234.5
    assert metrics["sim_funded_share"] == 0.07


def test_reclassify_persists_funded_share_and_f15(client, gateway_state,
                                                  fake_analyze_funded) -> None:
    """O reclassify usa o mesmo `_suggestion_extras`; backfill de uma linha
    legada (metrics_confidence NULL) também grava os dois campos do 0074."""
    from engine.strategies.copy_trade.traders_store import (set_status,
                                                            upsert_candidate)
    db = gateway_state.db
    upsert_candidate(db, address=GOOD, origin="usuário", score=50.0)
    set_status(db, GOOD, "TESTNET", by="dashboard-humano", human_gate=True)

    r = client.post("/control/discovery/reclassify",
                    json={"addresses": [GOOD]}, headers=HDR)
    assert r.status_code == 200
    assert r.json()["reclassified"] == 1
    row = db.query(
        "SELECT sim_funded_share, sim_f15_net_usd, status FROM traders "
        "WHERE address = ?", (GOOD,))[0]
    assert row["sim_f15_net_usd"] == 1234.5
    assert row["sim_funded_share"] == 0.07
    assert row["status"] == "TESTNET"      # gate humano intacto


def test_suggestion_extras_includes_new_fields() -> None:
    from engine.gateway.server import _suggestion_extras
    from engine.strategies.copy_trade.funnel import Candidate
    c = Candidate(address=GOOD, sim_f15_net_usd=88.0, sim_funded_share=0.42)
    extras = _suggestion_extras(c)
    assert extras["sim_f15_net_usd"] == 88.0
    assert extras["sim_funded_share"] == 0.42


def test_suggestion_report_metrics_includes_new_fields() -> None:
    from engine.gateway.server import _suggestion_report
    from engine.strategies.copy_trade.funnel import Candidate
    c = Candidate(address=GOOD, sim_f15_net_usd=88.0, sim_funded_share=0.42)
    metrics = _suggestion_report(c)["metrics"]
    assert metrics["sim_f15_net_usd"] == 88.0
    assert metrics["sim_funded_share"] == 0.42


def test_save_funded_share_none_is_tolerated(client, gateway_state,
                                             monkeypatch) -> None:
    """Candidate com os dois campos None salva sem erro (coluna fica NULL) —
    cobre a guarda `getattr(..., None)`."""
    real = funnel.analyze_single_wallet

    def _fake(address, _client, _cfg, _logger=None):
        c = real((address or "").strip().lower(), make_client(), CFG)
        c.sim_f15_net_usd = None
        c.sim_funded_share = None
        return c

    monkeypatch.setattr(funnel, "analyze_single_wallet", _fake)
    r = client.post("/control/suggestions/save",
                    json={"addresses": [GOOD]}, headers=HDR)
    assert r.status_code == 200
    row = gateway_state.db.query(
        "SELECT sim_funded_share, sim_f15_net_usd FROM traders WHERE address = ?",
        (GOOD,))[0]
    assert row["sim_f15_net_usd"] is None
    assert row["sim_funded_share"] is None
