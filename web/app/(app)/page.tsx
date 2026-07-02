import StrategyActions from "@/components/StrategyActions";
import { createClient } from "@/lib/supabase/server";
import {
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

export default async function Dashboard() {
  const supabase = await createClient();
  const since = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10);

  // KPIs come from strategy_metrics_daily — dashboards never scan `events`.
  const [{ data: metrics }, { data: strategies }, { data: orders }, { data: fills }] =
    await Promise.all([
      supabase
        .from("strategy_metrics_daily")
        .select("net_pnl, win_rate, n_trades, fees, profit_factor, max_drawdown")
        .gte("day", since),
      supabase
        .from("strategies")
        .select("id, module, name, status, config_snapshot, created_at")
        .neq("status", "archived")
        .order("id"),
      supabase
        .from("orders")
        .select("cloid, strategy_id, symbol, side, type, size, price, status, created_at, latency_ms, reject_reason")
        .order("created_at", { ascending: false })
        .limit(15),
      supabase
        .from("fills")
        .select("cloid, strategy_id, symbol, side, price, size, fee, realized_pnl, ts")
        .order("ts", { ascending: false })
        .limit(15),
    ]);

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
  const otherStrategies = (strategies ?? []).filter((s) => s.module !== "copy_trade");

  return (
    <section>
      <div className="pagehead">
        <div>
          <div className="eyebrow">Estratégias · copy trade</div>
          <h1>Copy Trade</h1>
        </div>
        <div className="controls">
          <span className="segmented">
            <button className="on">30D</button>
          </span>
        </div>
      </div>

      <div className="kpis">
        <div className="kpi">
          <div className="lab">PnL líquido</div>
          <div className={`val ${pnlClass(netPnl)}`}>{fmtSigned(netPnl)}</div>
          <div className="sub">USDC · após fees + slippage</div>
        </div>
        <div className="kpi">
          <div className="lab">Trades</div>
          <div className="val">{nTrades}</div>
          <div className="sub">últimos 30 dias</div>
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
            descoberta via `strategy list` (banco) · discovery CLI ranqueia candidatos ·
            ativação de dry-run é gate humano
          </span>
        </div>
        <div className="tablewrap">
          {copyStrategies.length === 0 ? (
            <div className="empty">
              nenhum trader registrado — rode o discovery e adicione YAMLs em
              engine/strategies/copy_trade/traders/
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Trader</th>
                  <th>Endereço</th>
                  <th>Sizing</th>
                  <th>Status</th>
                  <th>Criado em</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {copyStrategies.map((s) => {
                  const cfg = (s.config_snapshot ?? {}) as Record<string, unknown>;
                  const mode = cfg["mode"] as string | undefined;
                  const value = cfg["value"] as number | undefined;
                  return (
                    <tr key={s.id}>
                      <td>
                        {s.name}
                        <span className="sub addr">{s.id}</span>
                      </td>
                      <td className="addr">{shortAddr(cfg["address"] as string)}</td>
                      <td>
                        {mode === "percent"
                          ? `${value ?? "—"}× prop.`
                          : mode === "fixed_usdc"
                            ? `${value ?? "—"} USDC fixo`
                            : "—"}
                      </td>
                      <td>
                        <span className={`chip ${statusChip[s.status] ?? "dry"}`}>
                          {s.status === "active" ? "COPIANDO" : s.status.toUpperCase()}
                        </span>
                      </td>
                      <td className="addr">{fmtTime(s.created_at)}</td>
                      <td>
                        <StrategyActions strategyId={s.id} status={s.status} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {otherStrategies.length > 0 && (
        <div className="card">
          <div className="cardhead">
            <h2>Outras estratégias</h2>
            <span className="cardnote">tradingview · standalone · dummy</span>
          </div>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Módulo</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {otherStrategies.map((s) => (
                  <tr key={s.id}>
                    <td>{s.id}</td>
                    <td>{s.module}</td>
                    <td>
                      <span className={`chip ${statusChip[s.status] ?? "dry"}`}>
                        {s.status.toUpperCase()}
                      </span>
                    </td>
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
            <div className="empty">nenhuma ordem registrada ainda</div>
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
            <div className="empty">nenhum fill registrado ainda</div>
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
                    <td>{fmtTime(f.ts)}</td>
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
