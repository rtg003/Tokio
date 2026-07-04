// web/lib/api.ts — cliente HTTP simples para o gateway do engine.
//
// Substitui o cliente Supabase no dashboard. O gateway expõe endpoints REST
// em http://localhost:8700/api/ (configurável via NEXT_PUBLIC_API_BASE).
//
// NOTA: este cliente é server-side (Server Components). Para uso client-side
// seria preciso um endpoint proxy do Next, mas o dashboard atual é SSR.

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8700/api";

/**
 * Wrapper de fetch com tratamento de erro e no-store (sempre fresco).
 * Retorna null em falha para que o dashboard degrade graciosamente.
 */
async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// ---- Tipos (espelham as tabelas do SQLite via gateway) ----

export type Strategy = {
  id: string;
  module: string;
  name: string | null;
  status: string;
  config_snapshot: Record<string, unknown> | null;
  created_at: string;
};

export type Trader = {
  address: string;
  name: string | null;
  score: number | null;
  cohort: string | null;
  twrr_30d: number | null;
  pnl_30d: number | null;
  windows_positive: string | null;
  profit_factor: number | null;
  win_rate: number | null;
  max_drawdown: number | null;
  status: string;
  copy_pinned: number | null;
  // colunas expandidas
  n_trades_30d: number | null;
  avg_holding_hours: number | null;
  avg_leverage: number | null;
  max_current_leverage: number | null;
  available_margin_pct: number | null;
  sim_net_pnl_usd: number | null;
  coverage_days: number | null;
  sim_half_old_net: number | null;
  sim_half_new_net: number | null;
  equity: number | null;
  top_assets: string[] | string | null;
  last_activity: string | null;
  mode: string | null;
  value: number | null;
  liq_distance: number | null;
  origin: string | null;
  logic_version: number | null;
};

export type Order = {
  cloid: string;
  strategy_id: string | null;
  symbol: string;
  side: string;
  type: string;
  size: number;
  price: number | null;
  status: string;
  created_at: string;
  latency_ms: number | null;
  reject_reason: string | null;
};

export type Fill = {
  cloid: string;
  strategy_id: string | null;
  symbol: string;
  side: string;
  price: number;
  size: number;
  fee: number;
  realized_pnl: number | null;
  ts: string;
};

export type MetricsDaily = {
  strategy_id: string;
  day: string;
  net_pnl: number;
  win_rate: number | null;
  n_trades: number;
  fees: number;
  profit_factor: number | null;
  max_drawdown: number | null;
};

export type Exchange = {
  name: string;
  network: string;
  status: string;
};

// Resposta genérica do gateway: pode vir como array direto ou envelopado
// em { data: [...] }. Normalizamos para array.
type MaybeEnvelope<T> = T[] | { data: T[] } | null;

function unwrap<T>(r: MaybeEnvelope<T> | null): T[] {
  if (!r) return [];
  if (Array.isArray(r)) return r;
  if (Array.isArray((r as { data: T[] }).data)) return (r as { data: T[] }).data;
  return [];
}

// ---- Funções públicas ----

export async function fetchStrategies(): Promise<Strategy[]> {
  return unwrap<Strategy>(await getJson<MaybeEnvelope<Strategy>>("/strategies"));
}

export async function fetchTraders(status?: string): Promise<Trader[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return unwrap<Trader>(await getJson<MaybeEnvelope<Trader>>(`/traders${qs}`));
}

export async function fetchTrader(address: string): Promise<Trader | null> {
  return getJson<Trader>(`/traders/${encodeURIComponent(address)}`);
}

export async function fetchFills(
  strategyId: string,
  limit?: number,
): Promise<Fill[]> {
  const params = new URLSearchParams({ strategy_id: strategyId });
  if (limit) params.set("limit", String(limit));
  return unwrap<Fill>(await getJson<MaybeEnvelope<Fill>>(`/fills?${params}`));
}

export async function fetchFillsForStrategies(
  strategyIds: string[],
  sinceTs: string,
  untilTs: string,
  limit = 15,
): Promise<Fill[]> {
  // O gateway pode não suportar múltiplos strategy_id de uma vez; fazemos
  // chamadas paralelas por strategy_id e mesclamos/ordenamos no cliente.
  if (strategyIds.length === 0) return [];
  const perStrategy = await Promise.all(
    strategyIds.map((id) => fetchFills(id, limit * 2)),
  );
  const all = perStrategy.flat();
  const filtered = all.filter(
    (f) => f.ts >= sinceTs && f.ts <= untilTs,
  );
  filtered.sort((a, b) => (a.ts < b.ts ? 1 : -1));
  return filtered.slice(0, limit);
}

export async function fetchOrdersForStrategies(
  strategyIds: string[],
  sinceTs: string,
  untilTs: string,
  limit = 15,
): Promise<Order[]> {
  // O gateway expõe /events?event_type=order que retorna ordens. Se não houver
  // endpoint dedicado de orders, fallback para /events filtrado.
  if (strategyIds.length === 0) return [];
  const params = new URLSearchParams({ event_type: "order" });
  if (limit) params.set("limit", String(limit * 2));
  const events = unwrap<Order>(
    await getJson<MaybeEnvelope<Order>>(`/events?${params}`),
  );
  const filtered = events.filter(
    (o) =>
      o.strategy_id !== null &&
      strategyIds.includes(o.strategy_id) &&
      o.created_at >= sinceTs &&
      o.created_at <= untilTs,
  );
  filtered.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  return filtered.slice(0, limit);
}

export async function fetchMetricsForStrategies(
  strategyIds: string[],
  sinceDay: string,
  untilDay: string,
): Promise<MetricsDaily[]> {
  // O gateway não tem endpoint dedicado de metrics; usamos /stats com filtro
  // de strategy_id. Se /stats não suportar filtros, retornamos vazio (degrada
  // graciosamente — os KPIs ficam zerados, não quebram o dashboard).
  if (strategyIds.length === 0) return [];
  const out: MetricsDaily[] = [];
  for (const id of strategyIds) {
    const stats = await getJson<MetricsDaily[] | MetricsDaily>(
      `/stats?strategy_id=${encodeURIComponent(id)}&from=${sinceDay}&to=${untilDay}`,
    );
    if (!stats) continue;
    if (Array.isArray(stats)) out.push(...stats);
    else out.push(stats);
  }
  return out;
}

export async function fetchExchanges(): Promise<Exchange[]> {
  return unwrap<Exchange>(
    await getJson<MaybeEnvelope<Exchange>>("/exchanges"),
  );
}

export type GatewayStats = {
  total_traders?: number;
  active_traders?: number;
  total_strategies?: number;
  total_fills?: number;
  total_orders?: number;
  net_pnl?: number;
  win_rate?: number | null;
  n_trades?: number;
  fees?: number;
  profit_factor?: number | null;
  max_drawdown?: number | null;
  [k: string]: unknown;
};

export async function fetchStats(): Promise<GatewayStats | null> {
  return getJson<GatewayStats>("/stats");
}
