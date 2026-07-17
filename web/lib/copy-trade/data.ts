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
  // Status da STRATEGY (active/auto_paused/…), separado do status operacional
  // do trader (SALVO/TESTNET/MAINNET). Alimenta o badge "AUTO-PAUSADA".
  strategy_status?: string | null;
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
  leverage?: number | null;
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
  leverage?: number | null;
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
  strategy_id?: string | null;
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

// Rótulos amigáveis de wallet geridos no app (SQLite, migration 0023). A
// MetaMask NÃO expõe o nome da conta a sites — guardamos um rótulo por endereço.
// Mapa {address(lower): label}. Server-side; tolera falha (combo não cai).
export async function getWalletLabels(): Promise<Record<string, string>> {
  const data = await gatewayGet<Record<string, string>>("/api/wallet-labels");
  return data ?? {};
}

// Wallets = masters de trading dos agents provisionados (hl-auth v2.0). O
// filtro por Wallet cruza com orders/fills.master_address (migration 0015).
// Quando há rótulo (migration 0023) o label vira "Hyperliquid 1 — 0x4124…".
export async function getWallets(): Promise<WalletOption[]> {
  const [snap, labels] = await Promise.all([
    gatewayGet<{ agents?: { master_address?: string }[] }>("/hl/agents"),
    getWalletLabels(),
  ]);
  const seen = new Set<string>();
  const options: WalletOption[] = [];
  for (const a of snap?.agents ?? []) {
    const addr = a.master_address;
    if (!addr || seen.has(addr)) continue;
    seen.add(addr);
    const short = `${addr.slice(0, 6)}…${addr.slice(-4)}`;
    const name = labels[addr.toLowerCase()];
    options.push({ value: addr, label: name ? `${name} — ${short}` : short });
  }
  return [{ value: "all", label: "Todas Wallets" }, ...options];
}

// Client-side: define (ou remove, se vazio) o rótulo de uma wallet. Ato humano
// autenticado — o proxy /api/control injeta o token de controle.
export async function setWalletLabel(
  address: string,
  label: string,
): Promise<{ ok: boolean; reason?: string }> {
  try {
    const res = await fetch(`/api/control/wallet/${address}/label`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ label }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return { ok: false, reason: data.reason ?? data.detail ?? "erro_label" };
    }
    return { ok: true };
  } catch {
    return { ok: false, reason: "gateway_indisponivel" };
  }
}

