"""Virtual ledger — per-strategy position/PnL attribution (ADR 0002, Phase A).

On the exchange, netting is per asset; attribution happens here via `cloid`.
The cloid embeds a strategy hash prefix so any fill can be attributed even
after a restart (the orders table is the authoritative map cloid->strategy).

Opposite-direction policy: when two strategies hold opposing virtual positions
on the same symbol, the ledger emits a `risk.opposite_directions` warning.
Default policy is ALLOW (virtual books stay correct; real netting just reduces
margin usage); a global config flag can force-block at the enforcer level.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any


def make_cloid(strategy_id: str) -> str:
    """128-bit hex cloid: 4-byte strategy hash prefix + 12 random bytes."""
    prefix = hashlib.sha256(strategy_id.encode()).hexdigest()[:8]
    return "0x" + prefix + secrets.token_hex(12)


def cloid_strategy_prefix(strategy_id: str) -> str:
    return hashlib.sha256(strategy_id.encode()).hexdigest()[:8]


@dataclass
class VirtualPosition:
    symbol: str
    size: float = 0.0            # signed
    avg_entry: float = 0.0
    realized_pnl: float = 0.0    # net of fees
    fees_paid: float = 0.0


@dataclass
class StrategyBook:
    strategy_id: str
    positions: dict[str, VirtualPosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0

    def exposure_usd(self, prices: dict[str, float]) -> float:
        return sum(
            abs(p.size) * prices.get(p.symbol, p.avg_entry)
            for p in self.positions.values()
        )


class Ledger:
    def __init__(self, logger: Any | None = None) -> None:
        self._books: dict[str, StrategyBook] = {}
        self._cloid_map: dict[str, str] = {}   # cloid -> strategy_id
        self._lock = threading.Lock()
        self.logger = logger

    def register_order(self, cloid: str, strategy_id: str) -> None:
        with self._lock:
            self._cloid_map[cloid] = strategy_id
            self._books.setdefault(strategy_id, StrategyBook(strategy_id))

    def strategy_for_cloid(self, cloid: str | None) -> str | None:
        if not cloid:
            return None
        return self._cloid_map.get(cloid)

    def book(self, strategy_id: str) -> StrategyBook:
        with self._lock:
            return self._books.setdefault(strategy_id, StrategyBook(strategy_id))

    def books(self) -> dict[str, StrategyBook]:
        with self._lock:
            return dict(self._books)

    def apply_fill(
        self,
        *,
        cloid: str | None,
        strategy_id: str | None = None,
        symbol: str,
        side: str,
        price: float,
        size: float,
        fee: float,
    ) -> float | None:
        """Update the virtual book. Returns realized PnL (net of this fill's fee)
        when the fill reduces/closes a position, else None."""
        sid = strategy_id or self.strategy_for_cloid(cloid)
        if sid is None:
            if self.logger:
                self.logger.warning("fill.unattributed", {"cloid": cloid, "symbol": symbol})
            return None

        signed = size if side == "buy" else -size
        realized: float | None = None
        with self._lock:
            book = self._books.setdefault(sid, StrategyBook(sid))
            pos = book.positions.setdefault(symbol, VirtualPosition(symbol))
            if pos.size == 0 or (pos.size > 0) == (signed > 0):
                total = abs(pos.size) + abs(signed)
                if total > 0:
                    pos.avg_entry = (pos.avg_entry * abs(pos.size) + price * abs(signed)) / total
                pos.size += signed
            else:
                closing = min(abs(signed), abs(pos.size))
                direction = 1.0 if pos.size > 0 else -1.0
                gross = (price - pos.avg_entry) * closing * direction
                realized = gross - fee
                pos.realized_pnl += realized
                book.realized_pnl += realized
                pos.size += signed
                if abs(pos.size) < 1e-12:
                    pos.size = 0.0
                if abs(signed) > closing:  # flipped through zero
                    pos.avg_entry = price
            pos.fees_paid += fee
            book.fees_paid += fee
        self._check_opposite_directions(symbol)
        return realized

    def _check_opposite_directions(self, symbol: str) -> None:
        with self._lock:
            longs = [b.strategy_id for b in self._books.values()
                     if b.positions.get(symbol) and b.positions[symbol].size > 0]
            shorts = [b.strategy_id for b in self._books.values()
                      if b.positions.get(symbol) and b.positions[symbol].size < 0]
        if longs and shorts and self.logger:
            self.logger.warning(
                "risk.opposite_directions",
                {"symbol": symbol, "long": longs, "short": shorts,
                 "policy": "allow (netting reduces real margin); review if unintended"},
            )

    def strategy_holding_symbol(self, symbol: str) -> str | None:
        """Estratégia ÚNICA que segura `symbol` agora, ou None se 0 ou >1.

        Usado para atribuir fills órfãos (ADL/liquidação, cloid=null): a venue
        deslevera por ativo, então um fill sem cloid só é atribuível sem
        ambiguidade quando exatamente uma estratégia tem posição naquele símbolo.
        Nunca cruza estratégias (§5.1): >1 dono ⇒ None (fica em visão de sistema).
        """
        with self._lock:
            holders = [
                sid for sid, book in self._books.items()
                if (pos := book.positions.get(symbol)) is not None
                and abs(pos.size) > 1e-12
            ]
        return holders[0] if len(holders) == 1 else None

    def hydrate_from_db(self, rows: list[dict[str, Any]]) -> None:
        """Reconstrói os books em memória a partir dos fills persistidos.

        `Ledger` é 100% em memória; após um restart do gateway o reconcile de
        startup compararia o alvo do trader contra um book VAZIO e reabriria tudo
        (posições dobradas). Reproduzir o histórico completo de fills (ordem
        `id ASC`) reconstrói o SIZE líquido corrente — aberturas e fechamentos se
        anulam. `strategy_id` vem explícito de cada linha, então independe do
        `_cloid_map` (que não sobrevive ao restart).

        Chamado no startup do gateway (antes dos runners). Não altera a
        assinatura de `apply_fill`.
        """
        with self._lock:
            self._books.clear()
        # `_lock` é não-reentrante e `apply_fill` o re-adquire — replay FORA do
        # lock. Silencia o logger no replay p/ evitar rajada de warnings no boot.
        saved_logger = self.logger
        self.logger = None
        try:
            for row in rows:
                self.apply_fill(
                    cloid=row.get("cloid"),
                    strategy_id=row["strategy_id"],
                    symbol=row["symbol"],
                    side=row["side"],
                    price=float(row["price"]),
                    size=float(row["size"]),
                    fee=float(row["fee"] or 0.0),
                )
        finally:
            self.logger = saved_logger

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                sid: {
                    "realized_pnl": round(book.realized_pnl, 6),
                    "fees_paid": round(book.fees_paid, 6),
                    "positions": {
                        sym: {"size": p.size, "avg_entry": p.avg_entry,
                              "realized_pnl": round(p.realized_pnl, 6)}
                        for sym, p in book.positions.items() if p.size != 0
                    },
                }
                for sid, book in self._books.items()
            }
