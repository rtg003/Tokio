"""UPDATE-0084 — blindagem de DIREÇÃO da cópia (incidente 19/07 `ct_1a5db900`).

O trader estava SHORT −400 HYPE e nossa cópia abriu LONG (35 buys, +59 HYPE).
Raiz: `_target_pos` nascia VAZIO no boot; um fill de fechamento de short (buy)
sem `startPosition` virava âncora 0 → `new_target = 0 + sz` → LONG fantasma.

Estes 8 testes cobrem: hidratação do âncora no boot (Fix #1); fechamento de
short que NÃO vira long (regressão do incidente); `fill.side_mismatch` (Fix #2);
guarda READ-ONLY `reconcile.direction_inversion` (Fix #3); auditoria de direção
em `drift.correcting` (Fix #4); e o checkbox `copy_existing_positions` (Fix #5).

Reusa o harness de `tests/test_copy_trade.py` (FakeWatcher/RecordingGateway/…)."""
from __future__ import annotations

from typing import Any

from engine.core.config import Settings
from engine.core.db import Database
from engine.strategies.copy_trade.executor import CopyTradeExecutor

from tests.test_copy_trade import (
    TARGET,
    FakeWatcher,
    RecordingGateway,
    fill,
    seed_trader,
)

HYPE_PX = 40.0  # preço de referência p/ HYPE nos testes (fixed_usdc value=100)


def build(settings: Settings, db: Database, *,
          positions: dict[str, float] | None = None,
          mids: dict[str, float] | None = None,
          **overrides: Any) -> tuple[CopyTradeExecutor, FakeWatcher, RecordingGateway]:
    """Como `make_executor`, mas semeia `watcher.positions`/`gw.mids` ANTES de
    construir o executor — a hidratação do âncora roda no `__init__` (via
    `reload_traders`) e precisa da clearinghouse (positions) e do preço (mids)
    já disponíveis para dimensionar o baseline."""
    watcher = FakeWatcher()
    gw = RecordingGateway()
    if positions is not None:
        watcher.positions[TARGET] = dict(positions)
    if mids is not None:
        gw.mids.update(mids)
    my_equity_fn = overrides.pop("my_equity_fn", lambda _env=None: 1_000.0)
    target_equity_fn = overrides.pop("target_equity_fn", lambda _a: 100_000.0)
    seed_trader(db, **overrides)
    ex = CopyTradeExecutor(settings=settings, db=db, gateway=gw, watcher=watcher,
                           my_equity_fn=my_equity_fn,
                           target_equity_fn=target_equity_fn,
                           target_positions_fn=watcher.target_positions)
    return ex, watcher, gw


def _events(db: Database, event_type: str) -> list[dict[str, Any]]:
    return db.query("SELECT payload FROM events WHERE event_type = ?", (event_type,))


# 1 — Boot hidrata o âncora short (Fix #1) -----------------------------------
def test_boot_hydrates_short_anchor(settings, db) -> None:
    ex, _watcher, _gw = build(settings, db, positions={"HYPE": -400.0},
                              mids={"HYPE": HYPE_PX})
    assert ex._target_pos[("ct_whale01", "HYPE")] == -400.0


# 2 — Fechamento de short NÃO vira LONG (regressão do incidente) --------------
def test_close_short_does_not_flip_long(settings, db) -> None:
    ex, watcher, gw = build(settings, db, positions={"HYPE": -400.0},
                            mids={"HYPE": HYPE_PX})
    # fill de fechamento parcial do short: buy 50, SEM startPosition (o caso do
    # incidente). Sem o âncora hidratado, prev_target cairia p/ 0 e new_target
    # viraria +50 (LONG). Com a hidratação, prev_target=-400 → new_target=-350.
    close = {"coin": "HYPE", "side": "B", "sz": "50", "px": str(HYPE_PX), "time": 0.0}
    watcher.emit(TARGET, close)
    # âncora continua short
    assert ex._target_pos[("ct_whale01", "HYPE")] == -350.0
    # nenhum intent de COMPRA (que abriria LONG contra o trader)
    assert all(i["side"] != "buy" for i in gw.intents), gw.intents


