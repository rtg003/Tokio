"""Webhook Receiver — FastAPI, container novo (porta 8702, bind 127.0.0.1) (§8.1).

Fluxo: recebe → persiste raw ANTES do parse → enfileira em `tv_queue` → 202 em
<500ms. A validação pesada (checklist §8.2) roda no worker, assíncrona. O
receiver só faz o barato e síncrono: rate-limit, autenticação de secret (path +
payload) para dar 401 rápido em sinal forjado (T1), e o enqueue.

Isolamento: `/tv/{url_secret}` é público (atrás do Caddy, allowlist de IPs do TV);
`/signals/internal` exige token interno (Hermes/manual/teste). `raw_payload`
sempre persistido para auditoria — sinal com secret errado fica `REJECTED`,
nunca vira sinal válido.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from engine.core.config import Settings, get_settings
from engine.core.db import Database
from engine.core.logger import EventLogger
from engine.tv import store
from engine.tv.validator import Decision

IP_LIMIT_PER_MIN = 30
STRATEGY_LIMIT_PER_MIN = 10


class SlidingWindow:
    """Rate-limit por chave em janela deslizante de 60s (em memória)."""

    def __init__(self, limit_per_min: int) -> None:
        self.limit = limit_per_min
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and dq[0] < now - 60.0:
                dq.popleft()
            if len(dq) >= self.limit:
                return False
            dq.append(now)
            return True


def _client_ip(request: Request) -> str:
    # Atrás do Caddy: confiar no X-Forwarded-For (1º IP). Fallback = peer.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _peek_strategy_id(raw: bytes) -> str | None:
    try:
        obj = json.loads(raw)
        sid = obj.get("strategy_id")
        return str(sid) if sid else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def build_app(*, settings: Settings | None = None,
              db: Database | None = None,
              internal_token: str | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = db or Database(settings.sqlite_path)
    logger = EventLogger("tv-receiver", settings.logs_dir, db=db)
    token = (internal_token if internal_token is not None
             else os.environ.get("TV_INTERNAL_TOKEN", ""))
    ip_rl = SlidingWindow(IP_LIMIT_PER_MIN)
    strat_rl = SlidingWindow(STRATEGY_LIMIT_PER_MIN)

    app = FastAPI(title="tokio-tv-receiver", docs_url=None, redoc_url=None)

    def _enqueue(source: str, raw_text: str, source_ip: str | None) -> int:
        signal_id = store.persist_raw(db, source=source, raw_payload=raw_text,
                                      source_ip=source_ip)
        store.enqueue(db, signal_id)
        return signal_id

    @app.post("/tv/{url_secret}")
    async def tv_webhook(url_secret: str, request: Request) -> JSONResponse:
        t0 = time.perf_counter()
        ip = _client_ip(request)
        if not ip_rl.allow(ip):
            return JSONResponse({"ok": False, "reason": "rate_limited_ip"}, status_code=429)
        raw = await request.body()
        raw_text = raw.decode("utf-8", errors="replace")
        sid = _peek_strategy_id(raw)
        if sid and not strat_rl.allow(sid):
            return JSONResponse({"ok": False, "reason": "rate_limited_strategy"},
                                status_code=429)

        url_hash = store.sha256_hex(url_secret)
        # Autenticação rápida (T1): estratégia + secret do path + secret do payload.
        cfg = store.get_strategy(db, sid) if sid else None
        payload_secret = _peek_secret(raw)
        auth_ok = (cfg is not None
                   and bool(cfg.url_secret_hash) and url_hash == cfg.url_secret_hash
                   and bool(cfg.secret_hash) and payload_secret is not None
                   and store.sha256_hex(payload_secret) == cfg.secret_hash)
        if not auth_ok:
            # Persistir para auditoria como REJECTED — nunca como sinal válido.
            signal_id = store.persist_raw(db, source="tradingview", raw_payload=raw_text,
                                          source_ip=ip)
            store.record_decision(db, signal_id, Decision(
                outcome="BLOCKED", block_code="AUTH_FAILED",
                checks=[{"n": 1, "check": "schema_and_secrets",
                         "required": {"url_secret": True, "payload_secret": True},
                         "actual": {"strategy_found": cfg is not None},
                         "result": "fail"}]))
            store.update_signal(db, signal_id, strategy_id=sid, state="REJECTED")
            logger.warning("tv.auth_failed", {"strategy_id": sid, "ip": ip})
            return JSONResponse({"ok": False, "reason": "auth_failed"}, status_code=401)

        signal_id = _enqueue("tradingview", raw_text, ip)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        logger.info("tv.signal_received", {"signal_id": signal_id, "strategy_id": sid,
                                           "ip": ip}, strategy_id=sid,
                    latency_ms=latency_ms)
        return JSONResponse({"ok": True, "signal_id": signal_id, "state": "QUEUED"},
                            status_code=202)

    @app.post("/signals/internal")
    async def signals_internal(request: Request,
                               x_internal_token: str = Header(default="")) -> JSONResponse:
        import hmac
        if not token or not hmac.compare_digest(x_internal_token, token):
            return JSONResponse({"ok": False, "reason": "invalid_token"}, status_code=401)
        raw = await request.body()
        raw_text = raw.decode("utf-8", errors="replace")
        source = _peek_source(raw) or "manual"
        if source not in ("hermes", "manual", "test"):
            return JSONResponse({"ok": False, "reason": "invalid_source"}, status_code=422)
        sid = _peek_strategy_id(raw)
        if sid and not strat_rl.allow(sid):
            return JSONResponse({"ok": False, "reason": "rate_limited_strategy"},
                                status_code=429)
        signal_id = _enqueue(source, raw_text, _client_ip(request))
        logger.info("tv.internal_signal", {"signal_id": signal_id, "source": source,
                                           "strategy_id": sid}, strategy_id=sid)
        return JSONResponse({"ok": True, "signal_id": signal_id, "state": "QUEUED"},
                            status_code=202)

    @app.get("/tv/healthz")
    def healthz() -> dict[str, Any]:
        pending = db.query("SELECT COUNT(*) AS n FROM tv_queue WHERE status = 'pending'")
        processing = db.query(
            "SELECT COUNT(*) AS n FROM tv_queue WHERE status = 'processing'")
        gateway_ok = True
        try:
            from engine.strategies.base_runner import GatewayClient
            GatewayClient().health()
        except Exception:  # noqa: BLE001
            gateway_ok = False
        return {"ok": True, "receiver": "online",
                "queue": {"pending": int(pending[0]["n"]),
                          "processing": int(processing[0]["n"])},
                "gateway": "online" if gateway_ok else "degraded"}

    return app


def _peek_secret(raw: bytes) -> str | None:
    try:
        v = json.loads(raw).get("secret")
        return str(v) if v else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def _peek_source(raw: bytes) -> str | None:
    try:
        v = json.loads(raw).get("source")
        return str(v).strip().lower() if v else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def main() -> None:  # pragma: no cover — entrypoint
    import uvicorn
    settings = get_settings()
    app = build_app(settings=settings)
    port = int(os.environ.get("TV_RECEIVER_PORT", "8702"))
    bind = os.environ.get("TV_RECEIVER_BIND", "127.0.0.1")
    uvicorn.run(app, host=bind, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
