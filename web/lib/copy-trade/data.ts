import { gatewayGet } from "@/lib/gateway";

export type AccountOption = {
  value: string;
  label: string;
};

export type TraderOption = {
  value: string;
  label: string;
};

export type WalletOption = {
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
  strategy_id: string;
  environment?: "testnet" | "mainnet" | null;
  copy_pinned?: number | null;
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

export type FillsSummary = {
  n_trades: number;
  net_pnl: number;
  fees: number;
  win_rate: number | null;
};

export type PnlSummary = {
  n_trades: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  fees: number;
  win_rate: number | null;
};

export type Position = Record<string, any> & {
  symbol: string;
  size: number;
  entry_price: number;
  unrealized_pnl: number;
  leverage?: number | null;
  liquidation_px?: number | null;
  position_value?: number | null;
  network?: string;
};

export async function getBalance(
  env?: string | null,
  wallet?: string | null,
): Promise<Balance> {
  const q = new URLSearchParams();
  if (env && env !== "all") q.set("env", env);
  if (wallet && wallet !== "all") q.set("wallet", wallet);
  const qs = q.toString();
  const data = await gatewayGet<{ ok?: boolean; equity_usd: number; network: string }>(
    `/balance${qs ? `?${qs}` : ""}`,
  );
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
  const live = (exchanges ?? []).filter((e) => e.network === "testnet" || e.network === "mainnet");
  const options = live.length
    ? live.map((e) => ({
        value: `${e.name === "hyperliquid" ? "hl" : e.name}:master:${e.network}`,
        label: `${e.name === "hyperliquid" ? "Hyperliquid" : e.name} - ${e.network === "mainnet" ? "Mainnet" : "Testnet"}`,
      }))
    : [
        { value: "hl:master:testnet", label: "Hyperliquid - Testnet" },
        { value: "hl:master:mainnet", label: "Hyperliquid - Mainnet" },
      ];
  return [{ value: "all", label: "Todos" }, ...options];
}

export async function getTraders(): Promise<Trader[] | null> {
  const rows = await gatewayGet<Trader[]>("/api/traders");
  return rows?.filter((t) => t.status !== "REJEITADO") ?? rows;
}

// Wallets = masters de trading dos agents provisionados (hl-auth v2.0). O
// filtro por Wallet cruza com orders/fills.master_address (migration 0015).
export async function getWallets(): Promise<WalletOption[]> {
  const snap = await gatewayGet<{ agents?: { master_address?: string }[] }>(
    "/hl/agents",
  );
  const seen = new Set<string>();
  const options: WalletOption[] = [];
  for (const a of snap?.agents ?? []) {
    const addr = a.master_address;
    if (!addr || seen.has(addr)) continue;
    seen.add(addr);
    options.push({ value: addr, label: `${addr.slice(0, 6)}…${addr.slice(-4)}` });
  }
  return [{ value: "all", label: "Todas as wallets" }, ...options];
}

export function environmentFromAccount(account: string | undefined): "testnet" | "mainnet" | "all" {
  if (!account || account === "all") return "all";
  if (account.endsWith(":mainnet")) return "mainnet";
  if (account.endsWith(":testnet")) return "testnet";
  return "all";
}

export function traderOptions(traders: Trader[] | null): TraderOption[] {
  const rows = (traders ?? []).filter((t) =>
    t.copy_pinned === 1 || ["SALVO", "TESTNET", "MAINNET"].includes(t.status),
  );
  return [
    { value: "all", label: "Todos" },
    ...rows.map((t) => ({
      value: t.address,
      label: String(t.name ?? t.address).slice(0, 12),
    })),
  ];
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

function withNetwork(
  q: URLSearchParams,
  network?: "testnet" | "mainnet" | null,
): URLSearchParams {
  if (network) q.set("network", network);
  return q;
}

function withWallet(q: URLSearchParams, wallet?: string | null): URLSearchParams {
  if (wallet && wallet !== "all") q.set("wallet", wallet);
  return q;
}

export async function getFillsSummary(
  strategyIds: string[],
  since: string,
  until: string,
  network?: "testnet" | "mainnet" | null,
): Promise<FillsSummary | null> {
  if (strategyIds.length === 0) {
    return { n_trades: 0, net_pnl: 0, fees: 0, win_rate: null };
  }
  const q = withNetwork(
    new URLSearchParams({
      strategy_id: strategyIds.join(","),
      since,
      until,
    }),
    network,
  );
  return gatewayGet<FillsSummary>(`/api/fills/summary?${q.toString()}`);
}

export async function getPnlSummary(
  strategyIds: string[],
  since: string,
  until: string,
  network?: "testnet" | "mainnet" | null,
): Promise<PnlSummary | null> {
  if (strategyIds.length === 0) {
    return {
      n_trades: 0,
      realized_pnl: 0,
      unrealized_pnl: 0,
      total_pnl: 0,
      fees: 0,
      win_rate: null,
    };
  }
  const q = withNetwork(
    new URLSearchParams({
      strategy_id: strategyIds.join(","),
      since,
      until,
    }),
    network,
  );
  return gatewayGet<PnlSummary>(`/api/pnl/summary?${q.toString()}`);
}

export async function getOrders(
  strategyIds: string[],
  since: string,
  until: string,
  network?: "testnet" | "mainnet" | null,
  wallet?: string | null,
): Promise<Order[] | null> {
  if (strategyIds.length === 0) return [];
  const q = withWallet(
    withNetwork(
      new URLSearchParams({
        strategy_id: strategyIds.join(","),
        since,
        until,
        limit: "15",
      }),
      network,
    ),
    wallet,
  );
  return gatewayGet<Order[]>(`/api/orders?${q.toString()}`);
}

export async function getFills(
  strategyIds: string[],
  since: string,
  until: string,
  network?: "testnet" | "mainnet" | null,
  wallet?: string | null,
): Promise<Fill[] | null> {
  if (strategyIds.length === 0) return [];
  const q = withWallet(
    withNetwork(
      new URLSearchParams({
        strategy_id: strategyIds.join(","),
        since,
        until,
        limit: "15",
      }),
      network,
    ),
    wallet,
  );
  return gatewayGet<Fill[]>(`/api/fills?${q.toString()}`);
}

export async function getPositions(
  strategyIds: string[],
  network?: "testnet" | "mainnet" | null,
  wallet?: string | null,
): Promise<Position[] | null> {
  // Posições da venue são escopadas no gateway aos símbolos do módulo (§5.1).
  if (strategyIds.length === 0) return [];
  const q = withWallet(
    withNetwork(
      new URLSearchParams({ strategy_id: strategyIds.join(",") }),
      network,
    ),
    wallet,
  );
  return gatewayGet<Position[]>(`/api/positions?${q.toString()}`);
}
