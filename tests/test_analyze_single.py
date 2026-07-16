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
    FakeClient,
    healthy_clearinghouse,
    make_client,
    swing_fills,
)

HYPER = "0x" + "11" * 20   # hiperativo: 2000 fills → amostra truncada
EMPTY = "0x" + "22" * 20   # sem fills → não pode estourar
RECENT_ONLY = "0x" + "33" * 20  # só fills_recent (fills_by_time vazio)


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


def test_analyze_does_not_mutate_caller_cfg() -> None:
    """UPDATE-0055: a análise opera numa CÓPIA do cfg — o cfg do chamador
    nunca é mutado (antes mexíamos em fills_max_pages; agora nem isso)."""
    client = make_client()
    before = CFG["collection"]["fills_max_pages"]
    analyze_single_wallet(GOOD, client, CFG)
    assert CFG["collection"]["fills_max_pages"] == before


def test_analyze_normal_wallet_not_truncated() -> None:
    """UPDATE-0055: wallet com <2.000 fills → sem truncamento, sem aviso ⚠️."""
    client = make_client()
    c = analyze_single_wallet(GOOD, client, CFG)
    assert c.history_truncated is False
    assert not any(r.startswith("⚠️") for r in c.reject_reasons)


def test_analyze_hyperactive_wallet_flags_truncation() -> None:
    """UPDATE-0055: fills_recent devolve 2.000 fills (limite da API) →
    history_truncated=True e o aviso ⚠️ vem PRIMEIRO em reject_reasons."""
    fills = swing_fills(n=1000, pnl_each=800)   # 2 fills/trade → 2000 fills
    assert len(fills) >= 2000
    client = FakeClient([], {HYPER: {"fills_recent": fills,
                                     "clearinghouse": healthy_clearinghouse()}})
    c = analyze_single_wallet(HYPER, client, CFG)
    assert c.history_truncated is True
    assert c.reject_reasons and c.reject_reasons[0].startswith("⚠️")
    assert "2.000" in c.reject_reasons[0]


def test_analyze_no_fills_does_not_crash() -> None:
    """UPDATE-0055: fills_recent vazio → análise não estoura, sem truncamento."""
    client = FakeClient([], {EMPTY: {"fills_recent": []}})
    c = analyze_single_wallet(EMPTY, client, CFG)
    assert c.history_truncated is False
    assert not any(r.startswith("⚠️") for r in c.reject_reasons)
    assert c.reject_reason is None


def test_analyze_uses_fills_recent_not_fills_by_time() -> None:
    """UPDATE-0055: a fonte primária é fills_recent. Perfil com fills_recent
    RICO mas fills (fills_by_time) VAZIO ⇒ as métricas vêm do recente."""
    client = FakeClient([], {RECENT_ONLY: {
        "fills_recent": swing_fills(pnl_each=800),   # 110 fills
        "fills": [],                                 # fills_by_time vazio
        "clearinghouse": healthy_clearinghouse()}})
    c = analyze_single_wallet(RECENT_ONLY, client, CFG)
    assert c.n_trades_30d > 0, "métricas deviam vir de fills_recent, não de fills_by_time"
    assert c.score is not None
