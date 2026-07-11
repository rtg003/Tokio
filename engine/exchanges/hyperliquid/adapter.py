"""Hyperliquid adapter — official SDK (hyperliquid-python-sdk), testnet default.

Signing model (ADR 0001): this adapter is instantiated ONLY inside the gateway
process, with the `engine_gateway` agent wallet private key from the
environment. The SDK signs with the agent key; queries always use the real
account address (a common pitfall is querying the agent address — empty
result). Nonces are handled by the SDK per signer; the gateway being the sole
signer serializes them.

Phase B (ADR 0002): `subaccount_address` on an order maps to the SDK's
`vault_address`, letting the same signer trade a subaccount.
"""
from __future__ import annotations

import os
import threading
from typing import Any

from engine.exchanges.base import (
    ExchangeAdapter,
    FillCallback,
    OrderRequest,
    OrderResult,
    Position,
)

TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"
MAINNET_API_URL = "https://api.hyperliquid.xyz"

# HL rejeita um IOC agressivo que não cruza o book com esta mensagem. É o único
# erro que vale a pena repetir alargando o slippage — os demais (margem, size,
# etc.) não mudam com mais slippage.
_IOC_NO_MATCH = "could not immediately match"
# Slippage padrão quando não vier da config (market_open/close do SDK usam 1%).
_DEFAULT_SLIPPAGE_STEPS = [0.05, 0.10, 0.15]


def _is_ioc_no_match(error: str | None) -> bool:
    return bool(error) and _IOC_NO_MATCH in error.lower()


