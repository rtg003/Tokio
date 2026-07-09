import { gatewayGet } from "@/lib/gateway";

// Camada de dados server-side da página HL. Lê o shape SEM segredos exposto
// pelo gateway em GET /hl/agents (privkey_enc jamais sai do gateway).

export type AgentStatus =
  | "pending"
  | "active"
  | "expiring"
  | "revoked"
  | "expired";

export type Agent = {
  id: string;
  env: string;
  master_address: string;
  agent_address: string;
  agent_name: string;
  status: AgentStatus;
  approved_at?: string | null;
  valid_until?: string | null;
  revoked_at?: string | null;
  created_at?: string | null;
};

export type AgentsSnapshot = {
  agents: Agent[];
  adapters: string[];
  keyring_configured: boolean;
};

export async function getAgentsSnapshot(): Promise<AgentsSnapshot> {
  const data = await gatewayGet<AgentsSnapshot>("/hl/agents");
  return (
    data ?? { agents: [], adapters: [], keyring_configured: false }
  );
}

export const ENVS = ["testnet", "mainnet"] as const;
export type Env = (typeof ENVS)[number];

export function agentsForEnv(snapshot: AgentsSnapshot, env: Env): Agent[] {
  return snapshot.agents.filter((a) => a.env === env);
}

export function activeAgent(agents: Agent[]): Agent | undefined {
  return agents.find((a) => a.status === "active" || a.status === "expiring");
}
