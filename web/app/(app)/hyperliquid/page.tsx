import { cookies } from "next/headers";
import { WalletProvider } from "@/components/wallet/WalletProvider";
import { EnvPanel } from "@/components/hyperliquid/EnvPanel";
import {
  agentsForEnv,
  getAgentsSnapshot,
  type Env,
} from "@/lib/hyperliquid/data";
import { readEnv } from "@/lib/prefs";

export const dynamic = "force-dynamic";

export default async function HyperliquidPage({
  searchParams,
}: {
  searchParams: Promise<{ provision?: string }>;
}) {
  const snapshot = await getAgentsSnapshot();
  // Ambientes isolados: mostra só o painel do ambiente ativo (controle global no
  // topo). Provisionamento habilitado; mainnet = fundos reais (o EnvPanel reforça
  // a confirmação). O gate humano de *status* de trader MAINNET segue intocado.
  //
  // UPDATE-0085: o combo do topo pode deep-linkar (`?provision=<env>`) para uma
  // wallet sem agente ATIVO em um ambiente que pode diferir do ambiente global —
  // nesse caso mostramos o painel do env pedido para o operador conectar+assinar.
  const activeEnv = readEnv(await cookies());
  const provisionParam = (await searchParams).provision;
  const targetEnv: Env =
    provisionParam === "testnet" || provisionParam === "mainnet"
      ? (provisionParam as Env)
      : (activeEnv as Env);
  const envs: { env: Env; provision: boolean }[] = [
    { env: targetEnv, provision: true },
  ];

  return (
    <section>
      <div className="pagehead">
        <div>
          <div className="eyebrow">Sistema</div>
          <h1>Hyperliquid · Agent Wallets</h1>
        </div>
        <span className={`chip ${snapshot.keyring_configured ? "live" : "dry"}`}>
          keyring {snapshot.keyring_configured ? "ATIVO" : "OFF"}
        </span>
      </div>

      <p className="pagelede">
        Provisione uma agent wallet aprovando <code>approveAgent</code> (EIP-712)
        na MetaMask. A chave do agent é cifrada (AES-256-GCM) no SQLite; o
        gateway segue como único signatário (ADR 0001). A carteira que aprova o
        agent vira o master de trading do ambiente.
      </p>

      <WalletProvider>
        <div className="settings-grid">
          {envs.map(({ env, provision }) => (
            <EnvPanel
              key={env}
              env={env}
              agents={agentsForEnv(snapshot, env)}
              adapterLive={snapshot.adapters.includes(env)}
              keyringConfigured={snapshot.keyring_configured}
              provisionEnabled={provision}
            />
          ))}
        </div>
      </WalletProvider>
    </section>
  );
}
