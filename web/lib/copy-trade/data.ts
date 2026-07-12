import { gatewayGet } from "@/lib/gateway";

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

export type Balance = {
  equity_usd: number;
  withdrawable_usd: number;
  available_usd?: number;
  spot_usdc?: number;
  unrealized_pnl?: number;
  margin_used?: number;
  network: string;
} | null;

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
  n_copy_fills?: number;
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

export type Position = Record<string, any> & {
  symbol: string;
  size: number;
  entry_price: number;
  unrealized_pnl: number;
  leverage?: number | null;
  liquidation_px?: number | null;
  position_value?: number | null;
  margin_used?: number | null;
  cum_funding?: number | null;
  network?: string;
};

export async function getBalance(
  env?: string | null,
  wallet?: string | null,
): Promise<Balance> {
  // Ambientes isolados: o cliente sempre envia testnet|mainnet (o controle
  // global nunca é "all"). Sem agregação — cada saldo é de um único ambiente.
  const q = new URLSearchParams();
  if (env && env !== "all") q.set("env", env);
  if (wallet && wallet !== "all") q.set("wallet", wallet);
  const qs = q.toString();
  const data = await gatewayGet<{
    ok?: boolean;
    equity_usd: number;
    withdrawable_usd?: number;
    available_usd?: number;
    spot_usdc?: number;
    unrealized_pnl?: number;
    margin_used?: number;
    network: string;
  }>(`/balance${qs ? `?${qs}` : ""}`);
  if (!data?.ok) return null;
  return {
    equity_usd: data.equity_usd,
    withdrawable_usd: data.withdrawable_usd ?? data.equity_usd,
    available_usd: data.available_usd,
    spot_usdc: data.spot_usdc,
    unrealized_pnl: data.unrealized_pnl,
    margin_used: data.margin_used,
    network: data.network,
  };
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
  return [{ value: "all", label: "Todas Wallets" }, ...options];
}

export function traderOptions(traders: Trader[] | null): TraderOption[] {
  const rows = (traders ?? []).filter((t) =>
    (t.n_copy_fills ?? 0) > 0 || ["TESTNET", "MAINNET"].includes(t.status),
  );
  return [
    { value: "all", label: "Todos Traders" },
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
  wallet?: string | null,
): Promise<FillsSummary | null> {
  if (strategyIds.length === 0) {
    return { n_trades: 0, net_pnl: 0, fees: 0, win_rate: null, profit_factor: null, max_drawdown: 0 };
  }
  const q = withWallet(
    withNetwork(
      new URLSearchParams({
        strategy_id: strategyIds.join(","),
        since,
        until,
      }),
      network,
    ),
    wallet,
  );
  return gatewayGet<FillsSummary>(`/api/fills/summary?${q.toString()}`);
}

export async function getPnlSummary(
  strategyIds: string[],
  since: string,
  until: string,
  network?: "testnet" | "mainnet" | null,
  wallet?: string | null,
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
  const q = withWallet(
    withNetwork(
      new URLSearchParams({
        strategy_id: strategyIds.join(","),
        since,
        until,
      }),
      network,
    ),
    wallet,
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

export type TraderExecConfig = {
  mode: "percent" | "fixed_usdc";
  value: number;
  max_leverage: number;
  blocked_assets: string[];
  thresholds?: Record<string, number>;
};

// Client-side: salva o sizing (endpoint /config já existente) e, se ok, ativa a
// cópia mudando o status. Duas chamadas sequenciais reusando os endpoints de
// controle — o proxy /api/control já libera ambos os paths.
export async function saveTraderConfigAndActivate(
  address: string,
  config: TraderExecConfig,
  nextStatus: string,
): Promise<{ ok: boolean; reason?: string }> {
  try {
    const cfgRes = await fetch(`/api/control/trader/${address}/config`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        mode: config.mode,
        value: config.value,
        max_leverage: config.max_leverage,
        blocked_assets: config.blocked_assets,
        ...(config.thresholds ? { thresholds: config.thresholds } : {}),
      }),
    });
    const cfgData = await cfgRes.json().catch(() => ({}));
    if (!cfgRes.ok || cfgData.ok === false) {
      return { ok: false, reason: cfgData.reason ?? cfgData.detail ?? "erro_config" };
    }
    const stRes = await fetch(
      `/api/control/trader/${address}/status?new_status=${encodeURIComponent(nextStatus)}`,
      { method: "POST" },
    );
    const stData = await stRes.json().catch(() => ({}));
    if (!stRes.ok || stData.ok === false) {
      return { ok: false, reason: stData.reason ?? stData.detail ?? "erro_status" };
    }
    return { ok: true };
  } catch {
    return { ok: false, reason: "gateway_indisponivel" };
  }
}

export type ClosePosition = {
  symbol: string;
  size: number;
  entry_price?: number | null;
  unrealized_pnl?: number | null;
  position_value?: number | null;
  network?: string;
};

export type CloseResult = { symbol: string; ok: boolean; reason?: string | null };

// Client-side: preview das posições abertas do trader no ambiente operante atual
// (execute=false — não envia ordem). Usado pela Seção A do modal.
export async function getTraderOpenPositions(
  address: string,
  env?: "testnet" | "mainnet" | null,
): Promise<{ ok: boolean; env: string | null; positions: ClosePosition[]; reason?: string }> {
  try {
    const res = await fetch(`/api/control/trader/${address}/close_positions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ env: env ?? undefined, execute: false }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return { ok: false, env: null, positions: [], reason: data.reason ?? "erro_preview" };
    }
    return { ok: true, env: data.env ?? null, positions: data.positions ?? [] };
  } catch {
    return { ok: false, env: null, positions: [], reason: "gateway_indisponivel" };
  }
}

// Client-side: fecha (reduce_only, best-effort) todas as posições abertas do
// trader no ambiente indicado (execute=true).
export async function closeAllPositions(
  address: string,
  env?: "testnet" | "mainnet" | null,
): Promise<{ ok: boolean; results: CloseResult[]; reason?: string }> {
  try {
    const res = await fetch(`/api/control/trader/${address}/close_positions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ env: env ?? undefined, execute: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return { ok: false, results: data.results ?? [], reason: data.reason ?? "erro_fechamento" };
    }
    return { ok: true, results: data.results ?? [] };
  } catch {
    return { ok: false, results: [], reason: "gateway_indisponivel" };
  }
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
