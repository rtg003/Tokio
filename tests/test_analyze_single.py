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
    DAY_MS,
    DEPOSIT,
    GOOD,
    NOW_MS,
    FakeClient,
    healthy_clearinghouse,
    make_client,
    swing_fills,
)

HYPER = "0x" + "11" * 20   # hiperativo: 2000 fills + histórico truncado
EMPTY = "0x" + "22" * 20   # sem fills → não pode estourar
RECENT_ONLY = "0x" + "33" * 20  # só fills_recent (fills_by_time vazio)
YOUNG = "0x" + "44" * 20   # conta NOVA (allTime 5d) mas muitos fills → F16 por idade
TINY = "0x" + "55" * 20    # poucos trades fechados → insufficient
MERGE = "0x" + "66" * 20   # recent + longitudinal disjuntos → coleta híbrida

# UPDATE-0056: filtros longitudinais que viram INDETERMINADOS quando a amostra
# não cobre a janela (espelha _LONGITUDINAL_CODES do funnel).
_LON = {"F2", "F2b", "F4", "F5", "F6", "F8", "F9", "F15", "F17", "F18", "F19",
        "copy_sim_negativa"}


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
    """UPDATE-0055/0056: wallet moderada → histórico COMPLETO, sem aviso ⚠️ e
    métricas com confiança `complete`."""
    client = make_client()
    c = analyze_single_wallet(GOOD, client, CFG)
    assert c.history_truncated is False
    assert c.fills_complete is True
    assert c.metrics_confidence == "complete"
    assert not any(r.startswith("⚠️") for r in c.reject_reasons)
    assert not any(w.startswith("⚠️") for w in c.metrics_warnings)


def test_analyze_hyperactive_wallet_flags_truncation() -> None:
    """UPDATE-0056: trader hiperativo demais p/ a janela (histórico longitudinal
    truncado) → history_truncated=True, confiança `sampled`, sim_* NULAS, aviso
    ⚠️ em metrics_warnings (NÃO em reject_reasons) e filtros longitudinais
    migram p/ indeterminate_filters (nunca reprovam)."""
    fills = swing_fills(n=1000, pnl_each=800)   # 2 fills/trade → 2000 fills
    assert len(fills) >= 2000
    client = FakeClient([], {HYPER: {"fills_recent": fills,
                                     "fills_truncated": True,
                                     "clearinghouse": healthy_clearinghouse()}})
    c = analyze_single_wallet(HYPER, client, CFG)
    assert c.history_truncated is True
    assert c.fills_complete is False
    assert c.metrics_confidence == "sampled"
    # Parte 6: nada de sim_* forjadas sobre horas de dado
    assert c.sim_net_pnl_usd is None
    assert c.sim_stage4_net_usd is None
    assert c.sim_max_dd_pct is None
    # aviso migrou p/ metrics_warnings
    assert any(w.startswith("⚠️") for w in c.metrics_warnings)
    assert not any(r.startswith("⚠️") for r in c.reject_reasons)
    # Parte 5: nenhum filtro longitudinal permanece como reprovação definitiva
    assert all(r.split(":", 1)[0] not in _LON for r in c.reject_reasons)


def test_analyze_no_fills_does_not_crash() -> None:
    """UPDATE-0055/0056: fills_recent vazio → análise não estoura, sem
    truncamento e confiança `insufficient` (sem amostra p/ julgar)."""
    client = FakeClient([], {EMPTY: {"fills_recent": []}})
    c = analyze_single_wallet(EMPTY, client, CFG)
    assert c.history_truncated is False
    assert c.metrics_confidence == "insufficient"
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


# ---------------------------------------------------------------------------
# UPDATE-0056: idade da wallet × span da amostra + confiança das métricas
# ---------------------------------------------------------------------------
def test_wallet_age_from_portfolio_alltime() -> None:
    """A idade vem do 1º ponto da série allTime (portfolio), não do span dos
    fills. GOOD usa a curva default (~90d) ⇒ wallet_age_days ≈ 90."""
    client = make_client()
    c = analyze_single_wallet(GOOD, client, CFG)
    assert c.wallet_age_days is not None
    assert 85 <= c.wallet_age_days <= 95


def test_metrics_confidence_complete_for_healthy_wallet() -> None:
    """Wallet moderada com histórico completo ⇒ `complete`, sim_* preenchidas e
    nenhum filtro indeterminado."""
    client = make_client()
    c = analyze_single_wallet(GOOD, client, CFG)
    assert c.metrics_confidence == "complete"
    assert c.sim_stage4_net_usd is not None
    assert c.indeterminate_filters == []


def test_f16_uses_wallet_age_not_fill_span() -> None:
    """F16 (idade mínima) reprova uma conta NOVA (allTime 5d) mesmo com fills
    cobrindo ~54d — o alvo é a MATURIDADE da wallet, não o span dos fills."""
    young_curve = [[NOW_MS - (5 - d) * DAY_MS, 50_000.0 + d * 100]
                   for d in range(6)]
    client = FakeClient([], {YOUNG: {"fills": swing_fills(pnl_each=800),
                                     "curve": young_curve,
                                     "clearinghouse": healthy_clearinghouse()}})
    c = analyze_single_wallet(YOUNG, client, CFG)
    assert c.wallet_age_days is not None and c.wallet_age_days < 10
    assert any(r.startswith("F16") for r in c.reject_reasons), \
        "F16 devia reprovar pela IDADE da wallet"
    assert any("idade da wallet" in r for r in c.reject_reasons)


