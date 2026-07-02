"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function StrategyActions({
  strategyId,
  status,
}: {
  strategyId: string;
  status: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function call(action: "pause" | "activate") {
    setBusy(true);
    try {
      await fetch(`/api/control/strategy/${strategyId}/${action}`, {
        method: "POST",
      });
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  if (status === "active") {
    return (
      <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => call("pause")}>
        Pausar
      </button>
    );
  }
  if (status === "paused" || status === "auto_paused") {
    return (
      <button className="btn btn-amber btn-sm" disabled={busy} onClick={() => call("activate")}>
        Ativar
      </button>
    );
  }
  // dry_run -> active is a human gate outside the web (evidence required)
  return (
    <button className="btn btn-ghost btn-sm" disabled title="promoção de dry-run é gate humano">
      —
    </button>
  );
}
