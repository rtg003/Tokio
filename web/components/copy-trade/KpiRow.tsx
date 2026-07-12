import { fmtNum, fmtSigned, pnlClass } from "@/lib/format";
import { Balance, FillsSummary, Metrics, PnlSummary } from "@/lib/copy-trade/data";

type Props = {
  balance: Balance;
  metrics: Metrics[] | null;
  fillsSummary: FillsSummary | null;
  pnlSummary?: PnlSummary | null;
  periodLabel: string;
  envFiltered?: boolean;
};

export default function KpiRow({
  balance,
  metrics,
  fillsSummary,
  pnlSummary = null,
  periodLabel,
  envFiltered = false,
}: Props) {
  const m = metrics ?? [];
  const summary = fillsSummary ?? { n_trades: 0, net_pnl: 0, fees: 0, win_rate: null };
  const metricsPnl = m.reduce((s, r) => s + (r.net_pnl ?? 0), 0);
  // PnL líquido = realizado + não-realizado (posições abertas). Cai para o
  // cálculo antigo (só realizado) se o endpoint de PnL estiver indisponível.
  const netPnl =
    pnlSummary !== null
      ? pnlSummary.total_pnl
      : envFiltered
        ? summary.net_pnl
        : metricsPnl;
  const nTrades = summary.n_trades;
  const withWr = m.filter((r) => r.win_rate !== null);
  const metricsWinRate = withWr.length
    ? (withWr.reduce((s, r) => s + (r.win_rate ?? 0), 0) / withWr.length) * 100
    : null;
  // Win rate prefere o summary de fills (respeita wallet/exchange/trader/período);
  // cai para a média diária por estratégia só se o summary não tiver amostra.
  const winRate =
    summary.win_rate !== null && summary.win_rate !== undefined
      ? summary.win_rate * 100
      : metricsWinRate;
  // Drawdown e Profit factor vêm do summary de fills (respeitam os filtros de
  // wallet/exchange/período). Fallback para as métricas diárias por estratégia
  // se o summary não trouxer os campos (gateway antigo).
  const metricsMaxDd = m.reduce(
    (worst, r) => Math.max(worst, Math.abs(r.max_drawdown ?? 0)),
    0,
  );
  const maxDd =
    summary.max_drawdown !== undefined && summary.max_drawdown !== null
      ? summary.max_drawdown
      : metricsMaxDd;
  const pfVals = m.filter((r) => r.profit_factor !== null);
  const metricsPf = pfVals.length
    ? pfVals.reduce((s, r) => s + (r.profit_factor ?? 0), 0) / pfVals.length
    : null;
  const profitFactor =
    summary.profit_factor !== undefined && summary.profit_factor !== null
      ? summary.profit_factor
      : metricsPf;

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
                balance.network === "all"
                  ? "total (testnet + mainnet)"
                  : balance.network === "mainnet"
                    ? "mainnet"
                    : "testnet"
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
