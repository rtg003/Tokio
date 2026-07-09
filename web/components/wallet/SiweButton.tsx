"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAccount, useConnect, useDisconnect, useSignMessage } from "wagmi";
import { injected } from "wagmi/connectors";
import { createSiweMessage } from "viem/siwe";
import { arbitrumSepolia } from "wagmi/chains";

// Botão "Conectar carteira" → fluxo SIWE completo no cliente:
//  1. conecta MetaMask (injected);
//  2. pega nonce de uso único do servidor;
//  3. monta a mensagem EIP-4361 e pede à MetaMask p/ assinar (personal_sign);
//  4. envia {message, signature} p/ /api/auth/siwe/verify;
//  5. em sucesso, o cookie tokio_session já veio no response → redireciona.
export function SiweButton() {
  const router = useRouter();
  const { address, isConnected, chainId } = useAccount();
  const { connectAsync } = useConnect();
  const { disconnect } = useDisconnect();
  const { signMessageAsync } = useSignMessage();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSiwe() {
    setLoading(true);
    setError(null);
    try {
      let addr = address;
      let cid = chainId;
      if (!isConnected || !addr) {
        const res = await connectAsync({ connector: injected() });
        addr = res.accounts[0];
        cid = res.chainId;
      }
      if (!addr) {
        setError("Nenhuma conta na carteira.");
        return;
      }

      const nonceRes = await fetch("/api/auth/siwe/nonce", { cache: "no-store" });
      if (!nonceRes.ok) {
        setError(nonceRes.status === 503 ? "SIWE não configurado no servidor." : "Falha ao obter nonce.");
        return;
      }
      const { nonce } = (await nonceRes.json()) as { nonce: string };

      const message = createSiweMessage({
        address: addr as `0x${string}`,
        chainId: cid ?? arbitrumSepolia.id,
        domain: window.location.host,
        nonce,
        uri: window.location.origin,
        version: "1",
        statement: "Login na dashboard Tokio.",
      });

      const signature = await signMessageAsync({ account: addr as `0x${string}`, message });

      const verifyRes = await fetch("/api/auth/siwe/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, signature }),
      });
      if (!verifyRes.ok) {
        const body = await verifyRes.json().catch(() => ({}));
        const reason = (body as { reason?: string }).reason;
        if (reason === "address_not_allowed") setError("Endereço não autorizado.");
        else if (reason === "nonce_invalid") setError("Nonce expirado — tente de novo.");
        else if (reason === "domain_mismatch") setError("Domínio inválido.");
        else setError("Assinatura recusada.");
        return;
      }
      router.push("/copy-trade");
      router.refresh();
    } catch (err) {
      // Usuário cancelou na MetaMask ou carteira ausente.
      setError(err instanceof Error && /reject/i.test(err.message) ? "Assinatura cancelada." : "Carteira indisponível.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="siwe">
      <button className="btn btn-ghost" type="button" onClick={handleSiwe} disabled={loading}>
        {loading ? "Conectando…" : "Conectar carteira (MetaMask)"}
      </button>
      {isConnected && address && (
        <button
          className="siwe-disconnect"
          type="button"
          onClick={() => disconnect()}
          title="Desconectar carteira"
        >
          {address.slice(0, 6)}…{address.slice(-4)} · sair
        </button>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
