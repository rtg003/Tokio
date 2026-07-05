import TraderActions from "@/components/TraderActions";
import { fmtDateTime, fmtNum, fmtSigned, pnlClass, shortAddr } from "@/lib/format";
import { Trader } from "@/lib/copy-trade/data";

const traderChip: Record<string, string> = {
  COPIANDO: "live",
  DRY_RUN: "dry",
  SUGERIDO: "sug",
  PAUSADO: "ack",
  REJEITADO: "rej",
  ARQUIVADO: "dry",
};

function parseTopAssets(value: unknown): string[] {
  try {
    return Array.isArray(value) ? value : JSON.parse(String(value ?? "[]"));
  } catch {
    return [];
  }
}

export default function TradersTable({
  traders,
  expanded,
  toggleHref,
}: {
  traders: Trader[] | null;
  expanded: boolean;
  toggleHref: string;
}) {
  const rows = traders ?? [];
  return (
    <div className="card">
      <div className="cardhead">
        <h2>Traders</h2>
        <span className="cardnote">
          fonte: tabela traders · ordenado por score · aprovação (Gate 2) via CLI humana
        </span>
        <a className="btn btn-ghost btn-sm" href={toggleHref}>
          {expanded ? "Colunas núcleo" : "Modo expandido"}
        </a>
      </div>
      <div className="tablewrap">
        {rows.length === 0 ? (
          <div className="empty">
            nenhum trader na tabela — rode o discovery (os candidatos aprovados entram aqui;
            YAMLs não existem mais)
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
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((t, i) => {
                const topAssets = parseTopAssets(t.top_assets);
                return (
                  <tr key={t.address}>
                    <td className="num">{i + 1}</td>
                    <td>
                      {t.name ?? shortAddr(t.address)}
                      <span className="sub addr">{shortAddr(t.address)}</span>
                    </td>
                    <td>
                      <span className="score">
                        {t.score === null || t.score === undefined ? "—" : Math.round(t.score)}
                        <span className="scorebar">
                          <i style={{ width: `${Math.min(100, t.score ?? 0)}%` }} />
                        </span>
                      </span>
                    </td>
                    <td>{t.cohort ?? "—"}</td>
                    <td className={`num ${pnlClass(t.twrr_30d)}`}>
                      {t.twrr_30d === null || t.twrr_30d === undefined
                        ? "—"
                        : `${fmtNum(t.twrr_30d, 1)}%`}
                    </td>
                    <td className={`num ${pnlClass(t.pnl_30d)}`}>
                      {t.pnl_30d === null || t.pnl_30d === undefined ? "—" : fmtSigned(t.pnl_30d, 0)}
                    </td>
                    <td>{t.windows_positive ?? "—"}</td>
                    <td className="num">
                      {t.profit_factor === null || t.profit_factor === undefined
                        ? "—"
                        : fmtNum(t.profit_factor, 2)}
                    </td>
                    <td className="num">
                      {t.win_rate === null || t.win_rate === undefined
                        ? "—"
                        : `${fmtNum(t.win_rate * 100, 0)}%`}
                    </td>
                    <td className="num">
                      {t.max_drawdown === null || t.max_drawdown === undefined
                        ? "—"
                        : `−${fmtNum(t.max_drawdown, 1)}%`}
                    </td>
                    <td>
                      <span className={`chip ${traderChip[t.status] ?? "dry"}`}>{t.status}</span>
                      {t.copy_pinned === 1 && (
                        <span className="chip pinned" title="copy_pinned — protegido do re-scan">
                          pinned
                        </span>
                      )}
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
                          {t.equity === null || t.equity === undefined ? "—" : fmtNum(t.equity, 0)}
                        </td>
                        <td className="addr">{topAssets.length ? topAssets.join(" ") : "—"}</td>
                        <td className="addr">{fmtDateTime(t.last_activity)}</td>
                        <td>
                          {t.mode === "percent" ? `${t.value}× prop.` : `${fmtNum(t.value, 0)} USDC fixo`}
                        </td>
                        <td className="num">
                          {t.liq_distance === null || t.liq_distance === undefined
                            ? "—"
                            : `${fmtNum(t.liq_distance, 1)}%`}
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
  );
}
