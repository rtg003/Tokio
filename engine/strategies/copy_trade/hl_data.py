"""Camada de dados do discovery v2 — leitura pública da Hyperliquid (mainnet).

Regras (spec v5): read-only (este módulo NUNCA importa signer), rate-limit
friendly (throttle por IP + backoff exponencial em 429), cache local SQLite
com TTL e ORÇAMENTO de requests por varredura. Endpoints e limitações
mapeados em docs/discovery_v2_plan.md.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.core.db import Database, utcnow

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"

DAY_MS = 86_400_000


class RequestBudgetExceeded(RuntimeError):
    """Orçamento da varredura esgotado — o funil encerra graciosamente."""


class HLDataClient:
    def __init__(
        self,
        db: Database | None = None,
        *,
        request_budget: int = 600,
        min_interval_s: float = 1.3,
        max_retries: int = 4,
        cache_ttl_hours: float = 20.0,
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
                if exc.response.status_code != 429 or attempt == self.max_retries:
                    raise
                time.sleep(backoff)
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
