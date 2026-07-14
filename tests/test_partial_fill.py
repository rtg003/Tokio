"""Regressão do Bug A — partial fill não pode ser tratado como total.

Quando a HL preenche PARCIALMENTE (ex.: ordem 20.98, preenche 0.16), o executor
gravava em `_my_pos` o valor DESEJADO (não o preenchido). Como a seleção
otimista×ledger escolhe o mais próximo do desejado, o otimista falso (=desired)
vencia o ledger real e `delta` virava 0 — o reconcile NUNCA corrigia o buraco.

Fix: `_my_pos` passa a refletir a posição REAL resultante via `filled_size`. Com
isso o reconcile enxerga o restante e completa a posição (respeitando cooldown).
`filled_size` ausente (dry_run) mantém o comportamento antigo (fallback).
"""
from __future__ import annotations

import pytest

from tests.test_copy_trade import TARGET, fill, make_executor


def _patch_partial(gw, fraction: float) -> None:
    """Faz o gateway preencher só `fraction` do tamanho pedido (partial fill)."""
    def send_intent(**payload):
        gw.intents.append(payload)
        return {"ok": True, "cloid": "0xpartial", "status": "filled",
                "filled_size": payload["size"] * fraction}
    gw.send_intent = send_intent


def test_partial_fill_sets_my_pos_to_filled_not_desired(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    _patch_partial(gw, 0.5)
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0))
    key = ("ct_whale01", "BTC")
    requested = gw.intents[0]["size"]          # delta enviado (== my_new, my_prev=0)
    # `_my_pos` reflete os 50% preenchidos — NÃO o desejado cheio.
    assert ex._my_pos[key] == pytest.approx(requested * 0.5)


def test_reconcile_completes_the_unfilled_remainder(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    _patch_partial(gw, 0.5)
    # trader segura 1.0 BTC; mid p/ reconcile = mesmo px do fill (sizing idêntico).
    watcher.positions[TARGET] = {"BTC": 1.0}
    gw.mids["BTC"] = 50_000.0
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0))
    key = ("ct_whale01", "BTC")
    partial = ex._my_pos[key]
    desired = partial / 0.5                      # my_new cheio
    # ledger reflete o parcial preenchido (o que a venue realmente tem).
    gw.ledger_response = {"ct_whale01": {"positions": {"BTC": {"size": partial}}}}
    corrections = ex.reconcile()
    # o reconcile detecta o buraco e envia o restante (~50%).
    assert len(gw.intents) == 2
    assert corrections and corrections[0]["symbol"] == "BTC"
    assert gw.intents[1]["side"] == "buy"
    assert gw.intents[1]["size"] == pytest.approx(desired - partial)


def test_full_fill_unchanged_behavior(settings, db) -> None:
    # fill cheio (fraction=1.0): `_my_pos` == desejado, como antes do fix.
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    _patch_partial(gw, 1.0)
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0))
    key = ("ct_whale01", "BTC")
    assert ex._my_pos[key] == pytest.approx(gw.intents[0]["size"])


def test_missing_filled_size_falls_back_to_desired(settings, db) -> None:
    # resposta sem `filled_size` (ex.: dry_run) ⇒ mantém comportamento antigo.
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    # RecordingGateway padrão devolve {"ok":True,...} SEM filled_size.
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0))
    key = ("ct_whale01", "BTC")
    assert ex._my_pos[key] == pytest.approx(gw.intents[0]["size"])
