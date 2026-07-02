"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { supabaseConfigured } from "@/lib/supabase/config";

const configured = supabaseConfigured();

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("rtg003@gmail.com");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!configured) {
      setError(
        "Supabase não configurado — confira NEXT_PUBLIC_SUPABASE_URL no .env " +
          "(precisa começar com https:// e ser rebuildado).",
      );
      return;
    }
    setLoading(true);
    setError(null);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) {
      setError("Credenciais inválidas.");
      return;
    }
    router.push("/");
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
        <label htmlFor="lemail">E-mail</label>
        <input
          className="input"
          id="lemail"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
        />
        <label htmlFor="lpass">Senha</label>
        <input
          className="input"
          id="lpass"
          type="password"
          placeholder="••••••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <button className="btn btn-amber" type="submit" disabled={loading}>
          {loading ? "Entrando…" : "Entrar"}
        </button>
        {error && <div className="error">{error}</div>}
        <div className="hint">
          cadastro desabilitado — usuário provisionado diretamente no banco
        </div>
      </form>
    </div>
  );
}
