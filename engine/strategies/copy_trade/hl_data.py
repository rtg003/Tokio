"""Camada de dados do discovery v2 — leitura pública da Hyperliquid (mainnet).

Regras (spec v5): read-only (este módulo NUNCA importa signer), rate-limit
friendly (throttle por IP + backoff exponencial em 429), cache local SQLite
com TTL e ORÇAMENTO de requests por varredura. Endpoints e limitações
mapeados em docs/discovery_v2_plan.md.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.core.db import Database, utcnow

logger = logging.getLogger(__name__)

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"
# v15: HyperTracker (coinmarketman) — posições consolidadas + cohorts/segmentos.
HT_BASE_URL = "https://ht-api.coinmarketman.com/api/external"

DAY_MS = 86_400_000

# HyperTracker host — usado para atribuir erros HTTP ao free tier HT (UPDATE-0065).
HT_HOST = "ht-api.coinmarketman.com"


def _ht_start_iso(days: int) -> str:
    """UPDATE-0065 (a): janela `start` ISO 8601 exigida por `/positions*`.

    Os endpoints `/api/external/positions` (posições, cohort, heatmap) retornam
    HTTP 400 ("start must be a valid ISO 8601 date string") sem este parâmetro —
    era a causa do pipeline HT de posições nunca ter rodado em produção. Retorna
    o instante UTC de `days` atrás no formato ``YYYY-MM-DDTHH:MM:SSZ``."""
    start = datetime.now(timezone.utc) - timedelta(days=days)
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ht_positions_page(data: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Desembrulha UMA página de `/api/external/positions`.

    UPDATE-0068 (bug reportado no UPDATE-0066 do Hermes): o envelope REAL de
    `/api/external/positions` usa a chave ``positions`` — ``{"positions": [...],
    "nextCursor": "..."}`` — e não ``items``. O probe em produção confirmou que o
    parser antigo (só ``items``/``data``) sempre devolvia ``[]`` → ZERO traders
    com `position_metrics_source=hypertracker`. Agora aceita ``positions`` (real),
    ``items`` e ``data`` (legados) ou lista crua. Retorna
    ``(itens, próximo_cursor|None)`` — helper puro, testável sem HTTP em
    `tests/test_hl_data.py`."""
    if isinstance(data, dict):
        items = data.get("positions")
        if items is None:
            items = data.get("items")
        if items is None:
            items = data.get("data")
        cursor = data.get("nextCursor") or data.get("next_cursor")
        return (items if isinstance(items, list) else [], cursor or None)
    if isinstance(data, list):
        return (data, None)
    return ([], None)


def _parse_ht_wallet(data: Any, address: str) -> dict[str, Any]:
    """UPDATE-0057 (correção pós-validação Hermes): desembrulha a resposta do
    `/api/external/wallets`. O envelope REAL é
    ``{"totalCount": N, "items": [{"address": ..., ...}]}`` — casa o item pelo
    endereço (case-insensitive); sem match ou lista vazia → ``{}``.

    Mantém o fallback para os formatos legados ``{"data": {...}}`` /
    ``{"data": [{...}]}`` / lista por robustez (helper puro, sem HTTP →
    testável isoladamente em `tests/test_hl_data.py`)."""
    if isinstance(data, dict) and "items" in data:
        items = data.get("items") or []
        return next(
            (it for it in items
             if isinstance(it, dict)
             and str(it.get("address", "")).lower() == address.lower()),
            {},
        )
    payload: Any = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    return payload if isinstance(payload, dict) else {}


def _flatten_cohort_ids(cohorts_cfg: Any) -> list[int]:
    """Achata a config de cohorts (`{money_printer: 8, whales: [2,3,4,5]}` ou
    lista de ids) numa lista de segmentIds únicos, preservando a ordem."""
    out: list[int] = []
    seen: set[int] = set()

    def _add(value: Any) -> None:
        try:
            sid = int(value)
        except (TypeError, ValueError):
            return
        if sid not in seen:
            seen.add(sid)
            out.append(sid)

    if isinstance(cohorts_cfg, dict):
        values: Any = cohorts_cfg.values()
    elif isinstance(cohorts_cfg, (list, tuple)):
        values = cohorts_cfg
    else:
        values = []
    for v in values:
        if isinstance(v, (list, tuple)):
            for item in v:
                _add(item)
        else:
            _add(v)
    return out


