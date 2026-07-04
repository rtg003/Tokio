"""Fontes de candidatos do laboratório — HL pública + externas (aprender, não depender).

PRINCÍPIO (diretiva humana): fontes externas alimentam ENDEREÇOS e servem de
validação cruzada; toda métrica/filtro/score é calculado por NÓS sobre os
dados públicos da HL.

Constatações de acesso (2026-07-04):
- HyperTracker (ht-api.coinmarketman.com): OK com Bearer key — free tier
  100 req/dia, usar com parcimônia (≤ 40/dia no laboratório).
- Copin (api.copin.io/public): endpoint responde mas devolve `data: []` para
  HYPERLIQUID e outros protocolos sem chave — leaderboard público foi fechado
  atrás de auth. Adapter fica pronto; sem chave, contribui 0 endereços.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

HT_BASE = "https://ht-api.coinmarketman.com"
COPIN_BASE = "https://api.copin.io"

_ht_requests_used = 0


def ht_requests_used() -> int:
    return _ht_requests_used


def _ht_get(path: str, params: dict[str, Any] | None = None) -> Any:
    global _ht_requests_used
    key = os.environ.get("HYPERTRACKER_API_KEY", "")
    if not key:
        return None
    _ht_requests_used += 1
    r = httpx.get(f"{HT_BASE}{path}", params=params or {},
                  headers={"Authorization": f"Bearer {key}",
                           "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    time.sleep(0.5)
    return r.json()


def hypertracker_candidates(*, pages_month: int = 3, pages_week: int = 2,
                            limit: int = 100) -> list[dict[str, Any]]:
    """Leaderboard perp-only por PnL 30d e 7d — ~5 requests p/ até 500 rows.

    Retorna dicts {address, equity, pnl_month, pnl_week} (campos que vierem).
    """
    out: dict[str, dict[str, Any]] = {}
    for rank_by, pages in (("pnlMonth", pages_month), ("pnlWeek", pages_week)):
        for page in range(pages):
            try:
                data = _ht_get("/api/external/leaderboards/perp-pnl", {
                    "rankBy": rank_by, "orderBy": rank_by, "order": "desc",
                    "limit": limit, "offset": page * limit,
                })
            except Exception as exc:  # noqa: BLE001
                print(f"[hypertracker] {rank_by} p{page}: {exc}")
                break
            rows = (data or {}).get("data") or []
            if not rows:
                break
            for r in rows:
                addr = str(r.get("address") or r.get("wallet") or "").lower()
                if not addr.startswith("0x"):
                    continue
                item = out.setdefault(addr, {"address": addr})
                item.setdefault("equity", r.get("perpEquity") or r.get("equity"))
                item[f"rank_{rank_by}"] = r.get("rank")
                item[rank_by] = (r.get(rank_by) or r.get("pnl"))
    return list(out.values())


def hypertracker_segments() -> list[dict[str, Any]]:
    """As 16 coortes deles (definições) — 1 request, p/ validação cruzada."""
    try:
        data = _ht_get("/api/external/segments")
    except Exception as exc:  # noqa: BLE001
        print(f"[hypertracker] segments: {exc}")
        return []
    return data if isinstance(data, list) else (data or {}).get("data", [])


def hypertracker_wallet(address: str) -> dict[str, Any] | None:
    """Stats + segments de UMA wallet (validação cruzada em amostra pequena)."""
    try:
        data = _ht_get("/api/external/wallets", {"address": address})
    except Exception as exc:  # noqa: BLE001
        print(f"[hypertracker] wallet {address[:10]}: {exc}")
        return None
    rows = (data or {}).get("data") if isinstance(data, dict) else data
    if isinstance(rows, list):
        return rows[0] if rows else None
    return rows


def copin_candidates(*, limit: int = 100, window: str = "D30") -> list[str]:
    """Trader Explorer público do Copin — HOJE devolve vazio sem chave.

    Mantido funcional: se o humano fornecer COPIN_API_KEY, os headers são
    enviados e a fonte passa a contribuir.
    """
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("COPIN_API_KEY", "")
    if key:
        headers["x-api-key"] = key
    try:
        r = httpx.post(
            f"{COPIN_BASE}/public/HYPERLIQUID/position/statistic/filter",
            headers=headers, timeout=30,
            json={"pagination": {"limit": limit, "offset": 0},
                  "queries": [{"fieldName": "type", "value": window}],
                  "sortBy": "realisedPnl", "sortType": "desc"})
        r.raise_for_status()
        rows = r.json().get("data", [])
        return [str(x.get("account", "")).lower() for x in rows
                if str(x.get("account", "")).startswith("0x")]
    except Exception as exc:  # noqa: BLE001
        print(f"[copin] filter: {exc}")
        return []
