"use client";

import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

// Filtros específicos do Copy Trade: trader + período (segmented Hoje/7D/30D/
// Personalizado com par de datas dd/mm/aa). Wallet e AMBIENTE são controles
// GLOBAIS e vivem no topo (Shell). O estado destes filtros vive na URL
// (?trader&period&from&to) — o server component refaz as queries a partir dela.

export type TraderOption = { value: string; label: string };

const PRESETS: { key: string; label: string }[] = [
  { key: "today", label: "Hoje" },
  { key: "yesterday", label: "Ontem" },
  { key: "7d", label: "7 dias" },
  { key: "custom", label: "Personalizado" },
];

function maskDate(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 6);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
}

export default function DashboardControls({
  period,
  from,
  to,
  trader,
  traders,
}: {
  period: string;
  from: string;
  to: string;
  trader: string;
  traders: TraderOption[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [fromInput, setFromInput] = useState(from);
  const [toInput, setToInput] = useState(to);

  function push(params: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams.toString());
    for (const [k, v] of Object.entries(params)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    router.push(`${pathname}?${next.toString()}`);
  }

  function applyCustomIfComplete(f: string, t: string) {
    const full = /^\d{2}\/\d{2}\/\d{2}$/;
    if (full.test(f) && full.test(t)) {
      push({ period: "custom", from: f, to: t });
    }
  }

  return (
    <div className="controls">
      <select
        className="select filter-select filter-select-trader"
        aria-label="Trader acompanhado"
        value={trader}
        onChange={(e) => push({ trader: e.target.value === "all" ? null : e.target.value })}
      >
        {traders.map((t) => (
          <option key={t.value} value={t.value}>
            {t.label}
          </option>
        ))}
      </select>

      <div className="segmented" role="group" aria-label="Período">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            className={period === p.key ? "on" : ""}
            onClick={() =>
              p.key === "custom"
                ? push({ period: "custom" })
                : push({ period: p.key, from: null, to: null })
            }
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className={`datewrap ${period === "custom" ? "show" : ""}`}>
        <input
          className="dateinput"
          aria-label="Data inicial"
          placeholder="dd/mm/aa"
          value={fromInput}
          onChange={(e) => {
            const v = maskDate(e.target.value);
            setFromInput(v);
            applyCustomIfComplete(v, toInput);
          }}
        />
        {" → "}
        <input
          className="dateinput"
          aria-label="Data final"
          placeholder="dd/mm/aa"
          value={toInput}
          onChange={(e) => {
            const v = maskDate(e.target.value);
            setToInput(v);
            applyCustomIfComplete(fromInput, v);
          }}
        />
      </div>
    </div>
  );
}
