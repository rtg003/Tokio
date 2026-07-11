"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AgentCard } from "./AgentCard";
import { ProvisionFlow } from "./ProvisionFlow";
import { activeAgent, type Agent, type Env } from "@/lib/hyperliquid/data";

// Painel de um ambiente HL (testnet | mainnet). Mostra o adapter vivo, o agent
// ativo (do keyring) e o histórico. Provisionamento habilitado nos dois
// ambientes (P3); mainnet ganha um aviso de fundos reais + confirmação no fluxo.
export function EnvPanel({
  env,
  agents,
  adapterLive,
  keyringConfigured,
  provisionEnabled,
}: {
  env: Env;
  agents: Agent[];
  adapterLive: boolean;
  keyringConfigured: boolean;
  provisionEnabled: boolean;
}) {
  const router = useRouter();
  const active = activeAgent(agents);
  const history = agents.filter((a) => a.id !== active?.id);
  const [busy, setBusy] = useState(false);

  async function revoke() {
    if (!confirm(`Revogar o agent ativo da ${env.toUpperCase()}? A engine perde o signer deste ambiente.`)) {
      return;
    }
    setBusy(true);
    try {
      await fetch(`/api/control/hl/agents/${env}/revoke`, { method: "POST" });
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card envpanel">
      <div className="cardhead">
        <div className="exhead">
          <span className="exlogo">HL</span>
          <h2 style={{ margin: 0 }}>{env === "testnet" ? "Testnet" : "Mainnet"}</h2>
        </div>
        <span className={`chip ${adapterLive ? "live" : "dry"}`}>
          adapter {adapterLive ? "ONLINE" : "OFFLINE"}
        </span>
      </div>

      {!keyringConfigured && (
        <div className="walletbar">
          <span className="t">
            <b>TOKIO_KEYRING_SECRET ausente</b> — keyring desligado. O gateway usa
            as chaves do <code>.env</code> (fallback). Configure o segredo para
            provisionar agents pela UI.
          </span>
        </div>
      )}

      <div className="agentgroup">
        {active ? (
          <AgentCard agent={active} onRevoke={revoke} busy={busy} />
        ) : (
          <div className="empty">nenhum agent ativo neste ambiente</div>
        )}
      </div>

      {provisionEnabled ? (
        keyringConfigured ? (
          <>
            {env === "mainnet" && (
              <div className="walletbar walletbar-warn">
                <span className="t">
                  <b>MAINNET · fundos reais.</b> Ativar um agent aqui troca a conta
                  de trading mainnet para a carteira conectada — a engine passa a
                  operar com dinheiro real nessa conta. Confirme a wallet antes de
                  assinar.
                </span>
              </div>
            )}
            <ProvisionFlow env={env} />
          </>
        ) : null
      ) : (
        <div className="walletbar">
          <span className="t">
            Provisionamento deste ambiente está desabilitado.
          </span>
        </div>
      )}

      {history.length > 0 && (
        <>
          <div className="navlabel" style={{ marginTop: 16 }}>Histórico</div>
          <div className="agentgroup">
            {history.map((a) => (
              <AgentCard key={a.id} agent={a} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
