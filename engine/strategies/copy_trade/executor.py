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
    max_leverage: float = 5.0  # UPDATE-0078: padrão 3→5 (máx permitido = 10 no clamp)
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
            max_leverage=float(row.get("max_leverage", 5.0)),  # UPDATE-0078: 3→5
            blocked_assets=json.loads(row.get("blocked_assets") or "[]"),
            status=row.get("status", "SUGERIDO"),
            dry_run=bool(row.get("dry_run", 1)),
            thresholds=json.loads(row.get("thresholds") or "{}"),
        )


class FillWatcher(Protocol):
    """Subscription source for third-party fills (WS in prod, fake in tests)."""

    def subscribe(self, address: str, callback: Callable[[dict[str, Any]], None]) -> None: ...


class HyperliquidWatcher:
    """Read-only access to arbitrary addresses via the official SDK.

    REST (positions/equity) uses a stable `skip_ws` Info; live fills go through a
    resilient `WsSupervisor` that reconnects + re-subscribes when the SDK's socket
    dies (the SDK itself never reconnects — UPDATE-0020)."""

    def __init__(self, network: str = "testnet", *, logger: Any | None = None,
                 max_backoff_s: float = 60.0) -> None:
        from engine.exchanges.hyperliquid.adapter import MAINNET_API_URL, TESTNET_API_URL
        from engine.exchanges.hyperliquid.ws_supervisor import WsSupervisor
        from hyperliquid.info import Info

        base = TESTNET_API_URL if network == "testnet" else MAINNET_API_URL
        self._rest = Info(base_url=base, skip_ws=True)
        self._ws = WsSupervisor(
            make_info=lambda: Info(base_url=base, skip_ws=False),
            max_backoff_s=max_backoff_s, logger=logger, name="ws-targetfills",
        )
        self._started = False

    def subscribe(self, address: str, callback: Callable[[dict[str, Any]], None]) -> None:
        def _handler(msg: dict[str, Any]) -> None:
            data = msg.get("data", {})
            if data.get("isSnapshot"):
                return  # snapshots replay history; we only mirror live fills
            for fill in data.get("fills", []):
                callback(fill)

        self._ws.subscribe({"type": "userFills", "user": address}, _handler)
        if not self._started:
            self._ws.start()
            self._started = True

    def target_equity(self, address: str) -> float:
        state = self._rest.user_state(address)
        return float(state.get("marginSummary", {}).get("accountValue", 0))

    def target_positions(self, address: str) -> dict[str, float]:
        """Trader's real signed position per symbol (clearinghouse, WS-independent).
        The reconcile anchor — converges our mirror regardless of missed fills."""
        state = self._rest.user_state(address)
        out: dict[str, float] = {}
        for ap in state.get("assetPositions", []):
            p = ap.get("position", {})
            szi = float(p.get("szi") or 0)
            if szi != 0:
                out[p["coin"]] = szi
        return out


