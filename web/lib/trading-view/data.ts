// Camada de dados PRÓPRIA do módulo Trading View (§5.3). Reusa o helper de
// gateway e os endpoints read-only já existentes, SEMPRE filtrando por
// estratégias do módulo TV + ambiente global (isolamento §5.1). Nenhuma query
// aqui enxerga Copy Trade ou qualquer outro módulo.
import { gatewayGet } from "@/lib/gateway";

export type TvStrategy = {
  strategy_id: string;
  name?: string | null;
  status: string;
  config_snapshot?: string | null;
  thresholds?: string | null;
  created_at?: string | null;
  archived_at?: string | null;
  environment: "testnet" | "mainnet";
  version: number;
  meta_updated_at?: string | null;
};

// Linha unificada da view tv_events (SIGNAL | INCIDENT | HERMES | USER | SYSTEM).
export type TvEvent = {
  ts: string;
  kind: string;
  severity: string;
  summary: string;
  ref_id?: string | null;
  detail?: string | null;
};

export type Metrics = {
  net_pnl: number | null;
  win_rate: number | null;
  n_trades: number | null;
  fees: number | null;
  profit_factor: number | null;
  max_drawdown: number | null;
};

export type FillsSummary = {
  n_trades: number;
  net_pnl: number;
  fees: number;
  win_rate: number | null;
  profit_factor?: number | null;
  max_drawdown?: number | null;
};

export type PnlSummary = {
  n_trades: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  fees: number;
  win_rate: number | null;
};

export type Order = Record<string, any> & {
  cloid: string;
  strategy_id: string;
  symbol: string;
  side: string;
  type: string;
  size: number;
  price?: number | null;
  status: string;
  created_at: string;
  network?: "testnet" | "mainnet" | null;
};

export type Fill = Record<string, any> & {
  cloid: string;
  strategy_id: string | null;
  symbol: string;
  side: string;
  price: number;
  size: number;
  fee: number;
  realized_pnl?: number | null;
  ts: string;
  network?: "testnet" | "mainnet" | null;
};

export type Position = Record<string, any> & {
  symbol: string;
  size: number;
  entry_price: number;
  unrealized_pnl: number;
  leverage?: number | null;
  liquidation_px?: number | null;
  position_value?: number | null;
  margin_used?: number | null;
  network?: string;
};

export type Balance = {
  equity_usd: number;
  withdrawable_usd: number;
  unrealized_pnl?: number;
  margin_used?: number;
  network: string;
} | null;

export type TvEnv = "testnet" | "mainnet";

export async function getBalance(
  env?: TvEnv | null,
  wallet?: string | null,
): Promise<Balance> {
  const q = new URLSearchParams();
  if (env) q.set("env", env);
  if (wallet && wallet !== "all") q.set("wallet", wallet);
  const qs = q.toString();
  const data = await gatewayGet<{
    ok?: boolean;
    equity_usd: number;
    withdrawable_usd?: number;
    unrealized_pnl?: number;
    margin_used?: number;
    network: string;
  }>(`/balance${qs ? `?${qs}` : ""}`);
  if (!data?.ok) return null;
  return {
    equity_usd: data.equity_usd,
    withdrawable_usd: data.withdrawable_usd ?? data.equity_usd,
    unrealized_pnl: data.unrealized_pnl,
    margin_used: data.margin_used,
    network: data.network,
  };
}

// -- estratégias e logs do módulo (endpoints dedicados, views 0019) ------------
export async function getTvStrategies(env?: TvEnv | null): Promise<TvStrategy[]> {
  const q = env ? `?environment=${env}` : "";
  return (await gatewayGet<TvStrategy[]>(`/api/tv/strategies${q}`)) ?? [];
}

export async function getTvStrategyIds(env?: TvEnv | null): Promise<string[]> {
  const rows = await getTvStrategies(env);
  return rows.filter((s) => s.status !== "archived").map((s) => s.strategy_id);
}

export async function getTvEvents(
  opts: { kind?: string; limit?: number; before?: string } = {},
): Promise<TvEvent[]> {
  const q = new URLSearchParams();
  if (opts.kind) q.set("kind", opts.kind);
  if (opts.before) q.set("before", opts.before);
  q.set("limit", String(opts.limit ?? 50));
  return (await gatewayGet<TvEvent[]>(`/api/tv/events?${q.toString()}`)) ?? [];
}

// -- endpoints compartilhados, escopados aos ids do módulo TV ------------------
function withScope(
  base: Record<string, string>,
  env?: TvEnv | null,
): URLSearchParams {
  const q = new URLSearchParams(base);
  if (env) q.set("network", env);
  return q;
}

export async function getMetrics(
  strategyIds: string[],
  since: string,
  until: string,
): Promise<Metrics[]> {
  if (strategyIds.length === 0) return [];
  const q = new URLSearchParams({ strategy_ids: strategyIds.join(","), since, until });
  return (await gatewayGet<Metrics[]>(`/api/metrics?${q.toString()}`)) ?? [];
}

export async function getFillsSummary(
  strategyIds: string[],
  since: string,
  until: string,
  env?: TvEnv | null,
): Promise<FillsSummary> {
  const zero = { n_trades: 0, net_pnl: 0, fees: 0, win_rate: null, profit_factor: null, max_drawdown: 0 };
  if (strategyIds.length === 0) return zero;
  const q = withScope({ strategy_id: strategyIds.join(","), since, until }, env);
  return (await gatewayGet<FillsSummary>(`/api/fills/summary?${q.toString()}`)) ?? zero;
}

export async function getPnlSummary(
  strategyIds: string[],
  since: string,
  until: string,
  env?: TvEnv | null,
): Promise<PnlSummary> {
  const zero = { n_trades: 0, realized_pnl: 0, unrealized_pnl: 0, total_pnl: 0, fees: 0, win_rate: null };
  if (strategyIds.length === 0) return zero;
  const q = withScope({ strategy_id: strategyIds.join(","), since, until }, env);
  return (await gatewayGet<PnlSummary>(`/api/pnl/summary?${q.toString()}`)) ?? zero;
}

export async function getOrders(
  strategyIds: string[],
  since: string,
  until: string,
  env?: TvEnv | null,
): Promise<Order[]> {
  if (strategyIds.length === 0) return [];
  const q = withScope({ strategy_id: strategyIds.join(","), since, until, limit: "15" }, env);
  return (await gatewayGet<Order[]>(`/api/orders?${q.toString()}`)) ?? [];
}

export async function getFills(
  strategyIds: string[],
  since: string,
  until: string,
  env?: TvEnv | null,
): Promise<Fill[]> {
  if (strategyIds.length === 0) return [];
  const q = withScope({ strategy_id: strategyIds.join(","), since, until, limit: "15" }, env);
  return (await gatewayGet<Fill[]>(`/api/fills?${q.toString()}`)) ?? [];
}

export async function getPositions(
  strategyIds: string[],
  env?: TvEnv | null,
): Promise<Position[]> {
  if (strategyIds.length === 0) return [];
  const q = withScope({ strategy_id: strategyIds.join(",") }, env);
  return (await gatewayGet<Position[]>(`/api/positions?${q.toString()}`)) ?? [];
}
