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

from engine.strategies.copy_trade.hl_data import (
    HLDataClient,
    _parse_ht_positions_page,
    _parse_ht_wallet,
)

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


# ============================================================================
# UPDATE-0068 (bug do UPDATE-0066 do Hermes) — envelope REAL de `/positions`
# usa a chave `positions`, não `items`. O parser antigo devolvia [] sempre.
# ============================================================================
def test_positions_page_parses_real_positions_key() -> None:
    """Envelope REAL `{"positions": [...], "nextCursor": "..."}` ⇒ itens + cursor."""
    page = {"positions": [{"coin": "BTC"}, {"coin": "ETH"}], "nextCursor": "c1"}
    items, cursor = _parse_ht_positions_page(page)
    assert items == [{"coin": "BTC"}, {"coin": "ETH"}]
    assert cursor == "c1"


def test_positions_page_last_page_has_no_cursor() -> None:
    """`nextCursor: null` ⇒ cursor None (fim da paginação)."""
    items, cursor = _parse_ht_positions_page(
        {"positions": [{"coin": "SOL"}], "nextCursor": None}
    )
    assert items == [{"coin": "SOL"}]
    assert cursor is None


def test_positions_page_legacy_items_still_supported() -> None:
    """Formato legado `items` segue funcionando (robustez)."""
    items, cursor = _parse_ht_positions_page(
        {"items": [{"coin": "ARB"}], "next_cursor": "c2"}
    )
    assert items == [{"coin": "ARB"}]
    assert cursor == "c2"


def test_positions_page_legacy_data_still_supported() -> None:
    """Formato legado `data` segue funcionando (robustez)."""
    items, cursor = _parse_ht_positions_page({"data": [{"coin": "OP"}]})
    assert items == [{"coin": "OP"}]
    assert cursor is None


def test_positions_page_positions_takes_precedence_over_items() -> None:
    """Se ambos existirem, a chave REAL `positions` vence."""
    items, _ = _parse_ht_positions_page(
        {"positions": [{"coin": "REAL"}], "items": [{"coin": "LEGACY"}]}
    )
    assert items == [{"coin": "REAL"}]


def test_positions_page_empty_and_non_mapping() -> None:
    """`positions: []` ⇒ []; lista crua ⇒ ela mesma; tipo inesperado ⇒ []."""
    assert _parse_ht_positions_page({"positions": []}) == ([], None)
    assert _parse_ht_positions_page([{"coin": "X"}]) == ([{"coin": "X"}], None)
    assert _parse_ht_positions_page(None) == ([], None)


# ============================================================================
# UPDATE-0062 (v15) — HyperTracker como fonte primária de POSIÇÕES
# ============================================================================
class _FakeResp:
    """Resposta HTTP mínima para o transporte de `HLDataClient._request`."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # noqa: D401 — sempre 200 nos testes
        return None

    def json(self) -> object:
        return self._payload


class _FakePositionsHttp:
    """`_http` sintético: pagina `/positions` por `cursor`/`nextCursor`."""

    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, headers: dict) -> _FakeResp:
        self.calls.append(params)
        cursor = params.get("cursor")
        idx = 0 if cursor is None else int(cursor)
        return _FakeResp(self.pages[idx])


def _client_no_db() -> HLDataClient:
    """Cliente sem BD e sem throttle (min_interval_s=0) para testes de rede fake."""
    return HLDataClient(None, request_budget=100, min_interval_s=0.0)


def test_ht_positions_paginates_by_next_cursor(monkeypatch) -> None:
    """v15: `ht_positions` segue `nextCursor` e agrega TODAS as páginas."""
    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    pages = [
        {"items": [{"coin": "BTC"}, {"coin": "ETH"}], "nextCursor": "1"},
        {"items": [{"coin": "SOL"}], "nextCursor": "2"},
        {"items": [{"coin": "ARB"}], "nextCursor": None},
    ]
    client = _client_no_db()
    fake = _FakePositionsHttp(pages)
    client._http = fake  # type: ignore[assignment]

    got = client.ht_positions(ADDR)

    assert [p["coin"] for p in got] == ["BTC", "ETH", "SOL", "ARB"]
    # 3 páginas visitadas; a última encerra por nextCursor vazio.
    assert len(fake.calls) == 3
    assert fake.calls[0].get("cursor") is None
    assert fake.calls[1]["cursor"] == "1"
    assert client.ht_requests_used == 3  # cada página consome 1 do orçamento HT


def test_ht_positions_no_key_returns_empty_and_spends_nothing(monkeypatch) -> None:
    """SOFT dependency: sem `HYPERTRACKER_API_KEY` → [] e zero requests HT/HTTP."""
    monkeypatch.delenv("HYPERTRACKER_API_KEY", raising=False)
    client = _client_no_db()

    def _boom(*_a, **_k):  # qualquer toque na rede é bug (deve nem chamar)
        raise AssertionError("ht_positions não pode tocar a rede sem chave")

    client._http = type("H", (), {"get": staticmethod(_boom)})()  # type: ignore[assignment]

    assert client.ht_positions(ADDR) == []
    assert client.ht_segments() == []
    assert client.ht_cohort_addresses([8, 9]) == []
    assert client.ht_heatmap() == {}
    assert client.ht_requests_used == 0


def test_ht_positions_budget_exhausted_degrades_without_raising(
        monkeypatch, caplog) -> None:
    """v15: orçamento HT esgotado (free tier) → `ht_positions` degrada p/ [] e
    LOGA `discovery.ht_budget_exhausted` — NUNCA levanta (o funil segue em fills)."""
    import logging

    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    # cap diário 0: qualquer request HT excede o orçamento antes de tocar a rede.
    client = HLDataClient(None, request_budget=100, min_interval_s=0.0,
                          ht_daily_cap=0, ht_per_scan_cap=0)

    def _boom(*_a, **_k):  # se degradar corretamente, nem chega a chamar a rede
        raise AssertionError("orçamento esgotado não pode tocar a rede")

    client._http = type("H", (), {"get": staticmethod(_boom)})()  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        got = client.ht_positions(ADDR)   # não deve levantar HTBudgetExhausted

    assert got == []
    assert any("discovery.ht_budget_exhausted" in r.getMessage()
               for r in caplog.records)


# ============================================================================
# UPDATE-0065 — (a) `start` ISO 8601; (b) leaderboard no orçamento HT; (c) erro
# HTTP visível + contagem por status
# ============================================================================
from datetime import datetime  # noqa: E402


def _parse_iso(value: str) -> datetime:
    """Aceita o formato `...Z` emitido por `_ht_start_iso`."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_ht_positions_sends_start_iso8601(monkeypatch) -> None:
    """UPDATE-0065 (a): `ht_positions` envia `start` ISO 8601 (sem ele → 400)."""
    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    client = _client_no_db()
    fake = _FakePositionsHttp([{"items": [{"coin": "BTC"}], "nextCursor": None}])
    client._http = fake  # type: ignore[assignment]

    client.ht_positions(ADDR, start_days=60)

    assert "start" in fake.calls[0]
    parsed = _parse_iso(fake.calls[0]["start"])  # não levanta = ISO 8601 válido
    assert parsed.year >= 2020