class HyperliquidAdapter(ExchangeAdapter):
    name = "hyperliquid"

    def __init__(
        self,
        network: str = "testnet",
        account_address: str | None = None,
        agent_private_key: str | None = None,
        slippage_steps: list[float] | None = None,
    ) -> None:
        # Imports are local so the rest of the engine (and tests with the
        # PaperAdapter) never require the SDK at import time.
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils.types import Cloid  # noqa: F401 — used below

        self.network = network
        base_url = TESTNET_API_URL if network == "testnet" else MAINNET_API_URL
        self.account_address = account_address or os.environ["HL_ACCOUNT_ADDRESS"]
        key = agent_private_key or os.environ["HL_AGENT_PRIVATE_KEY"]
        wallet = Account.from_key(key)

        # REST-only Info; the WS lives in a resilient supervisor (own-fills must
        # not silently die — it feeds the per-strategy ledger the reconcile relies on).
        self.info = Info(base_url=base_url, skip_ws=True)
        self._base_url = base_url
        self.exchange = Exchange(
            wallet, base_url=base_url, account_address=self.account_address
        )
        self._meta_cache: dict[str, dict[str, Any]] | None = None
        self._lock = threading.Lock()
        self._ws: Any | None = None
        self.slippage_steps = list(slippage_steps) if slippage_steps else list(
            _DEFAULT_SLIPPAGE_STEPS)

    # -- helpers ---------------------------------------------------------
    def _meta(self) -> dict[str, dict[str, Any]]:
        if self._meta_cache is None:
            meta = self.info.meta()
            self._meta_cache = {a["name"]: a for a in meta["universe"]}
        return self._meta_cache

    @staticmethod
    def _to_cloid(cloid: str | None) -> Any:
        if cloid is None:
            return None
        from hyperliquid.utils.types import Cloid

        return Cloid.from_str(cloid) if not isinstance(cloid, Cloid) else cloid

    # -- ExchangeAdapter -------------------------------------------------
    def place_order(self, request: OrderRequest) -> OrderResult:
        try:
            with self._lock:
                exchange = self.exchange
                if request.subaccount_address:
                    exchange.vault_address = request.subaccount_address
                is_buy = request.side == "buy"
                cloid = self._to_cloid(request.cloid)
                try:
                    if request.order_type == "market":
                        result = self._place_market_with_retry(
                            exchange, request, is_buy, cloid)
                    else:
                        assert request.price is not None, "limit order requires price"
                        resp = exchange.order(
                            request.symbol,
                            is_buy,
                            request.size,
                            request.price,
                            {"limit": {"tif": "Gtc"}},
                            reduce_only=request.reduce_only,
                            cloid=cloid,
                        )
                        result = self._parse_order_response(resp, request)
                finally:
                    if request.subaccount_address:
                        exchange.vault_address = None
            return result
        except Exception as exc:  # noqa: BLE001 — surfaced as a structured error
            return OrderResult(ok=False, cloid=request.cloid, status="error",
                               error=f"{request.symbol}: {exc}")

    def _place_market_with_retry(
        self, exchange: Any, request: OrderRequest, is_buy: bool, cloid: Any,
    ) -> OrderResult:
        """Market = IOC agressivo. Se o preço a `slippage` do mid não cruzar o
        book ("could not immediately match"), alarga o slippage e tenta de novo.
        Qualquer outro erro para na hora (mais slippage não resolve)."""
        last: OrderResult | None = None
        for slippage in self.slippage_steps:
            if request.reduce_only:
                resp = exchange.market_close(
                    request.symbol, request.size, None, slippage, cloid=cloid)
            else:
                resp = exchange.market_open(
                    request.symbol, is_buy, request.size, None, slippage, cloid=cloid)
            result = self._parse_order_response(resp, request)
            if result.ok:
                return result
            last = result
            if not _is_ioc_no_match(result.error):
                return result
        return last if last is not None else OrderResult(
            ok=False, cloid=request.cloid, status="rejected",
            error=f"{request.symbol}: no slippage steps configured")

    @staticmethod
    def _parse_order_response(resp: dict[str, Any], request: OrderRequest) -> OrderResult:
        # Erros carregam o NOME do coin (o SDK só reporta 'asset=<idx>').
        # Defesa: o SDK pode devolver None/algo não-dict (timeout, coin sem book);
        # sem isto o `.get` estoura "'NoneType' object has no attribute 'get'".
        if not isinstance(resp, dict):
            return OrderResult(ok=False, cloid=request.cloid, status="rejected",
                               error=f"{request.symbol}: empty response", raw=None)
        if resp.get("status") != "ok":
            return OrderResult(ok=False, cloid=request.cloid, status="rejected",
                               error=f"{request.symbol}: {resp}", raw=resp)
        statuses = resp["response"]["data"]["statuses"]
        st = statuses[0] if statuses else {}
        if "filled" in st:
            f = st["filled"]
            return OrderResult(ok=True, exchange_order_id=str(f.get("oid")),
                               cloid=request.cloid, status="filled",
                               filled_size=float(f.get("totalSz", 0)),
                               avg_price=float(f.get("avgPx", 0)), raw=resp)
        if "resting" in st:
            return OrderResult(ok=True, exchange_order_id=str(st["resting"].get("oid")),
                               cloid=request.cloid, status="acked", raw=resp)
        return OrderResult(ok=False, cloid=request.cloid, status="rejected",
                           error=f"{request.symbol}: {st.get('error', st)}", raw=resp)

    def cancel(self, symbol: str, exchange_order_id: str | None = None,
               cloid: str | None = None) -> bool:
        with self._lock:
            if cloid is not None:
                resp = self.exchange.cancel_by_cloid(symbol, self._to_cloid(cloid))
            elif exchange_order_id is not None:
                resp = self.exchange.cancel(symbol, int(exchange_order_id))
            else:
                raise ValueError("cancel requires exchange_order_id or cloid")
        return resp.get("status") == "ok"

    def positions(self, address: str | None = None) -> list[Position]:
        state = self.info.user_state(address or self.account_address)
        out: list[Position] = []
        for ap in state.get("assetPositions", []):
            p = ap["position"]
            szi = float(p["szi"])
            if szi == 0:
                continue
            out.append(Position(
                symbol=p["coin"],
                size=szi,
                entry_price=float(p.get("entryPx") or 0),
                unrealized_pnl=float(p.get("unrealizedPnl") or 0),
                leverage=float(p.get("leverage", {}).get("value") or 0) or None,
                liquidation_px=float(p.get("liquidationPx") or 0) or None,
                position_value=float(p.get("positionValue") or 0) or None,
                margin_used=float(p.get("marginUsed") or 0) or None,
                # cumFunding.sinceOpen: + = pagamos funding, − = recebemos.
                cum_funding=float((p.get("cumFunding") or {}).get("sinceOpen") or 0),
            ))
        return out

    def balances(self, address: str | None = None) -> dict[str, float]:
        addr = address or self.account_address
        state = self.info.user_state(addr)
        summary = state.get("marginSummary", {})
        perp_equity = float(summary.get("accountValue", 0))
        margin_used = float(summary.get("totalMarginUsed", 0) or 0)
        withdrawable = float(state.get("withdrawable", 0) or 0)
        # PnL não-realizado agregado das posições abertas (infla accountValue vs.
        # a UI da HL, que mostra withdrawable + spot).
        unrealized = 0.0
        for ap in state.get("assetPositions", []):
            unrealized += float(ap.get("position", {}).get("unrealizedPnl") or 0)
        # Somar saldo spot USDC (HL unificado — spot + perp)
        try:
            spot_state = self.info.spot_user_state(addr)
            spot_usdc = 0.0
            for b in spot_state.get("balances", []):
                if b.get("coin") == "USDC":
                    spot_usdc = float(b.get("total", 0))
                    break
        except Exception:
            spot_usdc = 0.0
        return {
            # chaves legadas mantidas por compatibilidade
            "USDC": perp_equity + spot_usdc,
            "withdrawable": withdrawable + spot_usdc,
            # dict rico (bug /balance — AJUSTES 2026-07-11)
            "accountValue": perp_equity,
            "withdrawable_perp": withdrawable,
            "spot_usdc": spot_usdc,
            "unrealized_pnl": unrealized,
            "margin_used": margin_used,
        }

    def subscribe_own_fills(self, callback: FillCallback) -> None:
        self.subscribe_user_fills(self.account_address, callback)

    def _ensure_ws(self) -> Any:
        if self._ws is None:
            from hyperliquid.info import Info

            from engine.exchanges.hyperliquid.ws_supervisor import WsSupervisor

            base = self._base_url
            self._ws = WsSupervisor(
                make_info=lambda: Info(base_url=base, skip_ws=False),
                name=f"ws-ownfills-{self.network}",
            )
            self._ws.start()
        return self._ws

    def subscribe_user_fills(self, address: str, callback: FillCallback) -> None:
        def _handler(msg: dict[str, Any]) -> None:
            data = msg.get("data", {})
            if data.get("isSnapshot"):
                # Snapshot replays the account's PAST fills on every (re)connect;
                # recording it would duplicate history. Only live fills count.
                return
            for fill in data.get("fills", []):
                callback(fill)

        self._ensure_ws().subscribe({"type": "userFills", "user": address}, _handler)

    def market_meta(self, symbol: str) -> dict[str, Any]:
        asset = self._meta().get(symbol)
        if asset is None:
            raise KeyError(f"unknown symbol: {symbol}")
        return {
            "name": asset["name"],
            "maxLeverage": int(asset.get("maxLeverage", 1)),
            "szDecimals": int(asset.get("szDecimals", 0)),
            "minNotional": 10.0,
        }

    def mid_price(self, symbol: str) -> float:
        mids = self.info.all_mids()
        return float(mids.get(symbol, 0.0))

    def candles(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        """Historical candles (max 5000 per call) — used by the backtest harness."""
        return self.info.candles_snapshot(symbol, interval, start_ms, end_ms)

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.stop()
            except Exception:  # noqa: BLE001
                pass


def make_adapter(
    exchange: str,
    network: str,
    *,
    account_address: str | None = None,
    agent_private_key: str | None = None,
    slippage_steps: list[float] | None = None,
) -> ExchangeAdapter:
    """Factory used by the gateway; venue and network come from settings."""
    if exchange == "hyperliquid":
        if slippage_steps is None:
            from engine.core.config import get_settings

            slippage_steps = get_settings().execution.market_slippage_steps
        return HyperliquidAdapter(
            network=network,
            account_address=account_address,
            agent_private_key=agent_private_key,
            slippage_steps=slippage_steps,
        )
    if exchange == "paper":
        from engine.exchanges.paper import PaperAdapter

        return PaperAdapter()
    raise ValueError(f"unsupported exchange: {exchange}")
