"""ADR 0008 — tabela `traders` única: upsert, Gate 2, transições e migração YAML."""
from __future__ import annotations

import json

from engine.strategies.copy_trade.traders_store import (
    import_yaml_trader,
    list_traders,
    operable_traders,
    set_status,
    unpin_trader,
    update_exec_config,
    upsert_candidate,
    would_downgrade_metrics,
    write_cohort_snapshot,
)

ADDR = "0x00000000000000000000000000000000000000AA"


def test_upsert_candidate_creates_sugerido(db) -> None:
    upsert_candidate(db, address=ADDR, name="whale", score=87.0, cohort="swing",
                     twrr_30d=23.2, pnl_30d=100_000.0, win_rate=0.6,
                     logic_version=1)
    rows = list_traders(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["address"] == ADDR.lower()      # normalizado
    assert r["status"] == "SUGERIDO"
    assert r["dry_run"] == 1                 # default, sem exceção
    assert r["logic_version"] == 1


def test_rescan_never_downgrades_operating_trader(db) -> None:
    upsert_candidate(db, address=ADDR, score=80.0)
    set_status(db, ADDR, "TESTNET", by="test", human_gate=True)
    # re-scan com métricas novas (logic v2) não mexe no status nem na config
    upsert_candidate(db, address=ADDR, score=91.5, cohort="smart", logic_version=2)
    r = list_traders(db)[0]
    assert r["status"] == "TESTNET"
    assert r["score"] == 91.5 and r["logic_version"] == 2


def test_gate2_blocked_without_human_flag(db) -> None:
    upsert_candidate(db, address=ADDR, score=80.0)
    res = set_status(db, ADDR, "TESTNET", by="control_api")   # sem ator humano
    assert not res["ok"] and res["reason"] == "transicao_nao_permitida"
    res = set_status(db, ADDR, "MAINNET", by="control_api")
    assert not res["ok"]
    # com o gate humano, passa — e fica logado em events
    res = set_status(db, ADDR, "TESTNET", by="cli_gate2_humano", human_gate=True)
    assert res["ok"]
    ev = db.query("SELECT payload FROM events WHERE event_type = 'trader.status_changed'")
    assert ev and "cli_gate2_humano" in ev[-1]["payload"]


def test_control_api_operational_transitions(db) -> None:
    upsert_candidate(db, address=ADDR)
    set_status(db, ADDR, "MAINNET", by="humano", human_gate=True)
    assert set_status(db, ADDR, "SALVO", by="dashboard_humano")["ok"]
    assert set_status(db, ADDR, "TESTNET", by="dashboard_humano")["ok"]
    assert set_status(db, ADDR, "REJEITADO", by="dashboard_humano")["ok"]
    # rejeitar candidato novo é operacional
    upsert_candidate(db, address="0xbb" + "0" * 38)
    assert set_status(db, "0xbb" + "0" * 38, "REJEITADO", by="dashboard_humano")["ok"]


def test_update_exec_config_logged_and_validated(db) -> None:
    upsert_candidate(db, address=ADDR)
    res = update_exec_config(db, ADDR, by="control_api",
                             mode="percent", value=0.5, blocked_assets=["DOGE"])
    assert res["ok"]
    r = list_traders(db)[0]
    assert r["mode"] == "percent" and r["value"] == 0.5
    assert json.loads(r["blocked_assets"]) == ["DOGE"]
    assert not update_exec_config(db, ADDR, by="x", campo_invalido=1)["ok"]
    assert db.query("SELECT 1 FROM events WHERE event_type = 'trader.config_changed'")


def test_update_exec_config_rejects_non_json_blocked_assets(db) -> None:
    """blocked_assets como string crua não-JSON ('ZEC') é rejeitada — nunca
    persistir um valor que quebraria json.loads no boot do runner (incidente
    0x8d7d49eb, 2026-07-18). Uma string JSON válida ('["ZEC"]') continua aceita."""
    upsert_candidate(db, address=ADDR)
    res = update_exec_config(db, ADDR, by="control_api", blocked_assets="ZEC")
    assert res["ok"] is False and res["reason"] == "json_invalido_blocked_assets"
    r = db.query("SELECT blocked_assets FROM traders WHERE address = ?",
                 (ADDR.lower(),))[0]
    assert r["blocked_assets"] != "ZEC"          # nada corrompido foi gravado
    # string JSON já-serializada é aceita e round-trips
    assert update_exec_config(db, ADDR, by="control_api",
                              blocked_assets='["ZEC"]')["ok"]
    r = db.query("SELECT blocked_assets FROM traders WHERE address = ?",
                 (ADDR.lower(),))[0]
    assert json.loads(r["blocked_assets"]) == ["ZEC"]


def test_operable_traders_filters_statuses(db) -> None:
    for i, st in enumerate(["SUGERIDO", "SALVO", "TESTNET", "MAINNET", "REJEITADO"]):
        addr = f"0x{i:040x}"
        upsert_candidate(db, address=addr, score=float(i))
        if st != "SUGERIDO":
            set_status(db, addr, st, by="t", human_gate=True)
    ops = {r["status"] for r in operable_traders(db)}
    assert ops == {"TESTNET", "MAINNET"}


def test_import_yaml_trader_preserves_config(db) -> None:
    import_yaml_trader(db, {
        "name": "legacy", "address": ADDR, "mode": "percent", "value": 2.5,
        "max_leverage": 4.0, "blocked_assets": ["MEME"], "active": True,
        "dry_run": True, "thresholds": {"min_trades": 5},
    })
    r = list_traders(db)[0]
    assert r["status"] == "TESTNET"          # YAML ativo legado vira TESTNET
    assert r["mode"] == "percent" and r["value"] == 2.5
    assert r["origin"] == "manual"


def test_cohort_snapshot_written(db) -> None:
    write_cohort_snapshot(db, logic_version=1, cohorts={
        "swing": {"n": 3, "avg_score": 71.2},
        "position": {"n": 1, "avg_score": 55.0},
    })
    rows = db.query("SELECT * FROM cohort_snapshots ORDER BY cohort")
    assert [r["cohort"] for r in rows] == ["position", "swing"]
    assert rows[1]["n_traders"] == 3 and rows[1]["logic_version"] == 1


# -- Bloco 3: flag inviolável copy_pinned -------------------------------------

def test_set_status_testnet_sets_copy_pinned(db) -> None:
    """Entrar em TESTNET via gate humano fixa copy_pinned = 1."""
    upsert_candidate(db, address=ADDR, score=80.0)
    set_status(db, ADDR, "TESTNET", by="cli_gate2_humano", human_gate=True)
    r = list_traders(db)[0]
    assert r["copy_pinned"] == 1
    assert r["dry_run"] == 0


def test_set_status_mainnet_via_human_by_pins(db) -> None:
    """by contendo 'human' também fixa o pin ao ir para MAINNET."""
    upsert_candidate(db, address=ADDR, score=80.0)
    set_status(db, ADDR, "SALVO", by="human", human_gate=True)
    set_status(db, ADDR, "MAINNET", by="human_operator", human_gate=True)
    r = list_traders(db)[0]
    assert r["status"] == "MAINNET"
    assert r["copy_pinned"] == 1


def test_unpin_refused_while_mainnet(db) -> None:
    """unpin recusado enquanto MAINNET levanta ValueError."""
    upsert_candidate(db, address=ADDR, score=80.0)
    set_status(db, ADDR, "TESTNET", by="human", human_gate=True)
    set_status(db, ADDR, "MAINNET", by="human", human_gate=True)
    try:
        unpin_trader(db, ADDR, by="hermes", human_gate=True)
        assert False, "devia ter levantado ValueError"
    except ValueError as exc:
        assert "pause" in str(exc).lower() or "desative" in str(exc).lower()


def test_unpin_without_human_gate_raises(db) -> None:
    """unpin sem human_gate levanta ValueError."""
    upsert_candidate(db, address=ADDR, score=80.0)
    set_status(db, ADDR, "TESTNET", by="human", human_gate=True)
    set_status(db, ADDR, "SALVO", by="dashboard_humano")
    try:
        unpin_trader(db, ADDR, by="hermes", human_gate=False)
        assert False, "devia ter levantado ValueError"
    except ValueError as exc:
        assert "human_gate" in str(exc)


def test_unpin_accepted_after_saved(db) -> None:
    """unpin aceito após SALVO com human_gate=True."""
    upsert_candidate(db, address=ADDR, score=80.0)
    set_status(db, ADDR, "TESTNET", by="human", human_gate=True)
    set_status(db, ADDR, "SALVO", by="dashboard_humano")
    res = unpin_trader(db, ADDR, by="hermes", human_gate=True)
    assert res["ok"]
    r = list_traders(db)[0]
    assert r["copy_pinned"] == 0
    # evento logado
    ev = db.query("SELECT payload FROM events WHERE event_type = 'trader.unpinned'")
    assert ev and "hermes" in ev[-1]["payload"]


def test_rescan_pinned_rejecting_keeps_status_and_reason(db) -> None:
    """(i) re-scan com pinned reprovando em F17 → status e reject_reason
    intactos. Simula o caminho do funnel.persist_scan para um trader pinned
    que o re-scan marcaria como rejeitado."""
    from engine.strategies.copy_trade.funnel import Candidate, ScanResult, persist_scan

    upsert_candidate(db, address=ADDR, score=80.0)
    set_status(db, ADDR, "TESTNET", by="human", human_gate=True)
    # trader está pinned e em TESTNET

    # cria um candidato reprovado pelo re-scan (reject_reason preenchido).
    # UPDATE-0054: um candidato REALMENTE reprovado tem dados de deep dive
    # (coverage/n_trades) — sem eles a guarda anti-wipe do persist_scan preserva
    # o histórico. Populamos para exercitar o caminho de atualização de métricas.
    c = Candidate(address=ADDR, name="whale", score=42.0)
    c.reject_reason = "F17: reprovação simulada do re-scan"
    c.cohort = "smart"
    c.coverage_days = 45.0
    c.n_trades_30d = 20
    result = ScanResult(scan_id="t1", approved=[], rejected=[c],
                        funnel_stats={}, rekt_sample=[])
    cfg = {"logic_version": 9}
    persist_scan(db, result, cfg)

    r = list_traders(db)[0]
    # status e reject_reason intactos — o re-scan NÃO rebaixa pinned
    assert r["status"] == "TESTNET"
    # reject_reason não foi sobrescrito pela reprovação do re-scan
    assert r["reject_reason"] is None or "F17" not in (r["reject_reason"] or "")
    # copy_pinned permanece 1
    assert r["copy_pinned"] == 1
    # métricas foram atualizadas
    assert r["score"] == 42.0


# ---------------------------------------------------------------------------
# UPDATE-0064 (Parte 1b): demoção de trader operante pausa a strategy e audita
# ---------------------------------------------------------------------------
def test_demotion_pauses_strategy_and_emits_trader_demoted(db) -> None:
    """TESTNET→SALVO: a strategy operante vira `paused` e emite
    strategy.paused{by:'trader_demoted'} com os status antigo/novo do trader."""
    upsert_candidate(db, address=ADDR, name="whale", score=80.0)
    set_status(db, ADDR, "TESTNET", by="human", human_gate=True)
    sid = db.query("SELECT id, status FROM strategies")[0]
    assert sid["status"] == "active"           # promovido ⇒ operante

    res = set_status(db, ADDR, "SALVO", by="dashboard_humano")
    assert res["ok"]
    strat = db.query("SELECT status FROM strategies")[0]
    assert strat["status"] == "paused"         # rebaixado ⇒ pausado

    ev = db.query(
        "SELECT payload FROM events WHERE event_type = 'strategy.paused' "
        "ORDER BY id DESC")
    assert ev, "faltou o evento strategy.paused"
    payload = json.loads(ev[0]["payload"])
    assert payload["by"] == "trader_demoted"
    assert payload["old_trader_status"] == "TESTNET"
    assert payload["new_trader_status"] == "SALVO"


def test_demotion_of_non_operating_strategy_emits_nothing(db) -> None:
    """SUGERIDO→REJEITADO (strategy nunca operou): nenhum strategy.paused —
    o gatilho só dispara quando havia estratégia active/dry_run."""
    upsert_candidate(db, address=ADDR, score=50.0)   # cria strategy paused
    set_status(db, ADDR, "REJEITADO", by="discovery_v9")   # automação permitida
    ev = db.query(
        "SELECT 1 FROM events WHERE event_type = 'strategy.paused' "
        "AND json_extract(payload, '$.by') = 'trader_demoted'")
    assert ev == []


# ---------------------------------------------------------------------------
# UPDATE-0057 (Fase 2, Parte 8): guarda anti-sobrescrita de métricas completas
# ---------------------------------------------------------------------------
def test_would_downgrade_metrics_logic() -> None:
    """`complete` → `sampled`/`insufficient` é rebaixamento; o resto não.
    Linha legada (confiança NULL) NUNCA bloqueia (permite atualização)."""
    assert would_downgrade_metrics("complete", "sampled") is True
    assert would_downgrade_metrics("complete", "insufficient") is True
    assert would_downgrade_metrics("complete", "complete") is False
    assert would_downgrade_metrics("sampled", "insufficient") is False
    assert would_downgrade_metrics("sampled", "complete") is False
    # legado (NULL) e alvo default (None ⇒ trata como complete) nunca bloqueiam
    assert would_downgrade_metrics(None, "sampled") is False
    assert would_downgrade_metrics("complete", None) is False


def test_persist_scan_preserves_complete_metrics_from_downgrade(db) -> None:
    """Parte 8: um re-scan que só rende amostra (`sampled`) NÃO sobrescreve uma
    linha com métricas COMPLETAS persistidas — o trader que virou hiperativo
    conserva os dados bons em vez de ganhar métricas sobre horas de dado."""
    from engine.strategies.copy_trade.funnel import Candidate, ScanResult, persist_scan

    # linha existente com métricas COMPLETAS e sim_net bom
    upsert_candidate(db, address=ADDR, score=90.0,
                     extras={"metrics_confidence": "complete",
                             "sim_net_pnl_usd": 1234.0, "n_trades_30d": 40,
                             "coverage_days": 55.0})

    # re-scan devolve o mesmo trader agora AMOSTRADO (hiperativo) com sim nulo
    c = Candidate(address=ADDR, score=12.0)
    c.metrics_confidence = "sampled"
    c.coverage_days = 0.2
    c.n_trades_30d = 5
    c.sim_net_pnl_usd = None
    result = ScanResult(scan_id="t2", approved=[c], rejected=[],
                        funnel_stats={}, rekt_sample=[])
    persist_scan(db, result, {"logic_version": 9})

    r = list_traders(db)[0]
    # métricas completas preservadas — o scan amostrado foi ignorado
    assert r["metrics_confidence"] == "complete"
    assert r["sim_net_pnl_usd"] == 1234.0
    assert r["score"] == 90.0


def test_persist_scan_updates_when_not_downgrade(db) -> None:
    """Controle: quando a nova amostra é igualmente COMPLETA, o upsert normal
    atualiza as métricas (a guarda só protege contra rebaixamento)."""
    from engine.strategies.copy_trade.funnel import Candidate, ScanResult, persist_scan

    upsert_candidate(db, address=ADDR, score=90.0,
                     extras={"metrics_confidence": "complete",
                             "sim_net_pnl_usd": 1234.0, "n_trades_30d": 40,
                             "coverage_days": 55.0})
    c = Candidate(address=ADDR, score=77.0)
    c.metrics_confidence = "complete"
    c.coverage_days = 60.0
    c.n_trades_30d = 50
    c.sim_net_pnl_usd = 999.0
    result = ScanResult(scan_id="t3", approved=[c], rejected=[],
                        funnel_stats={}, rekt_sample=[])
    persist_scan(db, result, {"logic_version": 9})

    r = list_traders(db)[0]
    assert r["score"] == 77.0
    assert r["sim_net_pnl_usd"] == 999.0
    assert r["metrics_confidence"] == "complete"