class CopyTradeExecutor:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        db: Database | None = None,
        gateway: GatewayClient | None = None,
        watcher: FillWatcher,
        my_equity_fn: Callable[[str | None], float] | None = None,
        target_equity_fn: Callable[[str], float] | None = None,
        target_positions_fn: Callable[[str], dict[str, float]] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings.sqlite_path)
        self.gateway = gateway or GatewayClient()
        self.watcher = watcher
        self.logger = EventLogger("runner-copytrade", self.settings.logs_dir, db=self.db)
        self.my_equity_fn = my_equity_fn or (lambda _env=None: 1_000.0)
        self.target_equity_fn = target_equity_fn or (lambda _addr: 0.0)
        # Real signed position of the trader per symbol — the reconcile anchor
        # (clearinghouse, WS-independent). Empty in tests unless injected.
        self.target_positions_fn = target_positions_fn or (lambda _addr: {})
        self.traders: dict[str, TraderConfig] = {}
        self._subscribed: set[str] = set()
        # (strategy_id, symbol) -> sizes for mirroring + drift check
        self._target_pos: dict[tuple[str, str], float] = {}
        self._my_pos: dict[tuple[str, str], float] = {}
        # (strategy_id, symbol) -> monotonic ts of last reconcile intent; the
        # optimistic anti-double-send window (order sent, fill not yet in ledger).
        self._reconcile_cooldown: dict[tuple[str, str], float] = {}
        # (strategy_id, symbol) -> consecutive reconcile attempts that still see
        # drift. Caps runaway retries (a persistently-rejected order must not loop
        # forever — the 407-rejections incident). Reset to 0 once the key aligns.
        self._reconcile_attempts: dict[tuple[str, str], int] = {}
        # (strategy_id, symbol) -> partial fills consecutivos. Book cronicamente
        # raso (ex.: HYPE na testnet) preenche pouco a cada ordem e nunca
        # converge; após PARTIAL_FILL_ILLIQUID_THRESHOLD seguidos, o símbolo vira
        # ilíquido em vez de travar no cap de tentativas. Fill cheio zera.
        # Compartilhado entre on_target_fill (WS) e reconcile.
        self._partial_fill_streaks: dict[tuple[str, str], int] = {}
        # symbol -> szDecimals (static venue metadata; cached to avoid per-fill RTT)
        self._sz_decimals: dict[str, int] = {}
        # símbolo ilíquido -> monotonic ts do último no-match. Enquanto fresco
        # (< ILLIQUID_TTL_S), pulamos o espelhamento sem reenviar a cada reconcile
        # ~60s (fonte da poluição de `rejected` na testnet). `_illiquid_logged`
        # garante UM log por símbolo até o cache expirar.
        self._illiquid: dict[str, float] = {}
        self._illiquid_logged: set[str] = set()
        # (strategy_id, symbol) -> última razão de falha do gateway no reconcile
        # (result.reason). Incluída no `reconcile.stuck` p/ diagnosticar POR QUE o
        # símbolo não converge (ex.: no_price, no_liquidity, cap_room_below_min).
        self._reconcile_last_reason: dict[tuple[str, str], str] = {}
        # (strategy_id, symbol) -> ciclos de reconcile a PULAR após atingir o cap
        # de tentativas. Sem backoff, um símbolo travado re-logava/reenviava a
        # cada ciclo (~60s), poluindo o loop. Reset quando o símbolo realinha.
        self._reconcile_backoff: dict[tuple[str, str], int] = {}
        self.reload_traders()

    # cooldown after a reconcile intent — MUST exceed the reconcile interval
    # (60s) so a symbol is never re-sent before its fill has had time to land in
    # the ledger (own-fills WS). 15s < 20s caused the 5-6x runaway: every cycle
    # re-sent the full delta because the cooldown had already expired.
    RECONCILE_COOLDOWN_S = 120.0

    # consecutive reconcile attempts on the same (strategy, symbol) before we stop
    # correcting and log `reconcile.stuck` — a persistently-rejected order must not
    # loop forever.
    RECONCILE_MAX_ATTEMPTS = 3

    # ciclos de reconcile a PULAR após um símbolo atingir RECONCILE_MAX_ATTEMPTS,
    # antes de re-avaliar (force-close reason-aware ou re-log). Sem isto, um
    # símbolo travado re-logava/reenviava a cada ciclo (~50s) e saturava o loop
    # (4 símbolos re-logados no incidente 2026-07-19). Reset quando realinha.
    RECONCILE_STUCK_BACKOFF_CYCLES = 5

    # partial fills consecutivos no MESMO (strategy, symbol) antes de tratar o
    # símbolo como ilíquido: um book raso (ex.: HYPE testnet) preenche pouco a
    # cada ordem e nunca converge — após N seguidos paramos de reabrir o delta a
    # cada ciclo (cache ilíquido, TTL abaixo) em vez de travar no cap. Fill cheio
    # zera a contagem. Distinguir progresso (partial) de rejeição persistente é
    # o cerne do fix: rejeição sobe o cap; partial que progride, não.
    PARTIAL_FILL_ILLIQUID_THRESHOLD = 5

    # TTL do cache de ativos ilíquidos (1h). Após expirar, re-tentamos o símbolo
    # uma vez — a liquidez pode ter voltado.
    ILLIQUID_TTL_S = 3600.0

    def _is_illiquid(self, symbol: str) -> bool:
        """True se o símbolo caiu como ilíquido e o cache ainda está fresco."""
        ts = self._illiquid.get(symbol)
        if ts is None:
            return False
        if time.monotonic() - ts > self.ILLIQUID_TTL_S:
            self._illiquid.pop(symbol, None)
            self._illiquid_logged.discard(symbol)
            return False
        return True

    def _mark_illiquid(self, symbol: str, strategy_id: str,
                       latency_ms: float | None = None) -> None:
        """Cacheia o símbolo como ilíquido e loga UMA vez até o cache expirar."""
        self._illiquid[symbol] = time.monotonic()
        if symbol not in self._illiquid_logged:
            self._illiquid_logged.add(symbol)
            self.logger.info("decision.skipped_no_liquidity", {"symbol": symbol},
                             strategy_id=strategy_id, latency_ms=latency_ms)

    def _clear_reconcile(self, key: tuple[str, str]) -> None:
        """Zera TODO o estado de reconcile de um (strategy, symbol) que realinhou:
        tentativas, última razão de falha e backoff. Chamado sempre que o símbolo
        converge (delta abaixo do step/min-notional/tolerância, progresso de fill
        ou ausência de preço) para que um travamento antigo não deixe resíduo."""
        self._reconcile_attempts.pop(key, None)
        self._reconcile_last_reason.pop(key, None)
        self._reconcile_backoff.pop(key, None)

    def _record_partial_streak(self, key: tuple[str, str], symbol: str,
                               strategy_id: str, *, filled: float,
                               requested: float,
                               latency_ms: float | None = None) -> None:
        """Conta partial fills consecutivos; após o limite, cacheia o símbolo
        como ilíquido (para de martelar um book raso). Fill cheio
        (>= requested) zera o streak. Chamado só no caminho ok + fill real
        (`filled_size` presente); dry_run/paper resetam via `pop` no chamador."""
        if 0.0 < filled < requested * (1.0 - 1e-6):        # partial de verdade
            streak = self._partial_fill_streaks.get(key, 0) + 1
            self._partial_fill_streaks[key] = streak
            if streak >= self.PARTIAL_FILL_ILLIQUID_THRESHOLD:
                self._mark_illiquid(symbol, strategy_id, latency_ms=latency_ms)
                self._partial_fill_streaks.pop(key, None)
        else:                                              # fill cheio ⇒ zera
            self._partial_fill_streaks.pop(key, None)

    # -- setup ---------------------------------------------------------------
    def reload_traders(self) -> None:
        """Recarrega da tabela `traders` (fonte única). Novos TESTNET/MAINNET
        ganham subscrição WS; quem saiu desses estados para de ser espelhado
        (o status é checado a cada fill de qualquer forma)."""
        for row in operable_traders(self.db):
            # Isolamento por-trader: uma linha malformada (ex.: blocked_assets
            # ='ZEC' não-JSON → JSONDecodeError em from_row) NUNCA pode abortar o
            # loop e derrubar TODO o copy trade. Incidente 0x8d7d49eb (2026-07-18):
            # um único registro ruim matava reload_traders no __init__ → run_forever
            # jamais rodava → nenhum trader era espelhado. Logamos e seguimos.
            try:
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
            except Exception as exc:  # noqa: BLE001 — resiliência por-trader
                self.logger.error("trader.load_failed",
                                  {"address": row.get("address"),
                                   "error": str(exc)[:200]})
                continue
        # Defesa em profundidade (UPDATE-0064, Parte 1a): pausa qualquer strategy
        # órfã — operante no BD mas com trader NÃO-copiável — no boot e em cada
        # reload. Fecha a brecha em que uma linha `strategies` ficou active/dry_run
        # após o trader ser rebaixado (ex.: reativação indevida do breaker).
        self._pause_orphan_strategies()

    def _pause_orphan_strategies(self) -> None:
        """Pausa strategies de copy_trade operantes (active/dry_run) cujo trader
        vinculado não está em TESTNET/MAINNET. Invariante: strategy só opera com
        trader copiável (gate humano, AGENTS.md)."""
        rows = self.db.query(
            "SELECT id, json_extract(config_snapshot, '$.address') AS address "
            "FROM strategies "
            "WHERE module = 'copy_trade' AND status IN ('active', 'dry_run')"
        )
        for row in rows:
            sid = row["id"]
            address = row["address"]
            trader_status = self._trader_status(address) if address else "REJEITADO"
            if trader_status in ("TESTNET", "MAINNET"):
                continue
            self.db.execute(
                "UPDATE strategies SET status = 'paused' WHERE id = ?", (sid,)
            )
            payload = {"address": address, "trader_status": trader_status,
                       "by": "trader_status_guard"}
            self.logger.warning("strategy.paused", payload, strategy_id=sid)
            self.logger.warning("strategy.trader_not_copyable",
                                {"address": address, "trader_status": trader_status},
                                strategy_id=sid)

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
        # Ativo ilíquido recente: pula sem reenviar (log 1x já feito ao cachear).
        if self._is_illiquid(symbol):
            self.logger.info("decision.skipped_illiquid_asset", {"symbol": symbol},
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
        # Absolute sizing on the trader's CURRENT position — same map the
        # reconcile uses, so the fast path (WS) and the corrective path never
        # fight each other. Stateless: survives missed fills / restarts.
        environment = environment_for_status(trader_status)
        my_new = self._desired_mirror(cfg, symbol, new_target, px, environment)
        # Round the TARGET position to the venue's step (szDecimals) so the size
        # we send is always a valid multiple — the HL API rejects otherwise with
        # "float_to_wire causes rounding". my_prev is already on the grid from a
        # prior fill, so delta stays a clean multiple too.
        sz_decimals = self._sz_decimals_for(symbol, environment)
        if sz_decimals is not None:
            my_new = self._round_to_step(my_new, sz_decimals)
        delta = my_new - my_prev

        fill_time_ms = float(fill.get("time", t0 * 1000))
        latency_ms = max(0.0, t0 * 1000 - fill_time_ms)

        # Guard anti-fechamento-fantasma: se este movimento REDUZ/fecha, confirmar
        # na venue real ANTES de emitir. `_my_pos` otimista pode estar stale
        # (fechamento manual pelo dashboard, reset/liquidação sem fill) e um
        # `reduce_only` sobre posição inexistente vira "empty response" → stuck.
        # Ressincroniza `_my_pos` à venue; os guards de step/min-notional abaixo
        # então pulam o envio quando já estamos flat.
        if abs(my_new) < abs(my_prev) or my_new == 0.0:
            step = 10 ** (-sz_decimals) if sz_decimals is not None else 1e-12
            venue = self._venue_position(strategy_id, symbol, environment)
            if venue is not None and abs(venue - my_prev) >= step:
                self.logger.info("decision.venue_resync",
                                 {"symbol": symbol, "stale": my_prev, "venue": venue},
                                 strategy_id=strategy_id, latency_ms=latency_ms)
                self._my_pos[key] = venue
                my_prev = venue
                delta = my_new - my_prev

        decision = {
            "symbol": symbol, "target_fill_sz": signed_fill, "px": px,
            "prev_target": prev_target, "new_target": new_target,
            "my_prev": my_prev, "my_new": my_new, "delta": delta,
        }

        if sz_decimals is not None and abs(delta) < 10 ** (-sz_decimals):
            self.logger.info("decision.skipped_size_too_small",
                             {**decision, "sz_decimals": sz_decimals},
                             strategy_id=strategy_id, latency_ms=latency_ms)
            return None

        if abs(delta) * px < self._min_notional_for(cfg):
            if abs(delta) > 0:
                self.logger.info("decision.skipped_min_notional",
                                 {**decision, "notional": abs(delta) * px},
                                 strategy_id=strategy_id, latency_ms=latency_ms)
            return None

        reduce_only = abs(my_new) < abs(my_prev) or my_new == 0.0
        self._ensure_margin_for_open(strategy_id, cfg, delta, px, reduce_only,
                                     environment, latency_ms)
        result = self.gateway.send_intent(
            strategy_id=strategy_id,
            symbol=symbol,
            side="buy" if delta > 0 else "sell",
            size=abs(delta),
            order_type="market",
            reduce_only=reduce_only,
            leverage=cfg.max_leverage,
            dry_run=self._is_dry_run(cfg),
            environment=environment,
        )
        if result.get("ok"):
            # Grava a posição REAL resultante a partir do quanto preencheu, não o
            # desejado: um partial fill (ex.: ordem 20.98, preenche 0.16) deixaria
            # `_my_pos` otimista mascarando o buraco e o reconcile nunca corrigiria.
            # `filled_size` ausente (dry_run) ⇒ fallback ao comportamento atual.
            filled = result.get("filled_size")
            if filled is None:
                self._my_pos[key] = my_new
                self._partial_fill_streaks.pop(key, None)   # dry_run/paper: cheio
            else:
                f = float(filled)
                signed = f if delta > 0 else -f
                self._my_pos[key] = my_prev + signed
                # partial crônico neste símbolo ⇒ eventualmente vira ilíquido
                # (para de reenviar); fill cheio zera o streak.
                self._record_partial_streak(key, symbol, strategy_id,
                                            filled=f, requested=abs(delta),
                                            latency_ms=latency_ms)
        elif result.get("reason") == "no_liquidity":
            # o gateway não achou book mesmo após todos os passos de slippage —
            # cacheia p/ não reenviar a cada reconcile (log 1x).
            self._mark_illiquid(symbol, strategy_id, latency_ms=latency_ms)
            return result
        self.logger.info("decision.mirrored", {**decision, "result": result},
                         strategy_id=strategy_id, latency_ms=latency_ms)
        return result

    def _ensure_margin_for_open(self, strategy_id: str, cfg: TraderConfig,
                                delta: float, px: float, reduce_only: bool,
                                environment: str | None,
                                latency_ms: float | None = None) -> None:
        """Auto-transfer spot→perp antes de ABRIR/aumentar posição (best-effort).

        Na HL spot e perp são pools de margem separados; se a conta tem USDC só no
        spot a ordem perp falha por falta de margem (incidente 2026-07-16). Aqui
        pedimos ao gateway para garantir a margem INTRA-CONTA. Fechamentos
        (reduce_only) liberam margem — não chamam. NUNCA aborta a cópia: erro/spot
        insuficiente só loga e deixa a venue/reconcile tratarem."""
        if reduce_only or abs(delta) <= 0:
            return
        if not self.settings.copy_trade.auto_transfer_margin:
            return
        required_margin = abs(delta) * px / max(cfg.max_leverage, 1.0)
        if required_margin <= 0:
            return
        tr = self.gateway.ensure_margin(strategy_id, required_margin, environment)
        if tr.get("transferred"):
            self.logger.info("decision.margin.auto_transfer",
                             {"amount": tr["transferred"], "required": required_margin,
                              "environment": environment},
                             strategy_id=strategy_id, latency_ms=latency_ms)
        elif tr.get("reason") == "spot_insuficiente":
            self.logger.warning("decision.margin.insufficient",
                                {"spot_free": tr.get("spot_free"),
                                 "needed": tr.get("needed"),
                                 "required": required_margin},
                                strategy_id=strategy_id, latency_ms=latency_ms)

    def _min_notional_for(self, cfg: TraderConfig) -> float:
        """Piso de notional efetivo para este trader.

        `max(global, per_trader)`: o teto per-trader (thresholds.min_notional_usd)
        só pode SUBIR o mínimo; nunca cai abaixo do piso global da Hyperliquid.
        Mesma semântica do guard global (só *skip* de ordens pequenas) — não
        adiciona gate novo ao caminho de ordem (INVARIANTE).
        """
        per_trader = float(cfg.thresholds.get("min_notional_usd", 0.0) or 0.0)
        return max(self.settings.risk.min_order_notional_usd, per_trader)

    def _sz_decimals_for(self, symbol: str, environment: str | None) -> int | None:
        """szDecimals of the asset (cached). None if the gateway can't provide it
        — the caller then sends the raw size and the gateway backstop rounds it."""
        cached = self._sz_decimals.get(symbol)
        if cached is not None:
            return cached
        try:
            meta = self.gateway.market_meta(symbol, environment)
        except Exception as exc:  # noqa: BLE001 — gateway hiccup must not drop the fill
            self.logger.warning("decision.no_market_meta",
                                 {"symbol": symbol, "error": str(exc)[:200]})
            return None
        if not meta.get("ok", True):
            self.logger.warning("decision.no_market_meta",
                                 {"symbol": symbol, "reason": meta.get("reason")})
            return None
        sz = int(meta.get("szDecimals", 0))
        self._sz_decimals[symbol] = sz
        return sz

    @staticmethod
    def _round_to_step(size: float, sz_decimals: int) -> float:
        # round(), never int()/truncation: HL needs the nearest valid multiple.
        return round(size, sz_decimals) if sz_decimals > 0 else float(round(size))

    def _desired_mirror(self, cfg: TraderConfig, symbol: str,
                        target_now: float, px: float,
                        environment: str | None) -> float:
        """Absolute mirrored position we SHOULD hold given the trader's current
        signed position `target_now`. Stateless (no dependence on our previous
        size) so the reconcile converges after any missed fill or restart.

        Semantics (UPDATE-0020, intentional change for `fixed_usdc`):
          target_now == 0   -> 0.0                       (trader flat -> we close)
          fixed_usdc        -> ($value / px) * sign      (hold $value of exposure,
                                                           does NOT scale w/ trader)
          percent           -> (|target_now|*px*value*my_eq/target_eq)/px * sign
                               (proportional to the trader's notional — unchanged)
        The result is rounded to the venue step by the caller.
        """
        if target_now == 0.0:
            return 0.0
        sign = 1.0 if target_now > 0 else -1.0
        if cfg.mode == "fixed_usdc":
            if px <= 0:
                return 0.0
            return (cfg.value / px) * sign
        # percent: proportional to the trader's notional and the equity ratio
        target_eq = self.target_equity_fn(cfg.address) or 0.0
        if target_eq <= 0 or px <= 0:
            self.logger.warning("decision.no_target_equity", {"address": cfg.address},
                                strategy_id=cfg.strategy_id)
            return self._my_pos.get((cfg.strategy_id, symbol), 0.0)
        my_eq = self.my_equity_fn(environment)
        if my_eq <= 0:
            # Equity real indisponível (erro do /balance sem cache / cold start).
            # Segura a posição atual — NUNCA fecha nem redimensiona com equity
            # desconhecido (voltar a $1.000 re-inflaria o teto). O reconcile
            # corrige quando o /balance responder.
            self.logger.warning("decision.no_my_equity",
                                 {"address": cfg.address, "env": environment},
                                 strategy_id=cfg.strategy_id)
            return self._my_pos.get((cfg.strategy_id, symbol), 0.0)
        notional = abs(target_now) * px * cfg.value * (my_eq / target_eq)
        # Teto de alavancagem: espelha o notional_cap da simulação
        # (metrics.simulate_copy) para manter a exposição ao vivo alinhada com o
        # que a simulação prometeu. Só dimensiona (reduz size) — nunca rejeita
        # ordem, então não adiciona gate no caminho de ordem (INVARIANTE).
        notional_max = my_eq * cfg.max_leverage
        if notional_max > 0 and notional > notional_max:
            # Observabilidade (Fix #5): registra QUANDO o teto de alavancagem corta
            # o size. Só ADICIONA log — não muda a fórmula (INVARIANTE de sizing) e
            # dispara apenas no corte (raro). Fora do hot path §8.4.1 do gateway.
            self.logger.info(
                "decision.size_capped",
                {"symbol": symbol,
                 "requested_notional": round(notional, 4),
                 "capped_notional": round(notional_max, 4),
                 "max_leverage": cfg.max_leverage},
                strategy_id=cfg.strategy_id)
            notional = notional_max
        return (notional / px) * sign

    # -- reconcile (corrective, per trader -> per strategy) ---------------------
    def reconcile(self) -> list[dict[str, Any]]:
        """Target-anchored reconciliation — the backbone that recovers missed
        fills and restarts (UPDATE-0020). Scoped strictly per trader, then per
        strategy (`ct_*`): each strategy's expected mirror is compared ONLY to
        ITS OWN attributed ledger book (§5.1/§5.2), never the aggregate
        clearinghouse. Overlapping symbols across `ct_*` reconcile in isolation.

        For each active trader: read the trader's real position (clearinghouse,
        WS-independent) and our per-strategy ledger position; per symbol emit the
        delta needed to reach `_desired_mirror`. Returns the corrections emitted.
        """
        corrections: list[dict[str, Any]] = []
        try:
            ledger = self.gateway.ledger()
        except Exception as exc:  # noqa: BLE001 — gateway may be booting/unreachable
            self.logger.warning("reconcile.ledger_failed", {"error": str(exc)[:200]})
            return corrections

        now = time.monotonic()
        for cfg in list(self.traders.values()):
            sid = cfg.strategy_id
            trader_status = self._trader_status(cfg.address)
            if trader_status not in ("TESTNET", "MAINNET"):
                continue
            if self._strategy_status(sid) not in ("dry_run", "active"):
                continue
            environment = environment_for_status(trader_status)
            try:
                target_pos = self.target_positions_fn(cfg.address)
            except Exception as exc:  # noqa: BLE001 — one trader must not kill the loop
                self.logger.warning("reconcile.target_positions_failed",
                                    {"address": cfg.address, "error": str(exc)[:200]},
                                    strategy_id=sid)
                continue
            mine = (ledger.get(sid) or {}).get("positions", {})
            symbols = set(target_pos) | set(mine)
            for symbol in symbols:
                if symbol in cfg.blocked_assets:
                    continue
                # ilíquido recente: não reenvia a cada ciclo de reconcile (fonte
                # da poluição de `rejected` — log 1x já feito ao cachear).
                if self._is_illiquid(symbol):
                    self._clear_reconcile((sid, symbol))
                    continue
                key = (sid, symbol)
                target_now = float(target_pos.get(symbol, 0.0))
                px = self._mid_price(symbol, environment)
                if px <= 0:
                    # Sem preço na NOSSA venue (ex.: ativo não listado na testnet).
                    # O alvo pode ter a posição (CRV +2921 no incidente 2026-07-19)
                    # mas não conseguimos espelhá-la. Antes era um skip SILENCIOSO
                    # (o símbolo sumia de TODOS os eventos, tornando o "não copiou"
                    # invisível); agora loga 1x e cacheia como ilíquido p/ não
                    # reprocessar todo ciclo. Reusa a plumbing de ilíquido
                    # (`_illiquid`/`_illiquid_logged`/TTL) SEM o log de no_liquidity.
                    if symbol not in self._illiquid_logged:
                        self._illiquid_logged.add(symbol)
                        self.logger.info(
                            "decision.skipped_no_price",
                            {"symbol": symbol, "target_now": target_now},
                            strategy_id=sid)
                    self._illiquid[symbol] = time.monotonic()
                    self._clear_reconcile(key)
                    continue
                desired = self._desired_mirror(cfg, symbol, target_now, px, environment)
                sz_decimals = self._sz_decimals_for(symbol, environment)
                if sz_decimals is not None:
                    desired = self._round_to_step(desired, sz_decimals)
                # `actual` = whichever of {ledger, our optimistic memory} is CLOSER
                # to `desired`. While an order is in flight the ledger still shows
                # the old size; trusting the optimistic `_my_pos` there prevents a
                # duplicate full-size re-send (the runaway). Uses distance-to-desired
                # rather than a signed max() so it works for shorts too.
                ledger_actual = float((mine.get(symbol) or {}).get("size", 0.0))
                optimistic = self._my_pos.get(key)
                actual = ledger_actual
                if optimistic is not None and \
                        abs(desired - optimistic) < abs(desired - ledger_actual):
                    actual = optimistic
                delta = desired - actual
                # Guard anti-fechamento-fantasma (ver on_target_fill): quando o
                # alvo REDUZ/fecha, confirmar na venue real antes de enviar.
                # Ledger E otimista podem estar stale (fechamento manual pelo
                # dashboard, reset/liquidação sem fill); um reduce_only sobre
                # posição inexistente vira "empty response" → reconcile.stuck.
                # Ressincroniza `actual` à venue; os guards abaixo pulam quando
                # já não há o que fechar.
                if abs(desired) < abs(actual) or desired == 0.0:
                    venue = self._venue_position(sid, symbol, environment)
                    if venue is not None and abs(venue) < abs(actual):
                        self.logger.info(
                            "drift.venue_resync",
                            {"symbol": symbol, "stale": actual, "venue": venue},
                            strategy_id=sid)
                        self._my_pos[key] = venue
                        actual = venue
                        delta = desired - actual
                        # Fix 1b: persiste a correção no ledger (fill sintético) p/
                        # sobreviver a restart — antes só o _my_pos local era
                        # corrigido e o size fantasma voltava no próximo hydrate,
                        # bloqueando o total_cap. Best-effort (fora do hot path).
                        self.gateway.ledger_resync(
                            sid, symbol, venue,
                            reason="drift.venue_resync", environment=environment)
                # step / min-notional guards (same as on_target_fill)
                if sz_decimals is not None and abs(delta) < 10 ** (-sz_decimals):
                    self._clear_reconcile(key)
                    continue
                if abs(delta) * px < self._min_notional_for(cfg):
                    self._clear_reconcile(key)
                    continue
                # drift tolerance: don't chase sub-5% differences (cents).
                base = max(abs(desired), abs(actual))
                if base > 0 and abs(delta) <= DRIFT_TOLERANCE * base:
                    self._clear_reconcile(key)
                    continue
                # anti-double-send: skip if we already corrected this key and the
                # fill hasn't had time to reflect in the ledger yet.
                last = self._reconcile_cooldown.get(key)
                if last is not None and now - last < self.RECONCILE_COOLDOWN_S:
                    continue
                reduce_only = abs(desired) < abs(actual) or desired == 0.0
                # runaway guard: a symbol that keeps drifting after N corrections
                # (e.g. persistently rejected) is stuck.
                attempts = self._reconcile_attempts.get(key, 0)
                if attempts >= self.RECONCILE_MAX_ATTEMPTS:
                    # backoff (Fix #3): após o cap, pula N ciclos antes de
                    # re-avaliar — sem isto o símbolo re-logava/reenviava a cada
                    # ciclo (~50s) e saturava o loop (incidente 2026-07-19).
                    backoff = self._reconcile_backoff.get(key, 0)
                    if backoff > 0:
                        self._reconcile_backoff[key] = backoff - 1
                        continue
                    reason = self._reconcile_last_reason.get(key)
                    # force-close REASON-AWARE (Fix #2): fecha o fantasma preso só
                    # quando é seguro (redução/fecho + venue confirma + razão
                    # recuperável). Detalhes em `_maybe_force_close`.
                    forced = self._maybe_force_close(
                        sid, cfg, symbol, key, reason, reduce_only,
                        environment, now)
                    if not forced:
                        # `reason` (Fix #4): diagnostica POR QUE não converge
                        # (order_rejected, cap_room_below_min, no_price, ...).
                        self.logger.warning(
                            "reconcile.stuck",
                            {"symbol": symbol, "desired": desired, "actual": actual,
                             "delta": delta, "attempts": attempts, "reason": reason},
                            strategy_id=sid)
                        self._reconcile_backoff[key] = \
                            self.RECONCILE_STUCK_BACKOFF_CYCLES
                    continue
                info = {"symbol": symbol, "target_now": target_now,
                        "desired": desired, "actual": actual, "delta": delta}
                self.logger.warning("drift.correcting", info, strategy_id=sid)
                self._ensure_margin_for_open(sid, cfg, delta, px, reduce_only,
                                             environment)
                result = self.gateway.send_intent(
                    strategy_id=sid,
                    symbol=symbol,
                    side="buy" if delta > 0 else "sell",
                    size=abs(delta),
                    order_type="market",
                    reduce_only=reduce_only,
                    leverage=cfg.max_leverage,
                    dry_run=self._is_dry_run(cfg),
                    environment=environment,
                )
                # count every SEND (not just fills): a persistently-rejected symbol
                # must still reach the stuck cap instead of looping forever.
                self._reconcile_attempts[key] = attempts + 1
                if result.get("ok"):
                    # posição REAL após o preenchido (partial fill não fica
                    # mascarado como se tivesse completado); cooldown cobre o gap
                    # order->ledger. `filled_size` ausente (dry_run) ⇒ fallback.
                    filled = result.get("filled_size")
                    if filled is None:
                        self._my_pos[key] = desired
                        self._partial_fill_streaks.pop(key, None)
                    else:
                        f = float(filled)
                        signed = f if delta > 0 else -f
                        self._my_pos[key] = actual + signed
                        self._record_partial_streak(key, symbol, sid,
                                                    filled=f, requested=abs(delta))
                    self._target_pos[key] = target_now
                    self._reconcile_cooldown[key] = now
                    # progresso (partial ou cheio) NÃO é rejeição persistente:
                    # zera o cap. O cooldown de 120s continua sendo o guard
                    # primário anti-runaway; o cap só acumula em rejeição
                    # (ok=False, sem cooldown) ou fill zero (sem progresso).
                    if filled is None or float(filled) > 0.0:
                        self._clear_reconcile(key)
                elif result.get("reason") == "no_liquidity":
                    # cacheia p/ parar de reenviar nos próximos ciclos (log 1x).
                    self._mark_illiquid(symbol, sid)
                    self._clear_reconcile(key)
                else:
                    # rejeição recuperável (order_rejected, cap_room_below_min, …):
                    # guarda a razão p/ o `reconcile.stuck`/force-close a usarem.
                    # UPDATE-0077: o caminho de execução do gateway NÃO devolve
                    # `reason` em ok=False (só `error`/`status`, server.py:776-778);
                    # o "unknown" de antes escondia a razão real. Cai no `error`
                    # verdadeiro da venue antes do fallback inútil.
                    self._reconcile_last_reason[key] = (
                        result.get("reason")
                        or result.get("error")
                        or "unknown")
                corrections.append({"strategy_id": sid, **info, "result": result})
        # venue cross-check (observability only — never corrects across strategies)
        self._venue_cross_check(ledger)
        return corrections

    def _maybe_force_close(self, sid: str, cfg: TraderConfig, symbol: str,
                           key: tuple[str, str], reason: str | None,
                           reduce_only: bool, environment: str | None,
                           now: float) -> bool:
        """Fecha um fantasma preso no cap de reconcile — SÓ quando é seguro (Fix #2).

        Um `reduce_only` que falha repetidamente pode ser (a) fantasma REAL na
        venue que precisa ser zerado à força, ou (b) posição inexistente
        ("empty response"/sem preço) onde forçar falharia igual. Distinguimos:
          - só age em redução/fecho (`reduce_only`);
          - só quando a venue REAL confirma a posição (`_venue_position` != 0);
          - só quando a razão da falha é RECUPERÁVEL (não `no_liquidity`/`no_price`).
        Emite um `reduce_only` de mercado do tamanho EXATO da venue, loga
        `reconcile.force_close` e retorna True se forçou (limpando o estado)."""
        if not reduce_only or reason is None:
            return False
        if reason == "no_liquidity" or reason.startswith("no_price"):
            return False
        venue = self._venue_position(sid, symbol, environment)
        if venue is None:
            # UPDATE-0077 ("expor erro + retry, nunca forçar cego"): a venue está
            # ILEGÍVEL (/api/positions não-2xx ⇒ _venue_position=None). Sem o
            # tamanho real NÃO forçamos — mas antes esse caminho era MUDO, e o
            # mesmo None fazia o resync (693-709) pular, deixando o fantasma preso
            # sem rastro. Emite evento distinto p/ o problema ficar visível. Só é
            # alcançado em attempts>=MAX após o backoff, então herda o cadence do
            # stuck (não re-introduz o spam que o UPDATE-0075 eliminou).
            self.logger.warning(
                "reconcile.venue_unreadable",
                {"symbol": symbol, "reason": reason, "environment": environment},
                strategy_id=sid)
            return False
        if abs(venue) <= 0:
            return False
        result = self.gateway.send_intent(
            strategy_id=sid,
            symbol=symbol,
            side="buy" if venue < 0 else "sell",
            size=abs(venue),
            order_type="market",
            reduce_only=True,
            leverage=cfg.max_leverage,
            dry_run=self._is_dry_run(cfg),
            environment=environment,
        )
        ok = bool(result.get("ok"))
        self.logger.warning(
            "reconcile.force_close",
            {"symbol": symbol, "venue": venue, "reason": reason, "ok": ok,
             "result_reason": result.get("reason")},
            strategy_id=sid)
        if not ok:
            # a força também falhou: atualiza a razão e deixa cair no stuck/backoff.
            self._reconcile_last_reason[key] = result.get("reason") or reason
            return False
        filled = result.get("filled_size")
        if filled is None:
            self._my_pos[key] = 0.0
        else:
            f = float(filled)
            signed = f if venue < 0 else -f
            self._my_pos[key] = venue + signed
        self._reconcile_cooldown[key] = now
        self._clear_reconcile(key)
        return True

    def _mid_price(self, symbol: str, environment: str | None) -> float:
        """Best-effort mid price for reconcile sizing (via gateway market-meta)."""
        try:
            meta = self.gateway.market_meta(symbol, environment)
        except Exception:  # noqa: BLE001
            return 0.0
        return float(meta.get("mid") or meta.get("mid_price") or 0.0)

    def _venue_cross_check(self, ledger: dict[str, Any]) -> None:
        """Σ(ledger position per symbol) vs the real clearinghouse per symbol,
        AGRUPADO por environment. Estratégias podem operar em redes diferentes ao
        mesmo tempo (uma ct_* em testnet, outra em mainnet), então consultar a
        venue com um único network fixo (`watch_network`, que é a rede do
        trader-FONTE, não a nossa) reportava `venue: 0.0` falso. Cada grupo é
        consultado na SUA rede via `environment_for_status`.
        Logs `reconcile.venue_mismatch` — respects §5.1 (does NOT correct by
        crossing strategies, since venue positions aren't attributable)."""
        by_env: dict[str, list[str]] = {}
        for cfg in self.traders.values():
            if self._trader_status(cfg.address) not in ("TESTNET", "MAINNET"):
                continue
            env = environment_for_status(self._trader_status(cfg.address))
            if env:
                by_env.setdefault(env, []).append(cfg.strategy_id)
        if not by_env:
            return
        for env, sids in by_env.items():
            try:
                venue = self.gateway.positions(sids, env)
            except Exception:  # noqa: BLE001 — cross-check is best-effort
                continue
            ledger_by_symbol: dict[str, float] = {}
            for sid in sids:
                for symbol, pos in (ledger.get(sid) or {}).get("positions", {}).items():
                    ledger_by_symbol[symbol] = ledger_by_symbol.get(symbol, 0.0) + \
                        float((pos or {}).get("size", 0.0))
            venue_by_symbol: dict[str, float] = {}
            for p in venue:
                sym = p.get("symbol") or p.get("coin")
                if sym:
                    venue_by_symbol[sym] = venue_by_symbol.get(sym, 0.0) + \
                        float(p.get("size", p.get("szi", 0.0)) or 0.0)
            for symbol in set(ledger_by_symbol) | set(venue_by_symbol):
                led = ledger_by_symbol.get(symbol, 0.0)
                ven = venue_by_symbol.get(symbol, 0.0)
                if abs(led - ven) > max(abs(led), abs(ven), 1e-9) * DRIFT_TOLERANCE:
                    self.logger.warning(
                        "reconcile.venue_mismatch",
                        {"symbol": symbol, "ledger_sum": led, "venue": ven,
                         "environment": env})

    def _venue_position(self, sid: str, symbol: str,
                        environment: str | None) -> float | None:
        """Tamanho SINALIZADO real da NOSSA posição na venue p/ (sid, symbol).

        Fonte de verdade para o guard anti-fechamento-fantasma: antes de emitir
        qualquer `reduce_only` o executor consulta a venue (via gateway) em vez
        de confiar cegamente no `_my_pos` otimista ou no ledger, que podem estar
        stale (fechamento manual pelo dashboard; reset/liquidação sem fill).

        `None`  ⇒ indisponível (exceção) — NÃO bloqueia, segue com a estimativa.
        Símbolo ausente na resposta OK ⇒ `0.0` (posição realmente flat)."""
        try:
            venue = self.gateway.positions([sid], environment)
        except Exception:  # noqa: BLE001 — indisponível ⇒ segue com estimativa
            return None
        for p in venue:
            if (p.get("symbol") or p.get("coin")) == symbol:
                return float(p.get("size", p.get("szi", 0.0)) or 0.0)
        return 0.0

    # -- drift check ------------------------------------------------------------
    def drift_check(self) -> list[dict[str, Any]]:
        """Compare expected mirrored sizes vs. the gateway ledger. Alerts on >5%."""
        drifts: list[dict[str, Any]] = []
        try:
            ledger = self.gateway.ledger()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("drift.check_failed", {"error": str(exc)[:200]})
            return drifts
        for (sid, symbol), expected in self._my_pos.items():
            positions = (ledger.get(sid) or {}).get("positions", {})
            actual = (positions.get(symbol) or {}).get("size", 0.0)
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
                    reload_interval_s: float = 30.0,
                    reconcile_interval_s: float | None = None) -> None:
        self.logger.info("strategy.runner_start",
                         {"module": "copy_trade",
                          "traders": [t.strategy_id for t in self.traders.values()],
                          "copying": [t.strategy_id for t in self.traders.values()
                                      if t.status in ("TESTNET", "MAINNET")]})
        if reconcile_interval_s is None:
            reconcile_interval_s = self.settings.copy_trade.reconcile_interval_s
        # Startup: wait for the gateway before the first reconcile so a runner
        # started ahead of the gateway doesn't fail with "Connection refused"
        # (UPDATE-0020 item 3). Silent until the last attempt.
        if not self.gateway.wait_ready():
            self.logger.error("gateway.unreachable_on_start", {})
        else:
            # Startup reconcile rebuilds our mirror from the trader's real
            # position after a restart/gap — recovers the missed fills.
            try:
                self.reconcile()
            except Exception as exc:  # noqa: BLE001 — never crash the boot
                self.logger.warning("reconcile.startup_failed", {"error": str(exc)[:200]})
        last_drift = 0.0
        last_reload = 0.0
        last_reconcile = time.monotonic()
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
            if now - last_reconcile >= reconcile_interval_s:
                # corrective backbone: converges each strategy to its trader's
                # real position, symbol by symbol (recovers missed fills).
                try:
                    self.reconcile()
                except Exception as exc:  # noqa: BLE001 — a cycle error must not kill the loop
                    self.logger.warning("reconcile.cycle_failed", {"error": str(exc)[:200]})
                last_reconcile = now
            if now - last_drift >= drift_interval_s:
                self.drift_check()  # read-only alert; the corrective is reconcile()
                self.logger.info("health.heartbeat",
                                 {"targets": len(self._target_pos)})
                last_drift = now
            time.sleep(1.0)


