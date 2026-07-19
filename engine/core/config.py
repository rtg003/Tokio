"""Typed configuration: config/settings.yaml (non-secret) + environment (.env secrets).

Secrets are NEVER read from YAML and NEVER logged. Only their PRESENCE may be
validated (see `validate_secrets_presence`).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"


class ExchangeSettings(BaseModel):
    active: str = "hyperliquid"
    network: str = "testnet"


class RiskSettings(BaseModel):
    max_order_notional_usd: float = 500.0
    max_total_exposure_usd: float = 2000.0
    max_strategy_exposure_usd: float = 500.0
    max_daily_loss_usd: float = 100.0
    max_leverage_global: float = 10.0  # UPDATE-0078: teto de risco 5→10 (aprovado pelo operador)
    min_order_notional_usd: float = 10.0


class ExecutionSettings(BaseModel):
    # Ordens market no HL são IOC agressivas (limit no mid ± slippage). Um
    # slippage fixo de 1% não cruza o book de ativos ilíquidos/voláteis (ex.:
    # HYPE) → "could not immediately match against any resting orders". Tentamos
    # cada slippage em ordem, alargando, antes de desistir.
    market_slippage_steps: list[float] = [0.05, 0.10, 0.15]


class RateLimitSettings(BaseModel):
    default_strategy_budget_per_min: int = 30
    reserve_for_cancels: float = 0.2


class WipLimits(BaseModel):
    max_active_strategies: int = 6
    max_dry_run_strategies: int = 10


class FeeSettings(BaseModel):
    taker_pct: float = 0.045
    maker_pct: float = 0.015


class PathSettings(BaseModel):
    data_dir: str = "data"
    logs_dir: str = "logs"
    kill_file: str = "KILL"


class GatewaySettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8700


class CopyTradeSettings(BaseModel):
    watch_network: str = "mainnet"
    # Reconciliação ancorada na posição real do trader (rede de segurança que
    # recupera fills perdidos e restart — UPDATE-0020). Independe do WS.
    # Mín. 60s: DEVE ser menor que RECONCILE_COOLDOWN_S (120s) para não re-enviar
    # a mesma correção antes do fill refletir no ledger (UPDATE-0023).
    reconcile_interval_s: float = 60.0
    # WS resiliente: reconecta se ficar silencioso além do timeout; backoff máx.
    ws_stale_timeout_s: float = 35.0
    ws_reconnect_max_backoff_s: float = 60.0
    # Auto-transfer spot→perp INTRA-CONTA (wallet+ambiente). Na HL spot e perp são
    # pools de margem separados — USDC no spot não cobre perp sem
    # usd_class_transfer. testnet liga por padrão; mainnet exige o gate extra.
    auto_transfer_margin: bool = True
    auto_transfer_margin_mainnet: bool = False
    margin_transfer_buffer_pct: float = 5.0
    min_transfer_usd: float = 1.0
    # Auto-resume: horas em auto_paused SEM novo breach antes de voltar a active.
    # None => comportamento manual (nenhum resume automático).
    auto_resume_after_hours: float | None = None


class Settings(BaseModel):
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    wip_limits: WipLimits = Field(default_factory=WipLimits)
    fees: FeeSettings = Field(default_factory=FeeSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    copy_trade: CopyTradeSettings = Field(default_factory=CopyTradeSettings)

    @staticmethod
    def _resolve(raw: str) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def data_dir(self) -> Path:
        p = self._resolve(self.paths.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self._resolve(self.paths.logs_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def kill_file(self) -> Path:
        return self._resolve(self.paths.kill_file)

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "tokio.db"


def load_settings(path: Path | None = None) -> Settings:
    # TOKIO_SETTINGS_PATH lets tests and one-off tooling point at an isolated
    # settings file (temp dirs, paper exchange) without touching the repo copy.
    env_path = os.environ.get("TOKIO_SETTINGS_PATH")
    p = path or (Path(env_path) if env_path else SETTINGS_PATH)
    if p.exists():
        raw = yaml.safe_load(p.read_text()) or {}
    else:
        raw = {}
    return Settings.model_validate(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


ENGINE_SECRET_VARS = (
    "HL_ACCOUNT_ADDRESS",
    "HL_AGENT_PRIVATE_KEY",
    "GATEWAY_CONTROL_TOKEN",
)


def validate_secrets_presence(required: tuple[str, ...] = ENGINE_SECRET_VARS) -> list[str]:
    """Return the names of missing env vars. Values are never read into logs."""
    return [name for name in required if not os.environ.get(name)]
