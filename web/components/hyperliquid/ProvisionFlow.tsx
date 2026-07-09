"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  useAccount,
  useConnect,
  useDisconnect,
  useSignTypedData,
  useSwitchChain,
} from "wagmi";
import { injected } from "wagmi/connectors";
import { arbitrumSepolia } from "wagmi/chains";
import type { Env } from "@/lib/hyperliquid/data";

// Fluxo de provisionamento de agent wallet HL (EIP-712 approveAgent):
//  1. conecta MetaMask (a wallet conectada = master de trading do ambiente);
//  2. POST /prepare → gateway gera o par do agent (cifrado como `pending`) e
//     devolve o typed data + nonce (nenhuma chave volta ao browser);
//  3. MetaMask assina o typed data (signTypedData / eth_signTypedData_v4);
//  4. POST /activate {agent_address, signature, nonce} → gateway submete o
//     approveAgent ao HL e, em sucesso, ativa + hot-reload do adapter do env.
// A engine do ambiente passa a operar NA CONTA da wallet conectada.

type PrepareResponse = {
  ok: boolean;
  agent_address: string;
  agent_name: string;
  nonce: number;
  signature_chain_id: string;
  typed_data: {
    domain: Record<string, unknown>;
    types: Record<string, { name: string; type: string }[]>;
    primaryType: string;
    message: Record<string, unknown>;
  };
};

export function ProvisionFlow({ env }: { env: Env }) {
  const router = useRouter();
  const { address, isConnected, chainId } = useAccount();
  const { connectAsync } = useConnect();
  const { disconnect } = useDisconnect();
  const { signTypedDataAsync } = useSignTypedData();
  const { switchChainAsync } = useSwitchChain();
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function provision() {
    setBusy(true);
    setError(null);
    setStep(null);
    try {
      let master = address;
      if (!isConnected || !master) {
        const res = await connectAsync({ connector: injected() });
        master = res.accounts[0];
      }
      if (!master) {
        setError("Nenhuma conta na carteira.");
        return;
      }

      setStep("Preparando agent no gateway…");
      const prepRes = await fetch("/api/control/hl/agents/prepare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ env, master_address: master }),
      });
      if (!prepRes.ok) {
        const b = await prepRes.json().catch(() => ({}));
        setError(
          (b as { detail?: string }).detail ??
            (prepRes.status === 403 ? "Rota não permitida." : "Falha ao preparar agent."),
        );
        return;
      }
      const prep = (await prepRes.json()) as PrepareResponse;

      // viem deriva o EIP712Domain a partir de `domain`; remover a entrada
      // evita o erro "Cannot redefine EIP712Domain".
      const { EIP712Domain: _ignored, ...types } = prep.typed_data.types;

      // O domain do approveAgent usa chainId 0x66eee (Arbitrum Sepolia) p/ os
      // dois ambientes (V2). O wagmi só sabe assinar numa cadeia que está na
      // config — se a MetaMask estiver noutra rede (ex.: Ethereum Mainnet), o
      // `signTypedData` lança "Chain not configured". Garantimos a rede antes.
      if (chainId !== arbitrumSepolia.id) {
        setStep("Troque para Arbitrum Sepolia na MetaMask…");
        await switchChainAsync({ chainId: arbitrumSepolia.id });
      }

      setStep("Assine o approveAgent na MetaMask…");
      const signature = await signTypedDataAsync({
        account: master as `0x${string}`,
        domain: prep.typed_data.domain,
        types,
        primaryType: prep.typed_data.primaryType,
        message: prep.typed_data.message,
      } as unknown as Parameters<typeof signTypedDataAsync>[0]);

      setStep("Ativando (submetendo ao Hyperliquid)…");
      const actRes = await fetch("/api/control/hl/agents/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          env,
          agent_address: prep.agent_address,
          signature,
          nonce: prep.nonce,
        }),
      });
      const act = await actRes.json().catch(() => ({}));
      if (!actRes.ok || !(act as { ok?: boolean }).ok) {
        setError(
          (act as { error?: string; detail?: string }).error ??
            (act as { detail?: string }).detail ??
            "Ativação recusada pelo Hyperliquid.",
        );
        return;
      }
      setStep("Agent ativo · adapter recarregado.");
      router.refresh();
    } catch (err) {
      // Superfície o erro real: o catch anterior colapsava tudo num texto
      // genérico, escondendo a causa (chain mismatch, viem, provider…). Mantém
      // a detecção de cancelamento amigável; o resto mostra a mensagem crua.
      const msg = err instanceof Error ? err.message : String(err);
      // eslint-disable-next-line no-console
      console.error("[provision] falhou:", err);
      setError(
        /reject|denied|user rejected/i.test(msg)
          ? "Assinatura cancelada na carteira."
          : `Erro: ${msg.slice(0, 300)}`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="provision">
      <div className="provision-actions">
        <button className="btn btn-amber" type="button" onClick={provision} disabled={busy}>
          {busy ? "Processando…" : isConnected ? "Provisionar agent" : "Conectar carteira e provisionar"}
        </button>
        {isConnected && address && (
          <button
            className="siwe-disconnect"
            type="button"
            onClick={() => disconnect()}
            title="Desconectar carteira"
          >
            {address.slice(0, 6)}…{address.slice(-4)} · trocar
          </button>
        )}
      </div>
      {step && <div className="provision-step">{step}</div>}
      {error && <div className="error">{error}</div>}
      <div className="hint">
        A carteira conectada vira o <b>master de trading</b> deste ambiente — a
        engine passa a operar nessa conta ao ativar.
      </div>
    </div>
  );
}
