"""TradingView runner — webhook server (ADR 0004: no official signals API).

One isolated process hosting N declarative sub-strategies, each defined by
`strategies/<name>/config.yaml` + `strategy.md` with its OWN exception
boundary: one sub-strategy blowing up never takes the webhook server down.

Alert contract (configured in the TradingView alert message):

    {
      "token": "<TV_WEBHOOK_TOKEN>",
      "strategy_id": "tv_gap_fade",
      "symbol": "BTC",
      "action": "buy" | "sell" | "close",
      "sizing": {"mode": "notional_usd", "value": 50},   // optional hint
      "timestamp": "2026-07-02T12:00:00Z"                 // optional
    }

Invalid token -> 401; malformed payload -> 422; both are logged. Routing is
by `strategy_id`. Every resulting order is an intent to the gateway.
"""
from __future__ import annotations

import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from engine.core.config import Settings, get_settings
from engine.core.db import Database
from engine.core.logger import EventLogger
from engine.strategies.base_runner import GatewayClient

STRATEGIES_DIR = Path(__file__).resolve().parent / "strategies"


class SizingHint(BaseModel):
    mode: str = Field(default="notional_usd", pattern="^(notional_usd|size)$")
    value: float = Field(gt=0)


class TVAlert(BaseModel):
    token: str
    strategy_id: str
    symbol: str
    action: str = Field(pattern="^(buy|sell|close)$")
    sizing: SizingHint | None = None
    timestamp: str | None = None


class SubStrategyConfig(BaseModel):
    id: str
    symbols: list[str]
    default_notional_usd: float = 25.0
    max_notional_usd: float = 100.0
    max_leverage: float = 3.0
    thresholds: dict[str, float] = Field(default_factory=dict)


class SubStrategy:
    """Declarative sub-strategy: config + uniform alert handling."""

    def __init__(self, config: SubStrategyConfig, db: Database,
                 gateway: GatewayClient, logger: EventLogger) -> None:
        self.config = config
        self.db = db
        self.gateway = gateway
        self.logger = logger
        self._register()

    def _register(self) -> None:
        rows = self.db.query("SELECT id FROM strategies WHERE id = ?", (self.config.id,))
        if not rows:
            self.db.upsert("strategies", {
                "id": self.config.id, "module": "tradingview", "name": self.config.id,
                "status": "dry_run",
                "config_snapshot": json.dumps(self.config.model_dump(), ensure_ascii=False),
                "thresholds": json.dumps(self.config.thresholds, ensure_ascii=False),
            }, ("id",))

    def status(self) -> str:
        rows = self.db.query("SELECT status FROM strategies WHERE id = ?", (self.config.id,))
        return rows[0]["status"] if rows else "draft"

    def handle(self, alert: TVAlert) -> dict[str, Any]:
        status = self.status()
        if status not in ("dry_run", "active"):
            self.logger.info("signal.ignored_status", {"status": status},
                             strategy_id=self.config.id)
            return {"ok": False, "reason": f"strategy_status_{status}"}
        if alert.symbol not in self.config.symbols:
            self.logger.warning("decision.skipped_symbol_not_allowed",
                                {"symbol": alert.symbol,
                                 "allowed": self.config.symbols},
                                strategy_id=self.config.id)
            return {"ok": False, "reason": "symbol_not_allowed"}

        dry_run = status != "active"
        if alert.action == "close":
            size = self._open_size(alert.symbol)
            if size == 0.0:
                self.logger.info("decision.close_noop", {"symbol": alert.symbol},
                                 strategy_id=self.config.id)
                return {"ok": True, "noop": True}
            return self.gateway.send_intent(
                strategy_id=self.config.id, symbol=alert.symbol,
                side="sell" if size > 0 else "buy", size=abs(size),
                order_type="market", reduce_only=True, dry_run=dry_run,
            )

        notional = self.config.default_notional_usd
        if alert.sizing and alert.sizing.mode == "notional_usd":
            notional = alert.sizing.value
        notional = min(notional, self.config.max_notional_usd)
        payload: dict[str, Any] = {
            "strategy_id": self.config.id, "symbol": alert.symbol,
            "side": alert.action, "order_type": "market",
            "leverage": self.config.max_leverage, "dry_run": dry_run,
            "strategy_cap_usd": self.config.max_notional_usd,
        }
        if alert.sizing and alert.sizing.mode == "size":
            payload["size"] = alert.sizing.value
        else:
            payload["notional_usd"] = notional
        return self.gateway.send_intent(**payload)

    def _open_size(self, symbol: str) -> float:
        try:
            ledger = self.gateway._client.get("/ledger").json()  # noqa: SLF001
            return float(ledger.get(self.config.id, {}).get("positions", {})
                         .get(symbol, {}).get("size", 0.0))
        except Exception:  # noqa: BLE001
            return 0.0


