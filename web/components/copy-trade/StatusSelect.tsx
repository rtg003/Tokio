"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import CopyConfigModal, { TraderStats } from "@/components/copy-trade/CopyConfigModal";
import {
  closeAllPositions,
  saveTraderConfigAndActivate,
  TraderExecConfig,
} from "@/lib/copy-trade/data";

function envForStatus(status: string): "testnet" | "mainnet" | null {
  if (status === "TESTNET") return "testnet";
  if (status === "MAINNET") return "mainnet";
  return null;
}

// Ambientes isolados: o combo só oferece o status operante do ambiente ativo
// (TESTNET no testnet, MAINNET no mainnet). Os demais (sem ambiente) sempre
// aparecem. Promoção cross-env é fluxo de 2 passos (SALVO → troca ambiente →
// promove). Isto é só UI: o gate humano do backend segue validando igual.
function statusesForEnv(env: "testnet" | "mainnet", current: string): string[] {
  const operating = env === "mainnet" ? "MAINNET" : "TESTNET";
  const base = ["SUGERIDO", "SALVO", operating, "REJEITADO"];
  // Defensivo: se o status atual não estiver na lista (não deve ocorrer, pois a
  // tabela filtra o outro ambiente), inclui-o para o <select> nativo não quebrar.
  return base.includes(current) ? base : [current, ...base];
}

export default function StatusSelect({
  address,
  status,
  env,
  name,
  config,
  stats,
  equity,
}: {
  address: string;
  status: string;
  env: "testnet" | "mainnet";
  name?: string;
  config?: {
    mode?: string;
    value?: number;
    max_leverage?: number;
    blocked_assets?: string[] | string;
    thresholds?: Record<string, number> | string;
  };
  stats?: TraderStats;
  equity?: number | null;
}) {
  const router = useRouter();
  const [value, setValue] = useState(status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [modalTarget, setModalTarget] = useState<"testnet" | "mainnet" | null>(null);

  // Ambiente operante atual (de onde partem as posições a fechar na troca).
  const currentEnv = envForStatus(value);

  async function postStatus(next: string) {
    const previous = value;
    setValue(next);
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/control/trader/${address}/status?new_status=${encodeURIComponent(next)}`,
        { method: "POST" },
      );
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        setValue(previous);
        setError(data.reason ?? data.detail ?? "erro_status");
        return;
      }
      router.refresh();
    } catch {
      setValue(previous);
      setError("gateway_indisponivel");
    } finally {
      setBusy(false);
    }
  }

  function onChange(next: string) {
    if (next === "TESTNET" || next === "MAINNET") {
      // Abre o modal de configuração; o status só muda após "Ativar cópia".
      setError(null);
      setModalTarget(next === "MAINNET" ? "mainnet" : "testnet");
      return;
    }
    postStatus(next);
  }

  async function onModalConfirm(cfg: TraderExecConfig, closePositions: boolean) {
    const next = modalTarget === "mainnet" ? "MAINNET" : "TESTNET";
    setBusy(true);
    setError(null);
    setProgress(null);

    // Fluxo unificado: (1) fecha posições no ambiente antigo se pedido, com
    // progresso; (2) salva o sizing; (3) muda o status; (4) toast de conclusão.
    let closedCount = 0;
    if (closePositions && currentEnv) {
      setProgress(`Fechando posições em ${currentEnv}…`);
      const closeRes = await closeAllPositions(address, currentEnv);
      if (!closeRes.ok) {
        setBusy(false);
        setProgress(null);
        const failed = closeRes.results.filter((r) => !r.ok).map((r) => r.symbol);
        setError(
          closeRes.reason ??
            (failed.length ? `falha ao fechar: ${failed.join(", ")}` : "erro_fechamento"),
        );
        return;
      }
      closedCount = closeRes.results.filter((r) => r.ok).length;
    }

    setProgress("Salvando configuração e ativando…");
    const result = await saveTraderConfigAndActivate(address, cfg, next);
    setBusy(false);
    setProgress(null);
    if (!result.ok) {
      setError(result.reason ?? "erro_ativacao");
      return;
    }

    const who = name ?? address;
    const env = next === "MAINNET" ? "mainnet" : "testnet";
    setToast(
      closePositions
        ? `Transição completa — ${closedCount} posição(ões) fechada(s), ${who} ativo em ${env}`
        : "Cópia Ativada",
    );
    setValue(next);
    setModalTarget(null);
    router.refresh();
    window.setTimeout(() => setToast(null), 6000);
  }

  return (
    <span className="status-select-wrap" title={error ?? undefined}>
      <select
        className={`select select-status status-${value.toLowerCase()}`}
        value={value}
        disabled={busy}
        onChange={(e) => onChange(e.target.value)}
        aria-label={`Status do trader ${address}`}
      >
        {statusesForEnv(env, value).map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      {error && <span className="status-error">{error}</span>}
      {toast && <span className="toast">{toast}</span>}
      {modalTarget && (
        <CopyConfigModal
          address={address}
          name={name ?? address}
          targetEnv={modalTarget}
          currentEnv={currentEnv}
          currentConfig={config}
          stats={stats}
          equity={equity}
          busy={busy}
          error={error}
          progress={progress}
          onClose={() => {
            if (busy) return;
            setModalTarget(null);
            setError(null);
            setProgress(null);
          }}
          onConfirm={onModalConfirm}
        />
      )}
    </span>
  );
}
