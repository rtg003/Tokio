"use client";

import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

// Fiel ao mockup aprovado: select nativo (seta em CSS puro) com value no
// formato exchange:conta:ambiente + segmented Hoje/7D/30D/Personalizado com
// par de datas dd/mm/aa (máscara leve de digitação). O estado vive na URL
// (?account&period&from&to) — o server component refaz as queries a partir dela.

export type AccountOption = { value: string; label: string };
export type TraderOption = { value: string; label: string };
export type WalletOption = { value: string; label: string };

const PRESETS: { key: string; label: string }[] = [
  { key: "today", label: "Hoje" },
  { key: "7d", label: "7D" },
  { key: "30d", label: "30D" },
  { key: "custom", label: "Personalizado" },
];

function maskDate(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 6);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
}

export default function DashboardControls({
  wallets,
  wallet,
  accounts,
  account,
  period,
  from,
  to,
  trader,
  traders,
}: {
  wallets: WalletOption[];
  wallet: string;
  accounts: AccountOption[];
  account: string;
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
      {wallets.length > 1 && (
        <select
          className="select filter-select filter-select-wallet"
          aria-label="Wallet (master de trading)"
          value={wallet}
          onChange={(e) => push({ wallet: e.target.value === "all" ? null : e.target.value })}
        >
          {wallets.map((w) => (
            <option key={w.value} value={w.value}>
              {w.label}
            </option>
          ))}
        </select>
      )}

      <select
        className="select filter-select filter-select-account"
        aria-label="Corretora e conta"
        value={account}
        onChange={(e) => push({ account: e.target.value })}
      >
        {accounts.map((a) => (
          <option key={a.value} value={a.value}>
            {a.label}
          </option>
        ))}
      </select>

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
