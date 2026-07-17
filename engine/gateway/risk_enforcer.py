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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from engine.core.config import Settings
from engine.gateway.ledger import Ledger


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        active_ids_provider: Callable[[], set[str] | None] | None = None,
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
        # Fix 1a: fonte dos strategy_ids que DEVEM contar no total_cap (status in
        # active/dry_run). Callable p/ desacoplar do DB e ficar testável; None =
        # sem filtro (compat/back-compat de testes que não injetam o provider).
        self._active_ids_provider = active_ids_provider
        self._active_ids_cache: set[str] | None = None
        self._active_ids_ts = 0.0
        self._last_orphan_log = 0.0
        # Fix 2: circuit breaker ESCOPADO por (wallet, environment). Cada escopo
        # é independente — uma perda em 0x4124/testnet NUNCA pausa 0xd2c7/mainnet
        # (isolamento de wallet, AGENTS.md §5.1/§5.2). {(wallet,env): {open,
        # opened_at, net_pnl, cap}}.
        self._breakers: dict[tuple[str, str], dict[str, Any]] = {}
        self._breaker_day: str | None = None
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

    # -- circuit breaker (per wallet+environment) --------------------------
    @property
    def circuit_open(self) -> bool:
        """OR global de todos os escopos abertos — só p/ display legado
        (/health.circuit_breaker). NUNCA alimenta o check_intent (que é escopado)."""
        with self._lock:
            return any(b["open"] for b in self._breakers.values())

    def is_open(self, wallet: str | None, environment: str | None) -> bool:
        if wallet is None or environment is None:
            return False
        with self._lock:
            b = self._breakers.get((wallet, environment))
            return bool(b and b["open"])

    def open_breakers(self) -> list[dict[str, Any]]:
        """Snapshot dos escopos p/ o /health e a UI."""
        with self._lock:
            return [
                {"wallet": w, "environment": e, "open": b["open"],
                 "opened_at": b.get("opened_at"), "net_pnl": b.get("net_pnl"),
                 "cap": b.get("cap")}
                for (w, e), b in self._breakers.items() if b["open"]
            ]

    def record_daily_pnl(
        self, day: str, per_scope: dict[tuple[str, str], float]
    ) -> list[tuple[str, str]]:
        """Chamado após fills. `per_scope` = {(wallet, environment): net_pnl do dia}
        (perdas reais; fills forced_close/synthetic já excluídos pelo chamador).
        Abre o breaker de cada escopo que atingir o cap. Devolve a lista de escopos
        que ABRIRAM AGORA (transição fechado→aberto) para o server pausar as
        estratégias, emitir eventos e persistir o estado. Escopos já reconhecidos
        hoje (reset do operador) NÃO devem estar em `per_scope` — o server os filtra
        via circuit_breaker_state.acknowledged_day (idempotência do reset)."""
        cap = abs(self.settings.risk.max_daily_loss_usd)
        newly_opened: list[tuple[str, str]] = []
        with self._lock:
            if self._breaker_day != day:
                # Rollover UTC: novo dia zera todos os escopos.
                self._breaker_day = day
                self._breakers = {}
            for scope, net_pnl in per_scope.items():
                b = self._breakers.get(scope)
                if net_pnl <= -cap and not (b and b["open"]):
                    self._breakers[scope] = {
                        "open": True, "opened_at": _utcnow_iso(),
                        "net_pnl": net_pnl, "cap": cap,
                    }
                    newly_opened.append(scope)
        for wallet, environment in newly_opened:
            net_pnl = per_scope[(wallet, environment)]
            if self.logger:
                self.logger.error(
                    "circuit_breaker.opened",
                    {"day": day, "wallet": wallet, "environment": environment,
                     "net_pnl": net_pnl, "cap": cap},
                )
            if self.notifier:
                self.notifier.send(
                    f"⛔ Circuit breaker [{wallet} · {environment}]: perda diária "
                    f"{net_pnl:.2f} USD atingiu o cap ({cap}). Estratégias desse "
                    "escopo pausadas (outras wallets/ambientes intactos)."
                )
        return newly_opened

    def restore_open(self, wallet: str, environment: str, day: str, *,
                     net_pnl: float | None = None, cap: float | None = None,
                     opened_at: str | None = None) -> None:
        """Rehidrata um escopo aberto do circuit_breaker_state no startup (antes do
        1º fill do dia), p/ o breaker sobreviver a restart."""
        with self._lock:
            self._breaker_day = day
            self._breakers[(wallet, environment)] = {
                "open": True, "opened_at": opened_at or _utcnow_iso(),
                "net_pnl": net_pnl, "cap": cap,
            }

    def reset_breaker(self, wallet: str | None = None,
                      environment: str | None = None) -> list[tuple[str, str]]:
        """Fecha o(s) breaker(s). Sem args → todos; com (wallet, environment) →
        só aquele escopo. Devolve os escopos que estavam abertos e foram fechados."""
        closed: list[tuple[str, str]] = []
        with self._lock:
            for scope, b in list(self._breakers.items()):
                if wallet is not None and scope[0] != wallet:
                    continue
                if environment is not None and scope[1] != environment:
                    continue
                if b["open"]:
                    b["open"] = False
                    closed.append(scope)
        return closed

    def reset_circuit(self) -> None:
        """Compat: fecha todos os escopos."""
        self.reset_breaker()

    # -- total exposure (Fix 1a) -------------------------------------------
    def _active_ids(self) -> set[str] | None:
        """Set cacheado (5 s) dos strategy_ids que contam no total_cap. None =
        sem filtro (nenhum provider injetado). Fora do custo do hot path: só um
        lookup em memória por check_intent; o refresh é raro."""
        if self._active_ids_provider is None:
            return None
        now = time.monotonic()
        if self._active_ids_cache is None or now - self._active_ids_ts > 5.0:
            try:
                self._active_ids_cache = self._active_ids_provider()
            except Exception:  # pragma: no cover - provider defensivo
                pass
            self._active_ids_ts = now
        return self._active_ids_cache

    def _total_exposure(self, prices: dict[str, float]) -> float:
        """Soma de exposição p/ o total_cap EXCLUINDO books de estratégias que não
        operam (status not in active/dry_run) e IGNORANDO books órfãos (strategy_id
        vazio/None). Posições fantasma de estratégias mortas não podem mais bloquear
        ordens reais (Fix 1a)."""
        active = self._active_ids()
        total = 0.0
        for sid, book in self.ledger.books().items():
            if not sid:
                self._log_orphan_book(book)
                continue
            if active is not None and sid not in active:
                continue
            total += book.exposure_usd(prices)
        return total

    def _log_orphan_book(self, book: Any) -> None:
        """Loga `ledger.orphan_book_ignored` no máx. 1×/hora (evita rajada)."""
        if not self.logger:
            return
        now = time.monotonic()
        if now - self._last_orphan_log < 3600.0:
            return
        self._last_orphan_log = now
        self.logger.warning(
            "ledger.orphan_book_ignored",
            {"symbols": list(getattr(book, "positions", {}).keys())},
        )

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
        wallet: str | None = None,
        environment: str | None = None,
    ) -> RiskVerdict:
        if self.kill_switch_engaged():
            return RiskVerdict(False, "kill_switch_engaged")
        if is_cancel:
            # Cancels bypass exposure checks (they reduce risk) but still meter.
            if not self.rate_budget.try_consume(strategy_id, is_cancel=True):
                return RiskVerdict(False, "rate_budget_exhausted_cancel")
            return RiskVerdict(True)
        if not reduce_only:
            # Circuit breaker ESCOPADO: bloqueia só o (wallet, environment) do intent.
            # Quando o intent traz wallet+env (hot path via adapter), consulta o
            # escopo; sem atribuição (testes/chamadores legados) cai no OR global
            # como fail-safe. Fechar posições continua liberado (reduce_only).
            if wallet is not None and environment is not None:
                blocked = self.is_open(wallet, environment)
            else:
                blocked = self.circuit_open
            if blocked:
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
            total = self._total_exposure(prices)
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