// Client-side: reseta o circuit breaker (ato humano autenticado). Sem escopo =
// todos os breakers abertos; com {wallet, environment} = seletivo. O backend
// reativa SÓ as estratégias pausadas PELO breaker (payload.by='circuit_breaker'),
// marca acknowledged_day (não reabre no mesmo dia UTC) e emite circuit_breaker.reset.
export async function resetCircuitBreaker(
  scope?: { wallet?: string; environment?: string },
): Promise<{ ok: boolean; reason?: string }> {
  try {
    const res = await fetch(`/api/control/circuit-breaker/reset`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(scope ?? {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return { ok: false, reason: data.reason ?? data.detail ?? "erro_reset" };
    }
    return { ok: true };
  } catch {
    return { ok: false, reason: "gateway_indisponivel" };
  }
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

// Client-side: fecha UMA posição (símbolo) via reduce_only market na venue.
// Ato humano autenticado (a UI confirma antes de chamar). `strategy_id` vem
// atribuído na linha da posição; a venue neta por conta.
export async function closeSinglePosition(args: {
  strategy_id: string;
  symbol: string;
  env: "testnet" | "mainnet";
}): Promise<{ ok: boolean; reason?: string | null }> {
  try {
    const res = await fetch(`/api/control/position/close`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(args),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return { ok: false, reason: data.reason ?? data.detail ?? "erro_fechamento" };
    }
    return { ok: true, reason: data.reason ?? null };
  } catch {
    return { ok: false, reason: "gateway_indisponivel" };
  }
}

// Client-side: cancela UMA ordem em aberto via ícone da tabela. Ato humano
// autenticado (a UI confirma antes de chamar). `cloid` identifica a ordem;
// `env` resolve o adapter correto no gateway.
export async function cancelOrder(args: {
  strategy_id: string;
  symbol: string;
  cloid: string;
  env: "testnet" | "mainnet";
}): Promise<{ ok: boolean; reason?: string | null }> {
  try {
    const res = await fetch(`/api/control/order/cancel`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(args),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return { ok: false, reason: data.reason ?? data.detail ?? "erro_cancelamento" };
    }
    return { ok: true, reason: data.reason ?? null };
  } catch {
    return { ok: false, reason: "gateway_indisponivel" };
  }
}

// ---- Sugestões manuais (origin="usuário") ---------------------------------
// Relatório de UMA wallet analisada pelo pipeline de discovery. `passes_filters`
// é só um rótulo de UI — o operador pode salvar mesmo o que reprova (força-salvar).
export type SuggestionMetrics = {
  n_trades_30d?: number | null;
  win_rate_30d?: number | null;
  avg_leverage?: number | null;
  avg_holding_hours?: number | null;
  equity?: number | null;
  twrr_30d?: number | null;
  pnl_30d?: number | null;
  profit_factor?: number | null;
  max_drawdown?: number | null;
  liq_distance?: number | null;
  sim_net_pnl_usd?: number | null;
  sim_stage4_net_usd?: number | null;
  sim_expectancy_usd?: number | null;
  sim_max_dd_pct?: number | null;
  sim_factor?: number | null;
  coverage_days?: number | null;
};

// UPDATE-0057 (Fase 2): enriquecimento AGREGADO do HyperTracker, em campos
// SEPARADOS — nunca substitui as métricas de trading da Hyperliquid (`metrics`).
export type HyperTrackerAggregate = {
  earliest_activity_ms: number | null;
  total_equity: number | null;
  perp_pnl: number | null;
  exposure_ratio: number | null;
};

// UPDATE-0059: simulação AMOSTRAL sobre o span REALMENTE coberto. Quando a
// confiança ≠ complete as sim_* longitudinais (`metrics.*`) ficam nulas (inv.
// 0056), mas ESTE bloco reporta honestamente "SIM ~$X em Yd" + projeção /30d.
export type SampleMetrics = {
  sim_net_usd: number | null;
  expectancy_usd: number | null;
  max_dd_pct: number | null;
  window_days: number | null;
  net_per_day: number | null;
  closed_trades: number | null;
};

export type SuggestionReport = {
  address: string;
  name?: string | null;
  passes_filters: boolean;
  score: number | null;
  cohort: string | null;
  reject_reasons: string[];
  rationale: string[];
  // UPDATE-0056/0057/0058 — confiança da amostra + separação idade × amostra.
  metrics_confidence?: string | null;
  wallet_age_days?: number | null;
  fills_sample_days?: number | null;
  fills_sample_count?: number | null;
  fills_complete?: boolean | null;
  metrics_warnings?: string[];
  indeterminate_reasons?: string[];
  hypertracker?: HyperTrackerAggregate | null;
  // UPDATE-0059: simulação amostral (paralela às sim_* longitudinais).
  sample_metrics?: SampleMetrics | null;
  // UPDATE-0062 (v15): fonte das métricas de posição + confiança dos fills. Um
  // trader pode ter posição `complete` via HT E copy sim `sampled` (fills).
  position_metrics_source?: "hypertracker" | "hl_fills" | null;
  fills_metrics_confidence?: string | null;
  metrics: SuggestionMetrics;
};

export type AnalyzeResponse = {
  ok: boolean;
  results: SuggestionReport[];
  summary: { total: number; passa_filtros: number; reprova_filtros: number };
  reason?: string;
};

export type SaveResponse = {
  ok: boolean;
  saved: { address: string; score: number | null; passes_filters: boolean }[];
  skipped: { address: string; reason: string }[];
  summary: { total: number; salvos: number; ignorados: number };
  reason?: string;
};

// Client-side: analisa 1..10 wallets (NÃO grava). Pode demorar (~8-10s/wallet
// fria); o proxy dá 120s de timeout p/ suggestions/*.
export async function analyzeSuggestions(
  addresses: string[],
): Promise<AnalyzeResponse> {
  try {
    const res = await fetch(`/api/control/suggestions/analyze`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ addresses }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return {
        ok: false, results: [],
        summary: { total: 0, passa_filtros: 0, reprova_filtros: 0 },
        reason: data.reason ?? data.detail ?? "erro_analise",
      };
    }
    return data as AnalyzeResponse;
  } catch {
    return {
      ok: false, results: [],
      summary: { total: 0, passa_filtros: 0, reprova_filtros: 0 },
      reason: "gateway_indisponivel",
    };
  }
}

// Client-side: força-salvar as wallets selecionadas como SUGERIDO (origin=usuário).
export async function saveSuggestions(
  addresses: string[],
): Promise<SaveResponse> {
  try {
    const res = await fetch(`/api/control/suggestions/save`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ addresses }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return {
        ok: false, saved: [], skipped: [],
        summary: { total: addresses.length, salvos: 0, ignorados: 0 },
        reason: data.reason ?? data.detail ?? "erro_salvar",
      };
    }
    return data as SaveResponse;
  } catch {
    return {
      ok: false, saved: [], skipped: [],
      summary: { total: addresses.length, salvos: 0, ignorados: 0 },
      reason: "gateway_indisponivel",
    };
  }
}

export type ReclassifyResponse = {
  ok: boolean;
  reclassified: number;
  summary?: { total: number; reclassificados: number; preservados_ou_erro: number };
  results?: { address: string; reclassified: boolean; metrics_confidence?: string | null; reason?: string }[];
  reason?: string;
};

// UPDATE-0059 (backfill): reprocessa linhas legadas (metrics_confidence NULL)
// p/ classificá-las honestamente, PRESERVANDO status/copy_pinned. `addresses`
// opcional: sem ele, o backend alcança todas as NULL em status operacional.
// Pode demorar (~8-10s/wallet fria); o proxy dá 120s p/ discovery/reclassify.
export async function reclassify(
  addresses?: string[],
): Promise<ReclassifyResponse> {
  try {
    const res = await fetch(`/api/control/discovery/reclassify`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(addresses && addresses.length ? { addresses } : {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      return { ok: false, reclassified: 0, reason: data.reason ?? data.detail ?? "erro_reclassify" };
    }
    return data as ReclassifyResponse;
  } catch {
    return { ok: false, reclassified: 0, reason: "gateway_indisponivel" };
  }
}

// UPDATE-0062 (v15): heatmap de viés de mercado do HyperTracker (posições
// abertas nos últimos 7d por ativo). INFORMATIVO — nunca entra no ranking.
export type MarketBias = {
  scan_ts: string;
  logic_version: number;
  payload: unknown | null;
} | null;

export async function getMarketBias(): Promise<MarketBias> {
  // Tolera ausência (sem chave HT / tabela vazia → {} no gateway). Nunca derruba
  // a dashboard: falha vira null e o componente simplesmente não renderiza.
  const data = await gatewayGet<{
    scan_ts?: string;
    logic_version?: number;
    payload?: unknown;
  }>("/api/copy-trade/market-bias");
  if (!data || !data.scan_ts) return null;
  return {
    scan_ts: data.scan_ts,
    logic_version: data.logic_version ?? 0,
    payload: data.payload ?? null,
  };
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
