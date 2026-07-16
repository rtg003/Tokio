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
