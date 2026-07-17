"""BaseRunner — the uniform contract every strategy runner inherits.

Lifecycle: draft -> dry_run -> active -> paused/auto_paused -> archived.
Runners send intents to the gateway (never to the exchange), heartbeat
periodically, honor the kill switch, and auto-pause on threshold breach.

The uniform contract is what makes ONE operational procedure work for any
strategy (scale requirement: dozens of strategies, one runbook).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from engine.core.config import Settings, get_settings
from engine.core.db import Database
from engine.core.logger import EventLogger

TERMINAL_STATUSES = {"archived"}
RUNNABLE_STATUSES = {"dry_run", "active"}


class GatewayClient:
    """Thin IPC client used by all runners (HTTP on the internal network)."""

    def __init__(self, base_url: str | None = None) -> None:
        host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
        port = os.environ.get("GATEWAY_PORT", "8700")
        self.base_url = base_url or f"http://{host}:{port}"
        self._client = httpx.Client(base_url=self.base_url, timeout=10.0)

    def send_intent(self, **payload: Any) -> dict[str, Any]:
        resp = self._client.post("/intent", json=payload)
        resp.raise_for_status()
        return resp.json()

    def cancel(self, **payload: Any) -> dict[str, Any]:
        resp = self._client.post("/cancel", json=payload)
        resp.raise_for_status()
        return resp.json()

    def ensure_margin(self, strategy_id: str, required_usd: float,
                      environment: str | None = None) -> dict[str, Any]:
        """Garante margem perp INTRA-CONTA antes de abrir (auto-transfer spot→perp).

        O gateway resolve o adapter por `environment` e transfere na PRÓPRIA conta;
        nunca cruza wallets/ambientes. Best-effort: erros de rede não abortam a
        cópia — devolvem transferred=0 e o fluxo normal (venue/reconcile) trata."""
        try:
            resp = self._client.post("/internal/ensure-margin", json={
                "strategy_id": strategy_id,
                "required_usd": required_usd,
                "environment": environment,
            }, timeout=20.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — nunca deixa a margem abortar a cópia
            return {"transferred": 0.0, "reason": "gateway_indisponivel",
                    "error": str(exc)[:200]}

    def ledger_resync(self, strategy_id: str, symbol: str, venue_size: float,
                      *, reason: str = "drift.venue_resync",
                      environment: str | None = None) -> dict[str, Any]:
        """Persiste a correção do ledger à venue (fill sintético) quando a venue
        está flat mas o book tem size fantasma. Best-effort: erro de rede não
        aborta o reconcile — o _my_pos local já foi corrigido pelo chamador; isto
        só garante que a correção sobreviva a restart (hydrate)."""
        try:
            resp = self._client.post("/internal/ledger-resync", json={
                "strategy_id": strategy_id, "symbol": symbol,
                "venue_size": venue_size, "reason": reason,
                "environment": environment,
            })
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — nunca aborta o reconcile
            return {"ok": False, "reason": "gateway_indisponivel",
                    "error": str(exc)[:200]}

    def market_meta(self, symbol: str, environment: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol}
        if environment is not None:
            params["environment"] = environment
        resp = self._client.get("/api/market-meta", params=params)
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict[str, Any]:
        resp = self._client.get("/health")
        resp.raise_for_status()
        return resp.json()

    def balance(self, env: str | None = None) -> dict[str, Any]:
        """Equity da conta ativa do adapter (a MINHA), escopada por ambiente.

        Sem `wallet`, o `/balance` usa a conta ativa do adapter de `env`
        (testnet/mainnet) e retorna `equity_usd` (perp c/ PnL não-realizado +
        spot). O gateway já cacheia 30s por network:address."""
        params: dict[str, Any] = {}
        if env is not None:
            params["env"] = env
        resp = self._client.get("/balance", params=params)
        resp.raise_for_status()
        return resp.json()

    def ledger(self) -> dict[str, Any]:
        """Virtual per-strategy book snapshot (attributed by strategy_id).

        Primary, §5.1-attributed source for reconcile/drift:
        ledger[sid]["positions"][symbol]["size"].
        """
        resp = self._client.get("/ledger")
        resp.raise_for_status()
        return resp.json()

    def positions(self, strategy_ids: list[str],
                  network: str | None = None) -> list[dict[str, Any]]:
        """Real clearinghouse positions scoped to the given strategies (§5.1).

        Used ONLY for the venue cross-check (observability) — never to correct a
        single strategy, since venue positions aren't attributable per strategy.
        """
        if not strategy_ids:
            return []
        params: dict[str, Any] = {"strategy_id": ",".join(strategy_ids)}
        if network is not None:
            params["network"] = network
        resp = self._client.get("/api/positions", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def wait_ready(self, attempts: int = 3, delay: float = 2.0) -> bool:
        """Poll /health with backoff so a runner started before the gateway
        doesn't spam 'Connection refused'. Returns True once healthy."""
        for attempt in range(1, attempts + 1):
            try:
                self.health()
                return True
            except Exception:  # noqa: BLE001 — gateway may not be up yet
                if attempt == attempts:
                    return False
                time.sleep(delay)
        return False

    def close(self) -> None:
        self._client.close()