def main() -> None:
    settings = get_settings()
    db = Database(settings.sqlite_path)
    # Shared logger so the WS supervisor's reconnect events land in the same
    # per-strategy log stream as the executor's decisions.
    logger = EventLogger("runner-copytrade", settings.logs_dir, db=db)
    watcher = HyperliquidWatcher(
        settings.copy_trade.watch_network,
        logger=logger,
        max_backoff_s=settings.copy_trade.ws_reconnect_max_backoff_s,
    )
    gateway = GatewayClient()

    # Equity real da MINHA conta por ambiente, via /balance (o /health não expõe
    # equity). Cache last-known por ambiente: em erro/0 usa a última leitura boa;
    # em cold start retorna 0.0 e o _desired_mirror segura a posição (nunca
    # re-infla o teto para $1.000).
    equity_cache: dict[str, float] = {}

    def my_equity(env: str | None = None) -> float:
        key = env or "default"
        try:
            eq = float(gateway.balance(env).get("equity_usd", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            eq = 0.0
        if eq > 0:
            equity_cache[key] = eq
            return eq
        return equity_cache.get(key, 0.0)

    executor = CopyTradeExecutor(
        settings=settings,
        db=db,
        watcher=watcher,
        gateway=gateway,
        my_equity_fn=my_equity,
        target_equity_fn=watcher.target_equity,
        target_positions_fn=watcher.target_positions,
    )
    executor.run_forever()


if __name__ == "__main__":
    main()
