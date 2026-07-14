"""Regressão do fuso no filtro de período das rotas de summary (Parte 4).

Bug: `fills.ts`/`orders.created_at` são gravados em UTC (`…+00:00`), mas os
limites da janela chegam do front em fuso SP (`…-03:00`). O SQLite comparava os
TEXTOS lexicograficamente — offsets diferentes NÃO correspondem ao instante
real, então um sell às 21:16 SP (que em UTC cai no dia seguinte) "vazava" da
janela "hoje" e levava o PnL realizado junto (n_trades caía, realized zerava).

O gateway normaliza agora `since`/`until` para UTC (`_normalize_iso_utc`) antes
de comparar. Estes testes provam a janela SP correta:
  * fill às 21:16 SP de 13/07 (UTC 14/07 00:16) → DENTRO de "hoje" (13/07 SP).
  * fill às 21:30 SP de 12/07 (UTC 13/07 00:30) → FORA (é o dia SP anterior).
"""
from __future__ import annotations

from engine.gateway.server import _normalize_iso_utc

from ..conftest import register_strategy

# Janela "hoje" (13/07) no fuso de São Paulo, como o front envia.
SINCE_SP = "2026-07-13T00:00:00-03:00"
UNTIL_SP = "2026-07-13T23:59:59-03:00"

# Fill DENTRO: 21:16 SP de 13/07 == 00:16 UTC de 14/07 (grava-se em UTC).
TS_INSIDE = "2026-07-14T00:16:00+00:00"
# Fill FORA: 21:30 SP de 12/07 == 00:30 UTC de 13/07 (dia SP anterior).
TS_OUTSIDE = "2026-07-13T00:30:00+00:00"


def _seed_two_fills(db) -> None:
    register_strategy(db, "ct_tz", module="copy_trade")
    db.insert("fills", {
        "cloid": "0xin", "strategy_id": "ct_tz", "symbol": "BTC", "side": "sell",
        "price": 100_000.0, "size": 0.001, "fee": 0.5,
        "realized_pnl": 54.26, "ts": TS_INSIDE, "network": "testnet",
    })
    db.insert("fills", {
        "cloid": "0xout", "strategy_id": "ct_tz", "symbol": "BTC", "side": "sell",
        "price": 100_000.0, "size": 0.001, "fee": 0.5,
        "realized_pnl": 99.0, "ts": TS_OUTSIDE, "network": "testnet",
    })


def test_normalize_iso_utc_aligns_offsets() -> None:
    # SP 00:00 de 13/07 == 03:00 UTC; offset passa a ser `+00:00`.
    assert _normalize_iso_utc(SINCE_SP) == "2026-07-13T03:00:00+00:00"
    # naïve ⇒ assume UTC; "Z" vira "+00:00"; entrada vazia/inválida preservada.
    assert _normalize_iso_utc("2026-07-13T10:00:00").endswith("+00:00")
    assert _normalize_iso_utc("2026-07-13T10:00:00Z") == "2026-07-13T10:00:00+00:00"
    assert _normalize_iso_utc(None) is None
    assert _normalize_iso_utc("lixo") == "lixo"


def test_fills_summary_respects_sp_window(client, gateway_state) -> None:
    _seed_two_fills(gateway_state.db)
    s = client.get("/api/fills/summary", params={
        "strategy_id": "ct_tz", "network": "testnet",
        "since": SINCE_SP, "until": UNTIL_SP,
    }).json()
    # Só o fill das 21:16 SP entra; o das 21:30 SP do dia anterior fica de fora.
    assert s["n_trades"] == 1
    assert s["net_pnl"] == 54.26


def test_pnl_summary_respects_sp_window(client, gateway_state) -> None:
    _seed_two_fills(gateway_state.db)
    s = client.get("/api/pnl/summary", params={
        "strategy_id": "ct_tz", "network": "testnet",
        "since": SINCE_SP, "until": UNTIL_SP,
    }).json()
    assert s["n_trades"] == 1
    assert s["realized_pnl"] == 54.26


def test_fills_list_respects_sp_window(client, gateway_state) -> None:
    _seed_two_fills(gateway_state.db)
    rows = client.get("/api/fills", params={
        "strategy_id": "ct_tz", "network": "testnet",
        "since": SINCE_SP, "until": UNTIL_SP,
    }).json()
    assert [r["cloid"] for r in rows] == ["0xin"]
