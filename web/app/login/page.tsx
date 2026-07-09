"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { WalletProvider } from "@/components/wallet/WalletProvider";
import { SiweButton } from "@/components/wallet/SiweButton";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    setLoading(false);
    if (!res.ok) {
      setError(res.status === 503 ? "Auth não configurado no servidor." : "Senha inválida.");
      return;
    }
    router.push("/copy-trade");
    router.refresh();
  }

  return (
    <div className="loginwrap">
      <div className="loginstatus">
        <span className="seg">
          <span className="dot on" /> ENGINE ONLINE
        </span>
        <span className="badge-env">TESTNET</span>
      </div>
      <form className="logincard" onSubmit={handleLogin}>
        <div className="logo">
          trade<span className="cursor">_</span>
        </div>
        <div className="tag">Acesso restrito · operação</div>
        <label htmlFor="lpass">Senha</label>
        <input
          className="input"
          id="lpass"
          type="password"
          placeholder="••••••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          autoFocus
        />
        <button className="btn btn-amber" type="submit" disabled={loading}>
          {loading ? "Entrando…" : "Entrar"}
        </button>
        {error && <div className="error">{error}</div>}
        <div className="or-sep">ou</div>
        <WalletProvider>
          <SiweButton />
        </WalletProvider>
        <div className="hint">
          cadastro desabilitado — senha operacional definida no .env da VPS;
          login por carteira restrito à allowlist
        </div>
      </form>
    </div>
  );
}
