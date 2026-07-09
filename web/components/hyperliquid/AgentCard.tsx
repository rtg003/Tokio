"use client";

import type { Agent } from "@/lib/hyperliquid/data";

function short(addr?: string | null): string {
  if (!addr) return "—";
  return addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
}

function fmtDate(s?: string | null): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  } catch {
    return s;
  }
}

const STATUS_CHIP: Record<string, string> = {
  active: "live",
  expiring: "ack",
  pending: "dry",
  revoked: "dry",
  expired: "dry",
};

export function AgentCard({
  agent,
  onRevoke,
  busy,
}: {
  agent: Agent;
  onRevoke?: () => void;
  busy?: boolean;
}) {
  const isActive = agent.status === "active" || agent.status === "expiring";
  return (
    <div className="agentcard">
      <div className="agentcard-head">
        <span className={`chip ${STATUS_CHIP[agent.status] ?? "dry"}`}>
          {agent.status.toUpperCase()}
        </span>
        <span className="agentcard-name">{agent.agent_name}</span>
        {isActive && onRevoke && (
          <button
            className="btn btn-danger btn-sm"
            type="button"
            onClick={onRevoke}
            disabled={busy}
          >
            {busy ? "…" : "revogar"}
          </button>
        )}
      </div>
      <dl className="agentcard-body">
        <div>
          <dt>master (trading)</dt>
          <dd className="addr" title={agent.master_address}>{short(agent.master_address)}</dd>
        </div>
        <div>
          <dt>agent (signer)</dt>
          <dd className="addr" title={agent.agent_address}>{short(agent.agent_address)}</dd>
        </div>
        <div>
          <dt>aprovado</dt>
          <dd>{fmtDate(agent.approved_at)}</dd>
        </div>
        <div>
          <dt>válido até</dt>
          <dd>{fmtDate(agent.valid_until)}</dd>
        </div>
      </dl>
    </div>
  );
}