# 3 — `fill.side_mismatch` logado, mas segue o side real (Fix #2) -------------
def test_side_dir_mismatch_logged(settings, db) -> None:
    ex, watcher, gw = build(settings, db, positions={"HYPE": -400.0},
                            mids={"HYPE": HYPE_PX})
    # side "A" (sell) mas dir "Open Long" (esperava buy) → divergência.
    bad = {"coin": "HYPE", "side": "A", "sz": "10", "px": str(HYPE_PX),
           "startPosition": "-400", "dir": "Open Long", "time": 0.0}
    watcher.emit(TARGET, bad)
    assert _events(db, "fill.side_mismatch"), "esperava evento fill.side_mismatch"
    # a cópia confia no side REAL (sell) — nunca inverte para buy.
    assert gw.intents and all(i["side"] == "sell" for i in gw.intents), gw.intents


# 4 — Guarda READ-ONLY `reconcile.direction_inversion` (Fix #3) ---------------
def test_reconcile_direction_inversion_guard(settings, db) -> None:
    # copy_existing=True ⇒ hidrata só o âncora (não semeia _my_pos); o ledger
    # está vazio (flat). Forçamos um `_desired_mirror` INVERTIDO (positivo) sobre
    # um alvo short p/ simular um bug de sinal em outro ponto: a guarda deve
    # capturar (delta>0 com alvo short e flat) e NÃO enviar ordem.
    ex, _watcher, gw = build(settings, db, positions={"HYPE": -400.0},
                             mids={"HYPE": HYPE_PX})
    ex._desired_mirror = lambda cfg, symbol, target_now, px, environment: 2.5  # type: ignore
    ex.reconcile()
    assert _events(db, "reconcile.direction_inversion"), \
        "esperava reconcile.direction_inversion"
    assert not any(i["symbol"] == "HYPE" for i in gw.intents), gw.intents


# 5 — `drift.correcting` carrega a direção (Fix #4) --------------------------
def test_drift_correcting_has_direction(settings, db) -> None:
    import json

    ex, _watcher, gw = build(settings, db, positions={"HYPE": -400.0},
                             mids={"HYPE": HYPE_PX})
    ex.reconcile()  # short/flat → correção normal (sell p/ abrir short)
    rows = _events(db, "drift.correcting")
    assert rows, "esperava drift.correcting"
    payload = json.loads(rows[-1]["payload"])
    assert payload["target_direction"] == "short"
    assert payload["order_direction"] == "sell"


# 6 — copy_existing=False NÃO abre o legado; fill novo é espelhado -----------
def test_copy_existing_false_skips_legacy_opens_new(settings, db) -> None:
    ex, watcher, gw = build(settings, db, positions={"HYPE": -400.0},
                            mids={"HYPE": HYPE_PX, "BTC": 50_000.0},
                            copy_existing_positions=0)
    # baseline semeado (âncora + _my_pos espelhado) ⇒ reconcile vê delta≈0.
    assert ex._my_pos[("ct_whale01", "HYPE")] < 0  # short mirror seedado
    ex.reconcile()
    assert not any(i["symbol"] == "HYPE" for i in gw.intents), gw.intents
    # um fill NOVO (símbolo novo) é espelhado normalmente.
    watcher.emit(TARGET, fill("BTC", "A", 1.0, 50_000.0, start_pos=0.0))
    assert any(i["symbol"] == "BTC" for i in gw.intents), gw.intents


# 7 — copy_existing=True (default) abre o legado na direção short ------------
def test_copy_existing_true_opens_legacy_short(settings, db) -> None:
    ex, _watcher, gw = build(settings, db, positions={"HYPE": -400.0},
                             mids={"HYPE": HYPE_PX})  # default: copia legado
    assert ("ct_whale01", "HYPE") not in ex._my_pos  # não semeia baseline
    ex.reconcile()
    hype = [i for i in gw.intents if i["symbol"] == "HYPE"]
    assert hype and all(i["side"] == "sell" for i in hype), gw.intents


# 8 — Regressão de sinal do reconcile: short→sell, long→buy ------------------
def test_reconcile_sign_regression(settings, db) -> None:
    ex_s, _w, gw_s = build(settings, db, positions={"HYPE": -400.0},
                           mids={"HYPE": HYPE_PX})
    ex_s.reconcile()
    hype_s = [i for i in gw_s.intents if i["symbol"] == "HYPE"]
    assert hype_s and all(i["side"] == "sell" for i in hype_s), gw_s.intents


def test_reconcile_sign_regression_long(settings, db) -> None:
    ex_l, _w, gw_l = build(settings, db, positions={"HYPE": 400.0},
                           mids={"HYPE": HYPE_PX})
    ex_l.reconcile()
    hype_l = [i for i in gw_l.intents if i["symbol"] == "HYPE"]
    assert hype_l and all(i["side"] == "buy" for i in hype_l), gw_l.intents