class RequestBudgetExceeded(RuntimeError):
    """Orçamento da varredura esgotado — o funil encerra graciosamente."""


class HTBudgetExhausted(RuntimeError):
    """v15: orçamento do HyperTracker (free tier 100 req/dia) esgotado — a fonte
    de posições degrada para fills HL silenciosamente (soft dependency)."""


class HLDataClient:
    def __init__(
        self,
        db: Database | None = None,
        *,
        request_budget: int = 600,
        min_interval_s: float = 1.3,
        max_retries: int = 4,
        cache_ttl_hours: float = 20.0,
        ht_daily_cap: int = 90,
        ht_per_scan_cap: int = 80,
    ) -> None:
        import httpx

        self._http = httpx.Client(timeout=30.0)
        self.db = db
        self.request_budget = request_budget
        self.requests_used = 0
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self._last_request_ts = 0.0
        # v15: orçamento HyperTracker SEPARADO do budget HL (free tier 100/dia).
        # `_ht_scan_used` conta esta varredura; o total do dia UTC é persistido em
        # discovery_cache (chave `ht_budget:<dia>`) p/ sobreviver a múltiplos scans.
        self.ht_daily_cap = ht_daily_cap
        self.ht_per_scan_cap = ht_per_scan_cap
        # UPDATE-0065 (c): contagem de erros HTTP do HyperTracker por status, p/
        # distinguir falha sistêmica (ex.: 400 em massa) de soft degradation.
        self.ht_errors_by_status: dict[int, int] = {}
        self._ht_scan_used = 0
        self._ht_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._ht_day_used_base = self._load_ht_day_used()

    # -- orçamento HyperTracker (dedicado) ---------------------------------
    def _load_ht_day_used(self) -> int:
        if self.db is None:
            return 0
        rows = self.db.query(
            "SELECT payload FROM discovery_cache WHERE cache_key = ?",
            (f"ht_budget:{self._ht_day}",))
        if not rows:
            return 0
        try:
            return int(json.loads(rows[0]["payload"]).get("used", 0))
        except Exception:  # noqa: BLE001
            return 0

    @property
    def ht_requests_used(self) -> int:
        """Requests HT usados HOJE (base persistida + esta varredura)."""
        return self._ht_day_used_base + self._ht_scan_used

    def _ht_budget_available(self) -> bool:
        return (self._ht_scan_used < self.ht_per_scan_cap
                and self.ht_requests_used < self.ht_daily_cap)

    def _ht_incr(self) -> None:
        self._ht_scan_used += 1
        if self.db is not None:
            self.db.upsert("discovery_cache", {
                "cache_key": f"ht_budget:{self._ht_day}",
                "payload": json.dumps({"used": self.ht_requests_used}),
                "created_at": utcnow(),
            }, ("cache_key",))

    def _ht_get(self, key: str, path: str, params: dict[str, Any],
                api_key: str) -> Any:
        """GET no HyperTracker com cache + orçamento HT dedicado. Cache HIT não
        consome orçamento; sem orçamento levanta HTBudgetExhausted."""
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        if not self._ht_budget_available():
            raise HTBudgetExhausted(key)
        self._ht_incr()
        return self._request(key, lambda: self._http.get(
            f"{HT_BASE_URL}{path}", params=params,
            headers={"Authorization": f"Bearer {api_key}",
                     "Accept": "application/json"}))

    # -- cache -----------------------------------------------------------
    def _cache_get(self, key: str) -> Any | None:
        if self.db is None:
            return None
        rows = self.db.query(
            "SELECT payload, created_at FROM discovery_cache WHERE cache_key = ?", (key,))
        if not rows:
            return None
        created = datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created > self.cache_ttl:
            return None
        return json.loads(rows[0]["payload"])

    def _cache_put(self, key: str, value: Any) -> None:
        if self.db is None:
            return
        self.db.upsert("discovery_cache", {
            "cache_key": key,
            "payload": json.dumps(value, ensure_ascii=False, default=str),
            "created_at": utcnow(),
        }, ("cache_key",))

    # -- transport ---------------------------------------------------------
    def _request(self, key: str, do_request: Any) -> Any:
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        if self.requests_used >= self.request_budget:
            raise RequestBudgetExceeded(
                f"orçamento de {self.request_budget} requests esgotado")

        import httpx

        backoff = 5.0
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last_request_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_request_ts = time.monotonic()
            self.requests_used += 1
            try:
                resp = do_request()
                resp.raise_for_status()
                data = resp.json()
                self._cache_put(key, data)
                return data
            except httpx.HTTPStatusError as exc:
                # UPDATE-0065 (c): logar o CORPO truncado além de URL+status — sem
                # ele a mensagem do 400 ("start must be a valid ISO 8601 date
                # string") se perde. Seguro: a API key vai no header Authorization,
                # não na URL nem no corpo da resposta.
                status = exc.response.status_code
                try:
                    body = exc.response.text[:200]
                except Exception:  # noqa: BLE001
                    body = ""
                logger.warning("discovery.http_error url=%s status=%s body=%s",
                               exc.request.url, status, body)
                # Atribui o erro ao free tier HT quando o host é o do HyperTracker.
                if HT_HOST in str(exc.request.url):
                    self.ht_errors_by_status[status] = (
                        self.ht_errors_by_status.get(status, 0) + 1)
                if status != 429 or attempt == self.max_retries:
                    raise
                # v15: respeita o header Retry-After do 429 (HyperTracker manda o
                # tempo exato) quando maior que o backoff exponencial corrente.
                delay = backoff
                retry_after = exc.response.headers.get("retry-after")
                if retry_after:
                    try:
                        delay = max(backoff, float(retry_after))
                    except (TypeError, ValueError):
                        pass
                time.sleep(delay)
                backoff *= 2
        raise RuntimeError("unreachable")

    def _info(self, key: str, payload: dict[str, Any]) -> Any:
        return self._request(key, lambda: self._http.post(INFO_URL, json=payload))

    # -- endpoints ------------------------------------------------------------
    def leaderboard(self) -> list[dict[str, Any]]:
        data = self._request("leaderboard", lambda: self._http.get(LEADERBOARD_URL))
        return data.get("leaderboardRows", [])

    def fills_by_time(self, address: str, *, window_days: int = 60,
                      max_pages: int = 4) -> tuple[list[dict[str, Any]], bool]:
        """Fills paginados na janela. Returns (fills, history_truncated).

        `userFillsByTime` devolve até ~2.000 fills por chamada; pagina
        avançando o startTime. truncated=True quando estouramos max_pages
        (métricas devem usar a janela efetivamente coberta).
        """
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - window_days * DAY_MS
        fills: list[dict[str, Any]] = []
        cursor = start_ms
        truncated = False
        for page in range(max_pages):
            batch = self._info(
                f"fills:{address}:{cursor}",
                {"type": "userFillsByTime", "user": address,
                 "startTime": cursor, "endTime": now_ms},
            )
            if not isinstance(batch, list) or not batch:
                break
            fills.extend(b for b in batch if not fills or float(b["time"]) > float(fills[-1]["time"]))
            if len(batch) < 2000:
                break
            cursor = int(float(batch[-1]["time"])) + 1
            if page == max_pages - 1:
                truncated = True
        return fills, truncated

    def fills_recent(self, address: str) -> list[dict[str, Any]]:
        """Fills mais recentes (userFills, ~2.000, ordem desc).

        Fonte da análise individual — evita o viés ASC do userFillsByTime
        (que pagina do mais antigo p/ o mais novo e, em traders hiperativos,
        nunca alcança a atividade recente). Pode voltar None → tratamos como
        []. Os consumidores (metrics.simulate_copy/position_episodes) ordenam
        internamente, então a ordem desc não exige reversão.
        """
        return self._info(
            f"fills_recent:{address}",
            {"type": "userFills", "user": address},
        ) or []

    def portfolio(self, address: str) -> dict[str, Any]:
        data = self._info(f"portfolio:{address}", {"type": "portfolio", "user": address})
        # API devolve lista de pares [janela, dados]; normalizar p/ dict
        return dict(data) if not isinstance(data, dict) else data

    def clearinghouse(self, address: str) -> dict[str, Any]:
        return self._info(f"clearinghouse:{address}",
                          {"type": "clearinghouseState", "user": address})

    def ledger_updates(self, address: str, *, window_days: int = 35) -> list[dict[str, Any]]:
        """Depósitos/saques (userNonFundingLedgerUpdates) — base do TWRR/F10."""
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - window_days * DAY_MS
        data = self._info(f"ledger:{address}:{window_days}",
                          {"type": "userNonFundingLedgerUpdates", "user": address,
                           "startTime": start_ms})
        return data if isinstance(data, list) else []

    def liquid_assets(self, top_n: int = 25) -> set[str]:
        """Top N ativos por volume 24h (lista de 'líquidos' p/ F8)."""
        data = self._info("metaAndAssetCtxs", {"type": "metaAndAssetCtxs"})
        universe, ctxs = data
        ranked = sorted(
            zip([a["name"] for a in universe["universe"]],
                [float(c.get("dayNtlVlm", 0) or 0) for c in ctxs]),
            key=lambda x: -x[1],
        )
        return {name for name, _ in ranked[:top_n]}

    # v5: varredura ativa — descobrir endereços via fills públicos recentes
    def active_addresses(self, *, window_hours: int = 48,
                         max_addresses: int = 200,
                         min_notional_usd: float = 1000) -> list[str]:
        """Descobre endereços ativos via notional snapshot da HL.

        Usa o endpoint de fundingHistory (que retorna endereços recentes)
        ou, se indisponível, consulta o leaderboard expandido + endereços
        já conhecidos. Retorna lista de endereços lowercase únicos.
        """
        import httpx

        # Estratégia: usar o endpoint de big fills recentes se disponível,
        # senão expandir o leaderboard. A HL não tem endpoint público de
        # "todos os fills recentes", mas o leaderboard traz top 500.
        # Para a varredura ativa, usamos o leaderboard expandido (top 1000)
        # + endereços da tabela traders já conhecidos.
        addresses: set[str] = set()

        # 1. Leaderboard expandido (top 1000 se disponível)
        data = self._request("leaderboard_expanded",
                             lambda: self._http.get(LEADERBOARD_URL))
        rows = data.get("leaderboardRows", [])
        for row in rows:
            addr = str(row.get("ethAddress", "")).lower()
            if addr:
                addresses.add(addr)

        # 2. Endereços já conhecidos na tabela traders
        if self.db is not None:
            known = self.db.query("SELECT address FROM traders")
            for r in known:
                addresses.add(r["address"].lower())

        # 3. Limitar e retornar
        result = sorted(addresses)[:max_addresses]
        return result

    # v8: fontes EXTERNAS opcionais — só alimentam endereços candidatos.
    # HL pública continua a fonte de verdade das métricas; nada aqui é
    # dependência dura (sem chave/flag → lista vazia, sem erro).
    def external_candidates_by_source(self, sources_cfg: dict[str, Any]) -> dict[str, list[str]]:
        import os

        by_source: dict[str, list[str]] = {}

        def dedup(addresses: list[str]) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for a in addresses:
                a = a.lower()
                if a.startswith("0x") and len(a) == 42 and a not in seen:
                    seen.add(a)
                    out.append(a)
            return out

        # v9: HyperTracker ON — lab mediu +274 endereços exclusivos (+53% de
        # pool) com qualidade fora da amostra igual/melhor (RESULTADOS.md §6)
        ht = sources_cfg.get("hypertracker") or {}
        if ht.get("enabled"):
            key = os.environ.get(str(ht.get("api_key_env", "HYPERTRACKER_API_KEY")), "")
            if key:
                by_source["hypertracker"] = dedup(self._hypertracker_leaderboard(
                    key, max_addresses=int(ht.get("max_addresses", 300))))
            else:
                by_source["hypertracker"] = []
            # v15: sourcing por COHORT — wallets com posição aberta nos segmentos
            # configurados (Money Printer/Smart Money/whales). Sub-fonte própria
            # p/ contabilizar `ht_cohort_*` no funil; sem chave/orçamento → [].
            cohorts_cfg = ht.get("cohorts") or {}
            segment_ids = _flatten_cohort_ids(cohorts_cfg)
            if key and segment_ids:
                by_source["hypertracker_cohorts"] = dedup(
                    self.ht_cohort_addresses(segment_ids))
            else:
                by_source["hypertracker_cohorts"] = []
        nansen = sources_cfg.get("nansen_leaderboard") or {}
        if nansen.get("enabled"):
            key = os.environ.get(str(nansen.get("api_key_env", "NANSEN_API_KEY")), "")
            if key:
                by_source["nansen_leaderboard"] = dedup(self._nansen_leaderboard(
                    key, max_addresses=int(nansen.get("max_addresses", 100)),
                    window_days=int(nansen.get("window_days", 30))))
            else:
                by_source["nansen_leaderboard"] = []
        apify = sources_cfg.get("apify_hl_scraper") or {}
        if apify.get("enabled"):
            token = os.environ.get(str(apify.get("api_key_env", "APIFY_TOKEN")), "")
            actor = apify.get("actor")
            if token and actor:
                by_source["apify_hl_scraper"] = dedup(self._apify_scraper(
                    token, str(actor),
                    max_addresses=int(apify.get("max_addresses", 100))))
            else:
                by_source["apify_hl_scraper"] = []
        return by_source

    def external_candidates(self, sources_cfg: dict[str, Any]) -> list[str]:
        by_source = self.external_candidates_by_source(sources_cfg)
        # dedup preservando ordem
        seen: set[str] = set()
        out: list[str] = []
        for addresses in by_source.values():
            for a in addresses:
                a = a.lower()
                if a.startswith("0x") and len(a) == 42 and a not in seen:
                    seen.add(a)
                    out.append(a)
        return out

    def hypertracker_wallet(self, address: str) -> dict[str, Any]:
        """UPDATE-0057 (Fase 2): agregado do HyperTracker p/ UMA wallet
        (`/api/external/wallets`). Fonte AUTORITATIVA da idade da conta
        (`earliestActivityAt`) + enriquecimento agregado (equity/pnl/exposição)
        guardado em campos SEPARADOS — nunca substitui as métricas de trading da
        Hyperliquid (que seguem como verdade).

        SOFT dependency: sem `HYPERTRACKER_API_KEY` no ambiente, ou qualquer
        erro de rede/HTTP, retorna `{}` — a análise segue com a idade via
        `portfolio.allTime` (Fase 1) e sem enriquecimento. Só a análise
        INDIVIDUAL chama isto; o scan em massa não gasta request por wallet aqui.
        """
        import os

        api_key = os.environ.get("HYPERTRACKER_API_KEY", "")
        if not api_key:
            return {}
        try:
            data = self._request(
                f"ht_wallet:{address}",
                lambda: self._http.get(
                    "https://ht-api.coinmarketman.com/api/external/wallets",
                    params={"address": address, "limit": 1},
                    headers={"Authorization": f"Bearer {api_key}",
                             "Accept": "application/json"},
                ))
        except Exception:  # noqa: BLE001 — soft dependency, nunca derruba a análise
            logger.warning("discovery.hypertracker_wallet_error address=%s", address)
            return {}
        return _parse_ht_wallet(data, address)

    # -- v15: posições consolidadas + cohorts + heatmap --------------------
    def ht_positions(self, address: str, *, start_days: int = 60,
                     max_pages: int = 10,
                     page_limit: int = 100) -> list[dict[str, Any]]:
        """Posições CONSOLIDADAS de UMA wallet no HyperTracker
        (`/api/external/positions`), paginadas por `nextCursor`.

        Fonte PRIMÁRIA de métricas de posição (v15) — não sofre o teto de ~2.000
        fills da HL. SOFT dependency: sem `HYPERTRACKER_API_KEY`, orçamento HT
        esgotado ou erro → ``[]`` (o funil degrada para fills HL).
        """
        api_key = os.environ.get("HYPERTRACKER_API_KEY", "")
        if not api_key:
            return []
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            # UPDATE-0065 (a): `start` ISO 8601 é OBRIGATÓRIO — sem ele o endpoint
            # devolve HTTP 400 e o pipeline degrada p/ fills silenciosamente.
            params: dict[str, Any] = {"address": address, "limit": page_limit,
                                      "start": _ht_start_iso(start_days)}
            if cursor:
                params["cursor"] = cursor
            try:
                data = self._ht_get(
                    f"ht_positions:{address}:{cursor or 0}", "/positions",
                    params, api_key)
            except HTBudgetExhausted:
                logger.warning("discovery.ht_budget_exhausted endpoint=positions "
                               "address=%s", address)
                return out
            except Exception:  # noqa: BLE001 — soft dependency, degrada p/ fills
                logger.warning("discovery.ht_positions_error address=%s", address)
                return out
            items, cursor = _parse_ht_positions_page(data)
            out.extend(it for it in items if isinstance(it, dict))
            if not cursor or not items:
                break
        return out

    def ht_segments(self) -> list[dict[str, Any]]:
        """Segmentos/cohorts disponíveis no HyperTracker (`/api/external/segments`).
        SOFT dependency: sem chave/orçamento/erro → ``[]``."""
        api_key = os.environ.get("HYPERTRACKER_API_KEY", "")
        if not api_key:
            return []
        try:
            data = self._ht_get("ht_segments", "/segments", {}, api_key)
        except HTBudgetExhausted:
            logger.warning("discovery.ht_budget_exhausted endpoint=segments")
            return []
        except Exception:  # noqa: BLE001
            logger.warning("discovery.ht_segments_error")
            return []
        if isinstance(data, dict):
            items = data.get("items")
            if items is None:
                items = data.get("data")
            return items if isinstance(items, list) else []
        return data if isinstance(data, list) else []

    def ht_cohort_addresses(self, segment_ids: list[int], *,
                            start_days: int = 60,
                            max_per_segment: int = 200,
                            page_limit: int = 100) -> list[str]:
        """Wallets com posição ABERTA por segmento
        (`/positions?segmentId=X&open=true`), deduplicadas. Alimenta o funil como
        ENDEREÇOS candidatos (métricas/filtros seguem 100% nossos). SOFT
        dependency: sem chave/orçamento/erro → o que já coletou (possivelmente
        ``[]``)."""
        api_key = os.environ.get("HYPERTRACKER_API_KEY", "")
        if not api_key:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for sid in segment_ids:
            cursor: str | None = None
            collected = 0
            for _ in range(max(1, max_per_segment // page_limit) + 1):
                # UPDATE-0065 (a): `start` ISO 8601 obrigatório (mesmo validador
                # de /positions) — sem ele o cohort retornava HTTP 400.
                params: dict[str, Any] = {"segmentId": sid, "open": "true",
                                          "limit": page_limit,
                                          "start": _ht_start_iso(start_days)}
                if cursor:
                    params["cursor"] = cursor
                try:
                    data = self._ht_get(
                        f"ht_cohort:{sid}:{cursor or 0}", "/positions",
                        params, api_key)
                except HTBudgetExhausted:
                    logger.warning("discovery.ht_budget_exhausted "
                                   "endpoint=cohort segment=%s", sid)
                    return out
                except Exception:  # noqa: BLE001
                    logger.warning("discovery.ht_cohort_error segment=%s", sid)
                    break
                items, cursor = _parse_ht_positions_page(data)
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    addr = str(it.get("address", "")).lower()
                    if addr.startswith("0x") and len(addr) == 42 and addr not in seen:
                        seen.add(addr)
                        out.append(addr)
                        collected += 1
                if not cursor or not items or collected >= max_per_segment:
                    break
        return out

    def ht_heatmap(self, *, opened_within: str = "7d") -> dict[str, Any]:
        """Heatmap de posicionamento agregado do HyperTracker
        (`/positions/heatmap?openedWithin=...`). INFORMATIVO — persistido em
        `market_bias` p/ exibição; NUNCA alimenta ranking. SOFT dependency:
        sem chave/orçamento/erro → ``{}``."""
        api_key = os.environ.get("HYPERTRACKER_API_KEY", "")
        if not api_key:
            return {}
        try:
            # UPDATE-0065 (a): inclui `start` (mesmo validador de /positions),
            # mantendo `openedWithin`. O probe confirma se o heatmap exige mesmo
            # `start` ou só `openedWithin`.
            data = self._ht_get(
                f"ht_heatmap:{opened_within}", "/positions/heatmap",
                {"openedWithin": opened_within,
                 "start": _ht_start_iso(60)}, api_key)
        except HTBudgetExhausted:
            logger.warning("discovery.ht_budget_exhausted endpoint=heatmap")
            return {}
        except Exception:  # noqa: BLE001
            logger.warning("discovery.ht_heatmap_error")
            return {}
        return data if isinstance(data, dict) else {}

    def _hypertracker_leaderboard(self, api_key: str, *,
                                  max_addresses: int) -> list[str]:
        """Leaderboard perp-only do HyperTracker (free tier: 100 req/dia).

        Pagina por PnL do mês e da semana (100 rows/página) até
        `max_addresses` — ~3-5 requests por scan, com cache TTL normal.
        """
        out: list[str] = []
        pages = max(1, max_addresses // 100)
        for rank_by in ("pnlMonth", "pnlWeek"):
            for page in range(pages):
                # UPDATE-0065 (b): passa por `_ht_get` (não `_request` direto),
                # então TODO request ao free tier HT conta no orçamento persistido
                # `ht_requests_used` e respeita o cap. `HT_BASE_URL` já é
                # `.../api/external`, então a URL final é idêntica à anterior.
                try:
                    data = self._ht_get(
                        f"ht_lb:{rank_by}:{page}", "/leaderboards/perp-pnl",
                        {"rankBy": rank_by, "orderBy": rank_by, "order": "desc",
                         "limit": 100, "offset": page * 100},
                        api_key)
                except HTBudgetExhausted:
                    logger.warning("discovery.ht_budget_exhausted "
                                   "endpoint=leaderboard rankBy=%s", rank_by)
                    return out[:max_addresses]
                rows = (data or {}).get("data") or []
                if not rows:
                    break
                out += [str(r.get("address", "")).lower() for r in rows
                        if isinstance(r, dict)]
                if len(out) >= max_addresses:
                    return out[:max_addresses]
        return out[:max_addresses]

    def _nansen_leaderboard(self, api_key: str, *, max_addresses: int,
                            window_days: int) -> list[str]:
        """Leaderboard da Nansen (API paga) com janela de datas arbitrária."""
        from datetime import date, timedelta as _td

        end = date.today()
        start = end - _td(days=window_days)
        data = self._request(
            f"nansen_lb:{start}:{end}",
            lambda: self._http.post(
                "https://api.nansen.ai/api/v1/perp/hyperliquid/leaderboard",
                headers={"apiKey": api_key},
                json={"parameters": {"date": {"from": str(start), "to": str(end)}},
                      "pagination": {"page": 1, "recordsPerPage": max_addresses}},
            ))
        rows = data.get("data", data) if isinstance(data, dict) else data
        return [str(r.get("address", r.get("trader_address", ""))).lower()
                for r in rows if isinstance(r, dict)][:max_addresses]

    def _apify_scraper(self, token: str, actor: str, *,
                       max_addresses: int) -> list[str]:
        """Backup: último dataset de um actor Apify que raspa wallets da HL."""
        data = self._request(
            f"apify:{actor}",
            lambda: self._http.get(
                f"https://api.apify.com/v2/acts/{actor}/runs/last/dataset/items",
                params={"token": token, "limit": max_addresses, "status": "SUCCEEDED"},
            ))
        rows = data if isinstance(data, list) else []
        return [str(r.get("address", "")).lower()
                for r in rows if isinstance(r, dict)][:max_addresses]
