"""Signal Validator — determinístico, zero LLM (§8.2).

Função PURA: recebe o sinal já parseado, a config da estratégia e um
`ValidatorContext` com TUDO pré-buscado (relógio, kill switch, idempotência,
market data, posições, ledger). Não faz I/O — isso a torna testável e
reprodutível. O `worker` monta o contexto (DB + gateway); os testes injetam
fakes.

Checklist ordenado (1–13). Cada check é logado com `required` vs `actual`,
mesmo passando. A PRIMEIRA falha encerra; os demais viram `skipped`. Resultado
sempre persistido com o array completo (`tv_signal_decisions.checks`).

Check 9 (spread) exige `bbo(symbol)` no adapter — que só chega na F1 (§8.4.1).
Em F0 o contexto traz `bbo=None` ⇒ check 9 `skipped` (sem execução). A LÓGICA do
check existe e é exercitada por teste com bbo injetado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from engine.tv import netting
from engine.tv.models import ParsedSignal, StrategyConfig, valid_combination

PASS, FAIL, SKIP = "pass", "fail", "skipped"


@dataclass
class ValidatorContext:
    """Estado pré-buscado. Todos os campos são primitivos → validator é puro."""
    now_epoch: float
    kill_switch: bool = False
    duplicate: bool = False
    # symbol map: coin resolvida e se está habilitada (None = não mapeado).
    coin: str | None = None
    symbol_enabled: bool | None = None
    # market data do ambiente da estratégia.
    mid: float | None = None                 # None = MARKET_DATA_UNAVAILABLE (check 8)
    bbo: tuple[float, float] | None = None    # (bid, ask); None em F0 ⇒ check 9 skipped
    # posição/estado do módulo no ambiente.
    position_size: float | None = None        # posição desta estratégia na coin
    symbol_locked_by: str | None = None        # outra estratégia segurando a coin (check 11)
    current_adds: int = 0
    # ledger do módulo no ambiente (check 10).
    trades_today: int = 0
    daily_loss_usd: float = 0.0                # magnitude da perda (>0 = perdeu)
    in_cooldown: bool = False


@dataclass
class Decision:
    outcome: str                               # APPROVED | BLOCKED | DUPLICATE
    block_code: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    netting_plan: list[dict[str, Any]] | None = None
    computed_size_usd: float | None = None

    def as_row(self) -> dict[str, Any]:
        import json
        return {
            "outcome": self.outcome,
            "block_code": self.block_code,
            "checks": json.dumps(self.checks, ensure_ascii=False),
            "netting_plan": (json.dumps(self.netting_plan, ensure_ascii=False)
                             if self.netting_plan is not None else None),
            "computed_size_usd": self.computed_size_usd,
        }


def _bar_time_epoch(bar_time: str) -> float | None:
    """bar_time do TradingView: epoch ms ({{time}}) ou ISO-8601. None se ilegível."""
    s = str(bar_time).strip()
    if s.isdigit():
        v = float(s)
        return v / 1000.0 if v > 1e11 else v   # ms → s
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def compute_size_usd(sizing: dict[str, Any], stop_loss_pct: float | None) -> float | None:
    """Sizing §6.3 (fixed_fractional). quarter_kelly é recalculado por job do
    sistema e persistido em `sizing` — aqui usamos o valor vigente; nunca em
    tempo real. Retorna None se faltam dados essenciais."""
    allocation = _f(sizing.get("allocation_usd"))
    if allocation is None or allocation <= 0:
        return None
    method = sizing.get("method", "fixed_fractional")
    max_pos = _f(sizing.get("max_position_usd"))

    if method == "quarter_kelly":
        kelly = sizing.get("kelly") or {}
        frac = _f(kelly.get("fraction")) or 0.25
        size = allocation * frac
    else:  # fixed_fractional (default)
        risk_pct = _f(sizing.get("risk_per_trade_pct"))
        if risk_pct is None or stop_loss_pct is None or stop_loss_pct <= 0:
            return None
        size = (risk_pct / 100.0 * allocation) / (stop_loss_pct / 100.0)

    if max_pos is not None and max_pos > 0:
        size = min(size, max_pos)
    return round(size, 2)


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def validate(sig: ParsedSignal, cfg: StrategyConfig | None,
             ctx: ValidatorContext, *,
             url_secret_ok: bool, payload_secret_ok: bool) -> Decision:
    """Roda o checklist. `cfg=None` = estratégia desconhecida (check 2)."""
    checks: list[dict[str, Any]] = []
    n = 0

    def rec(name: str, required: Any, actual: Any, ok: bool) -> bool:
        nonlocal n
        n += 1
        checks.append({"n": n, "check": name, "required": required,
                       "actual": actual, "result": PASS if ok else FAIL})
        return ok

    def skip_rest(from_check: str, remaining: list[str]) -> None:
        nonlocal n
        for name in remaining:
            n += 1
            checks.append({"n": n, "check": name, "required": None,
                           "actual": None, "result": SKIP})

    all_checks = [
        "schema_and_secrets", "strategy_active", "kill_switch", "symbol",
        "timeframe", "idempotency", "staleness", "price_deviation", "spread",
        "limits", "symbol_lock", "netting", "sizing",
    ]

    def block(code: str, idx: int, outcome: str = "BLOCKED") -> Decision:
        skip_rest(all_checks[idx], all_checks[idx + 1:])
        return Decision(outcome=outcome, block_code=code, checks=checks)

    # 1 — Schema + secrets (URL + da estratégia) + combinação válida.
    combo_ok = valid_combination(sig.action, sig.market_position)
    secrets_ok = url_secret_ok and payload_secret_ok
    if not rec("schema_and_secrets",
               {"url_secret": True, "payload_secret": True, "valid_combo": True},
               {"url_secret": url_secret_ok, "payload_secret": payload_secret_ok,
                "valid_combo": combo_ok},
               combo_ok and secrets_ok):
        code = "INVALID_COMBINATION" if not combo_ok else "AUTH_FAILED"
        if not url_secret_ok:
            code = "AUTH_FAILED"
        return block(code, 0)

    # 2 — Estratégia existe e status = active.
    status = cfg.status if cfg else "unknown"
    if not rec("strategy_active", "active", status, cfg is not None and cfg.is_active):
        if cfg is None:
            code = "STRATEGY_UNKNOWN"
        elif status in ("disabled", "draft"):
            code = "STRATEGY_DISABLED"
        else:
            code = "STRATEGY_PAUSED"
        return block(code, 1)

    # 3 — Kill switch global desligado (fonte única: /health.kill_switch).
    if not rec("kill_switch", False, ctx.kill_switch, not ctx.kill_switch):
        return block("KILL_SWITCH_ACTIVE", 2)

    # 4 — Símbolo mapeado e permitido.
    if ctx.symbol_enabled is None or ctx.coin is None:
        rec("symbol", {"mapped": True}, {"mapped": False, "ticker": sig.ticker}, False)
        return block("SYMBOL_UNMAPPED", 3)
    allowed = (not cfg.symbols_allowed) or (ctx.coin in cfg.symbols_allowed)
    if not rec("symbol",
               {"enabled": True, "in_allowed": cfg.symbols_allowed or "any"},
               {"coin": ctx.coin, "enabled": ctx.symbol_enabled, "in_allowed": allowed},
               ctx.symbol_enabled and allowed):
        return block("SYMBOL_NOT_ALLOWED", 3)

    # 5 — Timeframe permitido.
    tf_ok = (not cfg.timeframes_allowed) or (sig.timeframe in cfg.timeframes_allowed)
    if not rec("timeframe", cfg.timeframes_allowed or "any", sig.timeframe, tf_ok):
        return block("TIMEFRAME_NOT_ALLOWED", 4)

    # 6 — Idempotência (TTL 24h imposto na consulta do contexto).
    if not rec("idempotency", "unique", "duplicate" if ctx.duplicate else "unique",
               not ctx.duplicate):
        return block("DUPLICATE", 5, outcome="DUPLICATE")

    # 7 — Staleness: now − bar_time ≤ max_signal_age_seconds.
    max_age = _f(cfg.execution_guards.get("max_signal_age_seconds")) or 90.0
    bar_epoch = _bar_time_epoch(sig.bar_time)
    age = None if bar_epoch is None else max(0.0, ctx.now_epoch - bar_epoch)
    stale_ok = age is not None and age <= max_age
    if not rec("staleness", {"max_age_s": max_age},
               {"age_s": None if age is None else round(age, 1)}, stale_ok):
        return block("SIGNAL_STALE", 6)

    # 8 — Desvio de preço vs mid do ambiente da estratégia.
    max_dev = _f(cfg.execution_guards.get("max_price_deviation_pct")) or 0.5
    if ctx.mid is None:
        rec("price_deviation", {"max_dev_pct": max_dev}, {"mid": None}, False)
        return block("MARKET_DATA_UNAVAILABLE", 7)
    dev_pct = None
    if sig.price:
        dev_pct = abs(sig.price - ctx.mid) / ctx.mid * 100.0
    dev_ok = dev_pct is None or dev_pct <= max_dev
    if not rec("price_deviation", {"max_dev_pct": max_dev},
               {"mid": ctx.mid, "signal_price": sig.price,
                "dev_pct": None if dev_pct is None else round(dev_pct, 3)}, dev_ok):
        return block("PRICE_DEVIATION", 7)

    # 9 — Spread (ask−bid)/mid. Requer bbo (F1). Em F0, bbo=None ⇒ skipped.
    max_spread_bps = _f(cfg.execution_guards.get("max_spread_bps")) or 10.0
    if ctx.bbo is None:
        n += 1
        checks.append({"n": n, "check": "spread",
                       "required": {"max_spread_bps": max_spread_bps},
                       "actual": {"bbo": None, "note": "bbo indisponível (F0)"},
                       "result": SKIP})
    else:
        bid, ask = ctx.bbo
        mid = ctx.mid or ((bid + ask) / 2.0)
        spread_bps = (ask - bid) / mid * 10000.0 if mid else None
        if spread_bps is None:
            rec("spread", {"max_spread_bps": max_spread_bps}, {"mid": mid}, False)
            return block("MARKET_DATA_UNAVAILABLE", 8)
        if not rec("spread", {"max_spread_bps": max_spread_bps},
                   {"spread_bps": round(spread_bps, 2)}, spread_bps <= max_spread_bps):
            return block("SPREAD_TOO_WIDE", 8)

    # 10 — Limites: trades/dia, perda diária (ledger do módulo no ambiente), cooldown.
    rr = cfg.risk_rules
    max_trades = int(_f(rr.get("max_trades_per_day")) or 5)
    max_loss = _f(rr.get("max_daily_loss_usd")) or 100.0
    limits_actual = {"trades_today": ctx.trades_today, "daily_loss_usd": ctx.daily_loss_usd,
                     "in_cooldown": ctx.in_cooldown}
    limits_required = {"max_trades_per_day": max_trades, "max_daily_loss_usd": max_loss,
                       "cooldown": False}
    if ctx.trades_today >= max_trades:
        rec("limits", limits_required, limits_actual, False)
        return block("LIMIT_TRADES_PER_DAY", 9)
    if ctx.daily_loss_usd >= max_loss:
        rec("limits", limits_required, limits_actual, False)
        return block("LIMIT_DAILY_LOSS", 9)
    if ctx.in_cooldown:
        rec("limits", limits_required, limits_actual, False)
        return block("LIMIT_COOLDOWN", 9)
    rec("limits", limits_required, limits_actual, True)

    # 11 — Exclusividade de símbolo entre estratégias no MESMO ambiente.
    if not rec("symbol_lock", {"locked_by_other": None},
               {"locked_by": ctx.symbol_locked_by},
               ctx.symbol_locked_by is None):
        return block("SYMBOL_LOCKED_BY_STRATEGY", 10)

    # 12 — Netting (mesma estratégia) via position_policy.
    pp = cfg.position_policy
    plan = netting.plan_netting(
        market_position=sig.market_position,
        position_size=ctx.position_size,
        on_opposite_signal=pp.get("on_opposite_signal", "reject"),
        on_same_direction_signal=pp.get("on_same_direction_signal", "ignore"),
        max_adds=int(_f(pp.get("max_adds")) or 0),
        current_adds=ctx.current_adds,
    )
    plan_rows = [vars(i) for i in plan.intents]
    if not rec("netting", {"not_blocked": True},
               {"action": plan.action, "block_code": plan.block_code,
                "intents": plan_rows}, not plan.blocked):
        d = block(plan.block_code or "BLOCKED_OPPOSITE_POSITION", 11)
        d.netting_plan = plan_rows
        return d

    # 13 — Sizing (§6.3). Só para intenções que abrem/adicionam risco.
    computed = None
    needs_sizing = plan.action in ("open", "add", "flip")
    if needs_sizing:
        stop_pct = _f(cfg.exit_rules.get("stop_loss_pct"))
        computed = compute_size_usd(cfg.sizing, stop_pct)
        min_trade = _f(cfg.sizing.get("min_trade_usd")) or 0.0
        sizing_ok = computed is not None and computed >= min_trade
        if not rec("sizing", {"min_trade_usd": min_trade},
                   {"computed_size_usd": computed}, sizing_ok):
            d = block("SIZE_BELOW_MINIMUM", 12)
            d.netting_plan = plan_rows
            d.computed_size_usd = computed
            return d
    else:
        rec("sizing", {"applies": needs_sizing},
            {"action": plan.action, "note": "sem sizing (fechamento/no-op)"}, True)

    return Decision(outcome="APPROVED", checks=checks,
                    netting_plan=plan_rows, computed_size_usd=computed)
