"use client";

import { useState } from "react";
import TvWizard from "@/components/trading-view/TvWizard";

// Botão fantasma "+ nova estratégia" (§4 do design): só monta o wizard sob
// demanda, na rota /trading-view. O equity da wallet ativa alimenta as
// validações de alocação (soma das alocações ≤ equity).
export default function NewStrategyButton({ equity }: { equity?: number | null }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="btn ghost" onClick={() => setOpen(true)}>
        + nova estratégia
      </button>
      {open && <TvWizard equity={equity} onClose={() => setOpen(false)} />}
    </>
  );
}