def load_substrategies(db: Database, gateway: GatewayClient, logger: EventLogger,
                       strategies_dir: Path = STRATEGIES_DIR) -> dict[str, SubStrategy]:
    subs: dict[str, SubStrategy] = {}
    for cfg_file in sorted(strategies_dir.glob("*/config.yaml")):
        cfg = SubStrategyConfig.model_validate(yaml.safe_load(cfg_file.read_text()))
        subs[cfg.id] = SubStrategy(cfg, db, gateway, logger)
    return subs


def build_app(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    gateway: GatewayClient | None = None,
    strategies_dir: Path = STRATEGIES_DIR,
    webhook_token: str | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    db = db or Database(settings.sqlite_path)
    gateway = gateway or GatewayClient()
    logger = EventLogger("runner-tradingview", settings.logs_dir, db=db)
    token = webhook_token if webhook_token is not None else os.environ.get("TV_WEBHOOK_TOKEN", "")
    subs = load_substrategies(db, gateway, logger, strategies_dir)
    logger.info("strategy.runner_start", {"module": "tradingview",
                                          "substrategies": sorted(subs)})

    app = FastAPI(title="tokio-tradingview", docs_url=None, redoc_url=None)
    app.state.substrategies = subs

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "substrategies": sorted(subs)}

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        t0 = time.perf_counter()
        raw = await request.body()
        try:
            alert = TVAlert.model_validate_json(raw)
        except ValidationError as exc:
            logger.warning("signal.malformed_payload",
                           {"errors": json.loads(exc.json())[:5], "size": len(raw)})
            return JSONResponse({"ok": False, "reason": "malformed_payload"}, status_code=422)

        if not token or not hmac.compare_digest(alert.token, token):
            logger.warning("signal.invalid_token", {"strategy_id": alert.strategy_id})
            return JSONResponse({"ok": False, "reason": "invalid_token"}, status_code=401)

        sub = subs.get(alert.strategy_id)
        if sub is None:
            logger.warning("signal.unknown_strategy", {"strategy_id": alert.strategy_id})
            return JSONResponse({"ok": False, "reason": "unknown_strategy"}, status_code=404)

        logger.info("signal.received", alert.model_dump(exclude={"token"}),
                    strategy_id=alert.strategy_id)
        try:
            result = sub.handle(alert)
        except Exception as exc:  # noqa: BLE001 — exception boundary PER sub-strategy
            logger.error("strategy.substrategy_error",
                         {"error": str(exc)[:500]}, strategy_id=alert.strategy_id)
            return JSONResponse({"ok": False, "reason": "substrategy_error"}, status_code=500)
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info("decision.webhook_handled", {"result": result},
                    strategy_id=alert.strategy_id, latency_ms=latency_ms)
        return JSONResponse(result)

    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    app = build_app(settings=settings)
    port = int(os.environ.get("TV_WEBHOOK_PORT", "8701"))
    bind = os.environ.get("TV_WEBHOOK_BIND", "0.0.0.0")
    uvicorn.run(app, host=bind, port=port)


if __name__ == "__main__":
    main()
