"""UPDATE-0057 (correção pós-validação Hermes) — teste PURO do desembrulho do
envelope do HyperTracker (`/api/external/wallets`).

A validação de produção do Hermes reprovou as Partes 2/7 porque
`hypertracker_wallet` esperava o envelope errado (`{"data": {...}}`), enquanto o
endpoint REAL devolve `{"totalCount": N, "items": [{...}]}`. O `FakeClient` dos
demais testes mascarava o bug por representar a saída JÁ desembrulhada.

Este módulo exercita `_parse_ht_wallet` diretamente (sem rede), garantindo que o
envelope EXATO do Hermes é casado por endereço e que os formatos legados
continuam funcionando por robustez."""
from __future__ import annotations

from engine.strategies.copy_trade.hl_data import _parse_ht_wallet

ADDR = "0x3bca" + "00" * 18  # 42 chars, minúsculo


def _real_item(address: str = ADDR) -> dict:
    """Item exato observado pelo Hermes em produção (`0x3bca`)."""
    return {
        "address": address,
        "earliestActivityAt": "2024-08-21T21:12:00.118Z",
        "totalEquity": 11076826.57,
        "perpPnl": 1233610.11,
        "exposureRatio": 13.45,
    }


def test_parses_real_envelope_and_matches_by_address() -> None:
    """Envelope REAL do Hermes ⇒ retorna o item casado pelo endereço."""
    envelope = {"totalCount": 1, "items": [_real_item()]}
    got = _parse_ht_wallet(envelope, ADDR)
    assert got == _real_item()
    assert got["earliestActivityAt"] == "2024-08-21T21:12:00.118Z"
    assert got["totalEquity"] == 11076826.57
    assert got["perpPnl"] == 1233610.11
    assert got["exposureRatio"] == 13.45


def test_matches_case_insensitively() -> None:
    """O casamento por endereço ignora maiúsculas/minúsculas."""
    envelope = {"totalCount": 1, "items": [_real_item(ADDR.upper())]}
    assert _parse_ht_wallet(envelope, ADDR)["totalEquity"] == 11076826.57


def test_multiple_items_returns_the_matching_one() -> None:
    """Com vários itens, retorna SÓ o do endereço pedido."""
    other = "0x68f8" + "00" * 18
    envelope = {
        "totalCount": 2,
        "items": [
            {"address": other, "totalEquity": 788766.0},
            _real_item(),
        ],
    }
    assert _parse_ht_wallet(envelope, ADDR) == _real_item()


def test_address_mismatch_returns_empty() -> None:
    """Item com endereço DIVERGENTE ⇒ {} (defensivo, sem cross-wallet)."""
    envelope = {"totalCount": 1, "items": [_real_item("0xdead" + "00" * 18)]}
    assert _parse_ht_wallet(envelope, ADDR) == {}


def test_empty_items_returns_empty() -> None:
    """`items` vazio / `totalCount: 0` ⇒ {}."""
    assert _parse_ht_wallet({"totalCount": 0, "items": []}, ADDR) == {}


def test_items_none_returns_empty() -> None:
    """`items: null` ⇒ {} (não estoura)."""
    assert _parse_ht_wallet({"totalCount": 0, "items": None}, ADDR) == {}


def test_legacy_data_dict_still_works() -> None:
    """Regressão: formato legado `{"data": {...}}` ainda desembrulha."""
    assert _parse_ht_wallet({"data": _real_item()}, ADDR) == _real_item()


def test_legacy_data_list_still_works() -> None:
    """Regressão: formato legado `{"data": [{...}]}` pega o primeiro."""
    assert _parse_ht_wallet({"data": [_real_item()]}, ADDR) == _real_item()


def test_bare_dict_passthrough() -> None:
    """Dict sem `items`/`data` (já desembrulhado) passa direto."""
    assert _parse_ht_wallet(_real_item(), ADDR) == _real_item()


def test_bare_list_takes_first() -> None:
    """Lista crua ⇒ primeiro elemento."""
    assert _parse_ht_wallet([_real_item()], ADDR) == _real_item()


def test_non_mapping_returns_empty() -> None:
    """Tipo inesperado (None/str) ⇒ {}."""
    assert _parse_ht_wallet(None, ADDR) == {}
    assert _parse_ht_wallet("boom", ADDR) == {}
