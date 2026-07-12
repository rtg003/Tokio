"""Netting — função PURA (sinal, posição, policy) → plano de intenções (§8.3).

Reconcilia a posição REAL atual com a posição DESEJADA pelo sinal do TradingView
(`market_position`). Não toca em banco, exchange nem relógio: entra estado, sai
plano. É a fonte da decisão do check 12 do validator (§8.2) e, na F1, do que o
executor manda ao gateway.

`flip` = duas intenções com dependência de fill: fecha a posição oposta
(reduce-only) e só então abre a nova (§6.2). Falha na segunda perna ⇒ flat +
incidente (tratado no executor, não aqui).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# market_position do payload → direção desejada.
_WANT = {"long": 1, "short": -1, "flat": 0}


@dataclass(frozen=True)
class Intent:
    """Uma intenção de ordem no plano. `size_usd` é resolvido pelo sizing
    (entrada) ou pela posição atual (fechamento); netting só decide direção,
    reduce_only e dependência de fill."""
    side: str                       # buy | sell
    reduce_only: bool
    role: str                       # entry | add | close | reduce | flip_close | flip_open
    depends_on_prev_fill: bool = False


@dataclass(frozen=True)
class NettingPlan:
    action: str                     # open | add | close | reduce | flip | noop | blocked
    intents: list[Intent] = field(default_factory=list)
    block_code: str | None = None   # BLOCKED_OPPOSITE_POSITION | BLOCKED_ALREADY_IN_POSITION
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.action == "blocked"


def _pos_sign(position_size: float | None) -> int:
    if not position_size:
        return 0
    return 1 if position_size > 0 else -1


def plan_netting(
    *,
    market_position: str,
    position_size: float | None,
    on_opposite_signal: str = "reject",
    on_same_direction_signal: str = "ignore",
    max_adds: int = 0,
    current_adds: int = 0,
) -> NettingPlan:
    """Resolve o plano de intenções.

    - `market_position`: destino do sinal (long|short|flat).
    - `position_size`: posição REAL assinada (+long / -short / 0|None sem posição).
    - policies: §6.2.
    - `current_adds`: adds já feitos na posição atual (enforcement de max_adds).
    """
    if market_position not in _WANT:
        return NettingPlan("blocked", block_code="INVALID_COMBINATION",
                           reason=f"market_position inválido: {market_position!r}")

    want = _WANT[market_position]
    cur = _pos_sign(position_size)

    # Destino flat: fechar o que existir; sem posição = no-op (T12).
    if want == 0:
        if cur == 0:
            return NettingPlan("noop", reason="flat sem posição — no-op")
        close_side = "sell" if cur > 0 else "buy"
        return NettingPlan("close",
                           intents=[Intent(close_side, True, "close")],
                           reason="fechamento total reduce-only")

    entry_side = "buy" if want > 0 else "sell"

    # Sem posição: abertura simples.
    if cur == 0:
        return NettingPlan("open",
                           intents=[Intent(entry_side, False, "entry")],
                           reason="abertura")

    # Mesma direção: policy on_same_direction_signal.
    if cur == want:
        if on_same_direction_signal == "add":
            if current_adds >= max_adds:
                return NettingPlan("blocked",
                                   block_code="BLOCKED_ALREADY_IN_POSITION",
                                   reason=f"max_adds atingido ({current_adds}/{max_adds})")
            return NettingPlan("add",
                               intents=[Intent(entry_side, False, "add")],
                               reason=f"add {current_adds + 1}/{max_adds}")
        return NettingPlan("blocked",
                           block_code="BLOCKED_ALREADY_IN_POSITION",
                           reason="já posicionado na mesma direção (ignore)")

    # Direção oposta: policy on_opposite_signal.
    close_side = "sell" if cur > 0 else "buy"
    if on_opposite_signal == "reject":
        return NettingPlan("blocked",
                           block_code="BLOCKED_OPPOSITE_POSITION",
                           reason="sinal oposto com policy reject")
    if on_opposite_signal == "reduce":
        return NettingPlan("reduce",
                           intents=[Intent(close_side, True, "reduce")],
                           reason="reduz posição oposta (reduce-only)")
    if on_opposite_signal == "flip":
        return NettingPlan("flip",
                           intents=[
                               Intent(close_side, True, "flip_close"),
                               Intent(entry_side, False, "flip_open",
                                      depends_on_prev_fill=True),
                           ],
                           reason="flip em duas pernas (2ª após fill da 1ª)")
    return NettingPlan("blocked", block_code="BLOCKED_OPPOSITE_POSITION",
                       reason=f"on_opposite_signal desconhecido: {on_opposite_signal!r}")