class BaseRunner:
    """Common lifecycle for every strategy runner (one process per strategy)."""

    module: str = "dummy"

    def __init__(
        self,
        strategy_id: str,
        *,
        settings: Settings | None = None,
        db: Database | None = None,
        gateway: GatewayClient | None = None,
        config: dict[str, Any] | None = None,
        heartbeat_interval: float = 30.0,
    ) -> None:
        self.strategy_id = strategy_id
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings.sqlite_path)
        self.gateway = gateway or GatewayClient()
        self.config = config or {}
        self.heartbeat_interval = heartbeat_interval
        self.logger = EventLogger(f"runner-{strategy_id}", self.settings.logs_dir, db=self.db)
        self._stop = threading.Event()
        self._register()

    # -- registration / status --------------------------------------------
    def _register(self) -> None:
        rows = self.db.query("SELECT id FROM strategies WHERE id = ?", (self.strategy_id,))
        if not rows:
            self.db.upsert("strategies", {
                "id": self.strategy_id,
                "module": self.module,
                "name": self.config.get("name", self.strategy_id),
                "status": self.config.get("initial_status", "dry_run"),
                "config_snapshot": json.dumps(self.config, ensure_ascii=False, default=str),
                "thresholds": json.dumps(self.config.get("thresholds", {}), ensure_ascii=False),
            }, ("id",))
            self.logger.info("strategy.registered", {"module": self.module})

    def status(self) -> str:
        rows = self.db.query("SELECT status FROM strategies WHERE id = ?", (self.strategy_id,))
        return rows[0]["status"] if rows else "draft"

    def is_dry_run(self) -> bool:
        return self.status() != "active"

    # -- intents -------------------------------------------------------------
    def send_intent(self, **payload: Any) -> dict[str, Any]:
        payload.setdefault("strategy_id", self.strategy_id)
        payload.setdefault("dry_run", self.is_dry_run())
        cap = self.config.get("max_exposure_usd")
        if cap is not None:
            payload.setdefault("strategy_cap_usd", cap)
        result = self.gateway.send_intent(**payload)
        self.logger.info("signal.intent_sent", {"payload": payload, "result": result},
                         strategy_id=self.strategy_id)
        return result

    # -- thresholds / auto-pause ----------------------------------------------
    def _breach_reason(self) -> tuple[str | None, dict[str, Any]]:
        """Evaluate configured thresholds over the eval window.

        Thresholds (all optional) in config['thresholds']:
          min_net_pnl (over eval window), min_win_rate, max_drawdown_usd,
          eval_window_days (default 7), min_trades (evaluation needs a sample).

        Returns (breach description or None, details payload).

        B3 (regra de sanidade): o PnL do breach é computado DIRETO em `fills`
        EXCLUINDO `forced_close=1` (ADL/liquidação re-hidratada), para que um
        fechamento involuntário da venue não rebaixe a strategy. `n_trades`/
        `win_rate` continuam vindo de `strategy_metrics_daily` — as métricas
        REPORTADAS (dashboard) ficam intactas; só o cálculo do breach muda.
        """
        th = self.config.get("thresholds") or {}
        if not th:
            return None, {}
        window = int(th.get("eval_window_days", 7))
        mrows = self.db.query(
            """SELECT COALESCE(SUM(n_trades),0) AS n, AVG(win_rate) AS wr
               FROM strategy_metrics_daily
               WHERE strategy_id = ? AND day >= date('now', ?)""",
            (self.strategy_id, f"-{window} days"),
        )
        m = mrows[0]
        prows = self.db.query(
            """SELECT COALESCE(SUM(realized_pnl),0) AS pnl
               FROM fills
               WHERE strategy_id = ? AND date(ts) >= date('now', ?)
                 AND COALESCE(forced_close, 0) = 0""",
            (self.strategy_id, f"-{window} days"),
        )
        pnl = prows[0]["pnl"]
        n = m["n"]
        wr = m["wr"] or 0.0
        details: dict[str, Any] = {
            "pnl": pnl, "n_trades": n, "win_rate": wr,
            "window_days": window, "thresholds": th,
        }
        if n < int(th.get("min_trades", 5)):
            return None, details
        breach: str | None = None
        if "min_net_pnl" in th and pnl < th["min_net_pnl"]:
            breach = f"net_pnl {pnl:.2f} < {th['min_net_pnl']}"
        elif "min_win_rate" in th and wr < th["min_win_rate"]:
            breach = f"win_rate {wr:.2f} < {th['min_win_rate']}"
        return breach, details

    def check_thresholds(self) -> bool:
        """Auto-pause when metrics breach configured thresholds.

        Returns True when the strategy was auto-paused. The persisted
        `strategy.auto_paused` event carries a rich payload (breach + the
        evaluated pnl/n_trades/win_rate/thresholds) for audit/dashboard (B1).
        """
        breach, details = self._breach_reason()
        if breach:
            # Guarda o status ANTERIOR p/ o auto-resume restaurar sem cruzar o
            # gate humano (um dry_run pausado jamais volta como active).
            prev_status = self.status()
            self.db.execute(
                "UPDATE strategies SET status = 'auto_paused' WHERE id = ? AND status IN ('active','dry_run')",
                (self.strategy_id,),
            )
            self.logger.warning("strategy.auto_paused",
                                {"breach": breach, "prev_status": prev_status, **details},
                                strategy_id=self.strategy_id)
            return True
        return False

    def _auto_resume_after_hours(self) -> float | None:
        """Resolve the auto-resume window: per-strategy config overrides the
        global copy_trade default (None ⇒ manual resume only, current behavior)."""
        val = self.config.get("auto_resume_after_hours")
        if val is None:
            val = getattr(self.settings.copy_trade, "auto_resume_after_hours", None)
        return float(val) if val else None

    def maybe_auto_resume(self) -> bool:
        """Auto-resume an `auto_paused` strategy after N hours if no active breach.

        B2. Reads the last `strategy.auto_paused` event timestamp; once
        `auto_resume_after_hours` has elapsed AND `_breach_reason` finds no
        current breach, flips status back to 'active' and emits
        `strategy.auto_resumed`.

        Limitation (documented follow-up): circuit-breaker pauses
        (server.py) do NOT emit `strategy.auto_paused`, so this path never sees
        them — those still require a manual/operator resume.
        """
        hours = self._auto_resume_after_hours()
        if not hours or self.status() != "auto_paused":
            return False
        rows = self.db.query(
            """SELECT ts, payload FROM events
               WHERE event_type = 'strategy.auto_paused' AND strategy_id = ?
               ORDER BY ts DESC LIMIT 1""",
            (self.strategy_id,),
        )
        if not rows or not rows[0]["ts"]:
            return False
        last = rows[0]["ts"]
        try:
            paused_at = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        except ValueError:
            return False
        if paused_at.tzinfo is None:
            paused_at = paused_at.replace(tzinfo=timezone.utc)
        elapsed_h = (datetime.now(timezone.utc) - paused_at).total_seconds() / 3600.0
        if elapsed_h < hours:
            return False
        breach, _ = self._breach_reason()
        if breach:
            return False  # still breaching — keep paused
        # Restaura o status ANTERIOR (respeita o gate humano: dry_run não vira
        # active). Sem prev_status registrado, assume 'active' (comportamento
        # dos eventos antigos, que só ocorriam sobre strategies ativas).
        prev_status = "active"
        try:
            payload = json.loads(rows[0]["payload"] or "{}")
            if payload.get("prev_status") in ("active", "dry_run"):
                prev_status = payload["prev_status"]
        except (ValueError, TypeError):
            pass
        self.db.execute(
            "UPDATE strategies SET status = ? WHERE id = ? AND status = 'auto_paused'",
            (prev_status, self.strategy_id),
        )
        self.logger.info("strategy.auto_resumed",
                         {"elapsed_hours": round(elapsed_h, 2),
                          "auto_resume_after_hours": hours,
                          "restored_status": prev_status},
                         strategy_id=self.strategy_id)
        return True

    # -- lifecycle loop ---------------------------------------------------------
    def heartbeat(self) -> None:
        self.logger.info("health.heartbeat", {"status": self.status()},
                         strategy_id=self.strategy_id)

    def kill_switch_engaged(self) -> bool:
        return self.settings.kill_file.exists()

    def on_cycle(self) -> None:
        """Strategy work for one cycle. Override in subclasses."""

    def run_forever(self) -> None:
        self.logger.info("strategy.runner_start", {"module": self.module},
                         strategy_id=self.strategy_id)
        last_beat = 0.0
        while not self._stop.is_set():
            if self.kill_switch_engaged():
                self.logger.error("killswitch.runner_halt", {}, strategy_id=self.strategy_id)
                break
            st = self.status()
            if st in TERMINAL_STATUSES:
                self.logger.info("strategy.runner_exit", {"status": st},
                                 strategy_id=self.strategy_id)
                break
            now = time.monotonic()
            if now - last_beat >= self.heartbeat_interval:
                self.heartbeat()
                self.check_thresholds()
                self.maybe_auto_resume()
                last_beat = now
            if st in RUNNABLE_STATUSES:
                try:
                    self.on_cycle()
                except Exception as exc:  # noqa: BLE001 — a cycle error must not kill the process
                    self.logger.error("strategy.cycle_error", {"error": str(exc)[:500]},
                                      strategy_id=self.strategy_id)
            self._stop.wait(self.config.get("cycle_interval_s", 1.0))

    def stop(self) -> None:
        self._stop.set()
