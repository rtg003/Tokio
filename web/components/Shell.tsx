"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

type Health = {
  ok: boolean;
  exchange?: string;
  network?: string;
  kill_switch?: boolean;
  circuit_breaker?: boolean;
  uptime_s?: number;
};

function Clock() {
  const [now, setNow] = useState("");
  useEffect(() => {
    const tick = () =>
      setNow(
        new Date().toLocaleTimeString("pt-BR", { timeZone: "America/Sao_Paulo" }),
      );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  return <strong>{now}</strong>;
}

function fmtUptime(s?: number): string {
  if (!s) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  return d > 0 ? `${d}d ${h}h` : `${h}h ${Math.floor((s % 3600) / 60)}m`;
}

export default function Shell({
  email,
  children,
}: {
  email: string;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [drawer, setDrawer] = useState(false);
  const [light, setLight] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);

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
  const network = (health?.network ?? "testnet").toUpperCase();

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
        <span className="seg badge-env">{network}</span>
        <span className="seg hide-m">
          GATEWAY <strong>{health?.exchange ?? "—"}</strong>
        </span>
        <span className="seg hide-m">
          RISCO <strong>{health?.circuit_breaker ? "PAUSADO" : "OK"}</strong>
        </span>
        <span className="spacer" />
        <span className="seg">
          SP <Clock />
        </span>
        <span className="seg hide-m">
          {new Date().toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" })}
        </span>
      </div>
      {drawer && <div className="scrim show" onClick={() => setDrawer(false)} />}

      <aside className={`sidebar ${drawer ? "open" : ""}`}>
        <div className="logo">
          trade<span className="cursor">_</span>
        </div>
        <div className="navlabel">Estratégias</div>
        <nav className="nav">
          {nav("/copy-trade", "Copy Trade", "CT")}
          <a href="#" className="ghost" onClick={(e) => e.preventDefault()}>
            + nova estratégia
          </a>
        </nav>
        <div className="navlabel">Sistema</div>
        <nav className="nav">
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
