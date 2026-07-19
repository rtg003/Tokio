"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { reexecuteOrder } from "@/lib/copy-trade/data";
import { fmtNum, fmtSigned } from "@/lib/format";

// Botão flat/minimalista de reexecutar UMA ordem recusada (rejected/error) a
// PREÇO DE MERCADO atual. Ato humano autenticado: primeiro consulta o preço de
// mercado (preview), mostra na confirmação preço original vs. mercado atual (+
// variação %), e só então envia uma NOVA ordem via /api/control/order/reexecute.
// `strategyId`/`cloid`/`env` vêm da linha da ordem; sem eles o botão é desabilitado.
export default function ReexecuteOrderButton({
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

  async function onReexecute() {
    if (disabled || !strategyId || !cloid || !validEnv) return;
    setBusy(true);
    // 1) preview: consulta o preço de mercado atual (não envia ordem).
    const prev = await reexecuteOrder({
      strategy_id: strategyId,
      symbol,
      cloid,
      env: env as "testnet" | "mainnet",
      preview: true,
    });
    if (!prev.ok) {
      setBusy(false);
      window.alert(`Não foi possível reexecutar ${symbol}: ${prev.reason ?? "erro"}`);
      return;
    }
    // 2) confirmação com preço original vs. mercado atual.
    const sideLabel = prev.side === "buy" ? "LONG" : "SHORT";
    const orig =
      prev.original_price != null ? `$${fmtNum(prev.original_price)}` : "MKT";
    const mkt =
      prev.market_price != null ? `$${fmtNum(prev.market_price)}` : "—";
    const drift =
      prev.drift_pct != null ? ` (${fmtSigned(prev.drift_pct)}%)` : "";
    const sizeLbl = prev.size != null ? fmtNum(prev.size, 4) : "?";
    const ok = window.confirm(
      `Reexecutar ${sideLabel} ${sizeLbl} ${symbol} a mercado?\n\n` +
        `Preço original: ${orig}\n` +
        `Mercado agora: ${mkt}${drift}\n\n` +
        `Uma NOVA ordem será enviada à Hyperliquid (${env}) a preço de mercado.`,
    );
    if (!ok) {
      setBusy(false);
      return;
    }
    // 3) execução: envia a nova ordem a mercado.
    const res = await reexecuteOrder({
      strategy_id: strategyId,
      symbol,
      cloid,
      env: env as "testnet" | "mainnet",
      preview: false,
    });
    setBusy(false);
    if (res.ok) {
      router.refresh();
    } else {
      window.alert(`Falha ao reexecutar ${symbol}: ${res.reason ?? "erro"}`);
    }
  }

  return (
    <button
      type="button"
      className="pos-close-btn reexec"
      aria-label={`Reexecutar ordem de ${symbol}`}
      title={disabled && !busy ? "Reexecutar indisponível" : `Reexecutar ${symbol}`}
      disabled={disabled}
      onClick={onReexecute}
    >
      {busy ? (
        "…"
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round">
          <polyline points="23 4 23 10 17 10" />
          <polyline points="1 20 1 14 7 14" />
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
        </svg>
      )}
    </button>
  );
}
