"""ADR 0008 — tabela `traders` única: upsert, Gate 2, transições e migração YAML."""
from __future__ import annotations

import json

from engine.strategies.copy_trade.traders_store import (
    import_yaml_trader,
    list_traders,
    operable_traders,
    set_status,
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
    set_status(db, ADDR, "DRY_RUN", by="test", human_gate=True)
    # re-scan com métricas novas (logic v2) não mexe no status nem na config
    upsert_candidate(db, address=ADDR, score=91.5, cohort="smart", logic_version=2)
    r = list_traders(db)[0]
    assert r["status"] == "DRY_RUN"
    assert r["score"] == 91.5 and r["logic_version"] == 2


def test_gate2_blocked_without_human_flag(db) -> None:
    upsert_candidate(db, address=ADDR, score=80.0)
    res = set_status(db, ADDR, "DRY_RUN", by="control_api")   # sem human_gate
    assert not res["ok"] and res["reason"] == "gate2_requer_autorizacao_humana"
    res = set_status(db, ADDR, "COPIANDO", by="control_api")
    assert not res["ok"]
    # com o gate humano, passa — e fica logado em events
    res = set_status(db, ADDR, "DRY_RUN", by="cli_gate2_humano", human_gate=True)
    assert res["ok"]
    ev = db.query("SELECT payload FROM events WHERE event_type = 'trader.status_changed'")
    assert ev and "cli_gate2_humano" in ev[-1]["payload"]


def test_control_api_operational_transitions(db) -> None:
    upsert_candidate(db, address=ADDR)
    set_status(db, ADDR, "COPIANDO", by="humano", human_gate=True)
    assert set_status(db, ADDR, "PAUSADO", by="control_api")["ok"]
    assert set_status(db, ADDR, "COPIANDO", by="control_api")["ok"]   # retomar
    assert set_status(db, ADDR, "PAUSADO", by="control_api")["ok"]
    assert set_status(db, ADDR, "DRY_RUN", by="control_api")["ok"]
    # rejeitar candidato novo é operacional
    upsert_candidate(db, address="0xbb" + "0" * 38)
    assert set_status(db, "0xbb" + "0" * 38, "REJEITADO", by="control_api")["ok"]


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
    for i, st in enumerate(["SUGERIDO", "DRY_RUN", "COPIANDO", "PAUSADO", "REJEITADO"]):
        addr = f"0x{i:040x}"
        upsert_candidate(db, address=addr, score=float(i))
        if st != "SUGERIDO":
            set_status(db, addr, st, by="t", human_gate=True) if st in ("DRY_RUN", "COPIANDO") \
                else (set_status(db, addr, "DRY_RUN", by="t", human_gate=True),
                      set_status(db, addr, st, by="t", human_gate=True))
    ops = {r["status"] for r in operable_traders(db)}
    assert ops == {"DRY_RUN", "COPIANDO"}


def test_import_yaml_trader_preserves_config(db) -> None:
    import_yaml_trader(db, {
        "name": "legacy", "address": ADDR, "mode": "percent", "value": 2.5,
        "max_leverage": 4.0, "blocked_assets": ["MEME"], "active": True,
        "dry_run": True, "thresholds": {"min_trades": 5},
    })
    r = list_traders(db)[0]
    assert r["status"] == "DRY_RUN"          # active+dry_run do YAML antigo
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
