"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ENV_COOKIE, Environment, WALLET_COOKIE } from "@/lib/prefs";
import { WalletOption, setWalletLabel } from "@/lib/copy-trade/data";

type Health = {
  ok: boolean;
  exchange?: string;
  network?: string;
  kill_switch?: boolean;
  circuit_breaker?: boolean;
  uptime_s?: number;
};

function fmtUptime(s?: number): string {
  if (!s) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  return d > 0 ? `${d}d ${h}h` : `${h}h ${Math.floor((s % 3600) / 60)}m`;
}

export default function Shell({
  email,
  env,
  wallet,
  wallets,
  children,
}: {
  email: string;
  env: Environment;
  wallet: string;
  wallets: WalletOption[];
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [drawer, setDrawer] = useState(false);
  const [light, setLight] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  // Edição inline do rótulo da wallet selecionada (combo do topo).
  const [editingLabel, setEditingLabel] = useState(false);
  const [labelDraft, setLabelDraft] = useState("");
  const [savingLabel, setSavingLabel] = useState(false);

  // Nome atual da wallet selecionada = parte antes de " — 0x…" no label.
  const selectedOpt = wallets.find((w) => w.value === wallet);
  const currentName =
    selectedOpt && selectedOpt.label.includes(" — ")
      ? selectedOpt.label.split(" — ")[0]
      : "";

  async function saveLabel() {
    if (savingLabel) return;
    setSavingLabel(true);
    const res = await setWalletLabel(wallet, labelDraft.trim());
    setSavingLabel(false);
    if (res.ok) {
      setEditingLabel(false);
      router.refresh();
    }
  }

  // Controle GLOBAL: grava o cookie (não-httpOnly) e recarrega os server
  // components. O valor exibido vem do servidor (props), então não lemos cookie
  // no cliente. Vale para TODAS as telas.
  function setPref(name: string, value: string) {
    document.cookie = `${name}=${value};path=/;max-age=31536000;samesite=lax`;
    router.refresh();
  }

  useEffect(() => {
    setLight(document.documentElement.classList.contains("light"));
  }, []);

  useEffect(() => {
    let live = true;
    const poll = async () => {
      try {
        const r = await fetch("/api/control/health");
        if (live) setHealth(r.ok ? await r.json() : null);
      } catch {
        if (live) setHealth(null);
      }
    };
    poll();
    const id = setInterval(poll, 15000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);

  function setTheme(mode: "dark" | "light") {
    const isLight = mode === "light";
    setLight(isLight);
    document.documentElement.classList.toggle("light", isLight);
    try {
      localStorage.setItem("tokio-theme", mode);
    } catch {}
  }

  async function logout(e: React.MouseEvent) {
    e.preventDefault();
    await fetch("/api/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  const online = health?.ok === true;

  const nav = (href: string, label: string, ico: string) => (
    <Link
      href={href}
      className={pathname === href ? "active" : ""}
      onClick={() => setDrawer(false)}
    >
      <span className="ico">{ico}</span> {label}
    </Link>
  );

  return (
    <>
      <div className="statusbar">
        <button className="hamb" aria-label="Abrir menu" onClick={() => setDrawer(!drawer)}>
          ≡
        </button>
        <span className="seg">
          <span className={`dot ${online ? "on" : "off"}`} /> ENGINE{" "}
          <strong>{online ? "ONLINE" : "OFFLINE"}</strong>
        </span>
        {wallets.length > 1 && !editingLabel && (
          <select
            className="statusbar-sel wallet-sel"
            aria-label="Wallet (master de trading)"
            value={wallet}
            onChange={(e) => setPref(WALLET_COOKIE, e.target.value)}
          >
            {wallets.map((w) => (
              <option key={w.value} value={w.value}>
                {w.label}
              </option>
            ))}
          </select>
        )}
        {wallets.length > 1 && wallet !== "all" && !editingLabel && (
          <button
            className="wallet-label-edit"
            aria-label="Editar rótulo da wallet"
            title="Editar rótulo da wallet"
            onClick={() => {
              setLabelDraft(currentName);
              setEditingLabel(true);
            }}
          >
            ✎
          </button>
        )}
        {editingLabel && (
          <span className="wallet-label-editor">
            <input
              className="statusbar-sel wallet-label-input"
              aria-label="Rótulo da wallet"
              placeholder="Rótulo (ex.: Hyperliquid 1)"
              value={labelDraft}
              autoFocus
              maxLength={64}
              disabled={savingLabel}
              onChange={(e) => setLabelDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveLabel();
                if (e.key === "Escape") setEditingLabel(false);
              }}
            />
            <button
              className="wallet-label-edit"
              aria-label="Salvar rótulo"
              title="Salvar"
              disabled={savingLabel}
              onClick={saveLabel}
            >
              ✓
            </button>
            <button
              className="wallet-label-edit"
              aria-label="Cancelar"
              title="Cancelar"
              disabled={savingLabel}
              onClick={() => setEditingLabel(false)}
            >
              ✕
            </button>
          </span>
        )}
        <select
          className={`statusbar-sel env-sel env-${env}`}
          aria-label="Ambiente"
          value={env}
          onChange={(e) => setPref(ENV_COOKIE, e.target.value)}
        >
          <option value="testnet">TESTNET</option>
          <option value="mainnet">MAINNET</option>
        </select>
        <span className="spacer" />
      </div>
      {drawer && <div className="scrim show" onClick={() => setDrawer(false)} />}

      <aside className={`sidebar ${drawer ? "open" : ""}`}>
        <div className="logo">
          trade<span className="cursor">_</span>
        </div>
        <div className="navlabel">Estratégias</div>
        <nav className="nav">
          {nav("/trading-view", "Trading View", "TV")}
          {nav("/copy-trade", "Copy Trade", "CT")}
          <a href="#" className="ghost" onClick={(e) => e.preventDefault()}>
            + nova estratégia
          </a>
        </nav>
        <div className="navlabel">Sistema</div>
        <nav className="nav">
          {nav("/hyperliquid", "Hyperliquid", "HL")}
          {nav("/config", "Configurações", "⚙")}
          {nav("/logs", "Logs", "≡")}
        </nav>
        <div className="sidefoot">
          <div className="row">
            <span>uptime</span>
            <b>{fmtUptime(health?.uptime_s)}</b>
          </div>
          <div className="row">
            <span>circuit breaker</span>
            <b style={{ color: health?.circuit_breaker ? "var(--neg)" : "var(--pos)" }}>
              {health?.circuit_breaker ? "ABERTO" : "ok"}
            </b>
          </div>
          <div className="row">
            <span>kill switch</span>
            <b style={{ color: health?.kill_switch ? "var(--neg)" : "var(--pos)" }}>
              {health?.kill_switch ? "ACIONADO" : "armado"}
            </b>
          </div>
        </div>
        <div className="themetoggle" role="group" aria-label="Tema">
          <button className={light ? "" : "on"} onClick={() => setTheme("dark")}>
            ● ESCURO
          </button>
          <button className={light ? "on" : ""} onClick={() => setTheme("light")}>
            ○ CLARO
          </button>
        </div>
        <div className="userline">
          <span>{email}</span>
          <a href="#" onClick={logout}>
            sair
          </a>
        </div>
      </aside>

      <main className="main">{children}</main>
    </>
  );
}
