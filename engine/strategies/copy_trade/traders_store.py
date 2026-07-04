"""Tabela `traders` — fonte ÚNICA de verdade para candidatos e copiados (ADR 0008).

Candidatos do discovery e traders copiados vivem na MESMA tabela, distinguidos
por `status`: SUGERIDO → (Gate 2, humano) → DRY_RUN/COPIANDO → PAUSADO /
REJEITADO / ARQUIVADO. Upsert por `address` (lowercase). Config de execução
são colunas; YAMLs por trader foram eliminados.

Toda mudança de status/config é logada em `events` (event_type `trader.*`).
"""
from __future__ import annotations

import json
from typing import Any

from engine.core.db import Database, utcnow

VALID_STATUSES = {"SUGERIDO", "DRY_RUN", "COPIANDO", "PAUSADO", "REJEITADO", "ARQUIVADO"}
# Gate 2: sair de SUGERIDO para operação exige autorização humana (CLI).
HUMAN_GATE_TRANSITIONS = {("SUGERIDO", "DRY_RUN"), ("SUGERIDO", "COPIANDO"),
                          ("DRY_RUN", "COPIANDO")}
# Transições operacionais permitidas via API de controle do gateway.
CONTROL_API_TRANSITIONS = {("DRY_RUN", "PAUSADO"), ("COPIANDO", "PAUSADO"),
                           ("PAUSADO", "DRY_RUN"), ("PAUSADO", "COPIANDO"),
                           ("SUGERIDO", "REJEITADO"),
                           # re-scan pode reabilitar um rejeitado que voltou a
                           # passar no funil (vira candidato de novo)
                           ("REJEITADO", "SUGERIDO")}


def strategy_id_for(address: str, name: str | None = None) -> str:
    slug = (name or address[2:10]).lower().replace(" ", "_")
    return f"ct_{slug}"


def upsert_candidate(db: Database, *, address: str, name: str | None = None,
                     score: float | None = None, cohort: str | None = None,
                     twrr_30d: float | None = None, pnl_30d: float | None = None,
                     windows: dict[str, Any] | None = None,
                     profit_factor: float | None = None, win_rate: float | None = None,
                     max_drawdown: float | None = None, liq_distance: float | None = None,
                     origin: str = "discovery", logic_version: int = 1,
                     extras: dict[str, Any] | None = None) -> None:
    """Upsert de candidato SEM tocar em status/config de execução existentes
    (um re-scan nunca rebaixa um trader COPIANDO para SUGERIDO).
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
        db.upsert("traders", updated, ("address",))  # re-enfileira p/ replicação
    else:
        db.upsert("traders", {"address": address, "status": "SUGERIDO", **metrics},
                  ("address",))


def import_yaml_trader(db: Database, cfg: dict[str, Any]) -> None:
    """Migração única dos YAMLs antigos: preserva config de execução e status."""
    address = str(cfg["address"]).lower()
    status = "SUGERIDO"
    if cfg.get("active"):
        status = "DRY_RUN" if cfg.get("dry_run", True) else "COPIANDO"
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
    """Traders que o executor deve espelhar (DRY_RUN e COPIANDO)."""
    return list_traders(db, {"DRY_RUN", "COPIANDO"})


def set_status(db: Database, address: str, new_status: str, *, by: str,
               logger: Any | None = None, human_gate: bool = False) -> dict[str, Any]:
    """Transição de status com enforcement do Gate 2.

    human_gate=True marca que a chamada veio do caminho humano (CLI). A API de
    controle NUNCA passa human_gate — logo transições de Gate 2 são recusadas lá.
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
    if transition in HUMAN_GATE_TRANSITIONS and not human_gate:
        return {"ok": False, "reason": "gate2_requer_autorizacao_humana",
                "transition": f"{current}->{new_status}"}
    if not human_gate and transition not in CONTROL_API_TRANSITIONS \
            and new_status != "ARQUIVADO":
        return {"ok": False, "reason": "transicao_nao_permitida",
                "transition": f"{current}->{new_status}"}

    db.execute("UPDATE traders SET status = ?, updated_at = ? WHERE address = ?",
               (new_status, utcnow(), address))

    # Bloco 3 — flag inviolável: ao entrar em DRY_RUN/COPIANDO via gate humano
    # (by contém 'human' ou 'gate'), fixa copy_pinned = 1. O re-scan passa a
    # atualizar métricas sem jamais rebaixar/rejeitar o trader. Só removido
    # por unpin_trader(human_gate=True) com a cópia pausada.
    if new_status in ("DRY_RUN", "COPIANDO") and (
            "human" in by.lower() or "gate" in by.lower() or human_gate):
        db.execute("UPDATE traders SET copy_pinned = 1 WHERE address = ?",
                   (address,))

    updated = db.query("SELECT * FROM traders WHERE address = ?", (address,))[0]
    db.upsert("traders", updated, ("address",))
    if logger:
        logger.info("trader.status_changed",
                    {"address": address, "from": current, "to": new_status, "by": by})
    else:
        db.insert_event(ts=utcnow(), strategy_id=strategy_id_for(address),
                        event_type="trader.status_changed", level="info",
                        payload={"address": address, "from": current,
                                 "to": new_status, "by": by})
    return {"ok": True, "from": current, "status": new_status}


def unpin_trader(db: Database, address: str, *, by: str,
                 human_gate: bool = False,
                 logger: Any | None = None) -> dict[str, Any]:
    """Remove a flag copy_pinned (Bloco 3). Inviolável: exige human_gate=True
    E que o trader NÃO esteja em DRY_RUN/COPIANDO (cópia precisa ser pausada
    ou desativada antes). Levanta ValueError em caso de violação."""
    address = address.lower()
    if not human_gate:
        raise ValueError("unpin exige human_gate=True")
    rows = db.query("SELECT status, copy_pinned FROM traders WHERE address = ?",
                    (address,))
    if not rows:
        raise ValueError(f"trader desconhecido: {address}")
    current_status = rows[0]["status"]
    if current_status in ("DRY_RUN", "COPIANDO"):
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
