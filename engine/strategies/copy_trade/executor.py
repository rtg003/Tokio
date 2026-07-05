"""Copy-trade executor — event-driven mirroring, 100% deterministic.

One isolated process for the module; each copied trader is its own strategy
(`ct_<name>`). A fonte ÚNICA de traders é a tabela `traders` (ADR 0008):
o executor espelha quem está em TESTNET/MAINNET e recarrega a tabela
periodicamente (mudanças via API de controle entram sem restart). Reading
target fills over WebSocket is public market data; every ORDER goes to the
gateway as an intent — never straight to the venue.

Mirroring model (see strategy.md):
- target opens from flat  -> open with `value` USDC (fixed) or proportional
  notional (percent mode: value * my_equity / target_equity);
- target scales the position by factor k -> ours scales by the same k;
- target goes flat -> close ours entirely (reduce-only).

`startPosition` from Hyperliquid fills anchors the target position so a missed
event cannot silently corrupt the ratio. Latency target->mirror is logged on
every trade; a periodic drift check compares expected vs. ledger positions.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field

from engine.core.config import Settings, get_settings
from engine.core.db import Database
from engine.core.logger import EventLogger
from engine.strategies.base_runner import GatewayClient
from engine.strategies.copy_trade.traders_store import (
    environment_for_status,
    operable_traders,
    strategy_id_for,
)

DRIFT_TOLERANCE = 0.05


class TraderConfig(BaseModel):
    name: str
    address: str
    mode: str = Field(default="fixed_usdc", pattern="^(fixed_usdc|percent)$")
    value: float = 50.0
    max_leverage: float = 3.0
    blocked_assets: list[str] = Field(default_factory=list)
    status: str = "SUGERIDO"      # da tabela traders
    dry_run: bool = True          # legado: status TESTNET/MAINNET decide execução
    thresholds: dict[str, float] = Field(default_factory=dict)

    @property
    def strategy_id(self) -> str:
        return strategy_id_for(self.address, self.name)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TraderConfig":
        return cls(
            name=row.get("name") or row["address"][2:10],
            address=row["address"],
            mode=row.get("mode", "fixed_usdc"),
            value=float(row.get("value", 50.0)),
            max_leverage=float(row.get("max_leverage", 3.0)),
            blocked_assets=json.loads(row.get("blocked_assets") or "[]"),
            status=row.get("status", "SUGERIDO"),
            dry_run=bool(row.get("dry_run", 1)),
            thresholds=json.loads(row.get("thresholds") or "{}"),
        )


class FillWatcher(Protocol):
    """Subscription source for third-party fills (WS in prod, fake in tests)."""

    def subscribe(self, address: str, callback: Callable[[dict[str, Any]], None]) -> None: ...


class HyperliquidWatcher:
    """Read-only WS subscription to arbitrary addresses via the official SDK."""

    def __init__(self, network: str = "testnet") -> None:
        from engine.exchanges.hyperliquid.adapter import MAINNET_API_URL, TESTNET_API_URL
        from hyperliquid.info import Info

        base = TESTNET_API_URL if network == "testnet" else MAINNET_API_URL
        self.info = Info(base_url=base, skip_ws=False)

    def subscribe(self, address: str, callback: Callable[[dict[str, Any]], None]) -> None:
        def _handler(msg: dict[str, Any]) -> None:
            data = msg.get("data", {})
            if data.get("isSnapshot"):
                return  # snapshots replay history; we only mirror live fills
            for fill in data.get("fills", []):
                callback(fill)

        self.info.subscribe({"type": "userFills", "user": address}, _handler)

    def target_equity(self, address: str) -> float:
        state = self.info.user_state(address)
        return float(state.get("marginSummary", {}).get("accountValue", 0))


class CopyTradeExecutor:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        db: Database | None = None,
        gateway: GatewayClient | None = None,
        watcher: FillWatcher,
        my_equity_fn: Callable[[], float] | None = None,
        target_equity_fn: Callable[[str], float] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings.sqlite_path)
        self.gateway = gateway or GatewayClient()
        self.watcher = watcher
        self.logger = EventLogger("runner-copytrade", self.settings.logs_dir, db=self.db)
        self.my_equity_fn = my_equity_fn or (lambda: 1_000.0)
        self.target_equity_fn = target_equity_fn or (lambda _addr: 0.0)
        self.traders: dict[str, TraderConfig] = {}
        self._subscribed: set[str] = set()
        # (strategy_id, symbol) -> sizes for mirroring + drift check
        self._target_pos: dict[tuple[str, str], float] = {}
        self._my_pos: dict[tuple[str, str], float] = {}
        self.reload_traders()

    # -- setup ---------------------------------------------------------------
    def reload_traders(self) -> None:
        """Recarrega da tabela `traders` (fonte única). Novos TESTNET/MAINNET
        ganham subscrição WS; quem saiu desses estados para de ser espelhado
        (o status é checado a cada fill de qualquer forma)."""
        for row in operable_traders(self.db):
            cfg = TraderConfig.from_row(row)
            self.traders[cfg.strategy_id] = cfg
            self._register_strategy(cfg)
            if cfg.address not in self._subscribed:
                self._subscribed.add(cfg.address)
                self.watcher.subscribe(
                    cfg.address,
                    lambda fill, sid=cfg.strategy_id: self.on_target_fill(sid, fill),
                )
                self.logger.info("ws.subscribed_target", {"address": cfg.address},
                                 strategy_id=cfg.strategy_id)

    def _register_strategy(self, cfg: TraderConfig) -> None:
        rows = self.db.query("SELECT id FROM strategies WHERE id = ?", (cfg.strategy_id,))
        if not rows:
            self.db.upsert("strategies", {
                "id": cfg.strategy_id,
                "module": "copy_trade",
                "name": cfg.name,
                "status": "active",
                "config_snapshot": json.dumps(cfg.model_dump(), ensure_ascii=False),
                "thresholds": json.dumps(cfg.thresholds, ensure_ascii=False),
            }, ("id",))

    def _strategy_status(self, strategy_id: str) -> str:
        rows = self.db.query("SELECT status FROM strategies WHERE id = ?", (strategy_id,))
        return rows[0]["status"] if rows else "draft"

    def _trader_status(self, address: str) -> str:
        rows = self.db.query("SELECT status FROM traders WHERE address = ?",
                             (address.lower(),))
        return rows[0]["status"] if rows else "REJEITADO"

    def _is_dry_run(self, cfg: TraderConfig) -> bool:
        # O combobox da dashboard é o ato humano. Uma vez em TESTNET/MAINNET,
        # a ordem é real no respectivo ambiente se a strategy ct_* estiver ativa.
        return (self._trader_status(cfg.address) not in ("TESTNET", "MAINNET")
                or self._strategy_status(cfg.strategy_id) != "active")

    # -- mirroring core ----------------------------------------------------------
    def on_target_fill(self, strategy_id: str, fill: dict[str, Any]) -> dict[str, Any] | None:
        t0 = time.time()
        cfg = self.traders[strategy_id]
        symbol = str(fill.get("coin", ""))
        trader_status = self._trader_status(cfg.address)
        if trader_status not in ("TESTNET", "MAINNET"):
            self.logger.debug("signal.ignored_status", {"trader_status": trader_status},
                              strategy_id=strategy_id)
            return None
        status = self._strategy_status(strategy_id)
        if status not in ("dry_run", "active"):
            self.logger.debug("signal.ignored_status", {"status": status},
                              strategy_id=strategy_id)
            return None
        if symbol in cfg.blocked_assets:
            self.logger.info("decision.skipped_blocked_asset", {"symbol": symbol},
                             strategy_id=strategy_id)
            return None

        side = {"B": "buy", "A": "sell"}.get(str(fill.get("side")), str(fill.get("side")))
        signed_fill = float(fill["sz"]) * (1 if side == "buy" else -1)
        px = float(fill["px"])
        key = (strategy_id, symbol)

        # Anchor target position on startPosition when present (anti event-loss).
        if "startPosition" in fill:
            prev_target = float(fill["startPosition"])
        else:
            prev_target = self._target_pos.get(key, 0.0)
        new_target = prev_target + signed_fill
        self._target_pos[key] = new_target

        my_prev = self._my_pos.get(key, 0.0)
        my_new = self._mirror_size(cfg, prev_target, new_target, my_prev, px)
        delta = my_new - my_prev

        fill_time_ms = float(fill.get("time", t0 * 1000))
        latency_ms = max(0.0, t0 * 1000 - fill_time_ms)
        decision = {
            "symbol": symbol, "target_fill_sz": signed_fill, "px": px,
            "prev_target": prev_target, "new_target": new_target,
            "my_prev": my_prev, "my_new": my_new, "delta": delta,
        }

        if abs(delta) * px < self.settings.risk.min_order_notional_usd:
            if abs(delta) > 0:
                self.logger.info("decision.skipped_min_notional",
                                 {**decision, "notional": abs(delta) * px},
                                 strategy_id=strategy_id, latency_ms=latency_ms)
            return None

        reduce_only = abs(my_new) < abs(my_prev) or my_new == 0.0
        result = self.gateway.send_intent(
            strategy_id=strategy_id,
            symbol=symbol,
            side="buy" if delta > 0 else "sell",
            size=abs(delta),
            order_type="market",
            reduce_only=reduce_only,
            leverage=cfg.max_leverage,
            dry_run=self._is_dry_run(cfg),
            environment=environment_for_status(trader_status),
        )
        if result.get("ok"):
            self._my_pos[key] = my_new
        self.logger.info("decision.mirrored", {**decision, "result": result},
                         strategy_id=strategy_id, latency_ms=latency_ms)
        return result

    def _mirror_size(self, cfg: TraderConfig, prev_target: float, new_target: float,
                     my_prev: float, px: float) -> float:
        if new_target == 0.0:
            return 0.0
        if prev_target == 0.0 or my_prev == 0.0:
            # opening from flat (ours or theirs): size by configured mode
            if cfg.mode == "fixed_usdc":
                notional = cfg.value
            else:  # percent: proportional to equity ratio
                target_eq = self.target_equity_fn(cfg.address) or 0.0
                if target_eq <= 0:
                    self.logger.warning("decision.no_target_equity",
                                        {"address": cfg.address},
                                        strategy_id=cfg.strategy_id)
                    return my_prev
                notional = abs(new_target) * px * cfg.value * (
                    self.my_equity_fn() / target_eq)
            return (notional / px) * (1 if new_target > 0 else -1)
        # scale ours by the same factor as the target's position change
        return my_prev * (new_target / prev_target)

    # -- drift check ------------------------------------------------------------
    def drift_check(self) -> list[dict[str, Any]]:
        """Compare expected mirrored sizes vs. the gateway ledger. Alerts on >5%."""
        drifts: list[dict[str, Any]] = []
        try:
            ledger = self.gateway._client.get("/ledger").json()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("drift.check_failed", {"error": str(exc)[:200]})
            return drifts
        for (sid, symbol), expected in self._my_pos.items():
            actual = (ledger.get(sid, {}).get("positions", {})
                      .get(symbol, {}).get("size", 0.0))
            base = max(abs(expected), 1e-9)
            rel = abs(actual - expected) / base
            if expected != 0 and rel > DRIFT_TOLERANCE:
                drift = {"symbol": symbol, "expected": expected,
                         "actual": actual, "rel_drift": round(rel, 4)}
                drifts.append({"strategy_id": sid, **drift})
                self.logger.warning("drift.detected", drift, strategy_id=sid)
        return drifts

    # -- process loop --------------------------------------------------------------
    def run_forever(self, drift_interval_s: float = 60.0,
                    reload_interval_s: float = 30.0) -> None:
        self.logger.info("strategy.runner_start",
                         {"module": "copy_trade",
                          "traders": [t.strategy_id for t in self.traders.values()],
                          "copying": [t.strategy_id for t in self.traders.values()
                                      if t.status in ("TESTNET", "MAINNET")]})
        last_drift = 0.0
        last_reload = 0.0
        while True:
            if self.settings.kill_file.exists():
                self.logger.error("killswitch.runner_halt", {})
                break
            now = time.monotonic()
            if now - last_reload >= reload_interval_s:
                # tabela `traders` é a fonte única: mudanças via API de
                # controle entram sem restart do runner
                self.reload_traders()
                last_reload = now
            if now - last_drift >= drift_interval_s:
                self.drift_check()
                self.logger.info("health.heartbeat",
                                 {"targets": len(self._target_pos)})
                last_drift = now
            time.sleep(1.0)


def main() -> None:
    settings = get_settings()
    watcher = HyperliquidWatcher(settings.exchange.network)
    gateway = GatewayClient()

    def my_equity() -> float:
        try:
            return float(gateway.health().get("equity", 0)) or 1_000.0
        except Exception:  # noqa: BLE001
            return 1_000.0

    executor = CopyTradeExecutor(
        settings=settings,
        watcher=watcher,
        gateway=gateway,
        my_equity_fn=my_equity,
        target_equity_fn=watcher.target_equity,
    )
    executor.run_forever()


if __name__ == "__main__":
    main()
