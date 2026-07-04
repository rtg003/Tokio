import DashboardControls, { AccountOption } from "@/components/DashboardControls";
import StrategyActions from "@/components/StrategyActions";
import TraderActions from "@/components/TraderActions";
import { createClient } from "@/lib/supabase/server";
import {
  fmtDateTime,
  fmtNum,
  fmtSigned,
  fmtTime,
  pnlClass,
  shortAddr,
  statusChip,
} from "@/lib/format";

export const dynamic = "force-dynamic";

type Metrics = {
  net_pnl: number;
  win_rate: number | null;
  n_trades: number;
  fees: number;
  profit_factor: number | null;
  max_drawdown: number | null;
};

type Balance = { equity_usd: number; network: string } | null;

async function fetchBalance(): Promise<Balance> {
  const host = process.env.GATEWAY_HOST ?? "gateway";
  const port = process.env.GATEWAY_PORT ?? "8700";
  try {
    const r = await fetch(`http://${host}:${port}/balance`, {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    const data = await r.json();
    if (!data.ok) return null;
    return { equity_usd: data.equity_usd, network: data.network };
  } catch {
    return null;
  }
}

function parseDdMmYy(v: string | undefined): string | null {
  // dd/mm/aa -> YYYY-MM-DD (UTC); null when malformed
  const m = /^(\d{2})\/(\d{2})\/(\d{2})$/.exec(v ?? "");
  if (!m) return null;
  const [, dd, mm, yy] = m;
  const day = Number(dd), month = Number(mm);
  if (day < 1 || day > 31 || month < 1 || month > 12) return null;
  return `20${yy}-${mm}-${dd}`;
}

const PERIOD_LABEL: Record<string, string> = {
  today: "hoje",
  "7d": "últimos 7 dias",
  "30d": "últimos 30 dias",
  custom: "período personalizado",
};

const traderChip: Record<string, string> = {
  COPIANDO: "live",
  DRY_RUN: "dry",
  SUGERIDO: "sug",
  PAUSADO: "ack",
  REJEITADO: "rej",
  ARQUIVADO: "dry",
};

export default async function Dashboard({
  searchParams,
}: {
  searchParams: Promise<{
    period?: string; from?: string; to?: string; account?: string; cols?: string;
  }>;
}) {
  const params = await searchParams;
  const expanded = params.cols === "all";
  const baseQuery = new URLSearchParams(
    Object.entries(params).filter(([k, v]) => k !== "cols" && v) as [string, string][],
  ).toString();
  const period = ["today", "7d", "30d", "custom"].includes(params.period ?? "")
    ? (params.period as string)
    : "30d";

  const todayIso = new Date().toISOString().slice(0, 10);
  let sinceDay = todayIso;
  let untilDay = todayIso;
  if (period === "7d") {
    sinceDay = new Date(Date.now() - 7 * 86400_000).toISOString().slice(0, 10);
  } else if (period === "30d") {
    sinceDay = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10);
  } else if (period === "custom") {
    sinceDay = parseDdMmYy(params.from) ?? sinceDay;
    untilDay = parseDdMmYy(params.to) ?? untilDay;
  }
  const sinceTs = `${sinceDay}T00:00:00Z`;
  const untilTs = `${untilDay}T23:59:59Z`;

  const supabase = await createClient();

  // REGRA DE ISOLAMENTO DE OBSERVABILIDADE (AGENTS.md / ADR 0010): esta é a
  // visão do módulo COPY TRADE — toda query de exibição é filtrada pelos
  // strategy_ids do módulo. Fills/ordens sem atribuição (strategy_id NULL) ou
  // de outros módulos NUNCA aparecem aqui (visão de sistema = tela Logs).
  const { data: strategies } = await supabase
    .from("strategies")
    .select("id, module, name, status, config_snapshot, created_at")
    .neq("status", "archived")
    .order("id");
  const ctIds = (strategies ?? [])
    .filter((s) => s.module === "copy_trade")
    .map((s) => s.id);

  // KPIs come from strategy_metrics_daily — dashboards never scan `events`.
  const [balance, { data: exchanges }, { data: traders }, { data: metrics },
         { data: orders }, { data: fills }] =
    await Promise.all([
      fetchBalance(),
      supabase.from("exchanges").select("name, network, status").order("id"),
      supabase
        .from("traders")
        .select("*")
        .not("status", "in", "(ARQUIVADO,REJEITADO)")
        .order("score", { ascending: false, nullsFirst: false }),
      supabase
        .from("strategy_metrics_daily")
        .select("net_pnl, win_rate, n_trades, fees, profit_factor, max_drawdown")
        .in("strategy_id", ctIds)
        .gte("day", sinceDay)
        .lte("day", untilDay),
      supabase
        .from("orders")
        .select("cloid, strategy_id, symbol, side, type, size, price, status, created_at, latency_ms, reject_reason")
        .in("strategy_id", ctIds)
        .gte("created_at", sinceTs)
        .lte("created_at", untilTs)
        .order("created_at", { ascending: false })
        .limit(15),
      supabase
        .from("fills")
        .select("cloid, strategy_id, symbol, side, price, size, fee, realized_pnl, ts")
        .in("strategy_id", ctIds)
        .gte("ts", sinceTs)
        .lte("ts", untilTs)
        .order("ts", { ascending: false })
        .limit(15),
    ]);

  // value no formato exchange:conta:ambiente (parametriza as queries futuras)
  const accounts: AccountOption[] = (exchanges ?? []).length
    ? (exchanges ?? []).map((e) => ({
        value: `${e.name === "hyperliquid" ? "hl" : e.name}:master:${e.network}`,
        label: `${e.name === "hyperliquid" ? "Hyperliquid" : e.name} · master (${e.network})`,
      }))
    : [{ value: "hl:master:testnet", label: "Hyperliquid · master (testnet)" }];
  const account = params.account ?? accounts[0].value;

  const m = (metrics ?? []) as Metrics[];
  const netPnl = m.reduce((s, r) => s + (r.net_pnl ?? 0), 0);
  const nTrades = m.reduce((s, r) => s + (r.n_trades ?? 0), 0);
  const withWr = m.filter((r) => r.win_rate !== null);
  const winRate = withWr.length
    ? (withWr.reduce((s, r) => s + (r.win_rate ?? 0), 0) / withWr.length) * 100
    : null;
  const maxDd = m.reduce(
    (worst, r) => Math.max(worst, Math.abs(r.max_drawdown ?? 0)),
    0,
  );
  const pfVals = m.filter((r) => r.profit_factor !== null);
  const profitFactor = pfVals.length
    ? pfVals.reduce((s, r) => s + (r.profit_factor ?? 0), 0) / pfVals.length
    : null;

  const copyStrategies = (strategies ?? []).filter((s) => s.module === "copy_trade");

  return (
    <section>
      <div className="pagehead">
        <div>
          <div className="eyebrow">Estratégias · copy trade</div>
          <h1>Copy Trade</h1>
        </div>
        <DashboardControls
          accounts={accounts}
          account={account}
          period={period}
          from={params.from ?? ""}
          to={params.to ?? ""}
        />
      </div>

      <div className="kpis">
        <div className="kpi">
          <div className="lab">Saldo</div>
          <div className="val">
            {balance === null ? "—" : fmtNum(balance.equity_usd)}
          </div>
          <div className="sub">
            {balance === null
              ? "gateway indisponível"
              : `USDC · ${balance.network === "mainnet" ? "mainnet" : "testnet"}`}
          </div>
        </div>
        <div className="kpi">
          <div className="lab">PnL líquido</div>
          <div className={`val ${pnlClass(netPnl)}`}>{fmtSigned(netPnl)}</div>
          <div className="sub">USDC · após fees + slippage</div>
        </div>
        <div className="kpi">
          <div className="lab">Trades</div>
          <div className="val">{nTrades}</div>
          <div className="sub">{PERIOD_LABEL[period]}</div>
        </div>
        <div className="kpi">
          <div className="lab">Win rate</div>
          <div className="val">{winRate === null ? "—" : `${fmtNum(winRate, 1)}%`}</div>
          <div className="sub">média diária ponderada</div>
        </div>
        <div className="kpi">
          <div className="lab">Drawdown</div>
          <div className={`val ${maxDd > 0 ? "neg" : ""}`}>
            {maxDd > 0 ? `−${fmtNum(maxDd, 1)}%` : "—"}
          </div>
          <div className="sub">máx. no período</div>
        </div>
        <div className="kpi">
          <div className="lab">Profit factor</div>
          <div className={`val ${profitFactor && profitFactor >= 1.2 ? "pos" : ""}`}>
            {profitFactor === null ? "—" : fmtNum(profitFactor, 2)}
          </div>
          <div className="sub">threshold ≥ 1,20</div>
        </div>
      </div>

      <div className="card">
        <div className="cardhead">
          <h2>Traders</h2>
          <span className="cardnote">
            fonte: tabela traders · ordenado por score · aprovação (Gate 2) via CLI humana
          </span>
          <a
            className="btn btn-ghost btn-sm"
            href={expanded ? "?" + baseQuery : "?" + baseQuery + "&cols=all"}
          >
            {expanded ? "Colunas núcleo" : "Modo expandido"}
          </a>
        </div>
        <div className="tablewrap">
          {(traders ?? []).length === 0 ? (
            <div className="empty">
              nenhum trader na tabela — rode o discovery (os candidatos aprovados
              entram aqui; YAMLs não existem mais)
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>Trader</th>
                  <th>Score</th>
                  <th>Coorte</th>
                  <th className="num">TWRR 30d</th>
                  <th className="num">PnL 30d</th>
                  <th>Janelas</th>
                  <th className="num">PF</th>
                  <th className="num">Win rate</th>
                  <th className="num">Max DD</th>
                  <th>Status</th>
                  {expanded && (
                    <>
                      <th className="num">Trades 30d</th>
                      <th className="num">Hold méd.</th>
                      <th className="num">Alav. méd.</th>
                      <th className="num">Alav. atual</th>
                      <th className="num">Margem disp.</th>
                      <th className="num">Cópia sim.</th>
                      <th className="num">Cobertura</th>
                      <th className="num">Metades A</th>
                      <th className="num">Equity</th>
                      <th>Ativos</th>
                      <th>Últ. atividade</th>
                      <th>Sizing</th>
                      <th className="num">Dist. liq.</th>
                      <th>Origem</th>
                      <th className="num">Lógica</th>
                    </>
                  )}
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {(traders ?? []).map((t, i) => {
                  const topAssets: string[] = (() => {
                    try {
                      const v = t.top_assets;
                      return Array.isArray(v) ? v : JSON.parse(v ?? "[]");
                    } catch {
                      return [];
                    }
                  })();
                  return (
                    <tr key={t.address}>
                      <td className="num">{i + 1}</td>
                      <td>
                        {t.name ?? shortAddr(t.address)}
                        <span className="sub addr">{shortAddr(t.address)}</span>
                      </td>
                      <td>
                        <span className="score">
                          {t.score === null ? "—" : Math.round(t.score)}
                          <span className="scorebar">
                            <i style={{ width: `${Math.min(100, t.score ?? 0)}%` }} />
                          </span>
                        </span>
                      </td>
                      <td>{t.cohort ?? "—"}</td>
                      <td className={`num ${pnlClass(t.twrr_30d)}`}>
                        {t.twrr_30d === null ? "—" : `${fmtNum(t.twrr_30d, 1)}%`}
                      </td>
                      <td className={`num ${pnlClass(t.pnl_30d)}`}>
                        {t.pnl_30d === null ? "—" : fmtSigned(t.pnl_30d, 0)}
                      </td>
                      <td>
                        {/* Fix 6 / spec col.7: consistência (janelas 7d/30d/60d/90d
                            positivas) — nunca o despejo de JSON da v1 */}
                        {t.windows_positive ?? "—"}
                      </td>
                      <td className="num">
                        {t.profit_factor === null ? "—" : fmtNum(t.profit_factor, 2)}
                      </td>
                      <td className="num">
                        {t.win_rate === null ? "—" : `${fmtNum(t.win_rate * 100, 0)}%`}
                      </td>
                      <td className="num">
                        {t.max_drawdown === null ? "—" : `−${fmtNum(t.max_drawdown, 1)}%`}
                      </td>
                      <td>
                        <span className={`chip ${traderChip[t.status] ?? "dry"}`}>
                          {t.status}
                        </span>
                      </td>
                      {expanded && (
                        <>
                          <td className="num">{t.n_trades_30d ?? "—"}</td>
                          <td className="num">
                            {t.avg_holding_hours === null || t.avg_holding_hours === undefined
                              ? "—"
                              : `${fmtNum(t.avg_holding_hours, 1)}h`}
                          </td>
                          <td className="num">
                            {t.avg_leverage === null || t.avg_leverage === undefined
                              ? "—"
                              : `${fmtNum(t.avg_leverage, 1)}x`}
                          </td>
                          {/* v7: copiabilidade real (posições abertas + simulação) */}
                          <td className="num">
                            {t.max_current_leverage === null || t.max_current_leverage === undefined
                              ? "—"
                              : `${fmtNum(t.max_current_leverage, 1)}x`}
                          </td>
                          <td className="num">
                            {t.available_margin_pct === null || t.available_margin_pct === undefined
                              ? "—"
                              : `${fmtNum(t.available_margin_pct, 0)}%`}
                          </td>
                          <td className={`num ${pnlClass(t.sim_net_pnl_usd)}`}>
                            {t.sim_net_pnl_usd === null || t.sim_net_pnl_usd === undefined
                              ? "—"
                              : fmtSigned(t.sim_net_pnl_usd, 2)}
                          </td>
                          {/* v9: cobertura (F16) e metades da cópia (F18) */}
                          <td className="num">
                            {t.coverage_days === null || t.coverage_days === undefined
                              ? "—"
                              : `${fmtNum(t.coverage_days, 0)}d`}
                          </td>
                          <td className="num">
                            {t.sim_half_new_net === null || t.sim_half_new_net === undefined
                              ? "—"
                              : `${t.sim_half_old_net === null || t.sim_half_old_net === undefined ? "n/d" : fmtSigned(t.sim_half_old_net, 0)} / ${fmtSigned(t.sim_half_new_net, 0)}`}
                          </td>
                          <td className="num">
                            {t.equity === null || t.equity === undefined
                              ? "—"
                              : fmtNum(t.equity, 0)}
                          </td>
                          <td className="addr">
                            {topAssets.length ? topAssets.join(" ") : "—"}
                          </td>
                          <td className="addr">{fmtDateTime(t.last_activity)}</td>
                          <td>
                            {t.mode === "percent"
                              ? `${t.value}× prop.`
                              : `${fmtNum(t.value, 0)} USDC fixo`}
                          </td>
                          <td className="num">
                            {t.liq_distance === null ? "—" : `${fmtNum(t.liq_distance, 1)}%`}
                          </td>
                          <td>{t.origin}</td>
                          <td className="num">v{t.logic_version}</td>
                        </>
                      )}
                      <td>
                        <TraderActions address={t.address} status={t.status} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {copyStrategies.length > 0 && (
        <div className="card">
          <div className="cardhead">
            <h2>Estratégias de espelhamento</h2>
            <span className="cardnote">
              1 estratégia por trader espelhado (atribuição via cloid) · fonte: strategies
            </span>
          </div>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Criada em</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {copyStrategies.map((s) => (
                  <tr key={s.id}>
                    <td>{s.id}</td>
                    <td>
                      <span className={`chip ${statusChip[s.status] ?? "dry"}`}>
                        {s.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="addr">{fmtTime(s.created_at)}</td>
                    <td>
                      <StrategyActions strategyId={s.id} status={s.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <div className="cardhead">
          <h2>Ordens</h2>
          <span className="cardnote">ciclo completo · atribuição por cloid · fonte: tabela orders</span>
        </div>
        <div className="tablewrap">
          {(orders ?? []).length === 0 ? (
            <div className="empty">nenhuma ordem de copy trade no período</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Hora</th>
                  <th>Estratégia</th>
                  <th>Par</th>
                  <th>Lado</th>
                  <th>Tipo</th>
                  <th className="num">Qtd</th>
                  <th className="num">Preço</th>
                  <th>Status</th>
                  <th className="num">Latência</th>
                  <th>cloid</th>
                </tr>
              </thead>
              <tbody>
                {(orders ?? []).map((o) => (
                  <tr key={o.cloid}>
                    <td>{fmtTime(o.created_at)}</td>
                    <td>{o.strategy_id}</td>
                    <td>{o.symbol}</td>
                    <td>
                      <span className={`side ${o.side === "buy" ? "long" : "short"}`}>
                        {o.side === "buy" ? "LONG" : "SHORT"}
                      </span>
                    </td>
                    <td>{String(o.type).toUpperCase()}</td>
                    <td className="num">{fmtNum(o.size, 4)}</td>
                    <td className="num">{o.price ? fmtNum(o.price) : "MKT"}</td>
                    <td>
                      <span className={`chip ${statusChip[o.status] ?? "dry"}`}>
                        {o.status.toUpperCase()}
                      </span>
                      {o.reject_reason && <span className="sub">{o.reject_reason}</span>}
                    </td>
                    <td className="num">
                      {o.latency_ms ? `${Math.round(o.latency_ms)}ms` : "—"}
                    </td>
                    <td className="addr">{shortAddr(o.cloid)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card">
        <div className="cardhead">
          <h2>Trades</h2>
          <span className="cardnote">fills executados · PnL realizado líquido de fees · fonte: tabela fills</span>
        </div>
        <div className="tablewrap">
          {(fills ?? []).length === 0 ? (
            <div className="empty">nenhum trade de copy trade no período</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Hora</th>
                  <th>Estratégia</th>
                  <th>Par</th>
                  <th>Lado</th>
                  <th className="num">Qtd</th>
                  <th className="num">Preço</th>
                  <th className="num">Fee</th>
                  <th className="num">PnL líquido</th>
                  <th>cloid</th>
                </tr>
              </thead>
              <tbody>
                {(fills ?? []).map((f, i) => (
                  <tr key={`${f.cloid}-${i}`}>
                    <td>{fmtDateTime(f.ts)}</td>
                    <td>{f.strategy_id ?? "—"}</td>
                    <td>{f.symbol}</td>
                    <td>
                      <span className={`side ${f.side === "buy" ? "long" : "short"}`}>
                        {f.side === "buy" ? "LONG" : "SHORT"}
                      </span>
                    </td>
                    <td className="num">{fmtNum(f.size, 4)}</td>
                    <td className="num">{fmtNum(f.price)}</td>
                    <td className="num">{fmtNum(f.fee, 4)}</td>
                    <td className={`num ${pnlClass(f.realized_pnl)}`}>
                      {f.realized_pnl === null ? "—" : fmtSigned(f.realized_pnl)}
                    </td>
                    <td className="addr">{shortAddr(f.cloid)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
