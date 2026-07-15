"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { cancelOrder } from "@/lib/copy-trade/data";

// Botão flat/minimalista de cancelar UMA ordem em aberto. Ato humano
// autenticado: pede confirmação e então cancela a ordem na venue (Hyperliquid)
// via /api/control/order/cancel. `strategyId`/`cloid`/`env` vêm da linha da
// ordem; sem eles o botão fica desabilitado.
export default function CancelOrderButton({
  strategyId,
  symbol,
  cloid,
  env,
}: {
  strategyId?: string | null;
  symbol: string;
  cloid?: string | null;
  env?: string | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const validEnv = env === "testnet" || env === "mainnet";
  const disabled = busy || !strategyId || !cloid || !validEnv;

  async function onCancel() {
    if (disabled || !strategyId || !cloid || !validEnv) return;
    const ok = window.confirm(
      `Cancelar a ordem de ${symbol}?\n\nIsto cancela a ordem em aberto na ` +
        `Hyperliquid (${env}). A ação é imediata.`,
    );
    if (!ok) return;
    setBusy(true);
    const res = await cancelOrder({
      strategy_id: strategyId,
      symbol,
      cloid,
      env: env as "testnet" | "mainnet",
    });
    setBusy(false);
    if (res.ok) {
      router.refresh();
    } else {
      window.alert(`Falha ao cancelar ${symbol}: ${res.reason ?? "erro"}`);
    }
  }

  return (
    <button
      type="button"
      className="pos-close-btn"
      aria-label={`Cancelar ordem de ${symbol}`}
      title={disabled && !busy ? "Cancelar indisponível" : `Cancelar ${symbol}`}
      disabled={disabled}
      onClick={onCancel}
    >
      {busy ? (
        "…"
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round">
          <polyline points="3 6 5 6 21 6" />
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
          <path d="M10 11v6M14 11v6" />
          <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
        </svg>
      )}
    </button>
  );
}
