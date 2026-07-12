"""Modelos e parsing do contrato de payload (§5) e da estratégia (§6.1).

Parsing é estrito: campo ausente/malformado ⇒ erro `SCHEMA_INVALID` com o campo
ofensor (§5.3). NUNCA "consertar" payload. `signal_key` = idempotência (§5.3).
Sizing do payload (`position_size`) é informativo — nunca vira input (§10.9).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# Combinações válidas (action, market_position) — §5.2. A direção da ordem casa
# com o estado resultante: buy↔long, sell↔short; flat fecha (buy=fecha short,
# sell=fecha long). Qualquer outra = INVALID_COMBINATION.
_VALID_EVENTS = {
    ("buy", "long"),    # abrir/adicionar long (ou 2ª perna de flip p/ long)
    ("sell", "short"),  # abrir/adicionar short (ou 2ª perna de flip p/ short)
    ("sell", "flat"),   # fechar long
    ("buy", "flat"),    # fechar short
}
_ACTIONS = {"buy", "sell"}
_POSITIONS = {"long", "short", "flat"}
_SOURCES = {"tradingview", "hermes", "manual", "test"}


def valid_combination(action: str, market_position: str) -> bool:
    return (action, market_position) in _VALID_EVENTS

_REQUIRED_FIELDS = (
    "strategy_id", "alert_id", "ticker", "action",
    "market_position", "bar_time",
)


class SchemaError(ValueError):
    """Payload malformado — carrega o campo ofensor (§5.3)."""

    def __init__(self, field_name: str, detail: str = "") -> None:
        self.field_name = field_name
        super().__init__(f"{field_name}: {detail}" if detail else field_name)


@dataclass(frozen=True)
class ParsedSignal:
    strategy_id: str
    alert_id: str
    source: str
    ticker: str
    action: str            # buy | sell
    market_position: str   # long | short | flat
    bar_time: str
    price: float | None = None
    timeframe: str | None = None
    position_size: float | None = None   # INFORMATIVO (§5.3) — nunca sizing
    comment: str | None = None
    secret: str | None = None            # do payload; validado contra o hash

    @property
    def signal_key(self) -> str:
        raw = (f"{self.strategy_id}|{self.alert_id}|{self.bar_time}"
               f"|{self.action}|{self.market_position}")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def redacted(self) -> dict[str, Any]:
        """Dict para logs SEM o secret."""
        d = {k: v for k, v in self.__dict__.items() if k != "secret"}
        return d


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SchemaError("price", f"não numérico: {value!r}")


def parse_signal(payload: dict[str, Any], *, source: str = "tradingview") -> ParsedSignal:
    """Parse estrito do payload cru. Levanta SchemaError no primeiro campo ruim."""
    if not isinstance(payload, dict):
        raise SchemaError("payload", "não é objeto JSON")
    for f in _REQUIRED_FIELDS:
        if payload.get(f) in (None, ""):
            raise SchemaError(f, "ausente")

    action = str(payload["action"]).strip().lower()
    if action not in _ACTIONS:
        raise SchemaError("action", f"inválido: {action!r}")
    market_position = str(payload["market_position"]).strip().lower()
    if market_position not in _POSITIONS:
        raise SchemaError("market_position", f"inválido: {market_position!r}")

    src = str(payload.get("source") or source).strip().lower()
    if src not in _SOURCES:
        raise SchemaError("source", f"inválido: {src!r}")

    return ParsedSignal(
        strategy_id=str(payload["strategy_id"]).strip(),
        alert_id=str(payload["alert_id"]).strip(),
        source=src,
        ticker=str(payload["ticker"]).strip(),
        action=action,
        market_position=market_position,
        bar_time=str(payload["bar_time"]).strip(),
        price=_as_float(payload.get("price")),
        timeframe=(str(payload["timeframe"]).strip()
                   if payload.get("timeframe") else None),
        position_size=_as_float(payload.get("position_size")),
        comment=(str(payload["comment"]) if payload.get("comment") else None),
        secret=(str(payload["secret"]) if payload.get("secret") else None),
    )


@dataclass(frozen=True)
class StrategyConfig:
    """Config §6.1 lida da view `tv_strategies` (strategies ⋈ tv_strategy_meta)."""
    strategy_id: str
    name: str
    status: str            # draft|dry_run|active|paused|auto_paused|archived
    environment: str       # testnet | mainnet — FONTE DE VERDADE da execução
    secret_hash: str | None
    url_secret_hash: str | None
    version: int
    symbols_allowed: list[str] = field(default_factory=list)
    timeframes_allowed: list[str] = field(default_factory=list)
    position_policy: dict[str, Any] = field(default_factory=dict)
    sizing: dict[str, Any] = field(default_factory=dict)
    risk_rules: dict[str, Any] = field(default_factory=dict)
    exit_rules: dict[str, Any] = field(default_factory=dict)
    execution_guards: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "StrategyConfig":
        cfg = row.get("config_snapshot")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg) if cfg else {}
            except json.JSONDecodeError:
                cfg = {}
        cfg = cfg or {}
        return cls(
            strategy_id=row["strategy_id"],
            name=row.get("name") or row["strategy_id"],
            status=row.get("status") or "draft",
            environment=row.get("environment") or "testnet",
            secret_hash=row.get("secret_hash"),
            url_secret_hash=row.get("url_secret_hash"),
            version=int(row.get("version") or 1),
            symbols_allowed=list(cfg.get("symbols_allowed") or []),
            timeframes_allowed=list(cfg.get("timeframes_allowed") or []),
            position_policy=dict(cfg.get("position_policy") or {}),
            sizing=dict(cfg.get("sizing") or {}),
            risk_rules=dict(cfg.get("risk_rules") or {}),
            exit_rules=dict(cfg.get("exit_rules") or {}),
            execution_guards=dict(cfg.get("execution_guards") or {}),
        )
