"""UPDATE-0081: helper compartilhado `funnel.reclassify_wallets` + job de 2h do
`discovery_scheduler`. O helper reprocessa cada wallet pelo pipeline individual e
regrava TODAS as colunas via `upsert_candidate`, SEM tocar status/copy_pinned
(gate humano) e mantendo a guarda anti-downgrade. O job `run_reclassify` seleciona
os traders NÃO-rejeitados, reusa o cache da HLDataClient e nunca derruba o daemon.
"""
from __future__ import annotations

from engine.core.logger import EventLogger
from engine.strategies.copy_trade import discovery_scheduler as sched
from engine.strategies.copy_trade import funnel
from engine.strategies.copy_trade.funnel import Candidate, load_config
from engine.strategies.copy_trade.traders_store import set_status, upsert_candidate

CFG = load_config()
LV = int(CFG["logic_version"])
A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
REJ = "0x" + "cc" * 20


def _seed(db, address: str, status: str, *, confidence: str = "complete") -> None:
    """Insere um trader e leva ao status desejado (transições humanas quando
    preciso). upsert_candidate nunca cria fora de SUGERIDO."""
    upsert_candidate(db, address=address, score=10.0, logic_version=LV,
                     extras={"metrics_confidence": confidence})
    if status != "SUGERIDO":
        res = set_status(db, address, status, by="dashboard_humano", human_gate=True)
        assert res.get("ok"), res


def test_reclassify_wallets_fills_columns_preserving_status(db, monkeypatch) -> None:
    _seed(db, A, "TESTNET")
    # trava humana intacta pré-condição
    before = db.query("SELECT status, copy_pinned FROM traders WHERE address = ?",
                      (A,))[0]
    assert before["status"] == "TESTNET" and before["copy_pinned"] == 1

    def _fake(address, _client, _cfg, _logger=None):
        return Candidate(address=address, name="novo-nome", score=77.5,
                         win_rate=0.61, metrics_confidence="complete")

    monkeypatch.setattr(funnel, "analyze_single_wallet", _fake)
    out = funnel.reclassify_wallets(db, [A], client=None, cfg=CFG, logger=None)

    assert out["reclassified"] == 1 and out["total"] == 1
    row = db.query("SELECT status, copy_pinned, score, name, win_rate, "
                   "metrics_confidence FROM traders WHERE address = ?", (A,))[0]
    assert row["status"] == "TESTNET"      # gate humano intacto
    assert row["copy_pinned"] == 1          # pin preservado
    assert row["score"] == 77.5             # coluna refrescada
    assert row["name"] == "novo-nome"
    assert row["win_rate"] == 0.61
    assert row["metrics_confidence"] == "complete"


def test_reclassify_wallets_guards_against_downgrade(db, monkeypatch) -> None:
    _seed(db, A, "SALVO", confidence="complete")

    def _fake(address, _client, _cfg, _logger=None):
        # nova análise só rende amostra insuficiente → NÃO pode rebaixar completa
        return Candidate(address=address, score=1.0,
                         metrics_confidence="insufficient")

    monkeypatch.setattr(funnel, "analyze_single_wallet", _fake)
    out = funnel.reclassify_wallets(db, [A], client=None, cfg=CFG, logger=None)

    assert out["reclassified"] == 0
    assert out["results"][0]["reason"] == "metricas_completas_preservadas"
    row = db.query("SELECT score, metrics_confidence FROM traders WHERE address = ?",
                   (A,))[0]
    assert row["score"] == 10.0             # coluna NÃO sobrescrita
    assert row["metrics_confidence"] == "complete"


def test_reclassify_wallets_one_failure_does_not_break_batch(db, monkeypatch) -> None:
    _seed(db, A, "SUGERIDO")
    _seed(db, B, "SUGERIDO")

    def _fake(address, _client, _cfg, _logger=None):
        if address == A:
            raise RuntimeError("api fora do ar (simulado)")
        return Candidate(address=address, score=42.0, metrics_confidence="complete")

    monkeypatch.setattr(funnel, "analyze_single_wallet", _fake)
    out = funnel.reclassify_wallets(db, [A, B], client=None, cfg=CFG, logger=None)

    assert out["reclassified"] == 1
    by_addr = {r["address"]: r for r in out["results"]}
    assert by_addr[A]["reason"] == "erro_na_analise"
    assert by_addr[B]["reclassified"] is True
    assert db.query("SELECT score FROM traders WHERE address = ?", (B,))[0]["score"] == 42.0


def test_run_reclassify_targets_only_non_rejected(settings, db, monkeypatch) -> None:
    _seed(db, A, "TESTNET")
    _seed(db, B, "SUGERIDO")
    _seed(db, REJ, "SUGERIDO")
    # leva REJ a REJEITADO (transição automática permitida SUGERIDO→REJEITADO)
    set_status(db, REJ, "REJEITADO", by="reclassify")

    seen: dict[str, list[str]] = {}

    def _fake_reclassify(_db, addresses, client, cfg, logger=None):
        seen["targets"] = list(addresses)
        return {"results": [], "reclassified": 0, "total": len(addresses)}

    monkeypatch.setattr(funnel, "reclassify_wallets", _fake_reclassify)
    logger = EventLogger("reclx-test", settings.logs_dir, db=db)
    ok = sched.run_reclassify(db, logger, reason="timer_2h")

    assert ok is True
    assert set(seen["targets"]) == {A, B}         # REJEITADO fora
    events = db.query("SELECT event_type FROM events "
                      "WHERE event_type = 'discovery.reclassify_timer'")
    assert len(events) == 1


def test_run_reclassify_never_crashes(settings, db, monkeypatch) -> None:
    _seed(db, A, "SALVO")

    def _boom(*a, **kw):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(funnel, "reclassify_wallets", _boom)
    logger = EventLogger("reclx-test", settings.logs_dir, db=db)
    ok = sched.run_reclassify(db, logger, reason="timer_2h")

    assert ok is False
    events = db.query("SELECT event_type FROM events "
                      "WHERE event_type = 'discovery.reclassify_timer_failed'")
    assert len(events) == 1


def test_run_reclassify_empty_targets_is_noop_ok(settings, db) -> None:
    logger = EventLogger("reclx-test", settings.logs_dir, db=db)
    ok = sched.run_reclassify(db, logger, reason="timer_2h")   # tabela vazia
    assert ok is True
    ev = db.query("SELECT event_type FROM events "
                  "WHERE event_type = 'discovery.reclassify_timer'")
    assert len(ev) == 1
