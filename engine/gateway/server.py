"""Gateway — the ONLY process that talks to the exchange.

Runners send intents over local IPC (HTTP inside the compose network); the
database is NEVER an order bus. Flow per intent:

    intent -> risk_enforcer.check_intent -> ExchangeAdapter -> orders/fills/ledger

Control API (pause/activate/kill/scan) is exposed ONLY on the internal
network and requires the shared `GATEWAY_CONTROL_TOKEN`.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from engine.core.config import Settings, get_settings
from engine.core.db import Database, utcnow
from engine.core.logger import EventLogger
from engine.core.notifier import Notifier
from engine.exchanges.base import ExchangeAdapter, OrderRequest
from engine.gateway.ledger import Ledger, make_cloid
from engine.gateway.risk_enforcer import RiskEnforcer


class IntentRequest(BaseModel):
    strategy_id: str
    symbol: str
    side: str = Field(pattern="^(buy|sell)$")
    size: float | None = None            # base units; either size or notional_usd
    notional_usd: float | None = None
    order_type: str = "market"
    price: float | None = None
    reduce_only: bool = False
    leverage: float | None = None
    dry_run: bool = False
    subaccount_address: str | None = None
    strategy_cap_usd: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class CancelRequest(BaseModel):
    strategy_id: str
    symbol: str
    cloid: str | None = None
    exchange_order_id: str | None = None


class GatewayState:
    def __init__(
        self,
        settings: Settings,
        adapter: ExchangeAdapter,
        db: Database,
        *,
        logger: EventLogger | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.db = db
        self.logger = logger or EventLogger("gateway", settings.logs_dir, db=db)
        self.notifier = notifier or Notifier(self.logger)
        self.ledger = Ledger(self.logger)
        self.enforcer = RiskEnforcer(
            settings, self.ledger, logger=self.logger, notifier=self.notifier,
            kill_file=settings.kill_file,
        )
        self.started_at = time.time()
        self._kill_handled = False
        adapter.subscribe_own_fills(self.on_own_fill)

    # -- kill switch: cancel open orders once when engaged ------------------
    def handle_kill_engaged(self) -> int:
        """Cancel every open order (best effort). Returns cancelled count.
        Called by the control API and by the sentinel-file watcher, so the
        CLI path (KILL file) also triggers cancellation."""
        if self._kill_handled:
            return 0
        self._kill_handled = True
        open_orders = self.db.query(
            "SELECT cloid, symbol FROM orders WHERE status IN "
            "('created','sent','acked','partially_filled')")
        cancelled = 0
        for o in open_orders:
            try:
                if self.adapter.cancel(o["symbol"], cloid=o["cloid"]):
                    self.db.update_order_status(o["cloid"], "cancelled",
                                                closed_at=utcnow())
                    cancelled += 1
            except Exception as exc:  # noqa: BLE001 — keep cancelling the rest
                self.logger.error("killswitch.cancel_failed",
                                  {"cloid": o["cloid"], "error": str(exc)[:200]})
        self.logger.error("killswitch.open_orders_cancelled",
                          {"cancelled": cancelled, "total_open": len(open_orders)})
        return cancelled

    def watch_kill_file(self, interval_s: float = 2.0) -> None:
        """Background watcher: reacts to the KILL sentinel created by the CLI."""
        import threading

        def _loop() -> None:
            while True:
                if self.enforcer.kill_switch_engaged():
                    self.handle_kill_engaged()
                else:
                    self._kill_handled = False
                time.sleep(interval_s)

        threading.Thread(target=_loop, daemon=True, name="kill-watcher").start()

    # -- fills ------------------------------------------------------------
    def on_own_fill(self, fill: dict[str, Any]) -> None:
        cloid = fill.get("cloid")
        side = fill.get("side", "")
        side = {"B": "buy", "A": "sell", "buy": "buy", "sell": "sell"}.get(side, side)
        price = float(fill.get("px", 0))
        size = abs(float(fill.get("sz", 0)))
        fee = float(fill.get("fee", 0))
        realized = self.ledger.apply_fill(
            cloid=cloid, symbol=str(fill.get("coin", "")), side=side,
            price=price, size=size, fee=fee,
        )
        strategy_id = self.ledger.strategy_for_cloid(cloid)
        order_rows = self.db.query("SELECT id FROM orders WHERE cloid = ?", (cloid,)) if cloid else []
        self.db.insert("fills", {
            "order_id": order_rows[0]["id"] if order_rows else None,
            "cloid": cloid,
            "strategy_id": strategy_id,
            "symbol": fill.get("coin"),
            "side": side,
            "price": price,
            "size": size,
            "fee": fee,
            "fee_asset": fill.get("feeToken", "USDC"),
            "realized_pnl": realized,
            "ts": utcnow(),
        })
        if cloid:
            self.db.update_order_status(cloid, "filled", closed_at=utcnow())
        self.logger.info("fill.recorded", {
            "cloid": cloid, "symbol": fill.get("coin"), "side": side,
            "price": price, "size": size, "fee": fee, "realized_pnl": realized,
        }, strategy_id=strategy_id)
        self._refresh_daily_metrics(strategy_id)

    def _refresh_daily_metrics(self, strategy_id: str | None) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if strategy_id:
            rows = self.db.query(
                """SELECT COALESCE(SUM(realized_pnl), 0) AS pnl,
                          COALESCE(SUM(fee), 0) AS fees,
                          COUNT(*) AS n,
                          AVG(CASE WHEN realized_pnl IS NOT NULL THEN
                              CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END END) AS wr
                   FROM fills WHERE strategy_id = ? AND date(ts) = ?""",
                (strategy_id, day),
            )
            r = rows[0]
            self.db.upsert("strategy_metrics_daily", {
                "strategy_id": strategy_id,
                "day": day,
                "net_pnl": r["pnl"] or 0.0,
                "win_rate": r["wr"],
                "n_trades": r["n"] or 0,
                "fees": r["fees"] or 0.0,
            }, ("strategy_id", "day"))
        total = self.db.query(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS pnl FROM fills WHERE date(ts) = ?",
            (day,),
        )[0]["pnl"]
        self.enforcer.record_daily_pnl(day, float(total or 0.0))
        if self.enforcer.circuit_open:
            self.db.execute(
                "UPDATE strategies SET status = 'auto_paused' WHERE status = 'active'"
            )

    # -- intents ------------------------------------------------------------
    def handle_intent(self, intent: IntentRequest) -> dict[str, Any]:
        t0 = time.perf_counter()
        price = intent.price or self.adapter.mid_price(intent.symbol)
        if price <= 0:
            return {"ok": False, "reason": f"no_price_for_{intent.symbol}"}
        size = intent.size if intent.size is not None else (
            (intent.notional_usd or 0.0) / price)
        notional = abs(size) * price

        meta = self.adapter.market_meta(intent.symbol)
        max_lev_asset = float(meta.get("maxLeverage", 1))
        leverage = intent.leverage
        if leverage is not None:
            leverage = min(leverage, max_lev_asset,
                           self.settings.risk.max_leverage_global)

        prices = {intent.symbol: price}
        verdict = self.enforcer.check_intent(
            strategy_id=intent.strategy_id, symbol=intent.symbol,
            notional_usd=notional, leverage=leverage, prices=prices,
            strategy_cap_usd=intent.strategy_cap_usd,
            reduce_only=intent.reduce_only,
        )
        cloid = make_cloid(intent.strategy_id)
        decision_payload = {
            "cloid": cloid, "symbol": intent.symbol, "side": intent.side,
            "size": size, "notional_usd": round(notional, 4),
            "leverage": leverage, "dry_run": intent.dry_run,
            "verdict": verdict.reason,
        }
        if not verdict.allowed:
            self.logger.warning("decision.rejected", decision_payload,
                                strategy_id=intent.strategy_id)
            return {"ok": False, "reason": verdict.reason, "cloid": cloid}

        self.ledger.register_order(cloid, intent.strategy_id)
        order_row = {
            "cloid": cloid,
            "strategy_id": intent.strategy_id,
            "symbol": intent.symbol,
            "side": intent.side,
            "type": intent.order_type,
            "size": abs(size),
            "price": intent.price,
            "status": "dry_run" if intent.dry_run else "created",
            "created_at": utcnow(),
        }
        self.db.insert("orders", order_row)

        if intent.dry_run:
            # Dry-run: full decision recorded, nothing sent to the venue.
            self.logger.info("decision.dry_run", decision_payload,
                             strategy_id=intent.strategy_id)
            return {"ok": True, "dry_run": True, "cloid": cloid,
                    "would_execute": decision_payload}

        result = self.adapter.place_order(OrderRequest(
            symbol=intent.symbol, side=intent.side, size=abs(size),
            order_type=intent.order_type, price=intent.price,
            reduce_only=intent.reduce_only, cloid=cloid,
            subaccount_address=intent.subaccount_address,
        ))
        latency_ms = (time.perf_counter() - t0) * 1000
        status = result.status if result.ok else (result.status or "error")
        self.db.update_order_status(
            cloid, status, sent_at=utcnow(),
            acked_at=utcnow() if result.ok else None,
            latency_ms=latency_ms,
            reject_reason=result.error,
        )
        log_fn = self.logger.info if result.ok else self.logger.error
        log_fn("order.result", {**decision_payload, "status": status,
                                "error": result.error,
                                "exchange_order_id": result.exchange_order_id},
               strategy_id=intent.strategy_id, latency_ms=latency_ms)
        return {"ok": result.ok, "cloid": cloid, "status": status,
                "filled_size": result.filled_size, "avg_price": result.avg_price,
                "error": result.error, "latency_ms": latency_ms}

    def handle_cancel(self, req: CancelRequest) -> dict[str, Any]:
        verdict = self.enforcer.check_intent(
            strategy_id=req.strategy_id, symbol=req.symbol, notional_usd=0,
            leverage=None, prices={}, is_cancel=True,
        )
        if not verdict.allowed:
            return {"ok": False, "reason": verdict.reason}
        ok = self.adapter.cancel(req.symbol, req.exchange_order_id, req.cloid)
        if req.cloid and ok:
            self.db.update_order_status(req.cloid, "cancelled", closed_at=utcnow())
        self.logger.info("order.cancel", {"cloid": req.cloid, "ok": ok},
                         strategy_id=req.strategy_id)
        return {"ok": ok}


def _control_auth(x_control_token: str = Header(default="")) -> None:
    expected = os.environ.get("GATEWAY_CONTROL_TOKEN", "")
    if not expected or x_control_token != expected:
        raise HTTPException(status_code=401, detail="invalid control token")


def build_app(state: GatewayState) -> FastAPI:
    app = FastAPI(title="tokio-gateway", docs_url=None, redoc_url=None)
    app.state.gateway = state

    @app.get("/health")
    def health() -> dict[str, Any]:
        from engine.core.db import replication_lag_seconds

        return {
            "ok": True,
            "uptime_s": round(time.time() - state.started_at, 1),
            "exchange": state.adapter.name,
            "network": state.adapter.network,
            "kill_switch": state.enforcer.kill_switch_engaged(),
            "circuit_breaker": state.enforcer.circuit_open,
            "replication_queue_depth": state.db.queue_depth(),
            "replication_lag_s": round(replication_lag_seconds(state.db), 1),
        }

    @app.post("/intent")
    def intent(req: IntentRequest) -> dict[str, Any]:
        return state.handle_intent(req)

    @app.post("/cancel")
    def cancel(req: CancelRequest) -> dict[str, Any]:
        return state.handle_cancel(req)

    @app.get("/ledger")
    def ledger() -> dict[str, Any]:
        return state.ledger.snapshot()

    @app.get("/positions")
    def positions() -> list[dict[str, Any]]:
        return [vars(p) for p in state.adapter.positions()]

    _balance_cache: dict[str, Any] = {"ts": 0.0, "data": None}

    @app.get("/balance")
    def balance() -> dict[str, Any]:
        # 30s cache: balance queries hit the venue's rate-limited info API.
        now = time.time()
        if _balance_cache["data"] is None or now - _balance_cache["ts"] > 30:
            try:
                balances = state.adapter.balances()
            except Exception as exc:  # noqa: BLE001 — venue hiccup must not 500 the UI
                return {"ok": False, "error": str(exc)[:200],
                        "network": state.adapter.network}
            _balance_cache["data"] = balances
            _balance_cache["ts"] = now
        data = _balance_cache["data"]
        return {
            "ok": True,
            "equity_usd": float(data.get("USDC", 0.0)),
            "withdrawable_usd": float(data.get("withdrawable", 0.0)),
            "network": state.adapter.network,
        }

    # -- control API (internal network only; web is the only client) -------
    @app.post("/control/strategy/{strategy_id}/pause", dependencies=[Depends(_control_auth)])
    def pause_strategy(strategy_id: str) -> dict[str, Any]:
        state.db.execute("UPDATE strategies SET status = 'paused' WHERE id = ?", (strategy_id,))
        state.logger.info("strategy.paused", {"by": "control_api"}, strategy_id=strategy_id)
        return {"ok": True}

    @app.post("/control/strategy/{strategy_id}/activate", dependencies=[Depends(_control_auth)])
    def activate_strategy(strategy_id: str) -> dict[str, Any]:
        # Activation via API only re-enables strategies previously active;
        # dry_run -> active promotion is a human gate (CLI + evidence in docs/).
        rows = state.db.query("SELECT status FROM strategies WHERE id = ?", (strategy_id,))
        if not rows:
            raise HTTPException(404, "unknown strategy")
        if rows[0]["status"] not in ("paused", "auto_paused"):
            raise HTTPException(409, f"cannot activate from status {rows[0]['status']}")
        state.db.execute("UPDATE strategies SET status = 'active' WHERE id = ?", (strategy_id,))
        state.logger.info("strategy.activated", {"by": "control_api"}, strategy_id=strategy_id)
        return {"ok": True}

    @app.post("/control/kill", dependencies=[Depends(_control_auth)])
    def kill(reason: str = "control_api") -> dict[str, Any]:
        state.enforcer.engage_kill_switch(reason)
        cancelled = state.handle_kill_engaged()
        return {"ok": True, "open_orders_cancelled": cancelled}

    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    db = Database(settings.sqlite_path)
    db.migrate()
    from engine.exchanges.hyperliquid.adapter import make_adapter

    adapter = make_adapter(settings.exchange.active, settings.exchange.network)
    state = GatewayState(settings, adapter, db)
    state.watch_kill_file()
    app = build_app(state)
    # GATEWAY_BIND overrides the listen address (VPS: 127.0.0.1 — nothing
    # from the engine is ever exposed publicly; see ADR 0007).
    bind = os.environ.get("GATEWAY_BIND", settings.gateway.host)
    port = int(os.environ.get("GATEWAY_PORT", settings.gateway.port))
    state.logger.info("health.gateway_start", {
        "exchange": adapter.name, "network": adapter.network, "bind": bind,
    })
    uvicorn.run(app, host=bind, port=port)


if __name__ == "__main__":
    main()
