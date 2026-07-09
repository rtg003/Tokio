"""Gateway — the ONLY process that talks to the exchange.

Runners send intents over local IPC (HTTP inside the compose network); the
database is NEVER an order bus. Flow per intent:

    intent -> risk_enforcer.check_intent -> ExchangeAdapter -> orders/fills/ledger

Control API (pause/activate/kill/scan) is exposed ONLY on the internal
network and requires the shared `GATEWAY_CONTROL_TOKEN`.
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
    environment: str | None = Field(default=None, pattern="^(testnet|mainnet|paper)$")
    subaccount_address: str | None = None
    strategy_cap_usd: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class CancelRequest(BaseModel):
    strategy_id: str
    symbol: str
    cloid: str | None = None
    exchange_order_id: str | None = None


class AgentPrepareRequest(BaseModel):
    env: str = Field(pattern="^(testnet|mainnet)$")
    master_address: str
    agent_name: str = "engine_gateway"


class AgentActivateRequest(BaseModel):
    env: str = Field(pattern="^(testnet|mainnet)$")
    agent_address: str
    signature: str
    nonce: int


def _build_env_adapter(
    settings: Settings, db: Database, env: str,
) -> ExchangeAdapter | None:
    """Resolve o adapter de um ambiente na ordem keyring > .env (D3).

    1. keyring: se `TOKIO_KEYRING_SECRET` configurado e há agente `active`,
       constrói o adapter com `account_address = master_address` (a wallet que
       aprovou o agent — REQUISITO rtg003) + agent key decifrada.
    2. fallback `.env` (compat durante a migração — P3 remove as chaves):
       testnet usa HL_ACCOUNT_ADDRESS/HL_AGENT_PRIVATE_KEY; mainnet usa
       HL_MAINNET_*. Sem nenhum dos dois ⇒ None (ambiente não configurado)."""
    if settings.exchange.active != "hyperliquid":
        return None
    from engine.exchanges.hyperliquid.adapter import make_adapter
    from engine.core import keyring as _keyring
    from engine.gateway import hl_agents

    if _keyring.keyring_configured():
        try:
            resolved = hl_agents.resolve_active_key(db, env)
        except Exception:  # noqa: BLE001 — keyring corrompido não pode matar o boot
            resolved = None
        if resolved is not None:
            master_address, agent_key = resolved
            return make_adapter(
                "hyperliquid", env,
                account_address=master_address, agent_private_key=agent_key,
            )
    if env == "testnet":
        if os.environ.get("HL_ACCOUNT_ADDRESS") and os.environ.get("HL_AGENT_PRIVATE_KEY"):
            return make_adapter("hyperliquid", "testnet")
    elif env == "mainnet":
        addr = os.environ.get("HL_MAINNET_ACCOUNT_ADDRESS")
        key = os.environ.get("HL_MAINNET_AGENT_PRIVATE_KEY")
        if addr and key:
            return make_adapter(
                "hyperliquid", "mainnet",
                account_address=addr, agent_private_key=key,
            )
    return None


class GatewayState:
    def __init__(
        self,
        settings: Settings,
        adapter: ExchangeAdapter,
        db: Database,
        *,
        adapters: dict[str, ExchangeAdapter] | None = None,
        logger: EventLogger | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter
        self.adapters = adapters or {adapter.network: adapter}
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
        # Caches das rotas read-only (info API é rate-limited). Ficam no state —
        # não em closures — para o reload_adapter poder invalidá-los quando a
        # conta do ambiente muda (novo master via keyring).
        self._balance_cache: dict[str, dict[str, Any]] = {}
        self._positions_cache: dict[str, dict[str, Any]] = {}
        for network, configured_adapter in self.adapters.items():
            configured_adapter.subscribe_own_fills(
                lambda fill, env=network: self.on_own_fill({**fill, "_network": env})
            )
        self._seed_exchanges()

    # -- hot-reload de adapter por ambiente (keyring > .env; D3/D5) ----------
    def reload_adapter(self, env: str) -> bool:
        """Reconstrói adapters[env] a partir do keyring (fallback .env). Sem
        signer disponível (revogação/expiração) ⇒ remove o adapter do dict —
        intents daquele env passam a falhar com 'ambiente não configurado', o
        outro ambiente segue operando (fato §1.1). Nunca deixa o gateway sem
        signer para um ambiente que TEM chave: se a construção falhar, mantém o
        adapter anterior. Retorna True se há adapter vivo no env após o reload."""
        old = self.adapters.get(env)
        try:
            adapter = _build_env_adapter(self.settings, self.db, env)
        except Exception as exc:  # noqa: BLE001 — reload não pode derrubar o gateway
            self.logger.error("adapter.reload_failed",
                              {"env": env, "error": str(exc)[:200]})
            return old is not None
        if adapter is None:
            if old is not None:
                self.adapters.pop(env, None)
                if old is not self.adapter:
                    try:
                        old.close()
                    except Exception:  # noqa: BLE001
                        pass
            self._balance_cache.pop(env, None)
            self._positions_cache.pop(env, None)
            self.logger.warning("adapter.removed", {"env": env})
            return False
        self.adapters[env] = adapter
        adapter.subscribe_own_fills(
            lambda fill, e=env: self.on_own_fill({**fill, "_network": e})
        )
        if self.adapter.network == env:
            self.adapter = adapter
        if old is not None and old is not adapter and old is not self.adapter:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
        self._seed_exchanges()
        self._balance_cache.pop(env, None)
        self._positions_cache.pop(env, None)
        self.logger.info("adapter.reloaded",
                         {"env": env, "account": adapter.account_address})
        return True

    def _seed_exchanges(self) -> None:
        for network, adapter in self.adapters.items():
            if network not in {"testnet", "mainnet"}:
                continue
            self.db.upsert("exchanges", {
                "name": adapter.name,
                "network": network,
                "status": "active",
            }, ("name", "network"))
        if self.adapter.name == "hyperliquid" and "mainnet" not in self.adapters:
            self.db.upsert("exchanges", {
                "name": "hyperliquid",
                "network": "mainnet",
                "status": "unconfigured",
            }, ("name", "network"))

    def _adapter_for(self, environment: str | None) -> ExchangeAdapter:
        network = environment or self.adapter.network
        adapter = self.adapters.get(network)
        if adapter is None:
            raise ValueError(f"ambiente não configurado: {network}")
        return adapter

    def _exchange_id_for(self, adapter: ExchangeAdapter) -> int | None:
        rows = self.db.query(
            "SELECT id FROM exchanges WHERE name = ? AND network = ?",
            (adapter.name, adapter.network),
        )
        return rows[0]["id"] if rows else None

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
        order_rows = self.db.query(
            "SELECT id, strategy_id FROM orders WHERE cloid = ?", (cloid,)
        ) if cloid else []
        strategy_id = self.ledger.strategy_for_cloid(cloid) or (
            order_rows[0]["strategy_id"] if order_rows else None
        )
        network = fill.get("_network")
        if network not in ("testnet", "mainnet") and cloid:
            ex_rows = self.db.query(
                "SELECT e.network FROM orders o "
                "JOIN exchanges e ON o.exchange_id = e.id WHERE o.cloid = ?",
                (cloid,),
            )
            if ex_rows:
                network = ex_rows[0]["network"]
        if network not in ("testnet", "mainnet"):
            network = (
                self.adapter.network
                if self.adapter.network in ("testnet", "mainnet")
                else "testnet"
            )
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
            "network": network,
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
        try:
            adapter = self._adapter_for(intent.environment)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        price = intent.price or adapter.mid_price(intent.symbol)
        if price <= 0:
            return {"ok": False, "reason": f"no_price_for_{intent.symbol}"}
        size = intent.size if intent.size is not None else (
            (intent.notional_usd or 0.0) / price)
        notional = abs(size) * price

        meta = adapter.market_meta(intent.symbol)
        # Arredondar size para sz_decimals (evita float_to_wire causes rounding)
        sz_decimals = int(meta.get("szDecimals", 0))
        if sz_decimals > 0:
            size = round(size, sz_decimals)
        else:
            size = float(round(size))  # step inteiro
        if abs(size) < 1e-12:
            return {"ok": False, "reason": "size_rounds_to_zero",
                    "cloid": make_cloid(intent.strategy_id)}
        notional = abs(size) * price
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
        # Truncate-to-cap: the enforcer allowed the intent but capped its
        # notional. Shrink the size (floor to sz_decimals so we NEVER breach the
        # cap) instead of dropping the order entirely.
        if verdict.allowed and verdict.max_notional_usd is not None:
            requested_size = size
            max_size = verdict.max_notional_usd / price
            if sz_decimals > 0:
                factor = 10 ** sz_decimals
                max_size = math.floor(max_size * factor) / factor
            else:
                max_size = float(math.floor(max_size))
            if abs(size) > max_size:
                size = math.copysign(max_size, size)
            notional = abs(size) * price
            if abs(size) < 1e-12:
                return {"ok": False, "reason": "cap_room_below_min", "cloid": cloid}
            self.logger.warning(
                "decision.truncated",
                {"cloid": cloid, "symbol": intent.symbol,
                 "requested_size": requested_size, "capped_size": size,
                 "max_notional_usd": round(verdict.max_notional_usd, 4)},
                strategy_id=intent.strategy_id)
        decision_payload = {
            "cloid": cloid, "symbol": intent.symbol, "side": intent.side,
            "size": size, "notional_usd": round(notional, 4),
            "leverage": leverage, "dry_run": intent.dry_run,
            "environment": adapter.network,
            "verdict": verdict.reason,
        }
        if not verdict.allowed:
            self.logger.warning("decision.rejected", decision_payload,
                                strategy_id=intent.strategy_id)
            return {"ok": False, "reason": verdict.reason, "cloid": cloid}

        self.ledger.register_order(cloid, intent.strategy_id)
        exchange_id = self._exchange_id_for(adapter)
        if exchange_id is None and adapter.network in {"testnet", "mainnet"}:
            self._seed_exchanges()
            exchange_id = self._exchange_id_for(adapter)
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
            "exchange_id": exchange_id,
        }
        self.db.insert("orders", order_row)

        if intent.dry_run:
            # Dry-run: full decision recorded, nothing sent to the venue.
            self.logger.info("decision.dry_run", decision_payload,
                             strategy_id=intent.strategy_id)
            return {"ok": True, "dry_run": True, "cloid": cloid,
                    "would_execute": decision_payload}

        result = adapter.place_order(OrderRequest(
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

    # CORS para o dashboard Next.js (localhost:3002) ler direto do SQLite (Bloco 1).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3002", "http://127.0.0.1:3002"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "uptime_s": round(time.time() - state.started_at, 1),
            "exchange": state.adapter.name,
            "network": state.adapter.network,
            "environments": sorted(state.adapters),
            "kill_switch": state.enforcer.kill_switch_engaged(),
            "circuit_breaker": state.enforcer.circuit_open,
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

    @app.get("/balance")
    def balance(env: str | None = None) -> dict[str, Any]:
        # 30s cache: balance queries hit the venue's rate-limited info API.
        try:
            adapter = state._adapter_for(env)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "network": env}
        now = time.time()
        cached = state._balance_cache.get(adapter.network)
        if cached is None or now - cached["ts"] > 30:
            try:
                balances = adapter.balances()
            except Exception as exc:  # noqa: BLE001 — venue hiccup must not 500 the UI
                return {"ok": False, "error": str(exc)[:200],
                        "network": adapter.network}
            state._balance_cache[adapter.network] = {"data": balances, "ts": now}
        data = state._balance_cache[adapter.network]["data"]
        return {
            "ok": True,
            "equity_usd": float(data.get("USDC", 0.0)),
            "withdrawable_usd": float(data.get("withdrawable", 0.0)),
            "network": adapter.network,
        }

    @app.get("/api/market-meta")
    def market_meta(symbol: str, environment: str | None = None) -> dict[str, Any]:
        # Asset metadata (szDecimals, maxLeverage) so callers can round size to
        # the venue's step before sending. szDecimals is the same across networks.
        try:
            adapter = state._adapter_for(environment)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        try:
            meta = adapter.market_meta(symbol)
        except KeyError:
            return {"ok": False, "reason": "unknown_symbol", "symbol": symbol}
        # mid price lets the copy-trade reconcile size absolute positions without
        # a second RTT (UPDATE-0020); best-effort — 0.0 if the venue is quiet.
        try:
            mid = float(adapter.mid_price(symbol))
        except Exception:  # noqa: BLE001 — meta is still useful without a price
            mid = 0.0
        return {"ok": True, "mid": mid, **meta}

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

    # -- traders (fonte única = tabela; ADR 0008). A dashboard autenticada é
    # caminho humano: cada mudança no combobox passa por confirmação no web.
    @app.get("/traders")
    def traders() -> list[dict[str, Any]]:
        from engine.strategies.copy_trade.traders_store import list_traders

        return list_traders(state.db)

    @app.post("/control/trader/{address}/status", dependencies=[Depends(_control_auth)])
    def trader_status(address: str, new_status: str) -> dict[str, Any]:
        from engine.strategies.copy_trade.traders_store import set_status

        if new_status == "MAINNET" and "mainnet" not in state.adapters:
            return {"ok": False, "reason": "mainnet_nao_configurado"}
        return set_status(state.db, address, new_status, by="dashboard_humano",
                          logger=state.logger, human_gate=True)

    @app.post("/control/trader/{address}/config", dependencies=[Depends(_control_auth)])
    def trader_config(address: str, fields: dict[str, Any]) -> dict[str, Any]:
        from engine.strategies.copy_trade.traders_store import update_exec_config

        # dry_run=False jamais entra por aqui — é parte do Gate 2 (CLI humana)
        fields.pop("dry_run", None)
        return update_exec_config(state.db, address, by="control_api",
                                  logger=state.logger, **fields)

    @app.post("/control/kill", dependencies=[Depends(_control_auth)])
    def kill(reason: str = "control_api") -> dict[str, Any]:
        state.enforcer.engage_kill_switch(reason)
        cancelled = state.handle_kill_engaged()
        return {"ok": True, "open_orders_cancelled": cancelled}

    # -- HL agent wallets (keyring; SPEC hl-auth §8). NÃO tocam no caminho de
    # ordem (/intent, /cancel). Leitura sem token (shape sem chaves); mutações
    # exigem GATEWAY_CONTROL_TOKEN, como o resto do control API. -------------
    @app.get("/hl/agents")
    def hl_agents_list() -> dict[str, Any]:
        from engine.core import keyring
        from engine.gateway import hl_agents

        return {
            "agents": hl_agents.list_agents(state.db),
            "adapters": sorted(state.adapters),
            "keyring_configured": keyring.keyring_configured(),
        }

    @app.post("/control/hl/agents/prepare", dependencies=[Depends(_control_auth)])
    def hl_agent_prepare(req: AgentPrepareRequest) -> dict[str, Any]:
        from engine.gateway import hl_agents

        try:
            return hl_agents.prepare(
                state.db, req.env, req.master_address, agent_name=req.agent_name,
                actor=req.master_address,
            )
        except hl_agents.HlAgentError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/control/hl/agents/activate", dependencies=[Depends(_control_auth)])
    def hl_agent_activate(req: AgentActivateRequest) -> dict[str, Any]:
        from engine.gateway import hl_agents

        try:
            result = hl_agents.activate(
                state.db, req.env, req.agent_address, req.signature, req.nonce,
            )
        except hl_agents.HlAgentError as exc:
            raise HTTPException(400, str(exc))
        if result.get("ok"):
            reloaded = state.reload_adapter(req.env)
            result["adapter_reloaded"] = reloaded
            hl_agents.audit(state.db, actor="control_api", action="adapter_reload",
                            env=req.env, detail={"reloaded": reloaded})
        return result

    @app.post("/control/hl/agents/{env}/revoke", dependencies=[Depends(_control_auth)])
    def hl_agent_revoke(env: str) -> dict[str, Any]:
        from engine.gateway import hl_agents

        if env not in ("testnet", "mainnet"):
            raise HTTPException(400, f"env inválido: {env}")
        result = hl_agents.revoke(state.db, env)
        if result.get("ok"):
            reloaded = state.reload_adapter(env)
            result["adapter_reloaded"] = reloaded
            hl_agents.audit(state.db, actor="control_api", action="adapter_reload",
                            env=env, detail={"reloaded": reloaded})
        return result

    # ------------------------------------------------------------------
    # Read-only API para o dashboard Next.js ler direto do SQLite.
    # SQLite é o único banco operacional; não há camada Supabase.
    # ADR 0010: toda visão de módulo filtra por strategy_id/módulo.
    # ------------------------------------------------------------------

    # Status permitidos no filtro de traders (tabela traders, ADR 0008).
    _TRADER_STATUSES = {
        "SUGERIDO", "SALVO", "TESTNET", "MAINNET", "REJEITADO",
    }

    def _strategy_ids_csv(value: str | None, *, field: str = "strategy_id") -> list[str]:
        ids = [s.strip() for s in (value or "").split(",") if s.strip()]
        if not ids:
            raise HTTPException(
                400,
                f"{field} é obrigatório (ADR 0010 — isolamento de módulo)",
            )
        return ids

    def _in_clause(values: list[str]) -> str:
        return ", ".join("?" for _ in values)

    _VALID_NETWORKS = {"testnet", "mainnet"}

    def _parse_network(network: str | None) -> str | None:
        if network is None:
            return None
        value = network.strip().lower()
        if value not in _VALID_NETWORKS:
            raise HTTPException(
                400,
                f"network inválido: {network}. Valores aceitos: testnet, mainnet",
            )
        return value

    @app.get("/api/traders")
    def api_traders(status: str | None = None) -> list[dict[str, Any]]:
        """Lista traders ordenados por score DESC.

        Filtro opcional ?status= valida contra o CHECK da tabela (ADR 0008).
        Retorna todas as colunas; JSON serializável (None -> null).
        """
        try:
            if status is not None:
                status_up = status.strip().upper()
                if status_up not in _TRADER_STATUSES:
                    raise HTTPException(
                        400,
                        f"status inválido: {status}. "
                        f"Valores aceitos: {sorted(_TRADER_STATUSES)}",
                    )
                rows = state.db.query(
                    "SELECT * FROM traders WHERE status = ? ORDER BY score DESC",
                    (status_up,),
                )
            else:
                rows = state.db.query(
                    "SELECT * FROM traders ORDER BY score DESC"
                )
            from engine.strategies.copy_trade.traders_store import (
                environment_for_status,
                strategy_id_for,
            )

            enriched = []
            for r in rows:
                row = dict(r)
                row["strategy_id"] = strategy_id_for(row["address"], row.get("name"))
                row["environment"] = environment_for_status(row["status"])
                enriched.append(row)
            return enriched
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — dashboard deve ver erro, não stacktrace
            raise HTTPException(500, f"traders: {str(exc)[:200]}")

    @app.get("/api/traders/{address}")
    def api_trader(address: str) -> dict[str, Any]:
        """Um trader específico por address (chave primária, ADR 0008)."""
        try:
            rows = state.db.query(
                "SELECT * FROM traders WHERE address = ?", (address,)
            )
            if not rows:
                raise HTTPException(404, f"trader não encontrado: {address}")
            return dict(rows[0])
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"trader: {str(exc)[:200]}")

    @app.get("/api/fills/summary")
    def api_fills_summary(
        strategy_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        network: str | None = None,
    ) -> dict[str, Any]:
        """Agregados de fills no período (contagem, PnL, fees, win rate).

        ADR 0010: ?strategy_id é OBRIGATÓRIO.
        ?network=testnet|mainnet filtra pela coluna fills.network.
        """
        try:
            strategy_ids = _strategy_ids_csv(strategy_id)
            network_filter = _parse_network(network)
            where = [f"strategy_id IN ({_in_clause(strategy_ids)})"]
            params: list[Any] = [*strategy_ids]
            if network_filter:
                where.append("network = ?")
                params.append(network_filter)
            if since:
                where.append("ts >= ?")
                params.append(since)
            if until:
                where.append("ts <= ?")
                params.append(until)
            sql = f"""
                SELECT COUNT(*) AS n_trades,
                       COALESCE(SUM(realized_pnl), 0) AS net_pnl,
                       COALESCE(SUM(fee), 0) AS fees,
                       AVG(CASE WHEN realized_pnl IS NOT NULL AND realized_pnl > 0
                                THEN 1.0 ELSE 0.0 END) AS win_rate
                FROM fills
                WHERE {' AND '.join(where)}
            """
            rows = state.db.query(sql, params)
            row = dict(rows[0]) if rows else {}
            return {
                "n_trades": int(row.get("n_trades") or 0),
                "net_pnl": float(row.get("net_pnl") or 0),
                "fees": float(row.get("fees") or 0),
                "win_rate": row.get("win_rate"),
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"fills/summary: {str(exc)[:200]}")

    @app.get("/api/fills")
    def api_fills(
        strategy_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        network: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fills ordenados por id DESC.

        ADR 0010: ?strategy_id é OBRIGATÓRIO — dashboard de copy trade só
        vê fills do módulo copy_trade (strategy_id começa com 'ct_').
        Sem filtro = 400 (não expor dados cross-módulo).
        ?network=testnet|mainnet filtra pela coluna fills.network.
        """
        try:
            strategy_ids = _strategy_ids_csv(strategy_id)
            network_filter = _parse_network(network)
            limit = max(1, min(int(limit), 500))
            where = [f"strategy_id IN ({_in_clause(strategy_ids)})"]
            params: list[Any] = [*strategy_ids]
            if network_filter:
                where.append("network = ?")
                params.append(network_filter)
            if since:
                where.append("ts >= ?")
                params.append(since)
            if until:
                where.append("ts <= ?")
                params.append(until)
            params.append(limit)
            rows = state.db.query(
                f"SELECT * FROM fills WHERE {' AND '.join(where)} "
                "ORDER BY id DESC LIMIT ?",
                params,
            )
            return [dict(r) for r in rows]
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"fills: {str(exc)[:200]}")

    @app.get("/api/strategies")
    def api_strategies() -> list[dict[str, Any]]:
        """Strategies ordenados por id (visão de sistema — sem filtro de módulo)."""
        try:
            rows = state.db.query("SELECT * FROM strategies ORDER BY id")
            return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"strategies: {str(exc)[:200]}")

    @app.get("/api/events")
    def api_events(
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Events ordenados por id DESC.

        Filtro opcional ?event_type= (ex: discovery.scan_completed).
        Visão de sistema: pode incluir events sem strategy_id (anomalia a
        investigar, ADR 0010 §5.1) — estes são de discovery/sistema.
        """
        try:
            limit = max(1, min(int(limit), 500))
            if event_type:
                rows = state.db.query(
                    "SELECT * FROM events WHERE event_type = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                rows = state.db.query(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
                )
            # payload é JSON em texto — já é serializável como string.
            return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"events: {str(exc)[:200]}")

    @app.get("/api/stats")
    def api_stats() -> dict[str, Any]:
        """Estatísticas do discovery: último scan, aprovados, reprovados, funil.

        Lê do último evento discovery.scan_completed (events) + contagem
        atual de traders por status (tabela traders).
        """
        try:
            # Último scan_completed.
            scan_rows = state.db.query(
                "SELECT ts, payload FROM events "
                "WHERE event_type = 'discovery.scan_completed' "
                "ORDER BY id DESC LIMIT 1"
            )
            last_scan: dict[str, Any] | None = None
            if scan_rows:
                import json as _json
                try:
                    payload = _json.loads(scan_rows[0]["payload"])
                except Exception:  # noqa: BLE001 — payload quebrado não derruba o endpoint
                    payload = {}
                last_scan = {
                    "ts": scan_rows[0]["ts"],
                    "scan_id": payload.get("scan_id"),
                    "logic_version": payload.get("logic_version"),
                    "approved": payload.get("approved"),
                    "rejected": payload.get("rejected"),
                    "funnel_stats": payload.get("funnel_stats"),
                    "requests_used": payload.get("requests_used"),
                    "duration_s": payload.get("duration_s"),
                    "reason": payload.get("reason"),
                }

            # Contagem por status (traders).
            status_rows = state.db.query(
                "SELECT status, COUNT(*) AS n FROM traders GROUP BY status"
            )
            by_status = {r["status"]: r["n"] for r in status_rows}

            # Total de traders.
            total_row = state.db.query(
                "SELECT COUNT(*) AS n FROM traders"
            )
            total_traders = total_row[0]["n"] if total_row else 0

            return {
                "last_scan": last_scan,
                "traders_total": total_traders,
                "traders_by_status": by_status,
                "aprovados": by_status.get("SUGERIDO", 0),
                "reprovados": by_status.get("REJEITADO", 0),
                "salvos": by_status.get("SALVO", 0),
                "testnet": by_status.get("TESTNET", 0),
                "mainnet": by_status.get("MAINNET", 0),
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"stats: {str(exc)[:200]}")

    # -- /api/exchanges (lista de exchanges configurados) -------------------
    @app.get("/api/exchanges")
    def api_exchanges():
        try:
            rows = state.db.query("SELECT * FROM exchanges ORDER BY id")
            return rows
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"exchanges: {str(exc)[:200]}")

    # -- /api/orders (orders com filtro strategy_id, ADR 0010) -------------
    @app.get("/api/orders")
    def api_orders(
        strategy_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        network: str | None = None,
        limit: int = 50,
    ):
        try:
            limit = max(1, min(500, limit))
            strategy_ids = _strategy_ids_csv(strategy_id)
            network_filter = _parse_network(network)
            where = [f"o.strategy_id IN ({_in_clause(strategy_ids)})"]
            params: list[Any] = [*strategy_ids]
            if network_filter:
                where.append("e.network = ?")
                params.append(network_filter)
            if since:
                where.append("o.created_at >= ?")
                params.append(since)
            if until:
                where.append("o.created_at <= ?")
                params.append(until)
            params.append(limit)
            join = "JOIN exchanges e ON o.exchange_id = e.id" if network_filter else (
                "LEFT JOIN exchanges e ON o.exchange_id = e.id"
            )
            rows = state.db.query(
                f"SELECT o.*, e.network AS network FROM orders o "
                f"{join} "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY o.id DESC LIMIT ?",
                params,
            )
            return [dict(r) for r in rows]
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"orders: {str(exc)[:200]}")

    def _scoped_positions(
        strategy_ids: list[str], network_filter: str | None,
    ) -> list[dict[str, Any]]:
        """Posições da venue ESCOPADAS aos símbolos que o(s) strategy_id(s)
        negocia(m) (ADR 0010 §5.1). A venue não atribui posição por strategy_id,
        então aproximamos o isolamento pelos símbolos com ordens/fills daquele(s)
        strategy_id(s). 15s de cache (info API é rate-limited). Falha da venue ou
        ambiente não configurado devolve [] (não derruba a UI)."""
        try:
            adapter = state._adapter_for(network_filter)
        except ValueError:
            return []   # ambiente não configurado → sem posições
        placeholders = _in_clause(strategy_ids)
        sym_rows = state.db.query(
            f"SELECT DISTINCT symbol FROM orders WHERE strategy_id IN ({placeholders}) "
            f"UNION "
            f"SELECT DISTINCT symbol FROM fills WHERE strategy_id IN ({placeholders})",
            [*strategy_ids, *strategy_ids],
        )
        symbols = {r["symbol"] for r in sym_rows}
        if not symbols:
            return []
        now = time.time()
        cached = state._positions_cache.get(adapter.network)
        if cached is None or now - cached["ts"] > 15:
            try:
                fetched = [vars(p) for p in adapter.positions()]
            except Exception:  # noqa: BLE001 — venue hiccup não pode 500 a UI
                return []
            state._positions_cache[adapter.network] = {"data": fetched, "ts": now}
        data = state._positions_cache[adapter.network]["data"]
        return [{**p, "network": adapter.network}
                for p in data if p.get("symbol") in symbols]

    @app.get("/api/positions")
    def api_positions(
        strategy_id: str | None = None,
        network: str | None = None,
    ) -> list[dict[str, Any]]:
        """Posições abertas no clearinghouse do ambiente, ESCOPADAS aos símbolos
        que o módulo negocia (ADR 0010 §5.1).

        ?strategy_id é OBRIGATÓRIO; ?network=testnet|mainnet escolhe o adapter
        (default = ambiente configurado). Falha da venue devolve [] (não derruba
        a UI)."""
        try:
            strategy_ids = _strategy_ids_csv(strategy_id)
            network_filter = _parse_network(network)
            return _scoped_positions(strategy_ids, network_filter)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"positions: {str(exc)[:200]}")

    @app.get("/api/pnl/summary")
    def api_pnl_summary(
        strategy_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        network: str | None = None,
    ) -> dict[str, Any]:
        """PnL realizado (fills) + não-realizado (posições abertas na venue).

        O KPI mostrava $0 porque só somávamos realized_pnl e posições abertas têm
        realized_pnl NULL. ?strategy_id é OBRIGATÓRIO (ADR 0010 §5.1);
        ?network=testnet|mainnet filtra fills e escolhe o adapter. Falha da venue
        → unrealized_pnl = 0 (não derruba a UI)."""
        try:
            strategy_ids = _strategy_ids_csv(strategy_id)
            network_filter = _parse_network(network)
            where = [f"strategy_id IN ({_in_clause(strategy_ids)})"]
            params: list[Any] = [*strategy_ids]
            if network_filter:
                where.append("network = ?")
                params.append(network_filter)
            if since:
                where.append("ts >= ?")
                params.append(since)
            if until:
                where.append("ts <= ?")
                params.append(until)
            sql = f"""
                SELECT COUNT(*) AS n_trades,
                       COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
                       COALESCE(SUM(fee), 0) AS fees,
                       AVG(CASE WHEN realized_pnl IS NOT NULL AND realized_pnl > 0
                                THEN 1.0 ELSE 0.0 END) AS win_rate
                FROM fills
                WHERE {' AND '.join(where)}
            """
            rows = state.db.query(sql, params)
            row = dict(rows[0]) if rows else {}
            realized = float(row.get("realized_pnl") or 0)
            positions = _scoped_positions(strategy_ids, network_filter)
            unrealized = sum(float(p.get("unrealized_pnl") or 0) for p in positions)
            return {
                "n_trades": int(row.get("n_trades") or 0),
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "total_pnl": realized + unrealized,
                "fees": float(row.get("fees") or 0),
                "win_rate": row.get("win_rate"),
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"pnl/summary: {str(exc)[:200]}")

    # -- /api/metrics (daily metrics scoped by strategy_ids, ADR 0010) -----
    @app.get("/api/metrics")
    def api_metrics(
        strategy_ids: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ):
        try:
            ids = _strategy_ids_csv(strategy_ids, field="strategy_ids")
            where = [f"strategy_id IN ({_in_clause(ids)})"]
            params: list[Any] = [*ids]
            if since:
                where.append("day >= ?")
                params.append(since)
            if until:
                where.append("day <= ?")
                params.append(until)
            rows = state.db.query(
                f"SELECT * FROM strategy_metrics_daily WHERE {' AND '.join(where)} "
                "ORDER BY day DESC",
                params,
            )
            return rows
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"metrics: {str(exc)[:200]}")

    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    db = Database(settings.sqlite_path)
    db.migrate()
    from engine.exchanges.hyperliquid.adapter import make_adapter

    if settings.exchange.active == "hyperliquid":
        # Resolução keyring > .env por ambiente (D3). O adapter default é o do
        # ambiente configurado; garantimos que ele exista (senão o gateway não
        # tem signer para o env ativo — falha explícita melhor que silenciosa).
        default_env = settings.exchange.network
        adapters: dict[str, ExchangeAdapter] = {}
        for env in ("testnet", "mainnet"):
            built = _build_env_adapter(settings, db, env)
            if built is not None:
                adapters[env] = built
        adapter = adapters.get(default_env)
        if adapter is None:
            # Sem keyring nem .env para o ambiente ativo: cai no comportamento
            # legado (make_adapter lê HL_ACCOUNT_ADDRESS/HL_AGENT_PRIVATE_KEY e
            # estoura se ausentes) — startup guard explícito.
            adapter = make_adapter(settings.exchange.active, default_env)
            adapters[adapter.network] = adapter
    else:
        adapter = make_adapter(settings.exchange.active, settings.exchange.network)
        adapters = {adapter.network: adapter}
    state = GatewayState(settings, adapter, db, adapters=adapters)
    state.watch_kill_file()
    app = build_app(state)
    # GATEWAY_BIND overrides the listen address (VPS: 127.0.0.1 — nothing
    # from the engine is ever exposed publicly; see ADR 0007).
    bind = os.environ.get("GATEWAY_BIND", settings.gateway.host)
    port = int(os.environ.get("GATEWAY_PORT", settings.gateway.port))
    state.logger.info("health.gateway_start", {
        "exchange": adapter.name, "network": adapter.network, "bind": bind,
        "environments": sorted(adapters),
    })
    uvicorn.run(app, host=bind, port=port)


if __name__ == "__main__":
    main()
