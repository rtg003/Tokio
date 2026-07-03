"""Métricas do discovery v2 (spec PROMPT_DISCOVERY_TRADERS_v5) — funções puras.

Este módulo concentra as funções de métrica exigidas pela spec, com testes
unitários obrigatórios. Nenhum I/O: entradas sintéticas nos testes.
"""
from __future__ import annotations

# --- Profit factor (patch de scoring aplicado 2026-07-03) --------------------
# Crédito gradativo no score (peso 20% do composto):
#   - integral até PF 3.0;
#   - meio-crédito de 3.0 a 5.0, valendo APENAS se n_trades >= 60 na janela
#     (PF extremo com amostra pequena é variância, não habilidade);
#   - acima de 5.0 não pontua.
# PF é calculado INCLUINDO o PnL não realizado das posições abertas no
# fechamento da janela — PF só de realizados é inflável ao não fechar
# perdedores.

PF_FULL_CREDIT_CAP = 3.0
PF_EXTENDED_CAP = 5.0
PF_EXTENDED_MIN_TRADES = 60
_PF_MAX_EFFECTIVE = PF_FULL_CREDIT_CAP + 0.5 * (PF_EXTENDED_CAP - PF_FULL_CREDIT_CAP)


def profit_factor(gross_gains: float, gross_losses: float,
                  unrealized_pnl: float = 0.0) -> float:
    """PF da janela incluindo o não realizado das posições abertas no fechamento.

    gross_gains: soma dos trades fechados vencedores (>= 0)
    gross_losses: soma ABSOLUTA dos trades fechados perdedores (>= 0)
    unrealized_pnl: PnL aberto no fechamento da janela (com sinal)
    """
    if gross_gains < 0 or gross_losses < 0:
        raise ValueError("gross_gains/gross_losses devem ser >= 0")
    gains = gross_gains + max(unrealized_pnl, 0.0)
    losses = gross_losses + max(-unrealized_pnl, 0.0)
    if losses == 0.0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def pf_effective(pf: float, n_trades: int) -> float:
    """PF efetivo para o score, com o crédito gradativo do patch."""
    if pf <= 0:
        return 0.0
    effective = min(pf, PF_FULL_CREDIT_CAP)
    if pf > PF_FULL_CREDIT_CAP and n_trades >= PF_EXTENDED_MIN_TRADES:
        effective += 0.5 * (min(pf, PF_EXTENDED_CAP) - PF_FULL_CREDIT_CAP)
    return effective


def pf_score_credit(pf: float, n_trades: int) -> float:
    """Crédito normalizado [0, 1] do componente profit factor do score."""
    return min(1.0, pf_effective(pf, n_trades) / _PF_MAX_EFFECTIVE)
