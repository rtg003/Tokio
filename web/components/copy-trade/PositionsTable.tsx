import { fmtNum, fmtSigned, pnlClass } from "@/lib/format";
import { Position, Trader } from "@/lib/copy-trade/data";
import ClosePositionButton from "./ClosePositionButton";

// Rótulo curto de carteira: 6 primeiros caracteres (mesmo padrão de
// TradesOrdersTable). UPDATE-0065: coluna "Trader" mostra quem copiamos.
const short6 = (a: string | null | undefined) => (a ? a.slice(0, 6) : null);

export default function PositionsTable({
  positions,
  traders,
}: {
  positions: Position[] | null;
  traders: Trader[] | null;
}) {
  const traderMap = new Map<string, Trader>();
  for (const t of traders ?? []) {
    if (t.strategy_id) traderMap.set(t.strategy_id, t);
  }
  const traderLabel = (p: Position): string => {
    const t = p.strategy_id ? traderMap.get(p.strategy_id) : undefined;
    return t?.name ?? short6(t?.address) ?? "—";
  };
  const rows = (positions ?? [])
    .filter((p) => p.size !== 0)
    .sort((a, b) => Math.abs(b.position_value ?? 0) - Math.abs(a.position_value ?? 0));
  return (
    <div className="card">
      <div className="cardhead">
        <h2>Posições</h2>
        <span className="cardnote">
          posições abertas no clearinghouse · escopadas aos símbolos do copy trade · fonte: venue
        </span>
      </div>
      <div className="tablewrap tablewrap-orders">
        {rows.length === 0 ? (
          <div className="empty">nenhuma posição aberta</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Trader</th>
                <th>Ativo</th>
                <th className="num" aria-label="Fechar"></th>
                <th>Lado</th>
                <th className="num">Tamanho</th>
                <th className="num">Entrada</th>
                <th className="num">Liq. Price</th>
                <th className="num">Valor</th>
                <th className="num">Margem</th>
                <th className="num">PnL</th>
                <th className="num">Funding</th>
                <th className="num">Alav.</th>
                <th className="num">TP/SL</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={`${p.strategy_id ?? ""}:${p.symbol}`}>
                  <td>{traderLabel(p)}</td>
                  <td>{p.symbol}</td>
                  <td className="num pos-close-cell">
                    <ClosePositionButton
                      strategyId={p.strategy_id}
                      symbol={p.symbol}
                      env={p.network}
                    />
                  </td>
                  <td>
                    <span className={`side ${p.size > 0 ? "long" : "short"}`}>
                      {p.size > 0 ? "LONG" : "SHORT"}
                    </span>
                  </td>
                  <td className="num">{fmtNum(Math.abs(p.size), 4)}</td>
                  <td className="num">${fmtNum(p.entry_price)}</td>
                  <td className="num">
                    {p.liquidation_px != null ? `$${fmtNum(p.liquidation_px)}` : "—"}
                  </td>
                  <td className="num">
                    {p.position_value != null ? `$${fmtNum(p.position_value)}` : "—"}
                  </td>
                  <td className="num margin-cell">
                    {p.margin_used != null ? `$${fmtNum(p.margin_used, 2)}` : "—"}
                  </td>
                  <td className={`num ${pnlClass(p.unrealized_pnl)}`}>
                    ${fmtSigned(p.unrealized_pnl)}
                  </td>
                  <td className={`num ${p.cum_funding != null ? pnlClass(-p.cum_funding) : ""}`}>
                    {p.cum_funding != null ? `$${fmtSigned(p.cum_funding, 4)}` : "—"}
                  </td>
                  <td className="num">{p.leverage != null ? `${fmtNum(p.leverage, 0)}x` : "—"}</td>
                  <td className="num">—</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
