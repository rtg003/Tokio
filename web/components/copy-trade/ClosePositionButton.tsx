"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { closeSinglePosition } from "@/lib/copy-trade/data";

// Botão flat/minimalista de fechar UMA posição. Ato humano autenticado: pede
// confirmação e então envia uma ordem reduce_only market na venue (Hyperliquid)
// via /api/control/position/close. `strategyId`/`env` vêm atribuídos na linha
// da posição; sem eles o botão fica desabilitado.
export default function ClosePositionButton({
  strategyId,
  symbol,
  env,
}: {
  strategyId?: string | null;
  symbol: string;
  env?: string | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const validEnv = env === "testnet" || env === "mainnet";
  const disabled = busy || !strategyId || !validEnv;

  async function onClose() {
    if (disabled || !strategyId || !validEnv) return;
    const ok = window.confirm(
      `Fechar a posição de ${symbol}?\n\nIsto envia uma ordem reduce-only a ` +
        `mercado na Hyperliquid (${env}). A ação é imediata.`,
    );
    if (!ok) return;
    setBusy(true);
    const res = await closeSinglePosition({
      strategy_id: strategyId,
      symbol,
      env: env as "testnet" | "mainnet",
    });
    setBusy(false);
    if (res.ok) {
      router.refresh();
    } else {
      window.alert(`Falha ao fechar ${symbol}: ${res.reason ?? "erro"}`);
    }
  }

  return (
    <button
      type="button"
      className="pos-close-btn"
      aria-label={`Fechar posição de ${symbol}`}
      title={disabled && !busy ? "Fechar indisponível" : `Fechar ${symbol}`}
      disabled={disabled}
      onClick={onClose}
    >
      {busy ? (
        "…"
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
          <line x1="6" y1="6" x2="18" y2="18" />
          <line x1="18" y1="6" x2="6" y2="18" />
        </svg>
      )}
    </button>
  );
}
