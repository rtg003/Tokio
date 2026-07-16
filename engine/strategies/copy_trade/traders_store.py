"""Tabela `traders` — fonte ÚNICA de verdade para candidatos e copiados (ADR 0008).

Candidatos do discovery e traders copiados vivem na MESMA tabela, distinguidos
por `status`: SUGERIDO → SALVO/TESTNET/MAINNET/REJEITADO. Upsert por `address`
(lowercase). Config de execução são colunas; YAMLs por trader foram eliminados.

Toda mudança de status/config é logada em `events` (event_type `trader.*`).
"""
from __future__ import annotations

import json
from typing import Any

from engine.core.db import Database, utcnow

VALID_STATUSES = {"SUGERIDO", "SALVO", "TESTNET", "MAINNET", "REJEITADO"}
OPERATING_STATUSES = {"TESTNET", "MAINNET"}
WATCHLIST_STATUSES = {"SALVO", "TESTNET", "MAINNET"}
AUTOMATED_TRANSITIONS = {
    ("SUGERIDO", "REJEITADO"),
    ("REJEITADO", "SUGERIDO"),
}


def would_downgrade_metrics(existing_confidence: str | None,
                            new_confidence: str | None) -> bool:
    """UPDATE-0057 (Fase 2, Parte 8) — guarda anti-sobrescrita.

    True quando uma linha com métricas COMPLETAS persistidas seria substituída
    por métricas amostradas/insuficientes (ex.: um trader que virou hiperativo
    e num scan futuro só rende horas de dado). Linhas legadas (confiança NULL)
    NUNCA bloqueiam — a Fase 1 ainda não gravava confiança, então tratamos NULL
    como "desconhecido" e permitimos a atualização normal."""
    return existing_confidence == "complete" and (new_confidence or "complete") != "complete"


def strategy_id_for(address: str, name: str | None = None) -> str:
    slug = (name or address[2:10]).lower().replace(" ", "_")
    return f"ct_{slug}"


def is_human_actor(by: str, human_gate: bool = False) -> bool:
    b = by.lower()
    return human_gate or "human" in b or "humano" in b or "gate" in b or "dashboard" in b


def environment_for_status(status: str) -> str | None:
    if status == "TESTNET":
        return "testnet"
    if status == "MAINNET":
        return "mainnet"
    return None


def upsert_candidate(db: Database, *, address: str, name: str | None = None,
                     score: float | None = None, cohort: str | None = None,
                     twrr_30d: float | None = None, pnl_30d: float | None = None,
                     windows: dict[str, Any] | None = None,
                     profit_factor: float | None = None, win_rate: float | None = None,
                     max_drawdown: float | None = None, liq_distance: float | None = None,
                     origin: str = "discovery", logic_version: int = 1,
                     extras: dict[str, Any] | None = None) -> None:
    """Upsert de candidato SEM tocar em status/config de execução existentes
    (um re-scan nunca rebaixa um trader em TESTNET/MAINNET para SUGERIDO).
    `extras`: colunas adicionais da logic_version 2 (migration 0004)."""
    address = address.lower()
    row = db.query("SELECT address FROM traders WHERE address = ?", (address,))
    metrics = {
        "name": name, "score": score, "cohort": cohort, "twrr_30d": twrr_30d,
        "pnl_30d": pnl_30d, "windows": json.dumps(windows or {}, ensure_ascii=False),
        "profit_factor": profit_factor, "win_rate": win_rate,
        "max_drawdown": max_drawdown, "liq_distance": liq_distance,
        "origin": origin, "logic_version": logic_version, "updated_at": utcnow(),
        **(extras or {}),
    }
    if row:
        sets = ", ".join(f"{k} = ?" for k in metrics)
        db.execute(f"UPDATE traders SET {sets} WHERE address = ?",
                   [*metrics.values(), address])
        updated = db.query("SELECT * FROM traders WHERE address = ?", (address,))[0]
        db.upsert("traders", updated, ("address",))
    else:
        db.upsert("traders", {"address": address, "status": "SUGERIDO", **metrics},
                  ("address",))


def import_yaml_trader(db: Database, cfg: dict[str, Any]) -> None:
    """Migração única dos YAMLs antigos: preserva config de execução e status."""
    address = str(cfg["address"]).lower()
    status = "SUGERIDO"
    if cfg.get("active"):
        status = "TESTNET"
    db.upsert("traders", {
        "address": address,
        "name": cfg.get("name"),
        "status": status,
        "mode": cfg.get("mode", "fixed_usdc"),
        "value": float(cfg.get("value", 50.0)),
        "max_leverage": float(cfg.get("max_leverage", 3.0)),
        "blocked_assets": json.dumps(cfg.get("blocked_assets", []), ensure_ascii=False),
        "dry_run": 1 if cfg.get("dry_run", True) else 0,
        "thresholds": json.dumps(cfg.get("thresholds", {}), ensure_ascii=False),
        "origin": "manual",
        "updated_at": utcnow(),
    }, ("address",))


def list_traders(db: Database, statuses: set[str] | None = None) -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM traders ORDER BY score DESC NULLS LAST, address")
    if statuses:
        rows = [r for r in rows if r["status"] in statuses]
    return rows


def operable_traders(db: Database) -> list[dict[str, Any]]:
    """Traders que o executor deve espelhar (TESTNET e MAINNET)."""
    return list_traders(db, OPERATING_STATUSES)


