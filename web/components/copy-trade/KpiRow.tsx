import { fmtNum, fmtSigned, pnlClass } from "@/lib/format";
import { Balance, Metrics } from "@/lib/copy-trade/data";

type Props = {
  balance: Balance;
  metrics: Metrics[] | null;
  periodLabel: string;
  tradeCount?: number;
};

export default function KpiRow({ balance, metrics, periodLabel, tradeCount }: Props) {
  const m = metrics ?? [];
  const netPnl = m.reduce((s, r) => s + (r.net_pnl ?? 0), 0);
  // KPI de trades: usa metrics.n_trades se houver; senão usa o count de fills
  const metricsTrades = m.reduce((s, r) => s + (r.n_trades ?? 0), 0);
  const nTrades = metricsTrades > 0 ? metricsTrades : (tradeCount ?? 0);
  const withWr = m.filter((r) => r.win_rate !== null);
  const winRate = withWr.length
    ? (withWr.reduce((s, r) => s + (r.win_rate ?? 0), 0) / withWr.length) * 100
    : null;
  const maxDd = m.reduce((worst, r) => Math.max(worst, Math.abs(r.max_drawdown ?? 0)), 0);
  const pfVals = m.filter((r) => r.profit_factor !== null);
  const profitFactor = pfVals.length
    ? pfVals.reduce((s, r) => s + (r.profit_factor ?? 0), 0) / pfVals.length
    : null;

  return (
    <div className="kpis">
      <div className="kpi">
        <div className="lab">Saldo</div>
        <div className="val">{balance === null ? "—" : `$${fmtNum(balance.equity_usd)}`}</div>
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
        <div className="sub">{periodLabel}</div>
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
  );
}