def test_ht_cohort_addresses_sends_start_iso8601(monkeypatch) -> None:
    """UPDATE-0065 (a): `ht_cohort_addresses` também envia `start`."""
    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    client = _client_no_db()
    fake = _FakePositionsHttp([
        {"items": [{"address": "0x" + "ab" * 20}], "nextCursor": None},
    ])
    client._http = fake  # type: ignore[assignment]

    client.ht_cohort_addresses([7], start_days=30)

    assert "start" in fake.calls[0]
    _parse_iso(fake.calls[0]["start"])  # ISO 8601 válido
    assert fake.calls[0]["segmentId"] == 7


class _Http400:
    """`_http` sintético que devolve HTTP 400 do HyperTracker (start ausente)."""

    BODY = "start must be a valid ISO 8601 date string"

    def get(self, url: str, *, params: dict, headers: dict):
        import httpx

        req = httpx.Request("GET", url, params=params)
        return httpx.Response(400, request=req, text=self.BODY)


def test_ht_400_degrades_logs_body_and_counts_status(monkeypatch, caplog) -> None:
    """UPDATE-0065 (c): 400 do HT degrada p/ [], LOGA o corpo (status + mensagem)
    e incrementa `ht_errors_by_status[400]`."""
    import logging

    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    client = _client_no_db()
    client._http = _Http400()  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        got = client.ht_positions(ADDR)

    assert got == []
    msgs = [r.getMessage() for r in caplog.records]
    assert any("discovery.http_error" in m and "status=400" in m
               and "ISO 8601" in m for m in msgs)
    assert client.ht_errors_by_status.get(400) == 1


class _FakeLeaderboardHttp:
    """`_http` sintético do leaderboard perp-pnl (`{"data": [...]}`)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, *, params: dict, headers: dict) -> _FakeResp:
        self.calls.append(url)
        return _FakeResp({"data": [{"address": "0x" + "cd" * 20}]})


def test_leaderboard_counts_ht_budget_and_persists(monkeypatch, db) -> None:
    """UPDATE-0065 (b): `_hypertracker_leaderboard` passa por `_ht_get` — conta no
    orçamento HT persistido (`ht_requests_used` > 0 e grava em discovery_cache)."""
    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    client = HLDataClient(db, request_budget=100, min_interval_s=0.0)
    client._http = _FakeLeaderboardHttp()  # type: ignore[assignment]

    out = client._hypertracker_leaderboard("k", max_addresses=2)

    assert out  # coletou pelo menos 1 endereço
    assert client.ht_requests_used > 0
    rows = db.query("SELECT payload FROM discovery_cache WHERE cache_key LIKE ?",
                    ("ht_budget:%",))
    assert rows, "consumo do leaderboard deve persistir em discovery_cache"


def test_leaderboard_degrades_when_budget_exhausted(monkeypatch, caplog) -> None:
    """UPDATE-0065 (b): com cap diário 0, o leaderboard degrada SEM levantar."""
    import logging

    monkeypatch.setenv("HYPERTRACKER_API_KEY", "k")
    client = HLDataClient(None, request_budget=100, min_interval_s=0.0,
                          ht_daily_cap=0, ht_per_scan_cap=0)

    def _boom(*_a, **_k):
        raise AssertionError("orçamento esgotado não pode tocar a rede")

    client._http = type("H", (), {"get": staticmethod(_boom)})()  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        out = client._hypertracker_leaderboard("k", max_addresses=2)

    assert out == []
    assert any("discovery.ht_budget_exhausted" in r.getMessage()
               and "leaderboard" in r.getMessage() for r in caplog.records)