def set_status(db: Database, address: str, new_status: str, *, by: str,
               logger: Any | None = None, human_gate: bool = False) -> dict[str, Any]:
    """Transição de status com enforcement do ator humano.

    A dashboard autenticada é o caminho humano para operar status. Processos
    automáticos ficam restritos a SUGERIDO↔REJEITADO.
    """
    address = address.lower()
    if new_status not in VALID_STATUSES:
        return {"ok": False, "reason": f"status_invalido_{new_status}"}
    rows = db.query("SELECT status FROM traders WHERE address = ?", (address,))
    if not rows:
        return {"ok": False, "reason": "trader_desconhecido"}
    current = rows[0]["status"]
    if current == new_status:
        return {"ok": True, "noop": True, "status": current}

    transition = (current, new_status)
    human = is_human_actor(by, human_gate)
    if not human and transition not in AUTOMATED_TRANSITIONS:
        return {"ok": False, "reason": "transicao_nao_permitida",
                "transition": f"{current}->{new_status}"}

    copy_pinned = 1 if human and new_status in WATCHLIST_STATUSES else None
    dry_run = 0 if new_status in OPERATING_STATUSES else 1
    if copy_pinned is None:
        db.execute(
            "UPDATE traders SET status = ?, dry_run = ?, updated_at = ? WHERE address = ?",
            (new_status, dry_run, utcnow(), address),
        )
    else:
        db.execute(
            "UPDATE traders SET status = ?, dry_run = ?, copy_pinned = ?, updated_at = ? "
            "WHERE address = ?",
            (new_status, dry_run, copy_pinned, utcnow(), address),
        )

    updated = db.query("SELECT * FROM traders WHERE address = ?", (address,))[0]
    db.upsert("traders", updated, ("address",))
    sid = strategy_id_for(address, updated.get("name"))
    strategy_status = "active" if new_status in OPERATING_STATUSES else "paused"
    db.upsert("strategies", {
        "id": sid,
        "module": "copy_trade",
        "name": updated.get("name") or address[2:10],
        "status": strategy_status,
        "config_snapshot": json.dumps(updated, ensure_ascii=False, default=str),
        "thresholds": updated.get("thresholds") or "{}",
    }, ("id",))
    if logger:
        logger.info("trader.status_changed",
                    {"address": address, "from": current, "to": new_status, "by": by})
    else:
        db.insert_event(ts=utcnow(), strategy_id=sid,
                        event_type="trader.status_changed", level="info",
                        payload={"address": address, "from": current,
                                 "to": new_status, "by": by})
    return {"ok": True, "from": current, "status": new_status}


def unpin_trader(db: Database, address: str, *, by: str,
                 human_gate: bool = False,
                 logger: Any | None = None) -> dict[str, Any]:
    """Remove a flag copy_pinned. Exige human_gate=True e status fora de
    TESTNET/MAINNET (cópia precisa sair de operação antes)."""
    address = address.lower()
    if not human_gate:
        raise ValueError("unpin exige human_gate=True")
    rows = db.query("SELECT status, copy_pinned FROM traders WHERE address = ?",
                    (address,))
    if not rows:
        raise ValueError(f"trader desconhecido: {address}")
    current_status = rows[0]["status"]
    if current_status in OPERATING_STATUSES:
        raise ValueError("pause/desative a cópia antes de unpin")
    db.execute("UPDATE traders SET copy_pinned = 0, updated_at = ? WHERE address = ?",
               (utcnow(), address))
    updated = db.query("SELECT * FROM traders WHERE address = ?", (address,))[0]
    db.upsert("traders", updated, ("address",))
    payload = {"address": address, "by": by}
    if logger:
        logger.info("trader.unpinned", payload)
    else:
        db.insert_event(ts=utcnow(), strategy_id=strategy_id_for(address),
                        event_type="trader.unpinned", level="info", payload=payload)
    return {"ok": True, "status": current_status, "copy_pinned": 0}


def update_exec_config(db: Database, address: str, *, by: str,
                       logger: Any | None = None, **fields: Any) -> dict[str, Any]:
    """Altera config de execução (mode/value/max_leverage/blocked_assets/
    dry_run/thresholds) — sempre logado; só via gateway/CLI."""
    allowed = {"mode", "value", "max_leverage", "blocked_assets", "dry_run", "thresholds"}
    bad = set(fields) - allowed
    if bad:
        return {"ok": False, "reason": f"campos_invalidos_{sorted(bad)}"}
    address = address.lower()
    if not db.query("SELECT 1 FROM traders WHERE address = ?", (address,)):
        return {"ok": False, "reason": "trader_desconhecido"}
    norm: dict[str, Any] = {}
    for k, v in fields.items():
        norm[k] = json.dumps(v, ensure_ascii=False) if k in ("blocked_assets", "thresholds") \
            and not isinstance(v, str) else v
    sets = ", ".join(f"{k} = ?" for k in norm)
    db.execute(f"UPDATE traders SET {sets}, updated_at = ? WHERE address = ?",
               [*norm.values(), utcnow(), address])
    updated = db.query("SELECT * FROM traders WHERE address = ?", (address,))[0]
    db.upsert("traders", updated, ("address",))
    payload = {"address": address, "changed": sorted(fields), "by": by}
    if logger:
        logger.info("trader.config_changed", payload)
    else:
        db.insert_event(ts=utcnow(), strategy_id=strategy_id_for(address),
                        event_type="trader.config_changed", level="info", payload=payload)
    return {"ok": True}


def write_cohort_snapshot(db: Database, *, logic_version: int,
                          cohorts: dict[str, dict[str, Any]]) -> None:
    ts = utcnow()
    for cohort, agg in cohorts.items():
        db.insert("cohort_snapshots", {
            "scan_ts": ts,
            "logic_version": logic_version,
            "cohort": cohort,
            "n_traders": int(agg.get("n", 0)),
            "avg_score": agg.get("avg_score"),
            "payload": json.dumps(agg, ensure_ascii=False, default=str),
        })
