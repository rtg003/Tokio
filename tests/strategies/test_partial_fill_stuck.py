"""Regressão — o cap `RECONCILE_MAX_ATTEMPTS` deve distinguir PROGRESSO (partial
fill) de REJEIÇÃO persistente.

Depois do fix do partial fill (UPDATE-0048), o `reconcile` passou a enxergar o
buraco e reenviar o restante. Mas o teto anti-runaway contava TODO send: um book
raso (ex.: HYPE na testnet) que preenche pouco a cada ordem batia o cap de 3 e
PARAVA de convergir, mesmo progredindo. Fix:

1. progresso (partial fill) NÃO consome tentativa — só rejeição (`ok=False`) sobe
   o cap; rejeição persistente ainda trava em `RECONCILE_MAX_ATTEMPTS` e loga
   `reconcile.stuck` (agora PERSISTIDO na tabela `events` — prefixo `reconcile.`);
2. partial fill CRÔNICO (N seguidos) vira ilíquido — para de martelar em vez de
   travar; um fill cheio zera a contagem.

O cooldown de 120s continua sendo o guard primário anti-runaway; nestes testes
ele é zerado (`RECONCILE_COOLDOWN_S = 0`) para dirigir os ciclos back-to-back.
"""
from __future__ import annotations

from tests.test_copy_trade import TARGET, fill, make_executor


def _patch_partial(gw, fraction: float) -> None:
    """Gateway preenche só `fraction` do tamanho pedido (partial fill)."""
    def send_intent(**payload):
        gw.intents.append(payload)
        return {"ok": True, "cloid": "0xpartial", "status": "filled",
                "filled_size": payload["size"] * fraction}
    gw.send_intent = send_intent


def _setup(settings, db, fraction: float):
    """Trader short em FARTCOIN, ledger sempre vazio (nunca reflete), mid=1.0 e
    cooldown desligado — o `_my_pos` otimista é o único freio, então o partial
    fill deixa um resto a cada ciclo. `desired` = -100 (fixed_usdc value=100)."""
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    ex.RECONCILE_COOLDOWN_S = 0.0
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {"FARTCOIN": 1.0}
    gw.ledger_response = {"ct_whale01": {"positions": {}}}
    _patch_partial(gw, fraction)
    return ex, watcher, gw


def test_partial_progress_does_not_hit_stuck_cap(settings, db) -> None:
    # fraction 0.1: o delta decai devagar (~0.9^n) e fica bem acima do drift/min
    # notional por muitos ciclos, mas cada send é um partial => progresso.
    ex, watcher, gw = _setup(settings, db, 0.1)
    for _ in range(3):
        ex.reconcile()
    key = ("ct_whale01", "FARTCOIN")
    # segue reenviando o restante a cada ciclo (não travou no cap de 3)...
    assert len(gw.intents) == 3
    # ...porque o progresso ZERA o contador de tentativas.
    assert key not in ex._reconcile_attempts
    # e nenhum `reconcile.stuck` foi logado (não é rejeição persistente).
    assert db.query(
        "SELECT 1 FROM events WHERE event_type = 'reconcile.stuck'") == []


def test_chronic_partial_becomes_illiquid_and_stops(settings, db) -> None:
    ex, watcher, gw = _setup(settings, db, 0.1)
    for _ in range(6):
        ex.reconcile()
    # 5 partials seguidos => símbolo vira ilíquido; o 6º ciclo pula sem enviar.
    assert len(gw.intents) == ex.PARTIAL_FILL_ILLIQUID_THRESHOLD
    assert ex._is_illiquid("FARTCOIN")
    # o caminho rápido (WS) também respeita o cache ilíquido: não abre ordem.
    before = len(gw.intents)
    watcher.emit(TARGET, fill("FARTCOIN", "A", 1.0, 1.0, start_pos=0.0))
    assert len(gw.intents) == before


def test_persistent_rejection_hits_cap_and_logs_stuck(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    ex.RECONCILE_COOLDOWN_S = 0.0
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {"FARTCOIN": 1.0}
    gw.ledger_response = {"ct_whale01": {"positions": {}}}  # nunca reflete

    def rejecting(**payload):  # rejeitada: _my_pos não avança, drift persiste
        gw.intents.append(payload)
        return {"ok": False, "reason": "rejected"}

    gw.send_intent = rejecting
    for _ in range(5):
        ex.reconcile()
    # rejeição persistente ainda trava no cap (3 sends, depois para)...
    assert len(gw.intents) == ex.RECONCILE_MAX_ATTEMPTS
    # ...e agora o alerta PERSISTE na tabela `events` (prefixo `reconcile.`).
    assert db.query(
        "SELECT event_type FROM events WHERE event_type = 'reconcile.stuck'")


def test_full_fill_resets_partial_streak(settings, db) -> None:
    ex, watcher, gw = _setup(settings, db, 0.1)
    key = ("ct_whale01", "FARTCOIN")
    for _ in range(4):
        ex.reconcile()
    # 4 partials: streak sobe mas ainda abaixo do limite (não é ilíquido).
    assert ex._partial_fill_streaks[key] == 4
    assert not ex._is_illiquid("FARTCOIN")
    # um fill CHEIO chega => zera a suspeita (o buraco fechou de vez).
    _patch_partial(gw, 1.0)
    ex.reconcile()
    assert key not in ex._partial_fill_streaks