def test_few_closed_fills_is_insufficient() -> None:
    """Poucos trades fechados ⇒ nem a amostra recente serve: `insufficient` e
    sim_* nulas."""
    client = FakeClient([], {TINY: {"fills": swing_fills(n=3, pnl_each=800),
                                    "clearinghouse": healthy_clearinghouse()}})
    c = analyze_single_wallet(TINY, client, CFG)
    assert c.metrics_confidence == "insufficient"
    assert c.sim_stage4_net_usd is None


# ---------------------------------------------------------------------------
# UPDATE-0057 (Fase 2): idade via HyperTracker (Parte 2) + enriquecimento
# agregado em campos separados (Parte 7)
# ---------------------------------------------------------------------------
HT = "0x" + "77" * 20   # wallet com agregado HyperTracker


def test_wallet_age_from_hypertracker_is_authoritative() -> None:
    """Parte 2: com `earliestActivityAt` do HyperTracker, a idade vem DELE
    (~200d) e NÃO do portfolio.allTime (~90d). Enriquecimento agregado
    (equity/pnl/exposição) fica em campos SEPARADOS, sem tocar as métricas HL."""
    earliest_ms = NOW_MS - 200 * DAY_MS
    client = FakeClient([], {HT: {
        "fills": swing_fills(pnl_each=800),
        "clearinghouse": healthy_clearinghouse(),
        "hypertracker": {"earliestActivityAt": earliest_ms,
                         "totalEquity": 123_456.0, "perpPnl": 7_890.0,
                         "exposureRatio": 0.42}}})
    c = analyze_single_wallet(HT, client, CFG)
    assert c.wallet_age_days is not None and 195 <= c.wallet_age_days <= 205
    assert c.ht_earliest_activity_ms is not None
    assert c.ht_total_equity == 123_456.0
    assert c.ht_perp_pnl == 7_890.0
    assert c.ht_exposure_ratio == 0.42


def test_hypertracker_iso_timestamp_is_parsed() -> None:
    """Partes 2/7 com os valores REAIS observados pelo Hermes em produção
    (`0x3bca`): `earliestActivityAt` como string ISO-8601 (`"2024-08-21T…Z"`)
    dirige `wallet_age_days` (NÃO o portfolio.allTime ~90d) e o enriquecimento
    agregado (equity/pnl/exposição) fica em campos SEPARADOS — a `equity` de
    trading segue vindo da Hyperliquid (clearinghouse), sem substituição."""
    from datetime import datetime, timezone
    earliest_iso = "2024-08-21T21:12:00.118Z"
    earliest_ms = datetime.fromisoformat(
        earliest_iso.replace("Z", "+00:00")).timestamp() * 1000
    expected_age = (NOW_MS - earliest_ms) / DAY_MS
    client = FakeClient([], {HT: {
        "fills": swing_fills(pnl_each=800),
        "clearinghouse": healthy_clearinghouse(),
        "hypertracker": {"address": HT,
                         "earliestActivityAt": earliest_iso,
                         "totalEquity": 11_076_826.57,
                         "perpPnl": 1_233_610.11,
                         "exposureRatio": 13.45}}})
    c = analyze_single_wallet(HT, client, CFG)
    assert c.wallet_age_days is not None
    assert abs(c.wallet_age_days - expected_age) <= 1.0
    # enriquecimento agregado (HyperTracker) em campos separados:
    assert c.ht_total_equity == 11_076_826.57
    assert c.ht_perp_pnl == 1_233_610.11
    assert c.ht_exposure_ratio == 13.45
    # equity de trading NÃO é substituída — segue da Hyperliquid (clearinghouse):
    assert c.equity == 50_000


def test_hypertracker_absent_falls_back_to_alltime() -> None:
    """Sem bloco HyperTracker (default {}), a idade cai no portfolio.allTime
    (~90d) e os campos ht_* ficam None — comportamento da Fase 1 preservado."""
    client = make_client()
    c = analyze_single_wallet(GOOD, client, CFG)
    assert 85 <= (c.wallet_age_days or 0) <= 95
    assert c.ht_total_equity is None
    assert c.ht_earliest_activity_ms is None


def test_hybrid_merges_recent_and_longitudinal() -> None:
    """Coleta híbrida: fills_recent (recentes) + fills_by_time (mais antigos,
    disjuntos) são UNIDOS/deduplicados — a amostra cobre a janela inteira."""
    recent = swing_fills(n=20, pnl_each=800, start_ms=NOW_MS - 20 * DAY_MS)
    older = swing_fills(n=20, pnl_each=500, start_ms=NOW_MS - 55 * DAY_MS)
    client = FakeClient([], {MERGE: {"fills_recent": recent,
                                     "fills_longitudinal": older,
                                     "clearinghouse": healthy_clearinghouse()}})
    c = analyze_single_wallet(MERGE, client, CFG)
    assert c.fills_sample_count == len(recent) + len(older)   # 80 fills únicos
    assert c.fills_sample_days is not None and c.fills_sample_days > 40
    assert c.metrics_confidence == "complete"
