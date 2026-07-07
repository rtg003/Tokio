"""Risk enforcement — the SINGLE point of enforcement, inside the gateway.

Runners never self-police: every intent passes through `check_intent` before
touching the ExchangeAdapter. Enforced here:

- max notional per order, per-strategy exposure cap, total exposure cap;
- global leverage ceiling (also truncated by the exchange's asset max);
- daily loss circuit breaker: pauses ALL strategies and notifies;
- kill switch: CLI command + sentinel file (KILL) checked on every cycle —
  cancels open orders and halts execution across all runners;
- per-strategy rate-limit budget (a hungry strategy cannot eat the others'
  budget; a fraction is always reserved for cancels).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.core.config import Settings
from engine.gateway.ledger import Ledger


@dataclass(frozen=True)
class RiskVerdict:
    allowed: bool
    reason: str = "ok"
    # When set, the intent is allowed but must be truncated so its notional does
    # not exceed this ceiling (the binding cap). None = no truncation needed.
    max_notional_usd: float | None = None


class RateBudget:
    """Sliding-window request budget per strategy."""

    def __init__(self, per_minute: int, reserve_for_cancels: float) -> None:
        self.per_minute = per_minute
        self.reserve = reserve_for_cancels
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def try_consume(self, strategy_id: str, *, is_cancel: bool = False) -> bool:
        now = time.monotonic()
        window_start = now - 60.0
        limit = self.per_minute if is_cancel else int(self.per_minute * (1 - self.reserve))
        with self._lock:
            hits = [t for t in self._hits.get(strategy_id, []) if t >= window_start]
            if len(hits) >= limit:
                self._hits[strategy_id] = hits
                return False
            hits.append(now)
            self._hits[strategy_id] = hits
            return True


class RiskEnforcer:
    def __init__(
        self,
        settings: Settings,
        ledger: Ledger,
        *,
        logger: Any | None = None,
        notifier: Any | None = None,
        kill_file: Path | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.logger = logger
        self.notifier = notifier
        self.kill_file = kill_file or settings.kill_file
        self.rate_budget = RateBudget(
            settings.rate_limit.default_strategy_budget_per_min,
            settings.rate_limit.reserve_for_cancels,
        )
        self._circuit_open = False
        self._daily_loss_day: str | None = None
        self._lock = threading.Lock()

    # -- kill switch -----------------------------------------------------
    def kill_switch_engaged(self) -> bool:
        return self.kill_file.exists()

    def engage_kill_switch(self, reason: str = "manual") -> None:
        self.kill_file.write_text(f"{time.time()}: {reason}\n")
        if self.logger:
            self.logger.error("killswitch.engaged", {"reason": reason})
        if self.notifier:
            self.notifier.send(f"🛑 KILL SWITCH acionado: {reason}")

    # -- circuit breaker ---------------------------------------------------
    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    def record_daily_pnl(self, day: str, total_net_pnl: float) -> None:
        """Called after fills; opens the circuit when daily loss cap is hit."""
        with self._lock:
            if self._daily_loss_day != day:
                self._daily_loss_day = day
                self._circuit_open = False
            if total_net_pnl <= -abs(self.settings.risk.max_daily_loss_usd) and not self._circuit_open:
                self._circuit_open = True
                if self.logger:
                    self.logger.error(
                        "circuit_breaker.opened",
                        {"day": day, "net_pnl": total_net_pnl,
                         "cap": self.settings.risk.max_daily_loss_usd},
                    )
                if self.notifier:
                    self.notifier.send(
                        f"⛔ Circuit breaker: perda diária {total_net_pnl:.2f} USD "
                        f"atingiu o cap ({self.settings.risk.max_daily_loss_usd}). "
                        "TODAS as estratégias pausadas."
                    )

    def reset_circuit(self) -> None:
        with self._lock:
            self._circuit_open = False

    # -- main gate ---------------------------------------------------------
    def check_intent(
        self,
        *,
        strategy_id: str,
        symbol: str,
        notional_usd: float,
        leverage: float | None,
        prices: dict[str, float],
        strategy_cap_usd: float | None = None,
        is_cancel: bool = False,
        reduce_only: bool = False,
    ) -> RiskVerdict:
        if self.kill_switch_engaged():
            return RiskVerdict(False, "kill_switch_engaged")
        if is_cancel:
            # Cancels bypass exposure checks (they reduce risk) but still meter.
            if not self.rate_budget.try_consume(strategy_id, is_cancel=True):
                return RiskVerdict(False, "rate_budget_exhausted_cancel")
            return RiskVerdict(True)
        if self._circuit_open and not reduce_only:
            # Closing positions stays allowed while the breaker is open.
            return RiskVerdict(False, "circuit_breaker_open")

        r = self.settings.risk
        if notional_usd < r.min_order_notional_usd:
            return RiskVerdict(False, f"below_min_notional_{r.min_order_notional_usd}")
        if leverage is not None and leverage > r.max_leverage_global:
            return RiskVerdict(False, f"exceeds_max_leverage_{r.max_leverage_global}")

        # Truncate-to-cap: instead of rejecting an oversized intent outright
        # (which leaves us with NO position), compute the binding ceiling across
        # the per-order cap and — for risk-adding orders — the per-strategy and
        # total exposure caps. The smallest applicable ceiling wins.
        ceilings: list[tuple[str, float]] = [
            ("max_order_notional", r.max_order_notional_usd)
        ]
        if not reduce_only:
            # Reduce-only orders shrink exposure; exposure caps apply only to
            # risk-adding orders.
            cap = strategy_cap_usd if strategy_cap_usd is not None else r.max_strategy_exposure_usd
            book = self.ledger.book(strategy_id)
            ceilings.append(("strategy_cap", cap - book.exposure_usd(prices)))
            total = sum(b.exposure_usd(prices) for b in self.ledger.books().values())
            ceilings.append(("total_cap", r.max_total_exposure_usd - total))

        reason_key, ceiling = min(ceilings, key=lambda c: c[1])
        truncated: float | None = None
        if notional_usd > ceiling:
            if ceiling <= 0:
                # No room at all under the binding cap → nothing to send.
                return RiskVerdict(False, f"{reason_key}_full")
            if ceiling < r.min_order_notional_usd:
                # The room left is smaller than the minimum order → truncating
                # would only produce a dust order the venue rejects.
                return RiskVerdict(False, "cap_room_below_min")
            truncated = ceiling

        if not self.rate_budget.try_consume(strategy_id):
            return RiskVerdict(False, "rate_budget_exhausted")
        if truncated is not None:
            return RiskVerdict(True, "truncated_to_cap", max_notional_usd=truncated)
        return RiskVerdict(True)
