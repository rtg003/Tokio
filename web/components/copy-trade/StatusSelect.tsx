"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import CopyConfigModal from "@/components/copy-trade/CopyConfigModal";
import { saveTraderConfigAndActivate, TraderExecConfig } from "@/lib/copy-trade/data";

const STATUSES = ["SUGERIDO", "SALVO", "TESTNET", "MAINNET", "REJEITADO"] as const;

export default function StatusSelect({
  address,
  status,
  name,
  config,
  equity,
}: {
  address: string;
  status: string;
  name?: string;
  config?: {
    mode?: string;
    value?: number;
    max_leverage?: number;
    blocked_assets?: string[] | string;
  };
  equity?: number | null;
}) {
  const router = useRouter();
  const [value, setValue] = useState(status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modalTarget, setModalTarget] = useState<"testnet" | "mainnet" | null>(null);

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

  async function onModalConfirm(cfg: TraderExecConfig) {
    const next = modalTarget === "mainnet" ? "MAINNET" : "TESTNET";
    setBusy(true);
    setError(null);
    const result = await saveTraderConfigAndActivate(address, cfg, next);
    setBusy(false);
    if (!result.ok) {
      setError(result.reason ?? "erro_ativacao");
      return;
    }
    setValue(next);
    setModalTarget(null);
    router.refresh();
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
        {STATUSES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      {error && <span className="status-error">{error}</span>}
      {modalTarget && (
        <CopyConfigModal
          address={address}
          name={name ?? address}
          targetEnv={modalTarget}
          currentConfig={config}
          equity={equity}
          busy={busy}
          error={error}
          onClose={() => {
            if (busy) return;
            setModalTarget(null);
            setError(null);
          }}
          onConfirm={onModalConfirm}
        />
      )}
    </span>
  );
}
