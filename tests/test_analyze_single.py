"""UPDATE-0053 — `analyze_single_wallet`: pipeline de discovery COMPLETO para
uma wallet, SEM gravar e SEM short-circuit.

Reusa as fixtures sintéticas do funil (`FakeClient`/`make_client`/GOOD/DEPOSIT)
para provar a decisão do operador: mesmo uma wallet que REPROVA um hard filter
é analisada por completo (score/cohort/sim_*) — os motivos ficam apenas
informativos em `reject_reasons`, com `reject_reason` sempre None."""
from __future__ import annotations

import pytest

from engine.strategies.copy_trade.funnel import analyze_single_wallet

from tests.test_discovery_funnel import (
    CFG,
    DEPOSIT,
    GOOD,
    make_client,
)


def test_good_wallet_passes_and_is_fully_analyzed(db) -> None:
    """Wallet saudável ⇒ reject_reasons vazio, score/cohort/sim_* preenchidos,
    e NADA é gravado (a análise não toca o BD)."""
    client = make_client()
    c = analyze_single_wallet(GOOD, client, CFG)

    assert c.reject_reasons == []
    assert c.reject_reason is None
    assert c.score is not None
    assert c.cohort
    assert c.sim_stage4_net_usd is not None

    # invariante: analyze não escreve em `traders`
    n = db.query("SELECT COUNT(*) AS n FROM traders")[0]["n"]
    assert n == 0


def test_failing_wallet_still_gets_score_no_short_circuit(db) -> None:
    """TESTE-CHAVE (decisão do operador): uma wallet que REPROVA um hard filter
    (DEPOSIT reprova F10 — inflada por aporte) ainda é analisada por completo.

    `reject_reasons` não-vazio (informativo) MAS `c.score is not None` e
    `c.reject_reason is None` — nunca marca REJEITADO, nunca dá short-circuit."""
    client = make_client()
    c = analyze_single_wallet(DEPOSIT, client, CFG)

    assert c.reject_reasons, "esperava motivos informativos de reprovação"
    assert any(r.startswith("F10") for r in c.reject_reasons)
    assert c.score is not None, "score deve ser calculado MESMO reprovando filtro"
    assert c.reject_reason is None, "curadoria manual nunca marca REJEITADO"

    n = db.query("SELECT COUNT(*) AS n FROM traders")[0]["n"]
    assert n == 0


def test_invalid_address_raises_valueerror() -> None:
    client = make_client()
    with pytest.raises(ValueError):
        analyze_single_wallet("nao-e-endereco", client, CFG)


def test_analyze_uses_reduced_fills_budget() -> None:
    """A análise manual limita fills_max_pages=2 numa CÓPIA do cfg — o cfg do
    chamador não é mutado."""
    client = make_client()
    before = CFG["collection"]["fills_max_pages"]
    analyze_single_wallet(GOOD, client, CFG)
    assert CFG["collection"]["fills_max_pages"] == before
