import { WalletProvider } from "@/components/wallet/WalletProvider";
import { EnvPanel } from "@/components/hyperliquid/EnvPanel";
import {
  agentsForEnv,
  getAgentsSnapshot,
  type Env,
} from "@/lib/hyperliquid/data";

export const dynamic = "force-dynamic";

export default async function HyperliquidPage() {
  const snapshot = await getAgentsSnapshot();
  const envs: { env: Env; provision: boolean }[] = [
    { env: "testnet", provision: true },
    { env: "mainnet", provision: false },
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
