import { fmtNum, fmtSigned, pnlClass } from "@/lib/format";
import { Balance, FillsSummary, Metrics, PnlSummary } from "@/lib/trading-view/data";

type Props = {
  balance: Balance;
  metrics: Metrics[];
  fillsSummary: FillsSummary;
  pnlSummary: PnlSummary;
  periodLabel: string;
};

export default function KpiRow({ balance, metrics, fillsSummary, pnlSummary, periodLabel }: Props) {
  const m = metrics ?? [];
  const netPnl = pnlSummary.total_pnl;
  const nTrades = fillsSummary.n_trades;
  const winRate =
    fillsSummary.win_rate != null ? fillsSummary.win_rate * 100 : null;
  const maxDd = fillsSummary.max_drawdown ?? 0;
  const pfVals = m.filter((r) => r.profit_factor !== null);
  const metricsPf = pfVals.length
    ? pfVals.reduce((s, r) => s + (r.profit_factor ?? 0), 0) / pfVals.length
    : null;
  const profitFactor =
    fillsSummary.profit_factor != null ? fillsSummary.profit_factor : metricsPf;

  return (
    <div className="kpis">
      <div className="kpi">
        <div className="lab">Saldo</div>
        <div className="val">
          {balance === null ? "—" : `$${fmtNum(balance.withdrawable_usd)}`}
        </div>
        <div className="sub">
          {balance === null
            ? "gateway indisponível"
            : `disponível · equity $${fmtNum(balance.equity_usd)} · ${
                balance.network === "mainnet" ? "mainnet" : "testnet"
              }`}
        </div>
      </div>
      <div className="kpi">
        <div className="lab">PnL líquido</div>
        <div className={`val ${pnlClass(netPnl)}`}>{fmtSigned(netPnl)}</div>
        <div className="sub">USDC · realizado + não-realizado</div>
      </div>
      <div className="kpi">
        <div className="lab">Trades</div>
        <div className="val">{nTrades}</div>
        <div className="sub">{periodLabel}</div>
      </div>
      <div className="kpi">
        <div className="lab">Win rate</div>
        <div className="val">{winRate === null ? "—" : `${fmtNum(winRate, 1)}%`}</div>
        <div className="sub">fills no período</div>
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
