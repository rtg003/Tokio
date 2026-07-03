"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

// Ações operacionais (API de controle). Gate 2 (SUGERIDO→operação e
// DRY_RUN→COPIANDO) é intencionalmente ausente aqui — só via CLI humana.
export default function TraderActions({
  address,
  status,
}: {
  address: string;
  status: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function setStatus(newStatus: string) {
    setBusy(true);
    try {
      await fetch(
        `/api/control/trader/${address}/status?new_status=${newStatus}`,
        { method: "POST" },
      );
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  if (status === "DRY_RUN" || status === "COPIANDO") {
    return (
      <button className="btn btn-ghost btn-sm" disabled={busy}
              onClick={() => setStatus("PAUSADO")}>
        Pausar
      </button>
    );
  }
  if (status === "PAUSADO") {
    return (
      <button className="btn btn-amber btn-sm" disabled={busy}
              onClick={() => setStatus("DRY_RUN")}>
        Retomar
      </button>
    );
  }
  if (status === "SUGERIDO") {
    return (
      <button className="btn btn-ghost btn-sm" disabled={busy}
              title="aprovação (Gate 2) é via CLI humana"
              onClick={() => setStatus("REJEITADO")}>
        Rejeitar
      </button>
    );
  }
  return null;
}
