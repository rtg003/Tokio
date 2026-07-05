import { gatewayGet } from "@/lib/gateway";

export type AccountOption = {
  value: string;
  label: string;
};

export type Strategy = {
  id: string;
  module: string;
  name?: string | null;
  status: string;
  config_snapshot?: string | null;
  created_at?: string | null;
};

export type Metrics = {
  net_pnl: number | null;
  win_rate: number | null;
  n_trades: number | null;
  fees: number | null;
  profit_factor: number | null;
  max_drawdown: number | null;
};

export type Balance = { equity_usd: number; network: string } | null;

export type Exchange = {
  id?: number;
  name: string;
  network: string;
  status: string;
};

export type Trader = Record<string, any> & {
  address: string;
  name?: string | null;
  status: string;
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
  latency_ms?: number | null;
  reject_reason?: string | null;
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
};

export async function getBalance(): Promise<Balance> {
  const data = await gatewayGet<{ ok?: boolean; equity_usd: number; network: string }>("/balance");
  if (!data?.ok) return null;
  return { equity_usd: data.equity_usd, network: data.network };
}

export async function getStrategies(): Promise<Strategy[] | null> {
  return gatewayGet<Strategy[]>("/api/strategies");
}

export async function getCopyStrategyIds(): Promise<string[] | null> {
  const strategies = await getStrategies();
  if (!strategies) return null;
  return strategies
    .filter((s) => s.module === "copy_trade" && s.status !== "archived")
    .map((s) => s.id);
}

export async function getExchanges(): Promise<Exchange[] | null> {
  return gatewayGet<Exchange[]>("/api/exchanges");
}

export function accountOptions(exchanges: Exchange[] | null): AccountOption[] {
  return exchanges?.length
    ? exchanges.map((e) => ({
        value: `${e.name === "hyperliquid" ? "hl" : e.name}:master:${e.network}`,
        label: `${e.name === "hyperliquid" ? "Hyperliquid" : e.name} · master (${e.network})`,
      }))
    : [{ value: "hl:master:testnet", label: "Hyperliquid · master (testnet)" }];
}

export async function getTraders(): Promise<Trader[] | null> {
  const rows = await gatewayGet<Trader[]>("/api/traders");
  return rows?.filter((t) => t.status !== "REJEITADO") ?? rows;
}

export async function getMetrics(
  strategyIds: string[],
  since: string,
  until: string,
): Promise<Metrics[] | null> {
  if (strategyIds.length === 0) return [];
  const q = new URLSearchParams({
    strategy_ids: strategyIds.join(","),
    since,
    until,
  });
  return gatewayGet<Metrics[]>(`/api/metrics?${q.toString()}`);
}

export async function getOrders(
  strategyIds: string[],
  since: string,
  until: string,
): Promise<Order[] | null> {
  if (strategyIds.length === 0) return [];
  const q = new URLSearchParams({
    strategy_id: strategyIds.join(","),
    since,
    until,
    limit: "15",
  });
  return gatewayGet<Order[]>(`/api/orders?${q.toString()}`);
}

export async function getFills(
  strategyIds: string[],
  since: string,
  until: string,
): Promise<Fill[] | null> {
  if (strategyIds.length === 0) return [];
  const q = new URLSearchParams({
    strategy_id: strategyIds.join(","),
    since,
    until,
    limit: "15",
  });
  return gatewayGet<Fill[]>(`/api/fills?${q.toString()}`);
}
