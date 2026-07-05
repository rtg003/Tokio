"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const STATUSES = ["SUGERIDO", "SALVO", "TESTNET", "MAINNET", "REJEITADO"] as const;

function confirmation(next: string): string | null {
  if (next === "TESTNET") {
    return "Iniciar cópia em TESTNET para este trader agora?";
  }
  if (next === "MAINNET") {
    return "ATENÇÃO: MAINNET usa dinheiro REAL. Confirmar promoção deste trader?";
  }
  return null;
}

export default function StatusSelect({
  address,
  status,
}: {
  address: string;
  status: string;
}) {
  const router = useRouter();
  const [value, setValue] = useState(status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onChange(next: string) {
    const previous = value;
    const message = confirmation(next);
    if (message && !window.confirm(message)) {
      setValue(previous);
      return;
    }
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
    </span>
  );
}
